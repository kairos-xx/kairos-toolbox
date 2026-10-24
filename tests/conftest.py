# Copyright (c) 2026 Project contributors.
"""Point pytest at the workspace root and share path helpers."""

from __future__ import annotations

from dataclasses import dataclass
from inspect import stack
from os import environ
from pathlib import Path
from sys import path
from typing import Final, final

import pytest


@final
class Constants:
    """Group module-level constants.

    Attributes:
        ambient_prefixes: Grouped module constant.

    """

    ambient_prefixes: Final[tuple[str, ...]] = (
        "AI_AGENT",
        "CLAUDE",
        "CODEX_",
        "CURSOR_",
        "DEVIN_",
        "DISTRICT_",
        "TERM_PROGRAM",
        "VSCODE_",
        "__CFBundleIdentifier",
    )


@dataclass(frozen=True)
class SiblingTree:
    """Group the mirrored workspace paths for one test file.

    Attributes:
        project: Project directory mirrored at the workspace root.
        installer: Project ``install.py`` mirrored at the workspace root.

    """

    project: Path
    installer: Path


def sibling_tree() -> SiblingTree:
    """Return the caller's project paths mirrored at the workspace root.

    Returns:
        Mirrored project and installer paths.

    Raises:
        ValueError: When no ancestor named ``tests`` exists.

    """
    current: Path = Path(stack()[1].filename).resolve().parent
    try:
        tests: Path = next(
            parent
            for parent in (current, *current.parents)
            if parent.name == "tests"
        )
    except StopIteration as error:
        msg: str = f"No ancestor named 'tests' found for {current}."
        raise ValueError(msg) from error
    project: Path = tests.parent / current.relative_to(tests)
    return SiblingTree(project=project, installer=project / "install.py")


if str(object=(root := Path(__file__).resolve().parent.parent)) not in path:
    path.insert(0, str(object=root))
tools_root: Path = root / "tools"
if str(object=tools_root) not in path:
    path.insert(0, str(object=tools_root))


@pytest.fixture(autouse=True)
def hermetic_agent_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide the host agent, editor and District identity from every test.

    Tests are routinely run from inside Claude Code, Devin or Cursor, whose
    exported markers would otherwise change detection results.

    Args:
        monkeypatch: Monkeypatch.

    """
    for name in tuple(environ):
        if name.startswith(Constants.ambient_prefixes):
            monkeypatch.delenv(name=name, raising=False)


@pytest.fixture(autouse=True)
def hermetic_conjure_user_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep every test away from the real ``~/.conjure/settings.toml``.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Monkeypatch.

    """
    monkeypatch.setattr(
        target="tools.conjure.settings.Constants.default_user_settings",
        name=tmp_path / "no-user-settings.toml",
    )
