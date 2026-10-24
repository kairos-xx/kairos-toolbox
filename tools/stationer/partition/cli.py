# Copyright (c) 2026 Project contributors.
"""Command-line surface for the content-addressed partition store."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Final, TextIO, cast, final

from .store import (
    ObjectStore,
    PartitionStore,
    RecipeScanner,
    deep_verify,
    garbage_collect,
    load_catalog,
    rollback,
    write_delta,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from .store import Catalog, Json, Recipe


@final
class Constants:
    """Group module-level constants.

    Attributes:
        platform: Platform label recorded in built catalogs.
        cache_name: Default build-cache directory name.

    """

    platform: Final[str] = "partition"
    cache_name: Final[str] = ".cache"


def _write(*, text: str, error: bool = False) -> None:
    """Write one line to stdout or stderr.

    Args:
        text: Text to write.
        error: Write to stderr instead of stdout.

    """
    stream: TextIO = cast(
        typ="TextIO",
        val=import_module(name="sys").stderr
        if error
        else import_module(name="sys").stdout,
    )
    _ = stream.write(text)


def _parser() -> ArgumentParser:
    """Build the partition subcommand parser.

    Returns:
        The configured argument parser.

    """
    parser: ArgumentParser = ArgumentParser(
        prog="stationer partition",
        description="Content-addressed partition store operations.",
    )
    add: Callable[..., ArgumentParser] = parser.add_subparsers(
        dest="operation",
        required=True,
    ).add_parser
    build: ArgumentParser = add(
        "build",
        help="Scan component directories into a catalog and packs",
    )
    _ = build.add_argument("source", type=Path)
    _ = build.add_argument("--out", type=Path, required=True)
    _ = build.add_argument("--cache", type=Path)
    _ = build.add_argument("--pack-target", type=int, default=0)
    verify: ArgumentParser = add(
        "verify",
        help="Verify a catalog's packs and framed objects",
    )
    _ = verify.add_argument("catalog", type=Path)
    _ = verify.add_argument("--deep", action="store_true")
    update: ArgumentParser = add(
        "update",
        help="Write only the packs a catalog adds over a baseline",
    )
    _ = update.add_argument(
        "--from",
        dest="baseline",
        type=Path,
        required=True,
    )
    _ = update.add_argument("--to", dest="current", type=Path, required=True)
    _ = update.add_argument("--out", type=Path, required=True)
    restore: ArgumentParser = add(
        "rollback",
        help="Restore a retained catalog into a store root",
    )
    _ = restore.add_argument("root", type=Path)
    _ = restore.add_argument("digest")
    collect: ArgumentParser = add(
        "gc",
        help="Remove packs unreachable from the current catalog",
    )
    _ = collect.add_argument("root", type=Path)
    _ = collect.add_argument("--dry-run", action="store_true")
    return parser


def _scan_recipes(*, source: Path, objects: ObjectStore) -> dict[str, Recipe]:
    """Scan every component directory under ``source`` into recipes.

    Args:
        source: Directory containing one directory per component, or a
            single component tree.
        objects: Content-addressed object store receiving chunks.

    Returns:
        Component recipes keyed by name.

    """
    scanner: RecipeScanner = RecipeScanner(objects=objects)
    roots: list[Path] = sorted(
        entry for entry in source.iterdir() if entry.is_dir()
    )
    if not roots:
        roots = [source]
    return {
        root.name: scanner.scan(root=root, name=root.name, metadata={})
        for root in roots
    }


def _build(*, args: Namespace) -> int:
    """Build a catalog and packs from component directories.

    Args:
        args: Parsed ``build`` arguments.

    Returns:
        Process exit code.

    """
    cache: Path = args.cache or args.out / Constants.cache_name
    store: PartitionStore = PartitionStore(cache=cache)
    recipes: dict[str, Recipe] = _scan_recipes(
        source=args.source,
        objects=store.objects,
    )
    built: Catalog = store.build(
        recipes=recipes,
        output=args.out,
        platform=Constants.platform,
        pack_target=args.pack_target or 64 * 1024,
    )
    _write(text=built["root_sha256"] + "\n")
    return 0


def _verify(*, args: Namespace) -> int:
    """Verify a catalog's transport hashes and framed objects.

    Args:
        args: Parsed ``verify`` arguments.

    Returns:
        Process exit code.

    """
    catalog: Catalog = load_catalog(path=args.catalog)
    root: Path = args.catalog.parent
    if args.deep:
        deep_verify(catalog=catalog, root=root)
    PartitionStore.verify(catalog=catalog, root=root)
    _write(text=f"Verified {catalog['root_sha256']}\n")
    return 0


def _update(*, args: Namespace) -> int:
    """Write a delta artifact containing only newly needed packs.

    Args:
        args: Parsed ``update`` arguments.

    Returns:
        Process exit code.

    """
    baseline: Catalog = load_catalog(path=args.baseline)
    current: Catalog = load_catalog(path=args.current)
    manifest: dict[str, Json] = write_delta(
        baseline=baseline,
        current=current,
        source=args.current.parent,
        output=args.out,
    )
    missing: list[str] = cast(
        typ="list[str]",
        val=manifest["missing_packs"],
    )
    _write(text=f"{len(missing)} packs\n")
    return 0


def _rollback(*, args: Namespace) -> int:
    """Restore a retained catalog as the store's active catalog.

    Args:
        args: Parsed ``rollback`` arguments.

    Returns:
        Process exit code.

    """
    restored: Catalog = rollback(root=args.root, digest=args.digest)
    _write(text=restored["root_sha256"] + "\n")
    return 0


def _gc(*, args: Namespace) -> int:
    """Collect packs unreachable from the retained catalogs.

    Args:
        args: Parsed ``gc`` arguments.

    Returns:
        Process exit code.

    """
    retained: Catalog = load_catalog(path=args.root / "catalog.json")
    removed: list[str] = garbage_collect(
        root=args.root,
        retained=[retained],
        dry_run=args.dry_run,
    )
    _write(text=f"{len(removed)} packs\n")
    return 0


def main(*, arguments: tuple[str, ...]) -> int:
    """Run a partition operation.

    Args:
        arguments: Operation name and its arguments.

    Returns:
        Process exit code: ``0`` on success, ``1`` on failure.

    """
    args: Namespace = _parser().parse_args(args=list(arguments))
    handlers: dict[str, Callable[..., int]] = {
        "build": _build,
        "verify": _verify,
        "update": _update,
        "rollback": _rollback,
        "gc": _gc,
    }
    try:
        return handlers[args.operation](args=args)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        _write(text=f"partition: {exc}\n", error=True)
        return 1
