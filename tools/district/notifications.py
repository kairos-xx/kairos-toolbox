# Copyright (c) 2026 Project contributors.
"""Rule-driven native notifications for significant agent events."""

from __future__ import annotations

from dataclasses import dataclass
from json import JSONDecodeError, loads
from platform import system
from typing import TYPE_CHECKING, cast

from .process import run
from .product import state_directory

if TYPE_CHECKING:
    from pathlib import Path

    from .events import DistrictEvent


@dataclass(frozen=True, slots=True, kw_only=True)
class NotificationRule:
    """One condition controlling notification delivery.

    Attributes:
        event_type: Event type.
        agent: Agent.
        min_duration_seconds: Min duration seconds.
        failed_only: Failed only.
        enabled: Enabled.

    """

    event_type: str = "COMMAND_FINISHED"
    agent: str = ""
    min_duration_seconds: float = 0.0
    failed_only: bool = False
    enabled: bool = True

    def matches(self, *, event: DistrictEvent) -> bool:
        """Return whether this rule accepts an event.

        Args:
            event: Event.

        Returns:
            The resulting value.

        """
        if not self.enabled or event.event_type != self.event_type:
            return False
        if self.agent and event.agent != self.agent:
            return False
        if event.duration_seconds < self.min_duration_seconds:
            return False
        return not self.failed_only or (
            event.exit_code is not None and event.exit_code != 0
        )


class NotificationRules:
    """Load notification rules from District Terminal state."""

    def __init__(self, *, home: Path) -> None:
        """Store the rules path.

        Args:
            home: Home.

        """
        self._path: Path = state_directory(home=home) / "notifications.json"

    def read(self) -> tuple[NotificationRule, ...]:
        """Return configured rules.

        Returns:
            The resulting value.

        """
        if not self._path.is_file():
            return ()
        try:
            raw: object = loads(s=self._path.read_text(encoding="utf-8"))
        except JSONDecodeError:
            return ()
        if not isinstance(raw, list):
            return ()
        rows: list[dict[str, str | float | bool]] = cast(
            typ="list[dict[str, str | float | bool]]",
            val=raw,
        )
        return tuple(
            NotificationRule(
                event_type=str(
                    object=row.get("event_type", "COMMAND_FINISHED"),
                ),
                agent=str(object=row.get("agent", "")),
                min_duration_seconds=float(
                    row.get("min_duration_seconds", 0.0),
                ),
                failed_only=bool(row.get("failed_only", False)),
                enabled=bool(row.get("enabled", True)),
            )
            for row in rows
        )


class Notifier:
    """Deliver a native desktop notification without shell interpolation."""

    @classmethod
    def for_event(
        cls,
        *,
        event: DistrictEvent,
        rules: tuple[NotificationRule, ...],
    ) -> bool:
        """Notify for the first matching rule.

        Args:
            event: Event.
            rules: Rules.

        Returns:
            The resulting value.

        """
        if not any(rule.matches(event=event) for rule in rules):
            return False
        command: str = event.command or event.event_type
        status: str = "finished"
        if event.exit_code not in (None, 0):
            status = f"failed ({event.exit_code})"
        return cls.send(
            title=f"District Terminal · {event.agent or 'agent'}",
            message=f"{command} {status} after {event.duration_seconds:.1f}s",
        )

    @staticmethod
    def send(*, title: str, message: str) -> bool:
        """Send a platform-native notification when supported.

        Args:
            title: Title.
            message: Message.

        Returns:
            The resulting value.

        """
        os_name: str = system().lower()
        if os_name == "darwin":
            script: str = (
                f"display notification {message!r} with title {title!r}"
            )
            args: tuple[str, ...] = ("osascript", "-e", script)
        elif os_name == "linux":
            args = ("notify-send", title, message)
        elif os_name == "windows":
            escaped_title: str = title.replace("'", "''")
            escaped_message: str = message.replace("'", "''")
            command: str = (
                "[Windows.UI.Notifications.ToastNotificationManager, "
                "Windows.UI.Notifications, "
                "ContentType = WindowsRuntime] > $null; "
                f"Write-Output '{escaped_title}: {escaped_message}'"
            )
            args = ("powershell.exe", "-NoProfile", "-Command", command)
        else:
            return False
        try:
            return run(args=args, check=False).returncode == 0
        except OSError:
            return False
