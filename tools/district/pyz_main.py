# Copyright (c) 2026 Project contributors.
"""Entry point of the self-contained ``district`` archive.

``tools.stationer --format pyz`` ships this file as the archive's
``__main__.py`` (the ``[pyz] bootstrap`` entry of ``stationer.toml``).
Python cannot import compiled extensions (such as ``psutil``) straight from
a zip, so the archive unpacks itself once into a per-build cache directory
and imports from there, the same approach ``shiv`` and ``pex`` use.
"""

from __future__ import annotations

from hashlib import sha256
from importlib import import_module
from os import getpid
from pathlib import Path
from shutil import rmtree
from sys import argv, path
from tempfile import gettempdir
from typing import TYPE_CHECKING, cast
from zipfile import ZipFile

if TYPE_CHECKING:
    from collections.abc import Callable
    from os import stat_result


def cache_root() -> Path:
    """Return the directory that holds extracted archive builds.

    Returns:
        ``~/.district/pyz-cache``.

    """
    return Path.home() / ".district" / "pyz-cache"


def extract(*, archive: Path, root: Path) -> Path:
    """Unpack ``archive`` once per build and prune replaced builds.

    Args:
        archive: The running ``.pyz`` file.
        root: Cache directory.

    Returns:
        Directory containing the unpacked archive.

    """
    stat: stat_result = archive.stat()
    location: str = sha256(
        data=str(object=archive.resolve()).encode(),
    ).hexdigest()[:8]
    owner: str = f"{archive.stem}-{location}"
    key: str = f"{owner}-{stat.st_size}-{stat.st_mtime_ns}"
    final: Path = root / key
    if final.is_dir():
        return final
    root.mkdir(parents=True, exist_ok=True)
    staging: Path = root / f".{key}.{getpid()}.tmp"
    rmtree(path=staging, ignore_errors=True)
    with ZipFile(file=archive) as bundle:
        bundle.extractall(path=staging)
    try:
        _ = staging.rename(target=final)
    except OSError:
        rmtree(path=staging, ignore_errors=True)
        if not final.is_dir():
            raise
    for stale in root.iterdir():
        if stale.name.startswith(f"{owner}-") and stale != final:
            rmtree(path=stale, ignore_errors=True)
    return final


def main() -> int:
    """Run the CLI, or the installer for ``run ...``.

    Returns:
        The process exit status.

    """
    archive: Path = Path(argv[0]).resolve()
    try:
        unpacked: Path = extract(archive=archive, root=cache_root())
    except OSError:
        unpacked = extract(
            archive=archive,
            root=Path(gettempdir()) / "district-pyz-cache",
        )
    path.insert(0, str(object=unpacked))
    args: tuple[str, ...] = tuple(argv[1:])
    if args[:1] == ("run",):
        install: Callable[..., int] = cast(
            typ="Callable[..., int]",
            val=import_module(name="district.installer").main,
        )
        return install(arguments=args[1:])
    entry: Callable[..., int] = cast(
        typ="Callable[..., int]",
        val=import_module(name="district.cli").main,
    )
    return entry(arguments=args)


if __name__ == "__main__":
    raise SystemExit(main())
