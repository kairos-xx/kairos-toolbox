# Copyright (c) 2026 Project contributors.
"""Central Lake Terminal product identity and state directory."""

from __future__ import annotations

from os import name as os_name
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path


class Product:
    """Define stable Lake Terminal product identity.

    Attributes:
        name: Human-readable product name.
        version: Product release version.
        command: Public command name.
        module: Python module used by launchers.
        state_directory: Current state directory basename.
        database_name: Current SQLite database basename.
        url_scheme: Clipboard URL scheme.

    """

    name: Final[str] = "Lake Terminal"
    version: Final[str] = "1.0.0"
    command: Final[str] = "lake"
    module: Final[str] = "lake_terminal"
    state_directory: Final[str] = ".lake_terminal"
    database_name: Final[str] = "lake.db"
    url_scheme: Final[str] = "lake-terminal"


def state_directory(*, home: Path) -> Path:
    """Return the secured state directory.

    Args:
        home: User home directory.

    Returns:
        Current Lake Terminal state directory.

    """
    current: Path = home / Product.state_directory
    current.mkdir(parents=True, exist_ok=True)
    _secure(path=current, mode=0o700)
    return current


def secure_private_file(*, path: Path) -> None:
    """Restrict a state file to the current user where supported.

    Args:
        path: File to secure.

    """
    _secure(path=path, mode=0o600)


def _secure(*, path: Path, mode: int) -> None:
    """Apply Unix permissions when the platform supports them.

    Args:
        path: Filesystem object to secure.
        mode: Unix permission bits.

    """
    if os_name != "nt":
        path.chmod(mode=mode)
