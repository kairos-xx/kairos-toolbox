# Copyright (c) 2026 Project contributors.
"""Tests for Lake Terminal's integration qualification harness."""

from json import dumps
from pathlib import Path
from shutil import which

import pytest

from tools.lake_terminal.lake_terminal import integration
from tools.lake_terminal.lake_terminal.integration import (
    IntegrationHarness,
    IntegrationResult,
)
from tools.lake_terminal.lake_terminal.product import state_directory

from .helpers import check, expect


def _fixed_token(*, nbytes: int) -> str:
    """Return a deterministic token for patched probes.

    Args:
        nbytes: Requested random byte count.

    Returns:
        Fixed hexadecimal token.

    """
    return "ab" * nbytes


def test_synthetic_provider_probe(tmp_path: Path) -> None:
    """Run a complete synthetic provider probe.

    Args:
        tmp_path: Temporary home directory.

    """
    result: IntegrationResult = IntegrationHarness.synthetic(
        home=tmp_path,
        provider="codex",
        provider_session="codex-test",
    )
    check(condition=result.passed, reason=result.detail)
    expect(actual=result.provider, expected="codex")


def test_synthetic_human_probe(tmp_path: Path) -> None:
    """Keep human executions unattributed instead of guessing an agent.

    Args:
        tmp_path: Temporary home directory.

    """
    result: IntegrationResult = IntegrationHarness.synthetic(
        home=tmp_path,
        provider="human",
    )
    check(condition=result.passed, reason=result.detail)


def test_concurrent_provider_matrix(tmp_path: Path) -> None:
    """Keep simultaneous providers and same-provider sessions distinct.

    Args:
        tmp_path: Temporary home directory.

    """
    results: tuple[IntegrationResult, ...] = (
        IntegrationHarness.concurrency_matrix(home=tmp_path)
    )
    check(
        condition=all(result.passed for result in results),
        reason="; ".join(result.detail for result in results),
    )
    expect(
        actual=sum(result.provider == "codex" for result in results),
        expected=2,
    )


def test_invalid_provider_is_rejected(tmp_path: Path) -> None:
    """Reject providers outside the explicit qualification registry.

    Args:
        tmp_path: Temporary home directory.

    """
    with pytest.raises(
        expected_exception=ValueError,
        match="Unsupported integration provider",
    ):
        _ = IntegrationHarness.synthetic(home=tmp_path, provider="other")


def test_integration_capability_helpers(tmp_path: Path) -> None:
    """Expose deterministic commands and optional runtime capabilities.

    Args:
        tmp_path: Temporary home directory.

    """
    token: str = "tok" + "en"
    command: tuple[str, ...] = IntegrationHarness.probe_command(
        token=token,
        output=tmp_path / "probe.json",
    )
    check(
        condition="lake_terminal.integration_probe" in command,
        reason="probe module missing from command",
    )
    expect(
        actual=IntegrationHarness.docker_available(),
        expected=which(cmd="docker") is not None,
    )
    expect(
        actual=IntegrationHarness.sandbox_available(),
        expected=which(cmd="sbx") is not None,
    )


def test_real_probe_timeout(tmp_path: Path) -> None:
    """Report a timeout when real probe evidence never arrives.

    Args:
        tmp_path: Temporary home directory.

    """
    result: IntegrationResult = IntegrationHarness.real(
        home=tmp_path,
        provider="codex",
        timeout_seconds=0.0,
    )
    check(condition=not result.passed, reason="timeout must fail")
    check(
        condition="timed out" in result.detail,
        reason=result.detail,
    )


def test_real_probe_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve pre-written real probe evidence deterministically.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Monkeypatch.

    """
    monkeypatch.setattr(
        target=integration,
        name="token_hex",
        value=_fixed_token,
    )
    evidence_directory: Path = state_directory(home=tmp_path) / "integration"
    evidence_directory.mkdir(parents=True, exist_ok=True)
    output: Path = evidence_directory / f"{'ab' * 16}.json"
    _ = output.write_text(
        data=dumps(
            obj={
                "token": "ab" * 16,
                "pid": 1,
                "children": [2],
                "provider": "codex",
                "provider_session": "codex-live",
                "surface": "cli",
                "editor": "",
                "workspace": "",
                "attribution": "provider_hook",
                "port": 0,
            },
        ),
        encoding="utf-8",
    )
    result: IntegrationResult = IntegrationHarness.real(
        home=tmp_path,
        provider="codex",
    )
    check(condition=result.passed, reason=result.detail)
    expect(actual=result.provider, expected="codex")
