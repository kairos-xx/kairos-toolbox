# Copyright (c) 2026 Project contributors.
"""Tests for builds that produce a frozen executable but no installer."""

from __future__ import annotations

from json import loads
from time import monotonic
from typing import TYPE_CHECKING

from tools.stationer import ReleasePublisher
from tools.stationer.build_report import BuildReport, BuildSource
from tools.stationer.builder import (
    ArtifactUtilities,
    BuildResult,
)

from .helpers import check, expect

if TYPE_CHECKING:
    from pathlib import Path


def _result(*, output: Path) -> BuildResult:
    """Build a result for an executable with no installer.

    Args:
        output: Output root.

    Returns:
        The verified result.

    """
    executable: Path = output / "windows" / "demo.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(data=b"MZ")
    return ArtifactUtilities.result(
        platform_name="windows",
        architecture="arm64",
        executable_path=executable,
        installer_path=None,
        started_at=monotonic(),
    )


def test_result_without_an_installer_is_valid(tmp_path: Path) -> None:
    """A missing installer is allowed when none was requested.

    Args:
        tmp_path: Pytest temporary directory.

    """
    result: BuildResult = _result(output=tmp_path)
    expect(actual=result.installer, expected=None)
    expect(actual=result.installer_sha256, expected="")
    check(condition=bool(result.executable_sha256), reason="executable hashed")


def test_report_and_manifest_handle_a_missing_installer(
    tmp_path: Path,
) -> None:
    """The manifest omits the installer and the report shows a dash.

    Args:
        tmp_path: Pytest temporary directory.

    """
    result: BuildResult = _result(output=tmp_path)
    report: Path = BuildReport.write(
        output=tmp_path,
        source=BuildSource(name="demo", version="1.0.0"),
        results=(result,),
    )
    entries: list[dict[str, str]] = loads(
        s=(tmp_path / "artifacts.json").read_text(encoding="utf-8"),
    )
    expect(actual=len(entries), expected=1)
    check(condition="installer" not in entries[0], reason=str(object=entries))
    text: str = report.read_text(encoding="utf-8")
    check(condition="| windows | arm64 |" in text, reason=text)
    check(condition="`windows/demo.exe` | — |" in text, reason=text)
    expect(actual=text.count(entries[0]["executable_sha256"]), expected=1)


def test_a_second_run_keeps_installerless_entries(tmp_path: Path) -> None:
    """Merging does not drop an entry just because it has no installer.

    Args:
        tmp_path: Pytest temporary directory.

    """
    first: BuildResult = _result(output=tmp_path)
    source: BuildSource = BuildSource(name="demo", version="1.0.0")
    _ = BuildReport.write(
        output=tmp_path,
        source=source,
        results=(first,),
    )
    _ = BuildReport.write(
        output=tmp_path,
        source=source,
    )
    entries: list[dict[str, str]] = loads(
        s=(tmp_path / "artifacts.json").read_text(encoding="utf-8"),
    )
    expect(actual=len(entries), expected=1)


def test_publisher_attaches_only_the_executable(tmp_path: Path) -> None:
    """Release assets for such a build are the executable and BUILD.md.

    Args:
        tmp_path: Pytest temporary directory.

    """
    result: BuildResult = _result(output=tmp_path)
    _ = BuildReport.write(
        output=tmp_path,
        source=BuildSource(name="demo", version="1.0.0"),
        results=(result,),
    )
    names: list[str] = [
        p.name for p in ReleasePublisher.files(output=tmp_path)
    ]
    expect(actual=names, expected=["BUILD.md", "SHA256SUMS", "demo.exe"])
