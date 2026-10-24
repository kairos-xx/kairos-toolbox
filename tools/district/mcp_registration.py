# Copyright (c) 2026 Project contributors.
"""Register the District Terminal MCP server with Claude clients."""

from __future__ import annotations

from json import dumps
from os import environ
from pathlib import Path
from platform import system
from shutil import which
from typing import TYPE_CHECKING, Final

from .json_config import JsonConfig, JsonObject, JsonValue
from .process import run

if TYPE_CHECKING:
    from subprocess import CompletedProcess


class Constants:
    """Group MCP registration names.

    Attributes:
        server_name: Name under which the server is registered.
        desktop_file: Claude Desktop configuration file name.
        code_file: Claude Code user configuration file name.
        servers_key: Configuration key holding MCP servers.

    """

    server_name: Final[str] = "district"
    desktop_file: Final[str] = "claude_desktop_config.json"
    code_file: Final[str] = ".claude.json"
    servers_key: Final[str] = "mcpServers"


class McpRegistration:
    """Add and remove the ``district --mcp`` server in Claude apps."""

    @classmethod
    def install(
        cls,
        *,
        home: Path,
        launcher: Path,
        read_only: bool = False,
    ) -> tuple[Path, ...]:
        """Register the server with every Claude client present.

        Claude Desktop is edited only when its directory exists. Claude Code
        is registered through its own CLI when available, because that tool
        rewrites ``~/.claude.json`` continuously.

        Args:
            home: User home directory.
            launcher: District launcher executable.
            read_only: Register the inspection-only tool profile.

        Returns:
            The configuration files edited directly.

        """
        args: list[JsonValue] = ["--mcp"]
        if read_only:
            args.append("--read-only")
        entry: JsonObject = {"command": str(object=launcher), "args": args}
        edited: list[Path] = []
        desktop: Path = cls.claude_desktop_path(home=home)
        if desktop.parent.is_dir():
            cls._write(path=desktop, entry=entry)
            edited.append(desktop)
        typed: JsonObject = {"type": "stdio", **entry}
        if not cls._cli_add(entry=typed):
            code: Path = cls.claude_code_path(home=home)
            cls._write(path=code, entry=typed)
            edited.append(code)
        return tuple(edited)

    @classmethod
    def uninstall(cls, *, home: Path) -> None:
        """Remove the District server from every Claude client.

        Args:
            home: User home directory.

        """
        for path in (
            cls.claude_desktop_path(home=home),
            cls.claude_code_path(home=home),
        ):
            if not path.is_file():
                continue
            data: JsonObject = JsonConfig.load(path=path)
            servers: JsonValue = data.get(Constants.servers_key)
            if isinstance(servers, dict) and Constants.server_name in servers:
                del servers[Constants.server_name]
                JsonConfig.save(path=path, data=data)

    @classmethod
    def installed(cls, *, home: Path) -> bool:
        """Return whether any Claude client has the server registered.

        Args:
            home: User home directory.

        Returns:
            Whether a registration exists.

        """
        for path in (
            cls.claude_desktop_path(home=home),
            cls.claude_code_path(home=home),
        ):
            try:
                data: JsonObject = JsonConfig.load(path=path)
            except ValueError:
                continue
            servers: JsonValue = data.get(Constants.servers_key)
            if isinstance(servers, dict) and Constants.server_name in servers:
                return True
        return False

    @staticmethod
    def claude_desktop_path(*, home: Path) -> Path:
        """Return Claude Desktop's configuration file for this platform.

        Args:
            home: User home directory.

        Returns:
            The ``claude_desktop_config.json`` path.

        """
        name: str = system()
        if name == "Darwin":
            base: Path = home / "Library" / "Application Support" / "Claude"
        elif name == "Windows":
            roaming: str = environ.get("APPDATA", "")
            base = (
                Path(roaming) if roaming else home / "AppData" / "Roaming"
            ) / "Claude"
        else:
            base = home / ".config" / "Claude"
        return base / Constants.desktop_file

    @staticmethod
    def claude_code_path(*, home: Path) -> Path:
        """Return Claude Code's user-scope configuration file.

        Args:
            home: User home directory.

        Returns:
            The ``~/.claude.json`` path.

        """
        return home / Constants.code_file

    @staticmethod
    def _write(*, path: Path, entry: JsonObject) -> None:
        """Insert the server entry into one configuration file.

        Args:
            path: Configuration file.
            entry: Server definition.

        """
        data: JsonObject = JsonConfig.load(path=path)
        servers: JsonValue = data.get(Constants.servers_key)
        merged: dict[str, JsonValue] = (
            servers if isinstance(servers, dict) else {}
        )
        merged[Constants.server_name] = entry
        data[Constants.servers_key] = merged
        JsonConfig.save(path=path, data=data)

    @staticmethod
    def _cli_add(*, entry: JsonObject) -> bool:
        """Register through ``claude mcp add-json`` when the CLI exists.

        Args:
            entry: Server definition including its transport type.

        Returns:
            Whether the CLI performed the registration.

        """
        executable: str | None = which(cmd="claude")
        if executable is None:
            return False
        payload: str = dumps(obj=entry, separators=(",", ":"))
        base: tuple[str, ...] = (executable, "mcp")
        add: tuple[str, ...] = (
            *base,
            "add-json",
            "--scope",
            "user",
            Constants.server_name,
            payload,
        )
        result: CompletedProcess[str] = run(
            args=add,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True
        _ = run(
            args=(
                *base,
                "remove",
                "--scope",
                "user",
                Constants.server_name,
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        retry: CompletedProcess[str] = run(
            args=add,
            check=False,
            capture_output=True,
            text=True,
        )
        return retry.returncode == 0
