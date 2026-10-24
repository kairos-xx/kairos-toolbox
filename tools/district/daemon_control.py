# Copyright (c) 2026 Project contributors.
"""Control-plane lifecycle for the detached local District Terminal daemon."""

from __future__ import annotations

from os import name as os_name
from subprocess import Popen
from subprocess import __dict__ as subprocess_symbols
from sys import executable
from typing import TYPE_CHECKING

from .ipc import EventServer
from .product import state_directory

if TYPE_CHECKING:
    from pathlib import Path


class DaemonControl:
    """Start, ping, and stop the detached local control-plane daemon.

    Kept free of ``district.core`` imports so session backends can
    request daemon startup without creating an import cycle.
    """

    @classmethod
    def start(cls, *, home: Path) -> bool:
        """Start a detached local daemon when one is not already running.

        Args:
            home: Home.

        Returns:
            Whether the daemon is running or was started.

        """
        if cls.ping(home=home):
            return True
        process: Popen[bytes] = cls.spawn()
        pid_path: Path = state_directory(home=home) / "daemon.pid"
        _ = pid_path.write_text(
            data=f"{process.pid}\n",
            encoding="ascii",
        )
        return True

    @staticmethod
    def ping(*, home: Path) -> bool:
        """Return whether a local daemon answers authenticated IPC.

        Args:
            home: Home.

        Returns:
            Whether the daemon answered the ping request.

        """
        try:
            return (
                EventServer.request(
                    home=home,
                    operation="ping",
                )
                == "PONG"
            )
        except (ConnectionError, FileNotFoundError, OSError, EOFError):
            return False

    @staticmethod
    def stop(*, home: Path) -> bool:
        """Request a running daemon to stop.

        Args:
            home: Home.

        Returns:
            Whether the daemon accepted the stop request.

        """
        try:
            return (
                EventServer.request(
                    home=home,
                    operation="stop",
                )
                == "STOPPING"
            )
        except (ConnectionError, FileNotFoundError, OSError, EOFError):
            return False

    @staticmethod
    def spawn() -> Popen[bytes]:
        """Start the daemon process using platform-specific detachment.

        Returns:
            The newly started daemon process.

        """
        arguments: tuple[str, ...] = (
            executable,
            "-m",
            "district",
            "--daemon",
        )
        if os_name == "nt":
            detached: int = int(
                subprocess_symbols.get("DETACHED_PROCESS", 0),
            )
            process_group: int = int(
                subprocess_symbols.get(
                    "CREATE_NEW_PROCESS_GROUP",
                    0,
                ),
            )
            return Popen(
                args=arguments,
                stdin=None,
                stdout=None,
                stderr=None,
                creationflags=detached | process_group,
            )
        return Popen(
            args=arguments,
            stdin=None,
            stdout=None,
            stderr=None,
            start_new_session=True,
        )
