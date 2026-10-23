# Copyright (c) 2026 Project contributors.
"""Minimal-latency MCP stdio server for Lake Terminal."""

from __future__ import annotations

from json import JSONDecodeError, dumps, loads
from shlex import split
from subprocess import CalledProcessError
from sys import stdin, stdout
from typing import TYPE_CHECKING, BinaryIO, Final, cast

from .core import Constants
from .monitoring import Monitor
from .product import Product

if TYPE_CHECKING:
    from collections.abc import Callable

    from .core import ExecResult, LakeTerminal
    from .models import SessionEntry
type JsonValue = (
    bool
    | int
    | float
    | str
    | list["JsonValue"]
    | dict[str, "JsonValue"]
    | None
)
type JsonObject = dict[str, JsonValue]


class LakeMcpServer:
    """Serve MCP tool calls against a resident Lake Terminal service.

    The server stays resident for the client's lifetime, so every call
    avoids interpreter and service startup cost; requests execute
    in-process against the existing session backends.

    Attributes:
        protocol_version: MCP protocol revision.
        server_name: MCP server name.
        send_keys: Key names accepted by the lake_send tool.
        mutating_tools: Tools hidden and refused in read-only mode.
        tools: Exposed MCP tool descriptors.

    """

    protocol_version: Final[str] = "2025-06-18"
    server_name: Final[str] = "lake-terminal"
    send_keys: Final[frozenset[str]] = Constants.sendable_keys
    mutating_tools: Final[frozenset[str]] = frozenset(
        {"lake_exec", "lake_send", "lake_new", "lake_kill"},
    )
    tools: Final[tuple[JsonObject, ...]] = (
        {
            "name": "lake_exec",
            "description": (
                "Run a shell command inside a persistent Lake Terminal "
                "session and return its captured output and exit status."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to run.",
                    },
                    "session_id": {
                        "type": "string",
                        "description": (
                            "Lake session identifier; defaults to the "
                            "only live session."
                        ),
                    },
                },
                "required": ["command"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "output": {"type": "string"},
                    "exit_status": {"type": "integer"},
                },
                "required": ["session_id", "output", "exit_status"],
            },
        },
        {
            "name": "lake_sessions",
            "description": "List live Lake Terminal sessions.",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "sessions": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                },
                "required": ["sessions"],
            },
        },
        {
            "name": "lake_new",
            "description": (
                "Create a new detached persistent Lake Terminal session "
                "and return its identifier."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "Optional initial shell command for the session."
                        ),
                    },
                    "cwd": {
                        "type": "string",
                        "description": (
                            "Working directory; defaults to the server "
                            "working directory."
                        ),
                    },
                },
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "session": {"type": "string"},
                    "cwd": {"type": "string"},
                },
                "required": ["session_id", "session", "cwd"],
            },
        },
        {
            "name": "lake_kill",
            "description": (
                "Destroy a Lake Terminal session and its backend."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": (
                            "Lake session identifier; defaults to the "
                            "only live session."
                        ),
                    },
                },
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                },
                "required": ["session_id"],
            },
        },
        {
            "name": "lake_send",
            "description": (
                "Type input or press keys in a Lake Terminal session, "
                "for example to answer a prompt or interrupt a process."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": (
                            "Lake session identifier; defaults to the "
                            "only live session."
                        ),
                    },
                    "text": {
                        "type": "string",
                        "description": "Literal text to type.",
                    },
                    "key": {
                        "type": "string",
                        "description": (
                            "Special key name such as C-c, Escape, Up "
                            "or Enter."
                        ),
                    },
                    "enter": {
                        "type": "boolean",
                        "description": (
                            "Press Enter after the input; defaults to "
                            "true when 'text' is given, false otherwise."
                        ),
                    },
                },
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                },
                "required": ["session_id"],
            },
        },
        {
            "name": "lake_output",
            "description": (
                "Read the recent terminal output of a Lake Terminal session."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": (
                            "Lake session identifier; defaults to the "
                            "only live session."
                        ),
                    },
                },
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "output": {"type": "string"},
                },
                "required": ["session_id", "output"],
            },
        },
        {
            "name": "lake_events",
            "description": (
                "List recent Lake Terminal activity events such as "
                "session lifecycle and command runs."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum events returned; default 20.",
                    },
                    "filter": {
                        "type": "string",
                        "description": (
                            "Keep only events containing this text, "
                            "for example a session identifier."
                        ),
                    },
                },
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "events": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                },
                "required": ["events"],
            },
        },
        {
            "name": "lake_commands",
            "description": (
                "List recent commands recorded inside Lake Terminal "
                "sessions with exit status and duration."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": (
                            "Maximum commands returned; default 20."
                        ),
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Keep only commands for this session.",
                    },
                },
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "commands": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                },
                "required": ["commands"],
            },
        },
    )

    def __init__(
        self,
        *,
        service: LakeTerminal,
        reader: BinaryIO | None = None,
        writer: BinaryIO | None = None,
        read_only: bool = False,
    ) -> None:
        """Bind the server to a service and stdio streams.

        Args:
            service: Resident Lake Terminal service.
            reader: Request byte stream; defaults to standard input.
            writer: Response byte stream; defaults to standard output.
            read_only: Expose only inspection tools, never execution.

        """
        self._read_only: bool = read_only
        self._service: LakeTerminal = service
        self._reader: BinaryIO = reader or stdin.buffer
        self._writer: BinaryIO = writer or stdout.buffer

    def serve(self) -> int:
        """Handle framed MCP requests until the client closes the stream.

        Returns:
            Exit status.

        """
        while (body := self._read()) is not None:
            response: JsonObject | None = self._respond(body=body)
            if response is not None:
                self._send(message=response)
        return 0

    @staticmethod
    def _tool_result(
        *,
        text: str,
        is_error: bool = False,
        structured: JsonObject | None = None,
    ) -> JsonObject:
        """Build an MCP tool result payload.

        Args:
            text: Text content.
            is_error: Whether the result reports a tool failure.
            structured: Optional machine-readable result payload.

        Returns:
            Tool result payload in MCP content form.

        """
        result: JsonObject = {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        }
        if structured is not None:
            result["structuredContent"] = structured
        return result

    @staticmethod
    def _error(
        *,
        identifier: JsonValue,
        code: int,
        message: str,
    ) -> JsonObject:
        """Build a JSON-RPC error response.

        Args:
            identifier: Request identifier.
            code: JSON-RPC error code.
            message: Error message.

        Returns:
            JSON-RPC error response object.

        """
        return {
            "jsonrpc": "2.0",
            "id": identifier,
            "error": {"code": code, "message": message},
        }

    @staticmethod
    def _event_line(*, event: JsonObject) -> str:
        """Format one event record for display.

        Args:
            event: Parsed event record.

        Returns:
            Single-line event summary.

        """
        subject: list[str] = [
            str(object=value)
            for value in (
                event.get("agent"),
                event.get("agent_session"),
                event.get("session_id"),
            )
            if value
        ]
        detail: list[str] = [
            str(object=value)
            for value in (
                event.get("state"),
                event.get("command"),
            )
            if value
        ]
        tail: str = " ".join((*subject, *detail))
        return (
            f"{event.get('occurred_at', '-')}  "
            f"{event.get('event_type', '-')}" + (f"  {tail}" if tail else "")
        )

    def _read(self) -> bytes | None:
        """Read one Content-Length framed request body.

        Returns:
            Raw request body, or None at end of stream.

        """
        length: int | None = None
        while True:
            line: bytes = self._reader.readline()
            if not line:
                return None
            header: str = line.decode(
                encoding="utf-8",
                errors="replace",
            ).strip()
            if not header:
                if length is not None:
                    break
                continue
            name: str
            value: str
            name, _, value = header.partition(":")
            if name.lower() == "content-length":
                try:
                    length = int(value.strip())
                except ValueError:
                    length = None
        if length <= 0:
            return None
        return self._reader.read(length)

    def _respond(self, *, body: bytes) -> JsonObject | None:
        """Build the JSON-RPC response for one request body.

        Args:
            body: Raw request body.

        Returns:
            Response object, or None for notifications.

        """
        try:
            parsed: JsonValue = cast(
                typ="JsonValue",
                val=loads(s=body.decode(encoding="utf-8")),
            )
        except (JSONDecodeError, UnicodeDecodeError):
            return self._error(
                identifier=None,
                code=-32700,
                message="Parse error.",
            )
        if not isinstance(parsed, dict):
            return self._error(
                identifier=None,
                code=-32600,
                message="Invalid request.",
            )
        identifier: JsonValue = parsed.get("id")
        method: JsonValue = parsed.get("method")
        if "id" not in parsed or (
            isinstance(method, str) and method.startswith("notifications/")
        ):
            return None
        if not isinstance(method, str):
            return self._error(
                identifier=identifier,
                code=-32600,
                message="Invalid request.",
            )
        result: JsonObject | None = self._result_for(
            parsed=parsed,
            method=method,
        )
        if result is None:
            return self._error(
                identifier=identifier,
                code=-32601,
                message=f"Method not found: {method}",
            )
        return {
            "jsonrpc": "2.0",
            "id": identifier,
            "result": result,
        }

    def _result_for(
        self,
        *,
        parsed: JsonObject,
        method: str,
    ) -> JsonObject | None:
        """Resolve the result payload for a known method.

        Args:
            parsed: Parsed request object.
            method: JSON-RPC method name.

        Returns:
            Result payload, or None when the method is unknown.

        """
        if method == "initialize":
            return {
                "protocolVersion": self.protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": self.server_name,
                    "version": Product.version,
                },
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return cast(
                typ="JsonObject",
                val={
                    "tools": [
                        tool
                        for tool in self.tools
                        if not self._read_only
                        or tool["name"] not in self.mutating_tools
                    ],
                },
            )
        if method == "tools/call":
            return self._call(parsed=parsed)
        return None

    def _call(self, *, parsed: JsonObject) -> JsonObject:
        """Dispatch one tools/call request to a Lake Terminal tool.

        Args:
            parsed: Parsed request object.

        Returns:
            Tool result payload in MCP content form.

        """
        params: JsonValue = parsed.get("params")
        parameters: JsonObject = params if isinstance(params, dict) else {}
        name: JsonValue = parameters.get("name")
        raw_arguments: JsonValue = parameters.get("arguments")
        arguments: JsonObject = (
            raw_arguments if isinstance(raw_arguments, dict) else {}
        )
        handlers: dict[str, Callable[[], JsonObject]] = {
            "lake_exec": lambda: self._tool_exec(arguments=arguments),
            "lake_sessions": self._tool_sessions,
            "lake_new": lambda: self._tool_new(arguments=arguments),
            "lake_kill": lambda: self._tool_kill(arguments=arguments),
            "lake_send": lambda: self._tool_send(arguments=arguments),
            "lake_output": lambda: self._tool_output(arguments=arguments),
            "lake_events": lambda: self._tool_events(arguments=arguments),
            "lake_commands": (
                lambda: self._tool_commands(arguments=arguments)
            ),
        }
        if self._read_only and name in self.mutating_tools:
            return self._tool_result(
                text=f"{name} is unavailable: server runs in read-only mode.",
                is_error=True,
            )
        handler: Callable[[], JsonObject] | None = (
            handlers.get(name) if isinstance(name, str) else None
        )
        if handler is None:
            return self._tool_result(
                text=f"Unknown lake tool: {name!r}.",
                is_error=True,
            )
        return handler()

    def _tool_exec(self, *, arguments: JsonObject) -> JsonObject:
        """Run a command inside a persistent Lake Terminal session.

        Args:
            arguments: Tool arguments.

        Returns:
            Tool result payload in MCP content form.

        """
        command: JsonValue = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return self._tool_result(
                text="Missing required 'command' argument.",
                is_error=True,
            )
        resolved: str | JsonObject = self._resolve_session(
            tool="lake_exec",
            arguments=arguments,
        )
        if not isinstance(resolved, str):
            return resolved
        session_id: str = resolved
        try:
            result: ExecResult = self._service.execute(
                session_id=session_id,
                command=command,
            )
        except KeyError:
            return self._tool_result(
                text=f"Unknown lake session: {session_id}.",
                is_error=True,
            )
        except TimeoutError:
            return self._tool_result(
                text=f"Command in lake session {session_id} timed out.",
                is_error=True,
            )
        except (OSError, CalledProcessError, RuntimeError) as failed:
            return self._tool_result(
                text=f"Could not run command in {session_id}: {failed}.",
                is_error=True,
            )
        output: str = result.output
        if not output.endswith("\n"):
            output += "\n"
        return self._tool_result(
            text=f"{output}[exit {result.exit_status}]",
            structured={
                "session_id": session_id,
                "output": result.output,
                "exit_status": result.exit_status,
            },
        )

    def _tool_sessions(self) -> JsonObject:
        """List live Lake Terminal sessions.

        Returns:
            Tool result payload in MCP content form.

        """
        entries: tuple[SessionEntry, ...] = self._service.list_entries()
        if not entries:
            return self._tool_result(
                text="No live lake sessions.",
                structured={"sessions": []},
            )
        rows: list[JsonObject] = [
            {
                "id": entry.id,
                "backend": entry.backend,
                "agent": entry.agent or "",
                "state": entry.agent_state,
                "command": entry.current_command or entry.command,
                "dir": entry.cwd or entry.workspace,
            }
            for entry in entries
        ]
        lines: list[str] = [
            "\t".join(
                (
                    str(object=row["id"]),
                    str(object=row["backend"]),
                    str(object=row["agent"]) or "-",
                    str(object=row["state"]),
                    str(object=row["command"]),
                    str(object=row["dir"]),
                ),
            )
            for row in rows
        ]
        return self._tool_result(
            text="ID\tBACKEND\tAGENT\tSTATE\tCOMMAND\tDIR\n"
            + "\n".join(lines),
            structured=cast(
                typ="JsonObject",
                val={"sessions": rows},
            ),
        )

    def _tool_new(self, *, arguments: JsonObject) -> JsonObject:
        """Create one detached persistent session.

        Args:
            arguments: Tool arguments.

        Returns:
            Tool result payload in MCP content form.

        """
        raw_command: JsonValue = arguments.get("command")
        if raw_command is not None and not isinstance(raw_command, str):
            return self._tool_result(
                text="'command' must be a string.",
                is_error=True,
            )
        raw_cwd: JsonValue = arguments.get("cwd")
        if raw_cwd is not None and not isinstance(raw_cwd, str):
            return self._tool_result(
                text="'cwd' must be a string.",
                is_error=True,
            )
        initial: tuple[str, ...] = (
            tuple(split(s=raw_command))
            if isinstance(raw_command, str) and raw_command.strip()
            else ()
        )
        try:
            entry: SessionEntry = self._service.spawn(
                initial_command=initial,
                initiator="agent",
                cwd=raw_cwd if isinstance(raw_cwd, str) else "",
            )
        except (RuntimeError, OSError) as failed:
            return self._tool_result(
                text=f"Could not create lake session: {failed}.",
                is_error=True,
            )
        return self._tool_result(
            text=(
                f"Created lake session {entry.id} "
                f"(attach with `lake {entry.id}`).\n"
            ),
            structured={
                "session_id": entry.id,
                "session": entry.session,
                "cwd": entry.cwd,
            },
        )

    def _tool_kill(self, *, arguments: JsonObject) -> JsonObject:
        """Destroy one session and its backend.

        Args:
            arguments: Tool arguments.

        Returns:
            Tool result payload in MCP content form.

        """
        resolved: str | JsonObject = self._resolve_session(
            tool="lake_kill",
            arguments=arguments,
        )
        if not isinstance(resolved, str):
            return resolved
        try:
            self._service.kill(session_id=resolved)
        except KeyError:
            return self._tool_result(
                text=f"Unknown lake session: {resolved}.",
                is_error=True,
            )
        except (RuntimeError, OSError) as failed:
            return self._tool_result(
                text=f"Could not kill lake session {resolved}: {failed}.",
                is_error=True,
            )
        return self._tool_result(
            text=f"Killed lake session {resolved}.\n",
            structured={"session_id": resolved},
        )

    def _tool_send(self, *, arguments: JsonObject) -> JsonObject:
        """Inject input or key presses into a session.

        Args:
            arguments: Tool arguments.

        Returns:
            Tool result payload in MCP content form.

        """
        resolved: str | JsonObject = self._resolve_session(
            tool="lake_send",
            arguments=arguments,
        )
        if not isinstance(resolved, str):
            return resolved
        payload: tuple[str, tuple[str, ...]] | JsonObject = self._send_payload(
            arguments=arguments,
        )
        if isinstance(payload, dict):
            return payload
        text: str
        keys: tuple[str, ...]
        text, keys = payload
        try:
            self._service.send(
                session_id=resolved,
                text=text,
                keys=keys,
            )
        except KeyError:
            return self._tool_result(
                text=f"Unknown lake session: {resolved}.",
                is_error=True,
            )
        except (
            RuntimeError,
            CalledProcessError,
            OSError,
            ValueError,
        ) as failed:
            return self._tool_result(
                text=f"Could not send input to {resolved}: {failed}.",
                is_error=True,
            )
        return self._tool_result(
            text=f"Sent input to lake session {resolved}.\n",
            structured={"session_id": resolved},
        )

    def _send_payload(
        self,
        *,
        arguments: JsonObject,
    ) -> tuple[str, tuple[str, ...]] | JsonObject:
        """Validate lake_send arguments into literal text and keys.

        Args:
            arguments: Tool arguments.

        Returns:
            Text and key tuple, or an error result payload.

        """
        raw_text: JsonValue = arguments.get("text")
        if raw_text is not None and not isinstance(raw_text, str):
            return self._tool_result(
                text="'text' must be a string.",
                is_error=True,
            )
        raw_key: JsonValue = arguments.get("key")
        if raw_key is not None and (
            not isinstance(raw_key, str) or raw_key not in self.send_keys
        ):
            return self._tool_result(
                text=f"Unsupported 'key' value: {raw_key!r}.",
                is_error=True,
            )
        raw_enter: JsonValue = arguments.get("enter")
        if raw_enter is not None and not isinstance(raw_enter, bool):
            return self._tool_result(
                text="'enter' must be a boolean.",
                is_error=True,
            )
        keys: list[str] = [raw_key] if isinstance(raw_key, str) else []
        enter: bool = bool(raw_text) if raw_enter is None else raw_enter
        if enter:
            keys.append("Enter")
        if (not raw_text) and not keys:
            return self._tool_result(
                text="Nothing to send: provide 'text', 'key' or 'enter'.",
                is_error=True,
            )
        return (
            raw_text if isinstance(raw_text, str) else "",
            tuple(keys),
        )

    def _resolve_session(
        self,
        *,
        tool: str,
        arguments: JsonObject,
    ) -> str | JsonObject:
        """Resolve the optional session argument to one identifier.

        Args:
            tool: Tool name used in error text.
            arguments: Tool arguments.

        Returns:
            Session identifier, or an error result payload.

        """
        raw_session: JsonValue = arguments.get("session_id")
        if raw_session is not None and not isinstance(raw_session, str):
            return self._tool_result(
                text=f"'session_id' for {tool} must be a string.",
                is_error=True,
            )
        if raw_session:
            return raw_session
        entries: tuple[SessionEntry, ...] = self._service.list_entries()
        if len(entries) != 1:
            return self._tool_result(
                text=(
                    f"{tool} needs exactly one live Lake Terminal "
                    f"session; found {len(entries)}."
                ),
                is_error=True,
            )
        return entries[0].id

    def _tool_output(self, *, arguments: JsonObject) -> JsonObject:
        """Read the recent terminal output of one session.

        Args:
            arguments: Tool arguments.

        Returns:
            Tool result payload in MCP content form.

        """
        resolved: str | JsonObject = self._resolve_session(
            tool="lake_output",
            arguments=arguments,
        )
        if not isinstance(resolved, str):
            return resolved
        entry: SessionEntry | None = next(
            (
                candidate
                for candidate in self._service.list_entries()
                if candidate.id == resolved
            ),
            None,
        )
        if entry is None:
            return self._tool_result(
                text=f"Unknown lake session: {resolved}.",
                is_error=True,
            )
        output: str = Monitor.snapshot(entry=entry).output
        if not output.strip():
            return self._tool_result(
                text=f"No captured output for lake session {resolved}.",
                structured={"session_id": resolved, "output": ""},
            )
        return self._tool_result(
            text=f"{output}\n",
            structured={"session_id": resolved, "output": output},
        )

    def _tool_events(self, *, arguments: JsonObject) -> JsonObject:
        """List recent Lake Terminal activity events.

        Args:
            arguments: Tool arguments.

        Returns:
            Tool result payload in MCP content form.

        """
        limit: JsonObject | int = self._limit(arguments=arguments)
        if isinstance(limit, dict):
            return limit
        raw_filter: JsonValue = arguments.get("filter")
        if raw_filter is not None and not isinstance(raw_filter, str):
            return self._tool_result(
                text="'filter' must be a string.",
                is_error=True,
            )
        filters: tuple[str, ...] = (
            (raw_filter,) if isinstance(raw_filter, str) and raw_filter else ()
        )
        rows: tuple[str, ...] = self._service.repository.database.event_json(
            filters=filters,
        )[-limit:]
        if not rows:
            return self._tool_result(
                text="No recorded lake events.",
                structured={"events": []},
            )
        events: list[JsonObject] = []
        for row in rows:
            event: JsonValue = cast(
                typ="JsonValue",
                val=loads(s=row),
            )
            if isinstance(event, dict):
                events.append(event)
        return self._tool_result(
            text=(
                "\n".join(self._event_line(event=event) for event in events)
                if events
                else "No recorded lake events."
            ),
            structured=cast(
                typ="JsonObject",
                val={"events": events},
            ),
        )

    def _tool_commands(self, *, arguments: JsonObject) -> JsonObject:
        """List recent commands recorded inside lake sessions.

        Args:
            arguments: Tool arguments.

        Returns:
            Tool result payload in MCP content form.

        """
        limit: JsonObject | int = self._limit(arguments=arguments)
        if isinstance(limit, dict):
            return limit
        raw_session: JsonValue = arguments.get("session_id")
        if raw_session is not None and not isinstance(raw_session, str):
            return self._tool_result(
                text="'session_id' must be a string.",
                is_error=True,
            )
        rows: list[JsonValue] = [
            cast(typ="JsonValue", val=loads(s=row))
            for row in self._service.repository.database.command_json()
        ]
        if isinstance(raw_session, str) and raw_session:
            rows = [
                record
                for record in rows
                if isinstance(record, dict)
                and record.get("session_id") == raw_session
            ]
        records: list[JsonObject] = [
            record for record in rows[-limit:] if isinstance(record, dict)
        ]
        if not records:
            return self._tool_result(
                text="No recorded lake commands.",
                structured={"commands": []},
            )
        lines: list[str] = [
            "EXIT\tSECONDS\tSESSION\tCOMMAND",
            *(
                "\t".join(
                    (
                        str(object=record.get("exit_code") or "-"),
                        str(object=record.get("duration_seconds") or "-"),
                        str(object=record.get("session_id") or "-"),
                        str(object=record.get("command") or "-"),
                    ),
                )
                for record in records
            ),
        ]
        return self._tool_result(
            text="\n".join(lines),
            structured=cast(
                typ="JsonObject",
                val={"commands": records},
            ),
        )

    def _limit(self, *, arguments: JsonObject) -> JsonObject | int:
        """Resolve the optional positive limit argument.

        Args:
            arguments: Tool arguments.

        Returns:
            Positive limit, or an error result payload.

        """
        raw_limit: JsonValue = arguments.get("limit")
        if raw_limit is None:
            return 20
        if (
            not isinstance(raw_limit, int)
            or isinstance(raw_limit, bool)
            or raw_limit <= 0
        ):
            return self._tool_result(
                text="'limit' must be a positive integer.",
                is_error=True,
            )
        return raw_limit

    def _send(self, *, message: JsonObject) -> None:
        """Write one Content-Length framed response.

        Args:
            message: Response object.

        """
        body: bytes = dumps(
            obj=message,
            separators=(",", ":"),
        ).encode(encoding="utf-8")
        self._writer.write(
            f"Content-Length: {len(body)}\r\n\r\n".encode(encoding="ascii")
            + body,
        )
        self._writer.flush()
