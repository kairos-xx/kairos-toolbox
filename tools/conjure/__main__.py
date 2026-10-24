"""Module entry point for ``python -m tools.conjure``."""

from __future__ import annotations

from pathlib import Path
from sys import path

if __package__ in {None, ""}:
    path.insert(0, str(object=Path(__file__).resolve().parents[2]))
if __name__ == "__main__":
    from tools.conjure.cli import main

    raise SystemExit(main())
