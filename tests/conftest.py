# Copyright (c) 2026 Project contributors.
"""Point pytest at the workspace root and share path helpers."""

from __future__ import annotations

from dataclasses import dataclass
from inspect import stack
from pathlib import Path
from sys import path


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
arrow_root: Path = root / "tools" / "arrow"
if str(object=arrow_root) not in path:
    path.insert(0, str(object=arrow_root))
