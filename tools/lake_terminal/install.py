#!/usr/bin/env python3
# Copyright (c) 2026 Project contributors.
"""Install, inspect, and remove the Lake Terminal control-plane integration."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from sys import path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType


def main(*, arguments: tuple[str, ...] | None = None) -> int:
    """Bootstrap the source root and delegate to the package installer.

    Args:
        arguments: Arguments.

    Returns:
        The resulting value.

    """
    source_root: Path = Path(__file__).resolve().parent.parent
    if str(object=source_root) not in path:
        path.insert(0, str(object=source_root))
    module: ModuleType = import_module(
        name="lake_terminal.lake_terminal.installer",
    )
    entry: Callable[..., int] = cast(
        typ="Callable[..., int]",
        val=module.main,
    )
    return entry(arguments=arguments)


if __name__ == "__main__":
    raise SystemExit(main())
