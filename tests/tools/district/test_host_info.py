# Copyright (c) 2026 Project contributors.
"""Tests for the host facts shown on the dashboard overview."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast, final

from district.host_info import HostInfo, HostSnapshot
from district.storage import DistrictDatabase
from psutil import boot_time

from .helpers import check, expect, section

if TYPE_CHECKING:
    from pathlib import Path

    from district.json_config import JsonValue


@final
class Constants:
    """Group test constants.

    Attributes:
        booted: Moment the machine started.
        uptime: Seconds the injected clock runs past boot.
        full: A complete percentage.
        rows: Process rows requested.

    """

    booted: Final[float] = boot_time()
    uptime: Final[float] = 3600.0
    full: Final[float] = 100.0
    rows: Final[int] = 5


def _snapshot(*, home: Path) -> HostSnapshot:
    """Collect one snapshot against a scratch home directory.

    Args:
        home: Scratch home directory.

    Returns:
        The collected snapshot.

    """
    _ = DistrictDatabase(home=home)
    return HostInfo.collect(
        home=home,
        process_limit=Constants.rows,
        now=Constants.booted + Constants.uptime,
    )


def test_memory_and_disk_figures_are_internally_consistent(
    tmp_path: Path,
) -> None:
    """Used never exceeds total and percentages stay in range.

    Args:
        tmp_path: Scratch home directory.

    """
    snapshot: HostSnapshot = _snapshot(home=tmp_path)
    check(
        condition=0 < snapshot.memory.used <= snapshot.memory.total,
        reason="memory used must be within total",
    )
    check(
        condition=0 < snapshot.disk.used <= snapshot.disk.total,
        reason="disk used must be within total",
    )
    check(
        condition=0.0 <= snapshot.memory.percent <= Constants.full,
        reason="memory percent must be a percentage",
    )
    check(
        condition=0.0 <= snapshot.cpu.percent <= Constants.full,
        reason="cpu percent must be a percentage",
    )


def test_each_logical_core_is_reported_once(tmp_path: Path) -> None:
    """The per-core list lines up with the logical core count.

    Args:
        tmp_path: Scratch home directory.

    """
    snapshot: HostSnapshot = _snapshot(home=tmp_path)
    expect(actual=len(snapshot.cpu.per_core), expected=snapshot.cpu.logical)


def test_busiest_processes_are_ranked_and_limited(tmp_path: Path) -> None:
    """The list is capped, sorted by memory, and names real processes.

    Args:
        tmp_path: Scratch home directory.

    """
    snapshot: HostSnapshot = _snapshot(home=tmp_path)
    sizes: list[int] = [item.rss_bytes for item in snapshot.processes]
    check(
        condition=0 < len(sizes) <= Constants.rows,
        reason="between one and the requested rows",
    )
    expect(actual=sizes, expected=sorted(sizes, reverse=True))
    check(
        condition=all(item.name for item in snapshot.processes),
        reason="every process has a name",
    )


def test_district_facts_describe_this_installation(tmp_path: Path) -> None:
    """The district block reports the database and the daemon state.

    Args:
        tmp_path: Scratch home directory.

    """
    snapshot: HostSnapshot = _snapshot(home=tmp_path)
    check(
        condition=snapshot.district.database_bytes > 0,
        reason="a created database has a size",
    )
    expect(actual=snapshot.district.daemon_running, expected=False)
    check(
        condition=snapshot.district.schema_version > 0,
        reason="the schema version is reported",
    )


def test_the_json_form_uses_the_dashboard_key_style(tmp_path: Path) -> None:
    """Keys are camelCase like the rest of the dashboard payload.

    Args:
        tmp_path: Scratch home directory.

    """
    payload: dict[str, JsonValue] = _snapshot(home=tmp_path).as_json()
    cpu: dict[str, object] = section(value=payload["cpu"])
    memory: dict[str, object] = section(value=payload["memory"])
    district: dict[str, object] = section(value=payload["district"])
    check(condition="perCore" in cpu, reason="cpu.perCore present")
    check(
        condition="availableBytes" in memory,
        reason="memory.availableBytes present",
    )
    check(
        condition="daemonRunning" in district,
        reason="district.daemonRunning present",
    )
    expect(
        actual=cast(typ="float", val=payload["uptimeSeconds"]),
        expected=Constants.uptime,
    )
