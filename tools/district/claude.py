# Copyright (c) 2026 Project contributors.
"""Integrate Claude Code lifecycle hooks with district attribution."""

from __future__ import annotations

from json import JSONDecodeError, load
from os import environ
from pathlib import Path
from shlex import quote
from sys import stdin
from typing import TYPE_CHECKING, Final, TypedDict, cast

from .agent_identity import AgentIdentity
from .hook_session import HookSessions
from .json_config import JsonConfig, JsonObject
from .provider_state import ProviderState

if TYPE_CHECKING:
    from .models import SessionEntry


class Constants:
    """Claude Code hook metadata.

    Attributes:
        config_directory_name: Config directory name.
        session_name: Session name.
        settings_relative: Settings relative.
        environment_variable: Environment variable.
        command_marker: Command marker.
        agent: Canonical provider identifier.

    """

    config_directory_name: Final[str] = ".district"
    session_name: Final[str] = "claude_session_id"
    settings_relative: Final[Path] = Path(".claude/settings.json")
    environment_variable: Final[str] = "CLAUDE_SESSION_ID"
    command_marker: Final[str] = "--claude-hook"
    agent: Final[str] = "claude-code"


class _Command(TypedDict):
    """Represent Command.

    Attributes:
        type: Type.
        command: Command.

    """

    type: str
    command: str


class _Entry(TypedDict, total=False):
    """Represent Entry.

    Attributes:
        matcher: Matcher.
        hooks: Hooks.

    """

    matcher: str
    hooks: list[_Command]


class _ToolInput(TypedDict, total=False):
    """Represent the Bash tool input.

    Attributes:
        command: Shell command.

    """

    command: str


class _Payload(TypedDict, total=False):
    """Represent Payload.

    Attributes:
        hook_event_name: Hook event name.
        session_id: Session id.
        cwd: Working directory reported by Claude Code.
        tool_name: Tool name.
        tool_use_id: Tool-use identifier.
        tool_input: Tool input.
        tool_response: Tool response.

    """

    hook_event_name: str
    session_id: str
    cwd: str
    tool_name: str
    tool_use_id: str
    tool_input: _ToolInput
    tool_response: JsonObject


type HooksFile = dict[str, list[_Entry]]
type JsonValue = (
    bool
    | int
    | float
    | str
    | _Entry
    | _Payload
    | HooksFile
    | list["JsonValue"]
    | dict[str, "JsonValue"]
    | None
)


class ClaudeHooks:
    """Install, remove, and handle district Claude Code hooks."""

    @classmethod
    def install(cls, *, home: Path, launcher: Path) -> Path:
        """Handle install.

        Args:
            home: Home.
            launcher: Launcher.

        Returns:
            The resulting value.

        """
        path: Path = cls.config_path(home=home)
        data: dict[str, JsonValue] = cls._load(path=path)
        hooks: HooksFile = cls._coerce_hooks(value=data.get("hooks", {}))
        command: str = (
            f"{quote(s=str(object=launcher))} {Constants.command_marker}"
        )
        for event, matcher in (
            ("SessionStart", None),
            ("UserPromptSubmit", None),
            ("PreToolUse", "Bash"),
            ("PermissionRequest", "Bash"),
            ("PostToolUse", "Bash"),
            ("PostToolUseFailure", "Bash"),
            ("Stop", None),
            ("StopFailure", None),
            ("SessionEnd", None),
        ):
            entries: list[_Entry] = [
                entry
                for entry in hooks.get(event, [])
                if not cls._is_ours(entry=entry)
            ]
            entry: _Entry = {
                "hooks": [{"type": "command", "command": command}],
            }
            if matcher is not None:
                entry["matcher"] = matcher
            entries.append(entry)
            hooks[event] = entries
        data["hooks"] = hooks
        cls._save(path=path, data=data)
        return path

    @classmethod
    def uninstall(cls, *, home: Path) -> None:
        """Handle uninstall.

        Args:
            home: Home.

        """
        path: Path = cls.config_path(home=home)
        if not path.is_file():
            return
        data: dict[str, JsonValue] = cls._load(path=path)
        existing: JsonValue | None = data.get("hooks")
        if not isinstance(existing, dict):
            return
        hooks: HooksFile = cls._coerce_hooks(value=existing)
        cleaned: HooksFile = {
            event: remaining
            for event, entries in hooks.items()
            if (
                remaining := [
                    entry for entry in entries if not cls._is_ours(entry=entry)
                ]
            )
        }
        if cleaned:
            data["hooks"] = cleaned
        else:
            _ = data.pop("hooks", None)
        cls._save(path=path, data=data)

    @classmethod
    def run_hook(cls, *, home: Path) -> int:
        """Handle run hook.

        Args:
            home: Home.

        Returns:
            The resulting value.

        """
        try:
            raw: JsonValue = cast(typ="JsonValue", val=load(fp=stdin))
            payload: _Payload = cast(typ="_Payload", val=raw)
        except JSONDecodeError:
            return 0
        session_id: str | None = payload.get("session_id")
        if not session_id:
            return 0
        event: str = payload.get("hook_event_name", "")
        cls._observe(
            home=home,
            session_id=session_id,
            event=event,
            payload=payload,
        )
        out: Path = cls.session_path(home=home)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp: Path = out.with_suffix(suffix=".tmp")
        _ = tmp.write_text(data=f"{session_id}\n", encoding="utf-8")
        _ = tmp.replace(target=out)
        if event == "SessionStart" and (
            env_file := environ.get("CLAUDE_ENV_FILE")
        ):
            cls._export(env_file=Path(env_file), session_id=session_id)
        return 0

    @staticmethod
    def session_path(*, home: Path) -> Path:
        """Handle session path.

        Args:
            home: Home.

        Returns:
            The resulting value.

        """
        return home / Constants.config_directory_name / Constants.session_name

    @staticmethod
    def config_path(*, home: Path) -> Path:
        """Handle config path.

        Args:
            home: Home.

        Returns:
            The resulting value.

        """
        return home / Constants.settings_relative

    @staticmethod
    def _observe(
        *,
        home: Path,
        session_id: str,
        event: str,
        payload: _Payload,
    ) -> None:
        """Mirror one Claude Code event into the hook-backed District session.

        Args:
            home: Home.
            session_id: Claude Code session identifier.
            event: Hook event name.
            payload: Hook payload.

        """
        workspace: str = payload.get("cwd") or str(object=Path.cwd())
        entry: SessionEntry | None = HookSessions.ensure(
            home=home,
            agent=Constants.agent,
            agent_session=session_id,
            workspace=workspace,
            create=event != "SessionEnd",
        )
        is_bash: bool = payload.get("tool_name") == "Bash"
        tool_use_id: str = payload.get("tool_use_id", "")
        command: str = (payload.get("tool_input") or {}).get("command", "")
        if entry is not None and is_bash and tool_use_id:
            if event == "PreToolUse" and command:
                HookSessions.command_started(
                    home=home,
                    entry=entry,
                    tool_use_id=tool_use_id,
                    command=command,
                    cwd=workspace,
                )
            elif event in {"PostToolUse", "PostToolUseFailure"}:
                HookSessions.command_finished(
                    home=home,
                    entry=entry,
                    tool_use_id=tool_use_id,
                    failed=event == "PostToolUseFailure",
                    response=payload.get("tool_response"),
                )
        ProviderState.record(
            home=home,
            agent=Constants.agent,
            agent_session=session_id,
            hook_event=event,
        )

    @staticmethod
    def _export(*, env_file: Path, session_id: str) -> None:
        """Append the District identity to Claude Code's per-session env file.

        Every later Bash tool call sources this file, so child shells and
        ``district --agent`` inherit the same provider session.

        Args:
            env_file: File named by ``CLAUDE_ENV_FILE``.
            session_id: Claude Code session identifier.

        """
        identity: AgentIdentity | None = AgentIdentity.from_environment(
            environment={
                **environ,
                "DISTRICT_AGENT": Constants.agent,
                "DISTRICT_AGENT_SESSION": session_id,
            },
        )
        variables: dict[str, str] = {
            Constants.environment_variable: session_id,
            **(identity.district_variables() if identity else {}),
        }
        with env_file.open(mode="a", encoding="utf-8") as stream:
            for name, value in variables.items():
                _ = stream.write(f"export {name}={quote(s=value)}\n")

    @staticmethod
    def _load(*, path: Path) -> dict[str, JsonValue]:
        """Load Claude's settings, refusing to touch invalid JSON.

        Args:
            path: Path.

        Returns:
            The resulting value.

        """
        return cast(typ="dict[str, JsonValue]", val=JsonConfig.load(path=path))

    @staticmethod
    def _save(*, path: Path, data: dict[str, JsonValue]) -> None:
        """Atomically save Claude's settings with a one-time backup.

        Args:
            path: Path.
            data: Data.

        """
        JsonConfig.save(path=path, data=cast(typ="JsonObject", val=data))

    @staticmethod
    def _coerce_hooks(*, value: JsonValue) -> HooksFile:
        """Handle coerce hooks.

        Args:
            value: Value.

        Returns:
            The resulting value.

        Raises:
            TypeError: When the operation cannot complete.

        """
        if not isinstance(value, dict):
            msg: str = "Claude 'hooks' must be a JSON object."
            raise TypeError(msg)
        result: HooksFile = {}
        for event, entries in value.items():
            if not isinstance(entries, list):
                msg = "Claude hook events must map strings to arrays."
                raise TypeError(msg)
            result[event] = cast(typ="list[_Entry]", val=entries)
        return result

    @staticmethod
    def _is_ours(*, entry: _Entry) -> bool:
        """Handle is ours.

        Args:
            entry: Entry.

        Returns:
            The resulting value.

        """
        return any(
            Constants.command_marker in hook.get("command", "")
            for hook in entry.get("hooks", [])
        )
