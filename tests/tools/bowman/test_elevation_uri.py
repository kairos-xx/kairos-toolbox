# Copyright (c) 2026 Project contributors.
"""Elevation URI tests."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from subprocess import CompletedProcess
from time import sleep
from typing import Final, final
from unittest.mock import patch

import pytest
from bowman.elevation_uri import (
    ElevationEnvelope,
    ElevationURIManager,
    PrivilegedRegistry,
)
from bowman.errors import ElevationError
from bowman.security import NonceStore

from .helpers import check, expect


@final
class Constants:
    """Group module-level constants.

    Attributes:
        dispatched_exit_code: Grouped module constant.
        macos_exit_code: Grouped module constant.
        linux_exit_code: Grouped module constant.

    """

    dispatched_exit_code: Final[int] = 9
    macos_exit_code: Final[int] = 3
    linux_exit_code: Final[int] = 4


def _manager() -> ElevationURIManager:
    """Build a manager with one registered install operation.

    Returns:
        The configured elevation URI manager.

    """
    registry: PrivilegedRegistry = PrivilegedRegistry()
    registry.register(
        name="system.install",
        executable_path=Path("/bin/echo"),
        argument_names=("source", "destination"),
        capability="system:install",
    )
    return ElevationURIManager(
        key=b"e" * 32,
        issuer="tests",
        audience="local-machine",
        scheme="uriapi-test",
        registry=registry,
        nonces=NonceStore(),
    )


def _install_uri(
    *,
    manager: ElevationURIManager,
    ttl: timedelta | None = None,
) -> str:
    """Create a signed URI for the registered install operation.

    Args:
        manager: Manager to create the URI with.
        ttl: Optional validity duration.

    Returns:
        The signed elevation URI.

    """
    return manager.create(
        operation="system.install",
        arguments={"source": "alpha", "destination": "beta"},
        subject="user",
        reason="Install component",
        ttl=ttl,
    )


def test_elevation_uri_round_trip() -> None:
    """A created URI validates back into its original envelope."""
    manager: ElevationURIManager = _manager()
    uri: str = _install_uri(manager=manager)
    envelope: ElevationEnvelope = manager.validate(uri=uri)
    check(
        condition=uri.startswith("uriapi-test://elevate/v1/"),
        reason="elevation URIs must use the elevate/v1 target",
    )
    expect(actual=envelope.operation, expected="system.install")
    expect(actual=envelope.capability, expected="system:install")
    expect(actual=envelope.arguments["source"], expected="alpha")


def test_elevation_uri_tamper_rejected() -> None:
    """A single changed payload byte invalidates the signature."""
    manager: ElevationURIManager = _manager()
    uri: str = _install_uri(manager=manager)
    marker: str = "/v1/"
    prefix: str
    token: str
    prefix, token = uri.split(sep=marker, maxsplit=1)
    body: str
    signature: str
    body, signature = token.split(sep=".", maxsplit=1)
    replacement: str = "A" if body[0] != "A" else "B"
    tampered: str = prefix + marker + replacement + body[1:] + "." + signature
    with pytest.raises(expected_exception=ElevationError):
        manager.validate(uri=tampered)


def test_argument_schema_is_fixed() -> None:
    """Missing schema arguments are rejected at creation time."""
    manager: ElevationURIManager = _manager()
    with pytest.raises(expected_exception=ElevationError):
        manager.create(
            operation="system.install",
            arguments={"source": "a"},
            subject="user",
            reason="Install",
        )


def test_unknown_operation_rejected() -> None:
    """Unregistered operations cannot create elevation URIs."""
    manager: ElevationURIManager = _manager()
    with pytest.raises(expected_exception=ElevationError):
        manager.create(
            operation="shell",
            arguments={},
            subject="user",
            reason="No",
        )


def test_expired_uri_rejected() -> None:
    """Expired envelopes are rejected during validation."""
    manager: ElevationURIManager = _manager()
    uri: str = _install_uri(manager=manager, ttl=timedelta(microseconds=1))
    sleep(0.002)
    with pytest.raises(expected_exception=ElevationError):
        manager.validate(uri=uri)


def test_execute_dispatches_exact_registered_operation() -> None:
    """Execution orders schema arguments and requires the capability."""
    manager: ElevationURIManager = _manager()
    uri: str = _install_uri(manager=manager)
    with patch(
        target="bowman.elevation_uri.PrivilegeBroker.execute",
        return_value=Constants.dispatched_exit_code,
    ) as execute:
        result: int = manager.execute(uri=uri)
    expect(actual=result, expected=Constants.dispatched_exit_code)
    expect(
        actual=execute.call_args.kwargs["arguments"],
        expected=("alpha", "beta"),
    )
    expect(
        actual=execute.call_args.kwargs["required_capability"],
        expected="system:install",
    )


def test_open_uses_native_platform_launchers() -> None:
    """MacOS uses open and Linux uses xdg-open."""
    manager: ElevationURIManager = _manager()
    uri: str = "uriapi-test://elevate/v1/token"
    with (
        patch(target="bowman.elevation_uri.system", return_value="Darwin"),
        patch(
            target="bowman.elevation_uri.run",
            return_value=CompletedProcess(
                args=(),
                returncode=Constants.macos_exit_code,
            ),
        ) as runner,
    ):
        expect(
            actual=manager.open(uri=uri),
            expected=Constants.macos_exit_code,
        )
        runner.assert_called_once_with(args=("open", uri), check=False)
    with (
        patch(target="bowman.elevation_uri.system", return_value="Linux"),
        patch(
            target="bowman.elevation_uri.run",
            return_value=CompletedProcess(
                args=(),
                returncode=Constants.linux_exit_code,
            ),
        ) as runner,
    ):
        expect(
            actual=manager.open(uri=uri),
            expected=Constants.linux_exit_code,
        )
        runner.assert_called_once_with(args=("xdg-open", uri), check=False)


def test_open_rejects_unknown_platform() -> None:
    """Unsupported operating systems cannot open elevation URIs."""
    manager: ElevationURIManager = _manager()
    with (
        patch(target="bowman.elevation_uri.system", return_value="Plan9"),
        pytest.raises(expected_exception=ElevationError),
    ):
        manager.open(uri="uriapi-test://elevate/v1/token")


def test_validation_rejects_wrong_target_and_timestamp() -> None:
    """Wrong scheme, authority, version, and timestamps are rejected."""
    manager: ElevationURIManager = _manager()
    with pytest.raises(expected_exception=ElevationError):
        manager.validate(uri="wrong://elevate/v1/token")
    with pytest.raises(expected_exception=ElevationError):
        manager.validate(uri="uriapi-test://wrong/v1/token")
    with pytest.raises(expected_exception=ElevationError):
        manager.validate(uri="uriapi-test://elevate/v2/token")
    with pytest.raises(expected_exception=ElevationError):
        manager.validate(uri="uriapi-test://elevate/v1/token")
    with pytest.raises(expected_exception=ElevationError):
        manager.timestamp(raw={"iat": "bad"}, name="iat")
