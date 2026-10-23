# Copyright (c) 2026 Project contributors.
"""Execute the extension installer as a module or directory."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from sys import argv, modules
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from _frozen_importlib import ModuleSpec
    from collections.abc import Callable
    from types import ModuleType


class Constants:
    """Group immutable dispatcher metadata.

    Attributes:
        verbs: Verb-to-flag mapping for installer actions.

    """

    verbs: Final[dict[str, tuple[str, ...]]] = {
        "install": (),
        "uninstall": ("--uninstall",),
        "list": ("--list",),
    }


def load_installer() -> ModuleType:
    """Load the sibling install.py module.

    Returns:
        Loaded installer module.

    Raises:
        RuntimeError: If the installer cannot be loaded.

    """
    location: Path = Path(__file__).resolve().parent / "install.py"
    name: str = f"{location.parent.name.replace('-', '_')}_install"
    spec: ModuleSpec | None = spec_from_file_location(
        name=name,
        location=location,
    )
    if spec is None or spec.loader is None:
        msg: str = f"Unable to load installer at {location}"
        raise RuntimeError(msg)
    module: ModuleType = module_from_spec(spec=spec)
    modules[name] = module
    spec.loader.exec_module(module=module)
    return module


def main(*, arguments: tuple[str, ...] | None = None) -> int:
    """Dispatch to install.py after normalizing the verb.

    Args:
        arguments: Arguments.

    Returns:
        The resulting value.

    """
    args: tuple[str, ...] = tuple(argv[1:]) if arguments is None else arguments
    if args[:1] == ("run",):
        args = args[1:]
    if args and args[0] in Constants.verbs:
        args = (*Constants.verbs[args[0]], *args[1:])
    module: ModuleType = load_installer()
    entry: Callable[..., int] = cast(typ="Callable[..., int]", val=module.main)
    return entry(arguments=args)


if __name__ == "__main__":
    raise SystemExit(main())
