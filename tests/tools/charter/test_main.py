# Copyright (c) 2026 Project contributors.
"""Module entry-point coverage."""

from __future__ import annotations

from pathlib import Path
from runpy import run_module, run_path
from sys import path as sys_path
from unittest.mock import patch

import pytest

from .helpers import expect


def test_module_entrypoint() -> None:
    """Execute the ``python -m tools.charter`` entry point."""
    with (
        patch(target="tools.charter.cli.main", return_value=0),
        pytest.raises(expected_exception=SystemExit) as raised,
    ):
        run_module(
            mod_name="tools.charter.__main__",
            run_name="__main__",
        )
    expect(actual=raised.value.code, expected=0)


def test_standalone_script_entrypoint() -> None:
    """Bootstrap ``sys.path`` when run without a package context."""
    script: Path = (
        Path(__file__).resolve().parents[3]
        / "tools"
        / "charter"
        / "__main__.py"
    )
    saved: list[str] = list(sys_path)
    try:
        with (
            patch(target="tools.charter.cli.main", return_value=0),
            pytest.raises(expected_exception=SystemExit) as raised,
        ):
            run_path(path_name=str(object=script), run_name="__main__")
        expect(actual=raised.value.code, expected=0)
    finally:
        sys_path[:] = saved
