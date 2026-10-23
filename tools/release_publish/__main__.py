# Copyright (c) 2026 Project contributors.
"""Run the release and publishing control plane.

Works both as ``python -m tools.release_publish`` and as
``python ./tools/release_publish`` — the latter puts the package directory
on ``sys.path`` instead of the repository root, so it is added back here.

Raises:
    SystemExit: Always, with the CLI exit code.

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
    module: ModuleType = import_module(name="tools.release_publish.cli")
    raise SystemExit(module.main())


if __name__ == "__main__":
    main()
