# Copyright (c) 2026 Project contributors.
"""Dependency-free terminal dashboard for live agent activity."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from os import name as os_name
from select import select
from sys import stdin, stdout
from time import sleep
from typing import TYPE_CHECKING, Final

from .monitoring import Monitor, SessionSnapshot
from .resources import (
    ResourceCollector,
    ResourceSnapshot,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from .models import SessionEntry


@dataclass(frozen=True, slots=True, kw_only=True)
class DashboardRow:
    """Rendered state for one observable session.

    Attributes:
        entry: Entry.
        snapshot: Snapshot.
        resources: Resources.

    """

    entry: SessionEntry
    snapshot: SessionSnapshot
    resources: ResourceSnapshot | None = None


class WatchTui:
    """Render and navigate a read-only multi-agent dashboard.

    Attributes:
        refresh_seconds: Refresh seconds.

    """

    refresh_seconds: Final[float] = 0.5

    def __init__(
        self,
        *,
        fetch: Callable[[], tuple[SessionEntry, ...]],
    ) -> None:
        """Initialize with a session provider.

        Args:
            fetch: Fetch.

        """
        self._fetch: Callable[[], tuple[SessionEntry, ...]] = fetch
        self._selected: int = 0

    @staticmethod
    def render(*, rows: tuple[DashboardRow, ...], selected: int = 0) -> str:
        """Return one complete dashboard frame.

        Args:
            rows: Rows.
            selected: Selected.

        Returns:
            The resulting value.

        """
        lines: list[str] = [
            (
                "AGENT         STATE        TIME    SURFACE      "
                "PROJECT      COMMAND"
            ),
            "─" * 88,
        ]
        len_project: int = 20
        len_command: int = 24
        for index, row in enumerate(iterable=rows):
            entry: SessionEntry = row.entry
            snapshot: SessionSnapshot = row.snapshot
            marker: str = ">" if index == selected else " "
            agent: str = entry.agent or entry.initiator or "unknown"
            project: str = entry.workspace or entry.cwd
            if len(project) > len_project:
                project = "…" + project[-(len_project - 1) :]
            command: str = snapshot.current_command or "—"
            if len(command) > len_command:
                command = command[: (len_command - 1)] + "…"
            lines.append(
                (
                    f"{marker}{agent:<13} {snapshot.state:<12} "
                    f"{snapshot.age_seconds:>5}s  {entry.surface:<12} "
                    f"{project:<20} {command}"
                ),
            )
            row_resources: ResourceSnapshot = (
                row.resources or ResourceCollector.empty()
            )
            ports: str = ",".join(
                str(object=value) for value in row_resources.listening_ports
            )
            lines.append(
                (
                    f"  CPU {row_resources.cpu_percent:.1f}% · "
                    f"RSS {row_resources.rss_bytes / 1048576:.1f} MiB · "
                    f"IO ↓{row_resources.read_bytes / 1048576:.1f}/"
                    f"↑{row_resources.write_bytes / 1048576:.1f} MiB · "
                    f"PROC {row_resources.process_count} · "
                    f"PORTS {ports or '—'}"
                ),
            )
        if rows:
            selected_row: DashboardRow = rows[min(selected, len(rows) - 1)]
            lines.extend(
                (
                    "",
                    "─" * 88,
                    (
                        f"{
                            selected_row.entry.agent
                            or selected_row.entry.initiator
                        }"
                        f" · {selected_row.entry.id} · "
                        f"{selected_row.snapshot.state}"
                    ),
                ),
            )
            resources: ResourceSnapshot = (
                selected_row.resources or ResourceCollector.empty()
            )
            if resources.processes:
                lines.append("Processes:")
                lines.extend(
                    (
                        f"  {process.pid:>7} ← {process.parent_pid:<7} "
                        f"{process.cpu_percent:>5.1f}% "
                        f"{process.rss_bytes / 1048576:>7.1f} MiB "
                        f"{process.name}"
                    )
                    for process in resources.processes[:12]
                )
            if selected_row.snapshot.output:
                lines.extend(selected_row.snapshot.output.splitlines()[-20:])
        else:
            lines.append("No observable sessions.")
        lines.extend(("", "j/k or ↑/↓ select · r refresh · q quit"))
        return "\n".join(lines)

    @staticmethod
    def key(*, timeout: float) -> str:
        """Read one navigation key without blocking dashboard refresh.

        Args:
            timeout: Timeout.

        Returns:
            The resulting value.

        """
        if os_name == "nt":
            try:
                msvcrt: ModuleType = import_module(name="msvcrt")
            except ImportError:
                sleep(timeout)
                return ""
            deadline: float = timeout
            while deadline > 0:
                if msvcrt.kbhit():
                    first: str = msvcrt.getwch()
                    if first in {"\x00", "\xe0"} and msvcrt.kbhit():
                        second: str = msvcrt.getwch()
                        return {"P": "\x1b[B", "H": "\x1b[A"}.get(
                            second,
                            second,
                        )
                    return first
                sleep(min(0.05, deadline))
                deadline -= 0.05
            return ""
        ready, _, _ = select([stdin], [], [], timeout)
        return stdin.read(1) if ready else ""

    def once(self) -> str:
        """Capture and render one frame.

        Returns:
            The resulting value.

        """
        entries: tuple[SessionEntry, ...] = self._fetch()
        rows: tuple[DashboardRow, ...] = tuple(
            DashboardRow(
                entry=entry,
                snapshot=Monitor.snapshot(entry=entry),
                resources=ResourceCollector.collect(entry=entry),
            )
            for entry in entries
        )
        if rows:
            self._selected = min(self._selected, len(rows) - 1)
        else:
            self._selected = 0
        return self.render(rows=rows, selected=self._selected)

    def run(self) -> int:
        """Run the interactive dashboard until ``q`` is pressed.

        Returns:
            The resulting value.

        """
        if not stdin.isatty() or not stdout.isatty():
            _ = stdout.write(self.once() + "\n")
            return 0
        try:
            while True:
                _ = stdout.write("\033[2J\033[H" + self.once())
                stdout.flush()
                key: str = self.key(timeout=self.refresh_seconds)
                if key in {"q", "Q"}:
                    return 0
                if key in {"j", "\x1b[B"}:
                    self._selected += 1
                elif key in {"k", "\x1b[A"}:
                    self._selected = max(0, self._selected - 1)
        except KeyboardInterrupt:
            return 0
