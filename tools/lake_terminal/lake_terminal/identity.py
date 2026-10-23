# Copyright (c) 2026 Project contributors.
"""Stable host, session, and process identity helpers."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from os import getpid
from os import name as os_name
from pathlib import Path
from platform import node
from typing import TYPE_CHECKING
from uuid import uuid4

from .process import run
from .product import state_directory

if TYPE_CHECKING:
    from subprocess import CompletedProcess


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessIdentity:
    """Stable-enough identity for one live process instance.

    Attributes:
        pid: Pid.
        started_token: Started token.

    """

    pid: int
    started_token: str

    @classmethod
    def capture(cls, *, pid: int) -> ProcessIdentity:
        """Capture a process identifier that detects PID reuse.

        Args:
            pid: Pid.

        Returns:
            The resulting value.

        """
        proc_stat: Path = Path(f"/proc/{pid}/stat")
        if proc_stat.is_file():
            parts: list[str] = proc_stat.read_text(encoding="utf-8").split()
            min_len: int = 21
            token: str = parts[min_len] if len(parts) > min_len else ""
            return cls(pid=pid, started_token=token)
        if os_name == "nt":
            result: CompletedProcess[str] = run(
                args=(
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    f"(Get-Process -Id {pid}).StartTime."
                    "ToUniversalTime().Ticks",
                ),
                check=False,
                capture_output=True,
                text=True,
            )
        else:
            result = run(
                args=("ps", "-o", "lstart=", "-p", str(object=pid)),
                check=False,
                capture_output=True,
                text=True,
            )
        return cls(
            pid=pid,
            started_token=(result.stdout or "").strip(),
        )


class IdentityStore:
    """Persist the current host identity and create globally unique IDs."""

    def __init__(self, *, home: Path) -> None:
        """Initialize identity paths.

        Args:
            home: Home.

        """
        self._directory: Path = state_directory(home=home)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._host_path: Path = self._directory / "host_id"

    @staticmethod
    def new_id() -> str:
        """Return a globally unique UUID.

        Returns:
            The resulting value.

        """
        return str(object=uuid4())

    @staticmethod
    def machine_fingerprint() -> str:
        """Return a non-secret diagnostic fingerprint for the local machine.

        Returns:
            The resulting value.

        """
        payload: str = f"{node()}:{getpid()}"
        return sha256(data=payload.encode()).hexdigest()[:12]

    def host_id(self) -> str:
        """Return a persistent UUID identifying this execution host.

        Returns:
            The resulting value.

        """
        if self._host_path.is_file():
            value: str = self._host_path.read_text(encoding="utf-8").strip()
            if value:
                return value
        value = str(object=uuid4())
        _unused_result_119: int = self._host_path.write_text(
            data=value + "\n",
            encoding="utf-8",
        )
        return value
