# Copyright (c) 2026 Project contributors.
"""Execute the District Terminal control plane as a module or directory."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from sys import argv, path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType


def main(*, arguments: tuple[str, ...] | None = None) -> int:
    """Dispatch invocations to the CLI or the installer.

    Args:
        arguments: Arguments.

    Returns:
        The resulting value.

    """
    package: str = __package__ or ""
    if not package:
        script_dir: str = str(object=Path(__file__).resolve().parent)
        path[:] = [entry for entry in path if entry != script_dir]
        source_root: Path = Path(__file__).resolve().parent.parent
        root_text: str = str(object=source_root)
        if root_text not in path:
            path.insert(0, root_text)
        package = "district"
    args: tuple[str, ...] = tuple(argv[1:]) if arguments is None else arguments
    if args[:1] == ("run",):
        installer: ModuleType = import_module(
            name=f"{package}.installer",
        )
        install_entry: Callable[..., int] = cast(
            typ="Callable[..., int]",
            val=installer.main,
        )
        return install_entry(arguments=args[1:])
    cli: ModuleType = import_module(name=f"{package}.cli")
    cli_entry: Callable[..., int] = cast(
        typ="Callable[..., int]",
        val=cli.main,
    )
    return cli_entry(arguments=args)


if __name__ == "__main__":
    raise SystemExit(main())
