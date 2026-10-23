# Copyright (c) 2026 Project contributors.
"""Entry script frozen by ``tools.release_builder`` into native installers.

PyInstaller runs this file as a top-level script, so it must use absolute
imports; ``lake_terminal/__main__.py`` relies on its package and cannot.
"""

from lake_terminal.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
