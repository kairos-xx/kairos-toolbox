# Copyright (c) 2026 Project contributors.
"""Cross-platform per-user web-dashboard startup management."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from plistlib import dump
from shutil import which
from subprocess import run
from sys import executable
from typing import Final

from .product import state_directory
from .runtime import RuntimePlatform


@dataclass(frozen=True, slots=True, kw_only=True)
class StartupState:
    """Effective startup configuration.

    Attributes:
        enabled: Enabled.
        mechanism: Mechanism.
        location: Location.

    """

    enabled: bool
    mechanism: str
    location: str


class WebStartup:
    """Install and remove OS-native per-user dashboard startup.

    Attributes:
        label: Label.

    """

    label: Final[str] = "com.district.control-plane-web"

    def __init__(self, *, home: Path) -> None:
        """Initialize for one user home.

        Args:
            home: Home.

        """
        self._home: Path = home
        self.runtime: RuntimePlatform = RuntimePlatform.detect()

    @staticmethod
    def quote(*, value: str) -> str:
        """Quote one command argument for native service definitions.

        Args:
            value: Value.

        Returns:
            The resulting value.

        """
        return '"' + value.replace('"', '\\"') + '"'

    def state(self) -> StartupState:
        """Return the effective native startup state.

        Returns:
            The resulting value.

        """
        path: Path = self._path()
        return StartupState(
            enabled=path.exists(),
            mechanism=self._mechanism(),
            location=str(object=path),
        )

    def enable(
        self,
        *,
        command: tuple[str, ...] | None = None,
    ) -> StartupState:
        """Install and activate the native per-user startup entry.

        Args:
            command: Command.

        Returns:
            The resulting value.

        """
        path: Path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        module: str = __package__ or "district"
        if command is None:
            command = (
                executable,
                "-m",
                module,
                "--web",
                "--no-browser",
            )
        source_root: str = str(
            object=Path(__file__).resolve().parents[2],
        )
        if self.runtime.os == "macos":
            with path.open(mode="wb") as stream:
                dump(
                    value={
                        "Label": self.label,
                        "ProgramArguments": list(command),
                        "EnvironmentVariables": {"PYTHONPATH": source_root},
                        "RunAtLoad": True,
                        "KeepAlive": False,
                    },
                    fp=stream,
                )
            _ = run(
                args=("launchctl", "load", "-w", str(object=path)),
                check=False,
                capture_output=True,
            )
        elif self.runtime.os == "linux":
            command_line: str = " ".join(
                self.quote(value=value) for value in command
            )
            _unused_result_101: int = path.write_text(
                data=(
                    "[Unit]\nDescription=District web control plane\n\n"
                    "[Service]\nType=simple\n"
                    f'Environment="PYTHONPATH={source_root}"\n'
                    "ExecStart=" + command_line + "\n"
                    "Restart=on-failure\n\n[Install]\n"
                    "WantedBy=default.target\n"
                ),
                encoding="utf-8",
            )
            if which(cmd="systemctl") is not None:
                _ = run(
                    args=("systemctl", "--user", "daemon-reload"),
                    check=False,
                    capture_output=True,
                )
                _ = run(
                    args=("systemctl", "--user", "enable", "--now", path.name),
                    check=False,
                    capture_output=True,
                )
        elif self.runtime.os == "windows":
            task_command: str = " ".join(
                self.quote(value=value) for value in command
            )
            _ = run(
                args=(
                    "schtasks.exe",
                    "/Create",
                    "/F",
                    "/SC",
                    "ONLOGON",
                    "/TN",
                    self.label,
                    "/TR",
                    task_command,
                ),
                check=False,
                capture_output=True,
            )
            _unused_result_138: int = path.write_text(
                data=task_command,
                encoding="utf-8",
            )
        return self.state()

    def disable(self) -> StartupState:
        """Disable and remove the native per-user startup entry.

        Returns:
            The resulting value.

        """
        path: Path = self._path()
        if self.runtime.os == "macos" and path.exists():
            _ = run(
                args=("launchctl", "unload", "-w", str(object=path)),
                check=False,
                capture_output=True,
            )
        elif self.runtime.os == "linux" and path.exists():
            if which(cmd="systemctl") is not None:
                _ = run(
                    args=(
                        "systemctl",
                        "--user",
                        "disable",
                        "--now",
                        path.name,
                    ),
                    check=False,
                    capture_output=True,
                )
        elif self.runtime.os == "windows":
            _ = run(
                args=("schtasks.exe", "/Delete", "/F", "/TN", self.label),
                check=False,
                capture_output=True,
            )
        path.unlink(missing_ok=True)
        return self.state()

    def _path(self) -> Path:
        """Return the native startup definition path.

        Returns:
            The resulting value.

        """
        if self.runtime.os == "macos":
            return (
                self._home / "Library" / "LaunchAgents" / f"{self.label}.plist"
            )
        if self.runtime.os == "linux":
            return (
                self._home
                / ".config"
                / "systemd"
                / "user"
                / "district-web.service"
            )
        return state_directory(home=self._home) / "district-web-startup.task"

    def _mechanism(self) -> str:
        """Return the native startup mechanism name.

        Returns:
            The resulting value.

        """
        return {
            "macos": "launchd",
            "linux": "systemd-user",
            "windows": "task-scheduler",
        }.get(self.runtime.os, "unsupported")
