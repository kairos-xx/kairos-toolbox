# Copyright (c) 2026 Project contributors.
"""Entry script frozen by ``tools.stationer`` into native installers.

PyInstaller runs this file as a top-level script, so it must use absolute
imports; ``district/__main__.py`` relies on its package and cannot.
"""

from district.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
