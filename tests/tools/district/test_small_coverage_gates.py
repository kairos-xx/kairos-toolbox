# Copyright (c) 2026 Project contributors.
"""Close small per-file coverage gates with public behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

import pytest
from district import daemon_control as daemon_module
from district.daemon_control import DaemonControl

from .helpers import check, expect

if TYPE_CHECKING:
    from pathlib import Path
    from subprocess import Popen


class Constants:
    """Group test constants.

    Attributes:
        pid: Fake process identifier.

    """

    pid: Final[int] = 123


def test_assertion_helpers_failure_paths() -> None:
    """Exercise both assertion-helper failure branches."""
    with pytest.raises(expected_exception=AssertionError, match="expected"):
        expect(actual=1, expected=2)
    with pytest.raises(expected_exception=AssertionError, match="reason"):
        check(condition=False, reason="reason")


def test_daemon_start_and_spawn_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise daemon startup and both process-detachment branches.

    Args:
        tmp_path: Scratch directory.
        monkeypatch: Pytest monkeypatch fixture.

    """

    class FakeProcess:
        """Provide the process identifier used by daemon startup."""

        def __init__(self) -> None:
            """Initialize the fake process."""
            self.pid: int = Constants.pid

    (tmp_path / ".district").mkdir()

    def fake_ping(*, home: Path) -> bool:
        """Report the daemon as stopped.

        Args:
            home: Home.

        Returns:
            False.

        """
        del home
        return False

    monkeypatch.setattr(
        target=DaemonControl,
        name="ping",
        value=staticmethod(fake_ping),
    )
    monkeypatch.setattr(
        target=DaemonControl,
        name="spawn",
        value=staticmethod(FakeProcess),
    )
    expect(actual=DaemonControl.start(home=tmp_path), expected=True)
    expect(
        actual=(tmp_path / ".district" / "daemon.pid").read_text(
            encoding="ascii",
        ),
        expected="123\n",
    )


def test_daemon_native_spawn_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise both native daemon process-detachment branches.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    calls: list[dict[str, object]] = []

    def fake_popen(**kwargs: object) -> Popen[bytes]:
        """Record process arguments and return a typed placeholder.

        Args:
            **kwargs: Process creation options.

        Returns:
            Typed process placeholder.

        """
        calls.append(kwargs)
        return cast(typ="Popen[bytes]", val=object())

    monkeypatch.setattr(target=daemon_module, name="Popen", value=fake_popen)
    monkeypatch.setattr(target=daemon_module, name="os_name", value="posix")
    _ = DaemonControl.spawn()
    check(
        condition=bool(calls[-1].get("start_new_session")),
        reason="POSIX daemon detaches",
    )
    monkeypatch.setattr(target=daemon_module, name="os_name", value="nt")
    _ = DaemonControl.spawn()
    check(
        condition="creationflags" in calls[-1],
        reason="Windows daemon uses creation flags",
    )
