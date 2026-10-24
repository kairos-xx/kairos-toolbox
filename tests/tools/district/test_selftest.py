# Copyright (c) 2026 Project contributors.
"""Tests for the structured ``district --test`` self-check."""

from __future__ import annotations

from io import StringIO
from json import loads
from typing import TYPE_CHECKING, Final, final

import pytest
from district import selftest
from district.selftest import SelfTest

from .helpers import check, expect

if TYPE_CHECKING:
    from pathlib import Path

    from district.json_config import JsonObject


@final
class Constants:
    """Group test constants.

    Attributes:
        missing_module: A module name no environment provides.
        always: Check names every healthy report contains.

    """

    missing_module: Final[str] = "district_no_such_module_xyz"
    always: Final[tuple[str, ...]] = (
        "python_runtime",
        "runtime_platform",
        "state_writable",
        "database",
        "daemon",
        "mcp_registration",
    )


def _outcome(*, report: JsonObject) -> dict[str, tuple[bool, str]]:
    """Index a report's checks by name.

    Args:
        report: Report produced by ``SelfTest.report``.

    Returns:
        Each check's name mapped to ``(passed, detail)``.

    Raises:
        TypeError: If the report is not shaped like a self-test report.

    """
    entries: object = report["checks"]
    if not isinstance(entries, list):
        msg: str = "checks must be a list"
        raise TypeError(msg)
    outcome: dict[str, tuple[bool, str]] = {}
    for entry in entries:
        if isinstance(entry, dict):
            outcome[str(object=entry["name"])] = (
                bool(entry["ok"]),
                str(object=entry["detail"]),
            )
    return outcome


def test_a_healthy_home_passes_every_required_check(tmp_path: Path) -> None:
    """The report lists each check and counts them consistently.

    Args:
        tmp_path: Pytest temporary directory used as the home.

    """
    report: JsonObject = SelfTest.report(home=tmp_path)
    outcome: dict[str, tuple[bool, str]] = _outcome(report=report)
    expect(actual=report["tool"], expected="district")
    expect(actual=report["failed"], expected=0)
    expect(actual=report["passed"], expected=len(outcome))
    expect(actual=report["ok"], expected=True)
    for name in Constants.always:
        check(condition=outcome[name][0], reason=f"{name}: {outcome}")
    check(
        condition=any(name.startswith("dependency:") for name in outcome),
        reason="dependencies are checked",
    )


def test_a_missing_dependency_fails_the_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An unimportable module is named in its own failing check.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory used as the home.

    """
    monkeypatch.setattr(
        target=SelfTest,
        name="dependencies",
        value=(("fake-requirement", Constants.missing_module),),
    )
    report: JsonObject = SelfTest.report(home=tmp_path)
    passed, detail = _outcome(report=report)["dependency:fake-requirement"]
    expect(actual=passed, expected=False)
    check(condition=Constants.missing_module in detail, reason=detail)
    expect(actual=report["ok"], expected=False)
    expect(actual=report["failed"], expected=1)


def test_an_old_interpreter_fails_the_python_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The runtime check enforces the Python 3.13 floor.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory used as the home.

    """
    monkeypatch.setattr(
        target=selftest,
        name="version_info",
        value=(3, 12, 0),
    )
    passed, detail = _outcome(report=SelfTest.report(home=tmp_path))[
        "python_runtime"
    ]
    expect(actual=passed, expected=False)
    check(condition="3.12.0" in detail, reason=detail)


def test_an_unwritable_state_directory_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A state directory that cannot be created fails its own check.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory used as the home.

    """

    def refuse(*, home: Path) -> Path:
        """Fail like a read-only home.

        Args:
            home: Ignored home directory.

        Returns:
            Never returns (always raises).

        Raises:
            PermissionError: Always.

        """
        _ = home
        msg: str = "read-only home"
        raise PermissionError(msg)

    monkeypatch.setattr(
        target=selftest,
        name="state_directory",
        value=refuse,
    )
    passed, detail = _outcome(report=SelfTest.report(home=tmp_path))[
        "state_writable"
    ]
    expect(actual=passed, expected=False)
    check(condition="read-only home" in detail, reason=detail)


@final
class FakeDatabase:
    """Stand in for the SQLite-backed database.

    Attributes:
        failure: Exception the constructor raises, if any.
        integrity_text: What ``integrity`` reports.

    """

    failure: Exception | None = None
    integrity_text: str = "ok"

    def __init__(self, *, home: Path) -> None:
        """Raise the scripted failure.

        Args:
            home: Ignored home directory.

        Raises:
            Exception: The scripted failure.

        """
        _ = home
        if self.failure is not None:
            raise self.failure

    @staticmethod
    def schema_version() -> int:
        """Report a fixed schema version.

        Returns:
            The version.

        """
        return 7

    def integrity(self) -> str:
        """Report the scripted integrity.

        Returns:
            The scripted integrity text.

        """
        return self.integrity_text


@pytest.mark.parametrize(
    ("failure", "integrity", "expected"),
    [
        (OSError("disk gone"), "ok", False),
        (RuntimeError("corrupt"), "ok", False),
        (None, "malformed page", False),
        (None, "ok", True),
    ],
)
def test_the_database_check_reflects_open_and_integrity(
    *,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: Exception | None,
    integrity: str,
    expected: bool,
) -> None:
    """Failures to open and bad integrity both fail the check.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory.
        failure: Exception the database constructor raises, if any.
        integrity: What ``integrity`` reports.
        expected: Whether the check should pass.

    """
    monkeypatch.setattr(target=FakeDatabase, name="failure", value=failure)
    monkeypatch.setattr(
        target=FakeDatabase,
        name="integrity_text",
        value=integrity,
    )
    monkeypatch.setattr(
        target=selftest,
        name="DistrictDatabase",
        value=FakeDatabase,
    )
    passed, detail = _outcome(report=SelfTest.report(home=tmp_path))[
        "database"
    ]
    expect(actual=passed, expected=expected)
    if failure is None:
        check(condition="schema v7" in detail, reason=detail)
    else:
        check(condition=str(object=failure) in detail, reason=detail)


@pytest.mark.parametrize("running", [True, False])
def test_daemon_and_mcp_are_notes_not_failures(
    *,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    running: bool,
) -> None:
    """A stopped daemon or missing registration never fails the report.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory.
        running: Whether the daemon and registration are present.

    """

    def ping(*, home: Path) -> bool:
        """Answer like a daemon that is up or down.

        Args:
            home: Ignored home directory.

        Returns:
            The scripted state.

        """
        _ = home
        return running

    def installed(*, home: Path) -> bool:
        """Answer like a registration that exists or not.

        Args:
            home: Ignored home directory.

        Returns:
            The scripted state.

        """
        _ = home
        return running

    monkeypatch.setattr(
        target=selftest.DistrictDaemon,
        name="ping",
        value=staticmethod(ping),
    )
    monkeypatch.setattr(
        target=selftest.McpRegistration,
        name="installed",
        value=staticmethod(installed),
    )
    outcome: dict[str, tuple[bool, str]] = _outcome(
        report=SelfTest.report(home=tmp_path),
    )
    expect(
        actual=(outcome["daemon"][0], outcome["mcp_registration"][0]),
        expected=(True, True),
    )
    expect(
        actual=outcome["daemon"][1],
        expected="running" if running else "stopped",
    )
    expect(
        actual=outcome["mcp_registration"][1],
        expected="registered" if running else "not registered",
    )


def test_self_test_prints_json_and_exits_zero_when_healthy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The entry point prints the report and a one-line summary.

    The module binds ``sys.stdout`` and ``sys.stderr`` when it is imported,
    so its names are replaced rather than captured.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory used as the home.

    """
    out: StringIO = StringIO()
    err: StringIO = StringIO()
    monkeypatch.setattr(target=selftest, name="stdout", value=out)
    monkeypatch.setattr(target=selftest, name="stderr", value=err)
    status: int = selftest.self_test(home=tmp_path)
    document: object = loads(s=out.getvalue())
    check(condition=isinstance(document, dict), reason=out.getvalue()[:200])
    expect(actual=status, expected=0)
    check(
        condition="district --test:" in err.getvalue()
        and "0 failed" in err.getvalue(),
        reason=err.getvalue(),
    )


def test_self_test_exits_one_when_a_check_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failing report turns into a non-zero status.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory used as the home.

    """
    err: StringIO = StringIO()
    monkeypatch.setattr(target=selftest, name="stdout", value=StringIO())
    monkeypatch.setattr(target=selftest, name="stderr", value=err)

    def failing_report(*, home: Path) -> JsonObject:
        """Fabricate a failed report.

        Args:
            home: Ignored home directory.

        Returns:
            A report with one failure.

        """
        _ = home
        return {"ok": False, "passed": 3, "failed": 1, "checks": []}

    monkeypatch.setattr(
        target=SelfTest,
        name="report",
        value=staticmethod(failing_report),
    )
    expect(actual=selftest.self_test(home=tmp_path), expected=1)
    check(condition="1 failed" in err.getvalue(), reason=err.getvalue())
