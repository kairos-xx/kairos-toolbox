# Copyright (c) 2026 Project contributors.
"""Integrate Codex lifecycle hooks with district attribution."""

from __future__ import annotations

from json import JSONDecodeError, dump, dumps, load, loads
from pathlib import Path
from shlex import quote
from sys import stdin, stdout
from typing import Final, TypedDict, cast

from .provider_state import ProviderState


class Constants:
    """Codex hook metadata.

    Attributes:
        config_directory_name: Config directory name.
        session_name: Session name.
        hooks_relative: Hooks relative.
        environment_variable: Environment variable.
        command_marker: Command marker.

    """

    config_directory_name: Final[str] = ".district"
    session_name: Final[str] = "codex_session_id"
    hooks_relative: Final[Path] = Path(".codex/hooks.json")
    environment_variable: Final[str] = "CODEX_SESSION_ID"
    command_marker: Final[str] = "--codex-hook"


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
    """Represent ToolInput.

    Attributes:
        command: Command.
        description: Description.

    """

    command: str
    description: str


class _Payload(TypedDict, total=False):
    """Represent Payload.

    Attributes:
        hook_event_name: Hook event name.
        session_id: Session id.
        tool_input: Tool input.

    """

    hook_event_name: str
    session_id: str
    tool_input: _ToolInput


type HooksFile = dict[str, list[_Entry]]
type JsonValue = (
    bool
    | int
    | float
    | str
    | _Entry
    | _ToolInput
    | _Payload
    | HooksFile
    | list["JsonValue"]
    | dict[str, "JsonValue"]
    | None
)


class CodexHooks:
    """Install, remove, and handle district Codex hooks."""

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
            ("Stop", None),
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
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(
            data=dumps(obj=data, indent=2) + "\n",
            encoding="utf-8",
        )
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
        _ = path.write_text(
            data=dumps(obj=data, indent=2) + "\n",
            encoding="utf-8",
        )

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
        ProviderState.record(
            home=home,
            agent="codex",
            agent_session=session_id,
            hook_event=payload.get("hook_event_name", ""),
        )
        cls._persist(home=home, session_id=session_id)
        if payload.get("hook_event_name") != "PreToolUse":
            return 0
        tool_input: _ToolInput | None = payload.get("tool_input")
        command: str | None = tool_input.get("command") if tool_input else None
        if tool_input is None or not command:
            return 0
        updated: dict[str, str] = {"command": command}
        if description := tool_input.get("description"):
            updated["description"] = description
        updated["command"] = (
            f"export {Constants.environment_variable}={quote(s=session_id)}; "
            "export DISTRICT_AGENT=codex; "
            f"export DISTRICT_AGENT_SESSION={quote(s=session_id)}; {command}"
        )
        dump(
            obj={
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "updatedInput": updated,
                },
            },
            fp=stdout,
        )
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
        return home / Constants.hooks_relative

    @classmethod
    def _persist(cls, *, home: Path, session_id: str) -> None:
        """Handle persist.

        Args:
            home: Home.
            session_id: Session id.

        """
        out: Path = cls.session_path(home=home)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp: Path = out.with_suffix(suffix=".tmp")
        _ = tmp.write_text(data=f"{session_id}\n", encoding="utf-8")
        _ = tmp.replace(target=out)

    @staticmethod
    def _load(*, path: Path) -> dict[str, JsonValue]:
        """Handle load.

        Args:
            path: Path.

        Returns:
            The resulting value.

        Raises:
            TypeError: When the operation cannot complete.

        """
        if not path.exists():
            return {}
        payload: JsonValue = cast(
            typ="JsonValue",
            val=loads(s=path.read_text(encoding="utf-8")),
        )
        if not isinstance(payload, dict):
            msg: str = f"Expected a JSON object in {path}."
            raise TypeError(msg)
        return cast(typ="dict[str, JsonValue]", val=payload)

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
            msg: str = "Codex 'hooks' must be a JSON object."
            raise TypeError(msg)
        result: HooksFile = {}
        for event, entries in value.items():
            if not isinstance(entries, list):
                msg = "Codex hook events must map strings to arrays."
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
