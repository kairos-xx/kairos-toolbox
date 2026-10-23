# Copyright (c) 2026 Project contributors.
"""Integrate Devin hook events with lake-terminal agent attribution."""

from __future__ import annotations

from json import JSONDecodeError, load
from pathlib import Path
from shlex import quote
from sys import stdin
from typing import TYPE_CHECKING, Final, TypedDict, cast

from .hook_session import HookSessions
from .identity import IdentityStore
from .json_config import JsonConfig, JsonObject
from .provider_state import ProviderState
from .storage import LakeDatabase

if TYPE_CHECKING:
    from .models import SessionEntry


class Constants:
    """Group immutable Devin integration paths and hook metadata.

    Attributes:
        config_directory_name: Config directory name.
        user_config_relative: User config relative.
        events: Events.
        command_marker: Command marker.
        agent: Canonical provider identifier.

    """

    config_directory_name: Final[str] = ".lake_terminal"
    user_config_relative: Final[Path] = Path(".config/devin/config.json")
    events: Final[tuple[tuple[str, str | None], ...]] = (
        ("SessionStart", None),
        ("UserPromptSubmit", None),
        ("PreToolUse", "exec"),
    )
    command_marker: Final[str] = "--devin-hook"
    agent: Final[str] = "devin"


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
        cwd: Working directory reported by Devin.
        tool_use_id: Tool-use identifier, when Devin provides one.
        tool_input: Tool input.

    """

    hook_event_name: str
    session_id: str
    cwd: str
    tool_use_id: str
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
        cls._save(path=path, data=data)
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
        cls._save(path=path, data=data)

    @staticmethod
    def run_hook(*, home: Path) -> int:
        """Register Devin session identity and mirror its activity.

        Devin's hooks are observed, never rewritten: the shell command is
        recorded in a hook-backed session and the tool call is left intact.

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
        workspace: str = payload.get("cwd") or str(object=Path.cwd())
        LakeDatabase(home=home).set_agent_context(
            provider=Constants.agent,
            session_id=session_id,
            workspace=workspace,
        )
        event: str = payload.get("hook_event_name", "")
        entry: SessionEntry | None = HookSessions.ensure(
            home=home,
            agent=Constants.agent,
            agent_session=session_id,
            workspace=workspace,
            create=event != "SessionEnd",
        )
        command: str = (payload.get("tool_input") or {}).get("command", "")
        if entry is not None and event == "PreToolUse" and command:
            HookSessions.command_started(
                home=home,
                entry=entry,
                tool_use_id=payload.get("tool_use_id")
                or IdentityStore.new_id(),
                command=command,
                cwd=workspace,
            )
        ProviderState.record(
            home=home,
            agent=Constants.agent,
            agent_session=session_id,
            hook_event=event,
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
        """Load Devin's configuration, refusing to touch invalid JSON.

        Args:
            path: Path.

        Returns:
            The resulting value.

        """
        return cast(typ="dict[str, JsonValue]", val=JsonConfig.load(path=path))

    @staticmethod
    def _save(*, path: Path, data: dict[str, JsonValue]) -> None:
        """Atomically save Devin's configuration with a one-time backup.

        Args:
            path: Path.
            data: Data.

        """
        JsonConfig.save(path=path, data=cast(typ="JsonObject", val=data))

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
