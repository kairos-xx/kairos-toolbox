"""Tests for immutable configuration validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.conjure.config import (
    DockerConfig,
    GuestConfig,
    QemuConfig,
    SshConfig,
)

from .helpers import expect


def test_qemu_rejects_non_positive_timeout() -> None:
    """Reject invalid QEMU timeout values."""
    with pytest.raises(
        expected_exception=ValueError,
        match="greater than zero",
    ):
        QemuConfig(
            executable=Path("qemu"),
            arguments=(),
            startup_timeout_seconds=0.0,
        )


def test_qemu_rejects_invalid_guest_port() -> None:
    """Reject invalid guest SSH ports."""
    with pytest.raises(
        expected_exception=ValueError,
        match="between 1 and",
    ):
        GuestConfig(ssh_port=0)


def test_ssh_allows_automatic_port() -> None:
    """Allow an unresolved port for QEMU automatic forwarding."""
    config: SshConfig = SshConfig(
        username="builder",
        identity_file=Path("key"),
    )
    expect(actual=config.port, expected=None)
    expect(actual=config.with_port(port=4567).port, expected=4567)


def test_ssh_rejects_invalid_explicit_port() -> None:
    """Reject invalid explicit forwarded SSH ports."""
    with pytest.raises(
        expected_exception=ValueError,
        match="between 1 and",
    ):
        SshConfig(
            username="builder",
            identity_file=Path("key"),
            port=0,
        )


def test_docker_rejects_empty_image() -> None:
    """Reject an empty Docker image."""
    with pytest.raises(
        expected_exception=ValueError,
        match="must not be empty",
    ):
        DockerConfig(image="")
