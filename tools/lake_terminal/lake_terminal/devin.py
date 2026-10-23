# Copyright (c) 2026 Project contributors.
"""Integrate Devin hook events with lake-terminal agent attribution."""

from __future__ import annotations

from json import JSONDecodeError, dumps, load, loads
from pathlib import Path
from shlex import quote
from sys import stdin
from typing import Final, TypedDict, cast

from .storage import LakeDatabase


class Constants:
    """Group immutable Devin integration paths and hook metadata.

    Attributes:
        config_directory_name: Config directory name.
        user_config_relative: User config relative.
        events: Events.
        command_marker: Command marker.

    """

    config_directory_name: Final[str] = ".lake_terminal"
    user_config_relative: Final[Path] = Path(".config/devin/config.json")
    events: Final[tuple[tuple[str, str | None], ...]] = (
        ("SessionStart", None),
        ("UserPromptSubmit", None),
        ("PreToolUse", "exec"),
    )
    command_marker: Final[str] = "--devin-hook"


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

    """

    command: str


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


class DevinHooks:
    """Install, remove, and handle Devin hooks owned by lake-terminal."""

    @classmethod
    def install(cls, *, home: Path, launcher: Path) -> Path:
        """Merge lake-terminal hook entries into Devin's user configuration.

        Args:
            home: Home.
            launcher: Launcher.

        Returns:
            The resulting value.

        """
        path: Path = cls.config_path(home=home)
        data: dict[str, JsonValue] = cls._load(path=path)
        existing: JsonValue = data.get("hooks", {})
        hooks: HooksFile = cls._coerce_hooks(value=existing)
        data["hooks"] = cls._merge(
            hooks=hooks,
            launcher=launcher,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(
            data=dumps(obj=data, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    @classmethod
    def uninstall(cls, *, home: Path) -> None:
        """Remove only lake-terminal-owned entries from Devin's user config.

        Args:
            home: Home.

        """
        path: Path = cls.config_path(home=home)
        if not path.is_file():
            return
        data: dict[str, JsonValue] = cls._load(path=path)
        existing: object = data.get("hooks")
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

    @staticmethod
    def run_hook(*, home: Path) -> int:
        """Register Devin session identity without rewriting tool commands.

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
        LakeDatabase(home=home).set_agent_context(
            provider="devin",
            session_id=session_id,
            workspace=str(object=Path.cwd()),
        )
        return 0

    @staticmethod
    def config_path(*, home: Path) -> Path:
        """Return Devin's user configuration path.

        Args:
            home: Home.

        Returns:
            The resulting value.

        """
        return home / Constants.user_config_relative

    @classmethod
    def _merge(
        cls,
        *,
        hooks: HooksFile,
        launcher: Path,
    ) -> HooksFile:
        """Add or replace this package's entries for each Devin hook event.

        Args:
            hooks: Hooks.
            launcher: Launcher.

        Returns:
            The resulting value.

        """
        merged: HooksFile = dict(hooks)
        command: str = (
            f"{quote(s=str(object=launcher))} {Constants.command_marker}"
        )
        for event, matcher in Constants.events:
            entries: list[_Entry] = [
                entry
                for entry in merged.get(event, [])
                if not cls._is_ours(entry=entry)
            ]
            hook: _Entry = {"hooks": [{"type": "command", "command": command}]}
            if matcher is not None:
                hook["matcher"] = matcher
            entries.append(hook)
            merged[event] = entries
        return merged

    @staticmethod
    def _load(*, path: Path) -> dict[str, JsonValue]:
        """Load a JSON object, returning an empty object when absent.

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
        """Validate and return Devin's hook mapping.

        Args:
            value: Value.

        Returns:
            The resulting value.

        Raises:
            TypeError: When the operation cannot complete.

        """
        if not isinstance(value, dict):
            msg: str = "Devin 'hooks' must be a JSON object."
            raise TypeError(msg)
        result: HooksFile = {}
        for event, entries in value.items():
            if not isinstance(entries, list):
                msg = "Devin hook events must map strings to arrays."
                raise TypeError(msg)
            result[event] = cast(typ="list[_Entry]", val=entries)
        return result

    @staticmethod
    def _is_ours(*, entry: _Entry) -> bool:
        """Return whether a Devin hook entry belongs to lake-terminal.

        Args:
            entry: Entry.

        Returns:
            The resulting value.

        """
        return any(
            Constants.command_marker in hook.get("command", "")
            for hook in entry.get("hooks", [])
        )
