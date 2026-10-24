# Copyright (c) 2026 Project contributors.
"""Tests for the lazy stationer public API."""

from __future__ import annotations

from subprocess import run as subprocess_run
from sys import executable as sys_executable
from typing import TYPE_CHECKING

from tests.tools.stationer.helpers import check
from tools import stationer

if TYPE_CHECKING:
    from subprocess import CompletedProcess


def _import_exit(*, module: str, forbidden: str) -> int:
    """Import ``module`` and check whether ``forbidden`` was pulled in.

    Args:
        module: Module imported in a fresh interpreter.
        forbidden: Module that must stay unimported.

    Returns:
        The interpreter's exit code (0 = lazy).

    """
    result: CompletedProcess[bytes] = subprocess_run(
        args=[
            sys_executable,
            "-c",
            (
                f"import {module}, sys;"
                f" sys.exit(int({forbidden!r} in sys.modules))"
            ),
        ],
        capture_output=True,
        check=False,
    )
    return result.returncode


def test_every_public_name_still_imports() -> None:
    """Every ``__all__`` name resolves through the lazy API."""
    for name in stationer.__all__:
        check(
            condition=getattr(stationer, name) is not None,
            reason=f"{name} did not resolve lazily.",
        )


def test_partition_import_stays_light() -> None:
    """``import tools.stationer.partition`` avoids the builders."""
    expect_exit: int = _import_exit(
        module="tools.stationer.partition",
        forbidden="tools.stationer.builder",
    )
    check(
        condition=expect_exit == 0,
        reason="partition import pulled builder.",
    )


def test_versioning_and_naming_import_light() -> None:
    """Versioning and naming modules stay builder-free on import."""
    for module in ("tools.stationer.versioning", "tools.stationer.naming"):
        check(
            condition=_import_exit(
                module=module,
                forbidden="tools.stationer.builder",
            )
            == 0,
            reason=f"{module} import pulled builder.",
        )
