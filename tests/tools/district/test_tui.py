# Copyright (c) 2026 Project contributors.
"""Tests for the dependency-free terminal dashboard."""

from __future__ import annotations

from district.models import SessionEntry
from district.monitoring import SessionSnapshot
from district.resources import ResourceSnapshot
from district.tui import DashboardRow, WatchTui

from .helpers import check, expect


def _entry() -> SessionEntry:
    """Build a dashboard session.

    Returns:
        Dashboard session.

    """
    return SessionEntry(
        id="abc123",
        command="district abc123",
        session="shared-abc123",
        agent="claude",
        cwd="/workspace/project",
        workspace="/workspace/project",
        surface="terminal",
    )


def test_render_rows_and_empty() -> None:
    """Render populated and empty dashboard frames."""
    snapshot: SessionSnapshot = SessionSnapshot(
        id="abc123",
        state="RUNNING",
        clients=1,
        current_command="python -m pytest",
        output="one\ntwo",
        age_seconds=7,
        reconnectable=True,
    )
    resources: ResourceSnapshot = ResourceSnapshot(
        processes=(),
        cpu_percent=12.5,
        rss_bytes=1048576,
        vms_bytes=2097152,
        read_bytes=1048576,
        write_bytes=2097152,
        connections=(),
        listening_ports=(8765,),
        partial=False,
    )
    frame: str = WatchTui.render(
        rows=(
            DashboardRow(
                entry=_entry(),
                snapshot=snapshot,
                resources=resources,
            ),
        ),
    )
    check(condition="claude" in frame, reason="agent rendered")
    check(condition="8765" in frame, reason="port rendered")
    check(condition="two" in frame, reason="output rendered")
    check(
        condition="No observable sessions." in WatchTui.render(rows=()),
        reason="empty state rendered",
    )


def test_once_and_noninteractive_run() -> None:
    """Capture an empty frame and use the non-interactive run path."""
    dashboard: WatchTui = WatchTui(fetch=lambda: ())
    check(
        condition="No observable sessions." in dashboard.once(),
        reason="once renders empty state",
    )
    expect(actual=dashboard.run(), expected=0)
