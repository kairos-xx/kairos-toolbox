# Copyright (c) 2026 Project contributors.
"""Tests for the partition store command-line surface."""

from __future__ import annotations

from shutil import copy as shutil_copy
from typing import TYPE_CHECKING

from stationer.partition.cli import main
from stationer.partition.store import load_catalog

from tests.tools.stationer.helpers import check, expect

if TYPE_CHECKING:
    from pathlib import Path


def _component(root: Path, name: str, payload: bytes) -> Path:
    """Create one component tree with a single payload file.

    Args:
        root: Parent directory.
        name: Component directory name.
        payload: Bytes written to ``data.txt``.

    Returns:
        The created component directory.

    """
    tree: Path = root / name
    tree.mkdir(parents=True)
    _ = (tree / "data.txt").write_bytes(data=payload)
    return tree


def _build(source: Path, out: Path) -> str:
    """Build a catalog from ``source`` and return its root digest.

    Args:
        source: Component source root.
        out: Catalog output directory.

    Returns:
        The built catalog's root SHA-256.

    """
    expect(
        actual=main(
            arguments=("build", str(source), "--out", str(out)),
        ),
        expected=0,
    )
    catalog: Path = out / "catalog.json"
    check(condition=catalog.is_file(), reason="catalog.json missing.")
    return load_catalog(path=catalog)["root_sha256"]


def test_build_produces_catalog_and_packs(tmp_path: Path) -> None:
    """`build` scans component dirs into a catalog plus pack files.

    Args:
        tmp_path: Per-test temporary directory.

    """
    source: Path = tmp_path / "src"
    _component(root=source, name="core", payload=b"core-payload" * 32)
    _component(root=source, name="extra", payload=b"extra-payload" * 32)
    out: Path = tmp_path / "dist"
    _build(source=source, out=out)
    catalog = load_catalog(path=out / "catalog.json")
    check(
        condition=set(catalog["components"]) == {"core", "extra"},
        reason="Components missing from the catalog.",
    )
    packs: list[Path] = list((out / "packs").glob(pattern="*/*.zst"))
    check(condition=bool(packs), reason="No pack files were written.")


def test_verify_detects_pack_corruption(tmp_path: Path) -> None:
    """`verify` passes a good catalog and fails a corrupted pack.

    Args:
        tmp_path: Per-test temporary directory.

    """
    source: Path = tmp_path / "src"
    _component(root=source, name="core", payload=b"x" * 2048)
    out: Path = tmp_path / "dist"
    _build(source=source, out=out)
    catalog_path: Path = out / "catalog.json"
    expect(
        actual=main(arguments=("verify", str(catalog_path))),
        expected=0,
    )
    pack: Path = next((out / "packs").glob(pattern="*/*.zst"))
    _ = pack.write_bytes(data=b"tampered" + pack.read_bytes()[8:])
    expect(
        actual=main(arguments=("verify", str(catalog_path))),
        expected=1,
    )


def test_update_writes_only_new_packs(tmp_path: Path) -> None:
    """`update` copies exactly the packs the target catalog adds.

    Args:
        tmp_path: Per-test temporary directory.

    """
    source: Path = tmp_path / "src"
    core: Path = _component(
        root=source,
        name="core",
        payload=b"v1" * 1024,
    )
    baseline_out: Path = tmp_path / "v1"
    _build(source=source, out=baseline_out)
    _ = (core / "data.txt").write_bytes(data=b"v2" * 1024)
    current_out: Path = tmp_path / "v2"
    _build(source=source, out=current_out)
    delta: Path = tmp_path / "delta"
    expect(
        actual=main(
            arguments=(
                "update",
                "--from",
                str(baseline_out / "catalog.json"),
                "--to",
                str(current_out / "catalog.json"),
                "--out",
                str(delta),
            ),
        ),
        expected=0,
    )
    baseline = load_catalog(path=baseline_out / "catalog.json")
    current = load_catalog(path=current_out / "catalog.json")
    added: set[str] = set(current["packs"]) - set(baseline["packs"])
    written: set[str] = {
        path.stem for path in (delta / "packs").glob(pattern="*/*.zst")
    }
    expect(actual=written, expected=added)
    check(
        condition=(delta / "update.json").is_file(),
        reason="update.json missing.",
    )


def test_rollback_restores_earlier_catalog(tmp_path: Path) -> None:
    """`rollback` switches the store back to a retained catalog.

    Args:
        tmp_path: Per-test temporary directory.

    """
    source: Path = tmp_path / "src"
    _component(root=source, name="core", payload=b"r1" * 512)
    store: Path = tmp_path / "store"
    earlier: str = _build(source=source, out=store)
    catalogs: Path = store / "catalogs"
    catalogs.mkdir()
    shutil_copy(src=store / "catalog.json", dst=catalogs / f"{earlier}.json")
    _ = (source / "core" / "data.txt").write_bytes(data=b"r2" * 512)
    newer: str = _build(source=source, out=store)
    expect(
        actual=main(arguments=("rollback", str(store), earlier)),
        expected=0,
    )
    active = load_catalog(path=store / "catalog.json")
    expect(actual=active["root_sha256"], expected=earlier)
    check(condition=active["root_sha256"] != newer, reason="No rollback.")


def test_gc_removes_unreachable_packs_only(tmp_path: Path) -> None:
    """`gc` deletes packs no retained catalog references.

    Args:
        tmp_path: Per-test temporary directory.

    """
    store: Path = tmp_path / "store"
    stale: str = "b" * 64
    stale_pack: Path = store / "packs" / stale[:2] / f"{stale}.zst"
    stale_pack.parent.mkdir(parents=True)
    _ = stale_pack.write_bytes(data=b"x")
    source: Path = tmp_path / "src"
    _component(root=source, name="core", payload=b"gc" * 512)
    _build(source=source, out=store)
    kept: list[Path] = [
        path
        for path in (store / "packs").glob(pattern="*/*.zst")
        if path != stale_pack
    ]
    expect(
        actual=main(arguments=("gc", str(store))),
        expected=0,
    )
    check(
        condition=not stale_pack.exists(),
        reason="Stale pack survived collection.",
    )
    check(
        condition=all(path.is_file() for path in kept),
        reason="Reachable pack was collected.",
    )
    expect(
        actual=main(arguments=("gc", str(store), "--dry-run")),
        expected=0,
    )
