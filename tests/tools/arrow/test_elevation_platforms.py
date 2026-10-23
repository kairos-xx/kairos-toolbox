# Copyright (c) 2026 Project contributors.
"""Arrow native-elevation platform branch tests."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from typing import Final, final
from unittest.mock import patch

import pytest
from arrow_uri.elevation import ElevationGrant, PrivilegeBroker
from arrow_uri.errors import ElevationError

from .helpers import check, expect


@final
class Constants:
    """Group module-level constants.

    Attributes:
        linux_exit_code: Grouped module constant.
        windows_exit_code: Grouped module constant.

    """

    linux_exit_code: Final[int] = 7
    windows_exit_code: Final[int] = 5


def test_linux_native_elevation() -> None:
    """Pkexec dispatches argv unchanged and returns its exit code."""
    grant: ElevationGrant = PrivilegeBroker.grant(
        capabilities=frozenset({"install"}),
        reason="test",
    )
    with (
        patch(target="arrow_uri.elevation.system", return_value="Linux"),
        patch(
            target="arrow_uri.elevation.which",
            return_value="/usr/bin/pkexec",
        ),
        patch(
            target="arrow_uri.elevation.run",
            return_value=CompletedProcess(
                args=(),
                returncode=Constants.linux_exit_code,
            ),
        ) as runner,
    ):
        result: int = PrivilegeBroker.execute(
            executable=Path("/helper"),
            arguments=("a", "b"),
            grant=grant,
            required_capability="install",
        )
    expect(actual=result, expected=Constants.linux_exit_code)
    runner.assert_called_once_with(
        args=("/usr/bin/pkexec", "/helper", "a", "b"),
        check=False,
    )


def test_linux_requires_polkit() -> None:
    """Missing pkexec raises before any subprocess runs."""
    with (
        patch(target="arrow_uri.elevation.which", return_value=None),
        pytest.raises(expected_exception=ElevationError),
    ):
        PrivilegeBroker.linux(executable=Path("/helper"), arguments=())


def test_macos_native_elevation_and_escaping() -> None:
    """Osascript quotes argv through the administrator dialog."""
    with (
        patch(
            target="arrow_uri.elevation.which",
            return_value="/usr/bin/osascript",
        ),
        patch(
            target="arrow_uri.elevation.run",
            return_value=CompletedProcess(args=(), returncode=0),
        ) as runner,
    ):
        result: int = PrivilegeBroker.macos(
            executable=Path("/helper"),
            arguments=("a'b",),
        )
    expect(actual=result, expected=0)
    check(
        condition="administrator privileges"
        in runner.call_args.kwargs["args"][2],
        reason="osascript must request administrator privileges",
    )
    expect(
        actual=PrivilegeBroker.shell_command(
            executable=Path("/helper"),
            arguments=("a'b",),
        ),
        expected="'/helper' 'a'\\''b'",
    )
    expect(
        actual=PrivilegeBroker.apple_string(value='a\\b"c'),
        expected='"a\\\\b\\"c"',
    )
    expect(
        actual=PrivilegeBroker.windows_quote(value='a"b'),
        expected='"a\\"b"',
    )


def test_macos_requires_osascript() -> None:
    """Missing osascript raises before any subprocess runs."""
    with (
        patch(target="arrow_uri.elevation.which", return_value=None),
        pytest.raises(expected_exception=ElevationError),
    ):
        PrivilegeBroker.macos(executable=Path("/helper"), arguments=())


def test_execute_rejects_capability_and_unknown_platform() -> None:
    """Grants cannot exceed their capabilities or unsupported OSes."""
    grant: ElevationGrant = PrivilegeBroker.grant(
        capabilities=frozenset({"read"}),
        reason="test",
    )
    with pytest.raises(expected_exception=ElevationError):
        PrivilegeBroker.execute(
            executable=Path("/helper"),
            arguments=(),
            grant=grant,
            required_capability="write",
        )
    with (
        patch(target="arrow_uri.elevation.system", return_value="Plan9"),
        pytest.raises(expected_exception=ElevationError),
    ):
        PrivilegeBroker.execute(
            executable=Path("/helper"),
            arguments=(),
            grant=grant,
            required_capability="read",
        )


def test_windows_native_elevation_uses_uac_powershell() -> None:
    """UAC uses Start-Process -Verb RunAs through PowerShell."""
    with patch(
        target="arrow_uri.elevation.run",
        return_value=CompletedProcess(
            args=(),
            returncode=Constants.windows_exit_code,
        ),
    ) as runner:
        result: int = PrivilegeBroker.windows(
            executable=Path("C:/helper.exe"),
            arguments=("a'b",),
        )
    expect(actual=result, expected=Constants.windows_exit_code)
    command: tuple[str, ...] = runner.call_args.kwargs["args"]
    expect(
        actual=command[:3],
        expected=("powershell.exe", "-NoProfile", "-NonInteractive"),
    )
    check(
        condition="-Verb RunAs" in command[-1],
        reason="PowerShell must request UAC elevation",
    )
    expect(
        actual=PrivilegeBroker.powershell_literal(value="a'b"),
        expected="'a''b'",
    )
