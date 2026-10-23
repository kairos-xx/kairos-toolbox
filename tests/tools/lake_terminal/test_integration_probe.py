# Copyright (c) 2026 Project contributors.
"""Tests for the deterministic integration probe workload."""

from pathlib import Path

import pytest

from tools.lake_terminal.lake_terminal.integration_probe import (
    ProbeArguments,
    main,
    parse_arguments,
)

from .helpers import check, expect


def test_probe_writes_evidence_without_children(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Write deterministic evidence for a zero-duration probe.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Monkeypatch.

    """
    monkeypatch.setenv(name="LAKE_AGENT", value="codex")
    output: Path = tmp_path / "probe.json"
    status: int = main(
        arguments=(
            "--token",
            "abc",
            "--output",
            str(object=output),
            "--duration",
            "0",
            "--children",
            "0",
        ),
    )
    expect(actual=status, expected=0)
    check(condition=output.is_file(), reason="evidence file missing")
    check(
        condition='"token": "abc"' in output.read_text(encoding="utf-8"),
        reason="token missing from evidence",
    )


def test_probe_argument_parser_and_validation(tmp_path: Path) -> None:
    """Parse optional values and reject incomplete argument surfaces.

    Args:
        tmp_path: Temporary home directory.

    """
    parsed: ProbeArguments = parse_arguments(
        arguments=(
            "--token",
            "abc",
            "--output",
            str(object=tmp_path / "out.json"),
            "--http",
        ),
    )
    check(condition=parsed.http, reason="--http flag not parsed")
    with pytest.raises(expected_exception=ValueError, match="requires"):
        _ = parse_arguments(arguments=("--token", "abc"))
    with pytest.raises(expected_exception=ValueError, match="Invalid"):
        _ = parse_arguments(arguments=("--wat",))


def test_probe_opens_loopback_listener(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the optional loopback-listener observation surface.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Monkeypatch.

    """
    monkeypatch.setenv(name="LAKE_AGENT", value="codex")
    output: Path = tmp_path / "http-probe.json"
    status: int = main(
        arguments=(
            "--token",
            "http",
            "--output",
            str(object=output),
            "--duration",
            "0",
            "--children",
            "0",
            "--http",
        ),
    )
    expect(actual=status, expected=0)
    check(
        condition='"port": 0' not in output.read_text(encoding="utf-8"),
        reason="listener port must not stay zero",
    )
