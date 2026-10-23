# Copyright (c) 2026 Project contributors.
"""Run the cross-platform release builder.

Works both as ``python -m tools.release_builder`` and as
``python ./tools/release_builder`` — the latter puts the package directory
on ``sys.path`` instead of the repository root, so it is added back here.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from sys import path as sys_path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def main() -> None:
    """Run the command-line interface.

    Raises:
        SystemExit: Always, with the CLI's exit status.

    """
    root: Path = Path(__file__).resolve().parents[2]
    if str(object=root) not in sys_path:
        sys_path.insert(0, str(object=root))
    module: ModuleType = import_module(
        name="tools.release_builder.release_builder",
    )
    raise SystemExit(module.Cli.main())


if __name__ == "__main__":
    main()
