# Copyright (c) 2026 Project contributors.
"""Run the partition CLI as ``python -m stationer.partition``."""

from sys import argv

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main(arguments=tuple(argv[1:])))
