# Copyright (c) 2026 Project contributors.
"""Tests for the content-addressed partition filesystem."""

from importlib import import_module
from json import loads
from os import link as os_link
from os import mkfifo as os_mkfifo
from pathlib import Path, PurePosixPath
from shutil import copytree as shutil_copytree
from stat import S_IMODE
from struct import pack as struct_pack
from sys import platform
from typing import TYPE_CHECKING, NoReturn, cast
from zipfile import ZipFile, ZipInfo

import pytest
from stationer.partition.store import (
    Catalog,
    ChunkingConfig,
    Constants,
    Entry,
    FormatError,
    Json,
    Materializer,
    ObjectStore,
    PackCodec,
    PartitionStore,
    Recipe,
    RecipeScanner,
    apply_delta,
    apply_update,
    canonical,
    component_name,
    deep_verify,
    destination,
    garbage_collect,
    identity,
    load_catalog,
    load_recipe,
    recipe_identity,
    rollback,
    safe_path,
    tree_identity,
    validate_recipe,
    verify_tree,
    write_delta,
)
from stationer.partition.zstd_portable import Zstd, ZstdOptions

from tests.tools.stationer.helpers import check, expect

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType


def _fail(*, message: str) -> NoReturn:
    """Fail the running test.

    Args:
        message: Explanation of the failed expectation.

    Raises:
        AssertionError: Always.

    """
    raise AssertionError(message)


def _private_result[T](
    *,
    owner: object,
    name: str,
    kind: type[T],
    **arguments: object,
) -> T:
    """Call a non-public callable of ``owner`` and type its result.

    Args:
        owner: Module, class, or instance holding the callable.
        name: Callable name, including its leading underscore.
        kind: Class the result must be an instance of.
        arguments: Keyword arguments forwarded to the callable.

    Returns:
        The result narrowed to ``kind``.

    """
    result: object = getattr(owner, name)(**arguments)
    check(
        condition=isinstance(result, kind),
        reason=f"{name} did not return {kind!r}: {result!r}",
    )
    return cast(typ="T", val=result)


def component_tree(root: Path, *, payload: bytes = b"payload" * 100) -> Path:
    """Create one component exercising files, links, and directories.

    Args:
        root: Parent directory for the component source tree.
        payload: File content written under the tree.

    Returns:
        Component source tree root.

    """
    source: Path = root / "source"
    bin_dir: Path = source / "bin"
    bin_dir.mkdir(parents=True)
    _ = (bin_dir / "run").write_bytes(data=b"#!/bin/sh\necho ok\n")
    data: Path = source / "data.txt"
    _ = data.write_bytes(data=payload)
    os_link(src=data, dst=source / "alias.txt")
    (source / "link").symlink_to(target="data.txt")
    (source / "empty").mkdir()
    return source


def scan_recipe(
    objects: ObjectStore,
    root: Path,
    name: str,
    metadata: dict[str, Json] | None = None,
    progress: object = None,
) -> Recipe:
    """Scan a component tree into a validated recipe.

    Args:
        objects: Content-addressed object store receiving chunks.
        root: Component source tree root.
        name: Component name recorded in the recipe.
        metadata: Optional component metadata.
        progress: Optional scan progress callback.

    Returns:
        Recipe with identity hashes computed.

    """
    scanner: RecipeScanner = RecipeScanner(
        objects=objects,
        progress=cast(
            typ="Callable[[int, int], None] | None",
            val=progress,
        ),
    )
    return scanner.scan(root=root, name=name, metadata=metadata or {})


def recook(
    recipe: Recipe,
    *,
    entries: list[Entry] | None = None,
    metadata: dict[str, Json] | None = None,
    name: str | None = None,
    logical_bytes: int | None = None,
) -> Recipe:
    """Return a mutated recipe whose identity hashes stay consistent.

    Args:
        recipe: Base recipe to mutate.
        entries: Replacement entry list.
        metadata: Replacement metadata.
        name: Replacement component name.
        logical_bytes: Replacement logical byte count.

    Returns:
        Mutated recipe with recomputed tree and recipe hashes.

    """
    cooked: Recipe = cast(typ="Recipe", val=dict(recipe))
    if entries is not None:
        cooked["entries"] = entries
    if metadata is not None:
        cooked["metadata"] = metadata
    if name is not None:
        cooked["name"] = name
    if logical_bytes is not None:
        cooked["logical_bytes"] = logical_bytes
    cooked["tree_sha256"] = tree_identity(entries=cooked["entries"])
    cooked["recipe_sha256"] = recipe_identity(recipe=cooked)
    return cooked


def file_entry(recipe: Recipe) -> Entry:
    """Return the first regular-file entry of a recipe.

    Args:
        recipe: Recipe to inspect.

    Returns:
        First entry with ``type == "file"``.

    """
    for entry in recipe["entries"]:
        if entry["type"] == "file":
            return entry
    return _fail(message="recipe has no file entry")


def publish(
    cache: Path,
    recipes: dict[str, Recipe],
    output: Path,
    *,
    pack_target: int = 64 * Constants.block,
) -> Catalog:
    """Build a real catalog and pack layout under ``output``.

    Args:
        cache: Persistent build cache for the store.
        recipes: Recipes to publish.
        output: Destination directory for packs and ``catalog.json``.
        pack_target: Target pack size in bytes.

    Returns:
        The published catalog.

    """
    store: PartitionStore = PartitionStore(cache=cache)
    return store.build(
        recipes=recipes,
        output=output,
        platform="test",
        pack_target=pack_target,
    )


def document(path: Path) -> dict[str, Json]:
    """Read a JSON document as a mutable mapping.

    Args:
        path: Document path.

    Returns:
        Decoded top-level mapping.

    """
    return cast(
        typ="dict[str, Json]",
        val=loads(s=path.read_text(encoding="utf-8")),
    )


def fix_root(doc: dict[str, Json]) -> None:
    """Recompute ``root_sha256`` after mutating a catalog document.

    Args:
        doc: Catalog document to repair.

    """
    body: dict[str, Json] = dict(doc)
    _ = body.pop("root_sha256", None)
    doc["root_sha256"] = identity(value=body)


def load_doc(doc: dict[str, Json], tmp_path: Path) -> None:
    """Write a mutated catalog document and load it for validation.

    Args:
        doc: Mutated catalog document.
        tmp_path: Scratch directory for the rewritten document.

    """
    index: int = len(list(tmp_path.glob(pattern="mutated-*")))
    target: Path = tmp_path / f"mutated-{index}.json"
    _ = target.write_bytes(data=canonical(value=doc))
    _ = load_catalog(path=target)


def fix_update_bytes(update: Path) -> None:
    """Repair ``update_bytes`` after mutating an update manifest.

    Args:
        update: Update artifact directory.

    """
    manifest: dict[str, Json] = document(path=update / "update.json")
    manifest["update_bytes"] = 0
    payload: int = sum(
        path.stat().st_size
        for path in update.rglob(pattern="*")
        if path.is_file() and path.name != "update.json"
    )
    while True:
        data: bytes = canonical(value=manifest)
        total: int = payload + len(data)
        if manifest["update_bytes"] == total:
            break
        manifest["update_bytes"] = total
    _ = (update / "update.json").write_bytes(data=data)


def zip_update(update: Path, artifact: Path) -> None:
    """Pack an update directory into a streamed ZIP artifact.

    Args:
        update: Update artifact directory.
        artifact: Destination ZIP path.

    """
    with ZipFile(file=artifact, mode="w") as archive:
        for path in sorted(update.rglob(pattern="*")):
            if path.is_file():
                archive.write(
                    filename=path,
                    arcname=path.relative_to(update).as_posix(),
                )


def write_stream(path: Path, payload: bytes) -> None:
    """Write one raw Zstandard frame for codec fixtures.

    Args:
        path: Destination stream path.
        payload: Decompressed payload bytes.

    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with Zstd(file=path, options=ZstdOptions(level=3)) as stream:
        _ = stream.write(payload)


def test_partition_scan_pack_and_materialize(tmp_path: Path) -> None:
    """Scan, publish, verify, materialize, and deep-verify a real tree.

    Args:
        tmp_path: Isolated filesystem root.

    """
    progress: list[tuple[int, int]] = []

    def record(count: int, logical: int) -> None:
        """Collect a progress callback pair.

        Args:
            count: Completed item count.
            logical: Total item count.

        """
        progress.append((count, logical))

    objects: ObjectStore = ObjectStore(root=tmp_path / "cache" / "objects")
    source: Path = component_tree(root=tmp_path)
    recipe: Recipe = scan_recipe(
        objects=objects,
        root=source,
        name="core",
        progress=record,
    )
    check(condition=bool(progress), reason="Publish emitted no progress.")
    catalog: Catalog = publish(
        cache=tmp_path / "cache",
        recipes={"core": recipe},
        output=tmp_path / "dist",
        pack_target=1,
    )
    store: ObjectStore = ObjectStore(root=tmp_path / "decoded")
    PartitionStore.verify(
        catalog=catalog,
        root=tmp_path / "dist",
        store=store,
    )
    materializer: Materializer = Materializer(store=store)
    target: Path = tmp_path / "components" / "core"
    materializer.materialize(recipe=recipe, root=target)
    verify_tree(recipe=recipe, root=target)
    verify_tree(recipe=recipe, root=target, contents=False)
    deep_verify(catalog=catalog, root=tmp_path / "dist")
    check(
        condition=(target / "alias.txt").samefile(
            other_path=target / "data.txt",
        ),
        reason="Hard link was not materialized.",
    )
    expect(
        actual=(target / "link").readlink().as_posix(),
        expected="data.txt",
    )


def test_partition_scanner_rejections(tmp_path: Path) -> None:
    """Reject unsafe names, unsupported entries, and large-file scans.

    Args:
        tmp_path: Isolated filesystem root.

    """
    objects: ObjectStore = ObjectStore(root=tmp_path / "objects")
    source: Path = component_tree(root=tmp_path)
    with pytest.raises(
        expected_exception=FormatError,
        match="Unsafe component name",
    ):
        _ = scan_recipe(objects=objects, root=source, name="a/b")
    if platform != "win32":
        os_mkfifo(path=source / "pipe")
        with pytest.raises(
            expected_exception=FormatError,
            match="Unsupported filesystem entry",
        ):
            _ = scan_recipe(objects=objects, root=source, name="core")
        (source / "pipe").unlink()
    large: Path = tmp_path / "large-src"
    large.mkdir()
    _ = (large / "big.bin").write_bytes(
        data=bytes(range(256)) * 5000,
    )
    recipe: Recipe = scan_recipe(objects=objects, root=large, name="core")
    check(
        condition=len(file_entry(recipe=recipe).get("chunks", [])) > 1,
        reason="Large payload must produce multiple chunks.",
    )


def test_partition_recipe_rejections(tmp_path: Path) -> None:
    """Exercise every recipe structural and entry rejection.

    Args:
        tmp_path: Isolated filesystem root.

    """
    objects: ObjectStore = ObjectStore(root=tmp_path / "objects")
    recipe: Recipe = scan_recipe(
        objects=objects,
        root=component_tree(root=tmp_path),
        name="core",
    )
    bad_hash: Recipe = recook(recipe=recipe)
    bad_hash["recipe_sha256"] = "0" * 64
    with pytest.raises(
        expected_exception=FormatError,
        match="Recipe hash mismatch",
    ):
        validate_recipe(recipe=bad_hash)
    bad_tree: Recipe = recook(recipe=recipe)
    bad_tree["tree_sha256"] = "0" * 64
    bad_tree["recipe_sha256"] = recipe_identity(recipe=bad_tree)
    with pytest.raises(
        expected_exception=FormatError,
        match="Tree hash mismatch",
    ):
        validate_recipe(recipe=bad_tree)
    verify_recipe_entry_rejections(recipe=recipe)
    verify_recipe_link_rejections(recipe=recipe)
    verify_recipe_document_rejections(recipe=recipe)


def verify_recipe_entry_rejections(*, recipe: Recipe) -> None:
    """Reject duplicate, unsorted, orphaned, and mistyped entries.

    Args:
        recipe: Valid recipe mutated per case.

    """
    entries: list[Entry] = recipe["entries"]
    first: Entry = entries[0]
    with pytest.raises(
        expected_exception=FormatError,
        match="Duplicate recipe path",
    ):
        validate_recipe(
            recipe=recook(recipe=recipe, entries=[*entries, first]),
        )
    with pytest.raises(
        expected_exception=FormatError,
        match="Unsorted recipe paths",
    ):
        validate_recipe(
            recipe=recook(recipe=recipe, entries=list(reversed(entries))),
        )
    parent_file: list[Entry] = [
        cast(
            typ="Entry",
            val={
                "path": "a",
                "type": "file",
                "mode": 0o644,
                "size": 0,
                "sha256": "0" * 64,
                "chunks": [],
            },
        ),
        cast(
            typ="Entry",
            val={
                "path": "a/b",
                "type": "file",
                "mode": 0o644,
                "size": 0,
                "sha256": "0" * 64,
                "chunks": [],
            },
        ),
    ]
    with pytest.raises(
        expected_exception=FormatError,
        match="Non-directory recipe parent",
    ):
        validate_recipe(
            recipe=recook(
                recipe=recipe,
                entries=parent_file,
                logical_bytes=0,
            ),
        )
    bad_mode: list[Entry] = [
        cast(typ="Entry", val={**first, "mode": 0o1000}),
        *entries[1:],
    ]
    with pytest.raises(
        expected_exception=FormatError,
        match="Unsafe file mode",
    ):
        validate_recipe(recipe=recook(recipe=recipe, entries=bad_mode))
    with pytest.raises(
        expected_exception=FormatError,
        match="Logical byte count mismatch",
    ):
        validate_recipe(
            recipe=recook(
                recipe=recipe,
                logical_bytes=recipe["logical_bytes"] + 1,
            ),
        )
    original: Entry = file_entry(recipe=recipe)
    bad_digest: list[Entry] = [
        cast(typ="Entry", val={**original, "sha256": "zz"})
        if entry["path"] == original["path"]
        else entry
        for entry in entries
    ]
    with pytest.raises(
        expected_exception=FormatError,
        match="Invalid SHA-256 address",
    ):
        validate_recipe(recipe=recook(recipe=recipe, entries=bad_digest))
    bad_chunk: list[Entry] = [
        cast(typ="Entry", val={**original, "chunks": ["zz"]})
        if entry["path"] == original["path"]
        else entry
        for entry in entries
    ]
    with pytest.raises(
        expected_exception=FormatError,
        match="Invalid SHA-256 address",
    ):
        validate_recipe(recipe=recook(recipe=recipe, entries=bad_chunk))


def verify_recipe_link_rejections(*, recipe: Recipe) -> None:
    """Reject broken hardlinks, bad symlinks, and unknown entry kinds.

    Args:
        recipe: Valid recipe mutated per case.

    """
    entries: list[Entry] = recipe["entries"]
    hardlink: Entry = cast(
        typ="Entry",
        val={
            "path": "zz-link",
            "type": "hardlink",
            "mode": 0o644,
            "target": "missing",
        },
    )
    with pytest.raises(
        expected_exception=FormatError,
        match="must reference a regular file",
    ):
        validate_recipe(
            recipe=recook(recipe=recipe, entries=[*entries, hardlink]),
        )
    dir_link: Entry = cast(
        typ="Entry",
        val={
            "path": "zz-link",
            "type": "hardlink",
            "mode": 0o755,
            "target": "empty",
        },
    )
    with pytest.raises(
        expected_exception=FormatError,
        match="must reference a regular file",
    ):
        validate_recipe(
            recipe=recook(recipe=recipe, entries=[*entries, dir_link]),
        )
    mode_link: Entry = cast(
        typ="Entry",
        val={
            "path": "zz-link",
            "type": "hardlink",
            "mode": file_entry(recipe=recipe)["mode"] ^ 0o400,
            "target": "alias.txt",
        },
    )
    with pytest.raises(
        expected_exception=FormatError,
        match="Incompatible hardlink modes",
    ):
        validate_recipe(
            recipe=recook(recipe=recipe, entries=[*entries, mode_link]),
        )
    for target in ("", "a\\b"):
        bad_link: Entry = cast(
            typ="Entry",
            val={
                "path": "zz-a",
                "type": "symlink",
                "mode": 0o755,
                "target": target,
            },
        )
        with pytest.raises(
            expected_exception=FormatError,
            match="Invalid symlink target",
        ):
            validate_recipe(
                recipe=recook(recipe=recipe, entries=[*entries, bad_link]),
            )
    alien: Entry = cast(
        typ="Entry",
        val={"path": "zz-x", "type": "socket", "mode": 0o644},
    )
    with pytest.raises(
        expected_exception=FormatError,
        match="Unknown recipe entry type",
    ):
        validate_recipe(
            recipe=recook(recipe=recipe, entries=[*entries, alien]),
        )


def verify_recipe_document_rejections(*, recipe: Recipe) -> None:
    """Round-trip and reject malformed serialized recipes.

    Args:
        recipe: Valid recipe mutated per case.

    """
    parsed: Recipe = load_recipe(
        value=loads(s=canonical(value=cast(typ="Json", val=dict(recipe)))),
    )
    expect(actual=parsed, expected=recipe)
    with pytest.raises(
        expected_exception=FormatError,
        match="Missing recipe entries",
    ):
        _ = load_recipe(value={"name": "core"})
    with pytest.raises(expected_exception=FormatError, match="Expected"):
        _ = load_recipe(
            value={
                "name": "core",
                "metadata": {},
                "entries": [{"path": "x", "type": "file"}],
                "tree_sha256": "0" * 64,
                "recipe_sha256": "0" * 64,
                "logical_bytes": 0,
            },
        )


def test_partition_catalog_rejections(tmp_path: Path) -> None:
    """Exercise catalog schema, membership, and identity rejections.

    Args:
        tmp_path: Isolated filesystem root.

    """
    objects: ObjectStore = ObjectStore(root=tmp_path / "cache" / "objects")
    recipes: dict[str, Recipe] = {
        "one": scan_recipe(
            objects=objects,
            root=component_tree(root=tmp_path / "a"),
            name="one",
        ),
        "two": scan_recipe(
            objects=objects,
            root=component_tree(root=tmp_path / "b"),
            name="two",
        ),
    }
    publish(
        cache=tmp_path / "cache",
        recipes=recipes,
        output=tmp_path / "dist",
    )
    catalog_path: Path = tmp_path / "dist" / "catalog.json"
    doc: dict[str, Json] = document(path=catalog_path)
    doc["schema_version"] = 2
    with pytest.raises(
        expected_exception=FormatError,
        match="Unsupported partition catalog",
    ):
        load_doc(doc=doc, tmp_path=tmp_path)
    doc = document(path=catalog_path)
    doc["platform"] = "other"
    with pytest.raises(
        expected_exception=FormatError,
        match="Catalog root hash mismatch",
    ):
        load_doc(doc=doc, tmp_path=tmp_path)
    verify_catalog_membership_rejections(
        catalog_path=catalog_path,
        tmp_path=tmp_path,
    )
    verify_catalog_pack_rejections(
        catalog_path=catalog_path,
        tmp_path=tmp_path,
    )
    verify_catalog_metadata_rejections(
        catalog_path=catalog_path,
        tmp_path=tmp_path,
        recipes=recipes,
    )


def verify_catalog_membership_rejections(
    *,
    catalog_path: Path,
    tmp_path: Path,
) -> None:
    """Reject name, chunker, and object-table inconsistencies.

    Args:
        catalog_path: Published catalog document path.
        tmp_path: Isolated filesystem root.

    """
    renamed: dict[str, Json] = document(path=catalog_path)
    components: dict[str, Json] = cast(
        typ="dict[str, Json]",
        val=renamed["components"],
    )
    components["three"] = components.pop("one")
    fix_root(doc=renamed)
    with pytest.raises(
        expected_exception=FormatError,
        match="Component name mismatch",
    ):
        load_doc(doc=renamed, tmp_path=tmp_path)
    chunker: dict[str, Json] = document(path=catalog_path)
    cast(typ="dict[str, Json]", val=chunker["chunking"])["algorithm"] = "bad"
    fix_root(doc=chunker)
    with pytest.raises(
        expected_exception=FormatError,
        match="Unknown chunker",
    ):
        load_doc(doc=chunker, tmp_path=tmp_path)
    oversized: dict[str, Json] = document(path=catalog_path)
    object_rows: dict[str, Json] = cast(
        typ="dict[str, Json]",
        val=oversized["objects"],
    )
    first_digest: str = sorted(object_rows)[0]
    cast(typ="dict[str, Json]", val=object_rows[first_digest])["size"] = (
        ChunkingConfig().maximum_bytes + 1
    )
    fix_root(doc=oversized)
    with pytest.raises(
        expected_exception=FormatError,
        match="exceeds chunk size policy",
    ):
        load_doc(doc=oversized, tmp_path=tmp_path)
    shrunk: dict[str, Json] = document(path=catalog_path)
    one: Recipe = cast(
        typ="Recipe",
        val=cast(typ="dict[str, Json]", val=shrunk["components"])["one"],
    )
    original: Entry = file_entry(recipe=one)
    less: list[Entry] = [
        cast(
            typ="Entry",
            val={
                **original,
                "chunks": [
                    *original.get("chunks", []),
                    original.get("chunks", [""])[0],
                ],
            },
        )
        if entry["path"] == original["path"]
        else entry
        for entry in one["entries"]
    ]
    cast(typ="dict[str, Json]", val=shrunk["components"])["one"] = cast(
        typ="Json",
        val=dict(recook(recipe=one, entries=less)),
    )
    fix_root(doc=shrunk)
    with pytest.raises(
        expected_exception=FormatError,
        match="File chunk sizes mismatch",
    ):
        load_doc(doc=shrunk, tmp_path=tmp_path)


def verify_catalog_pack_rejections(
    *,
    catalog_path: Path,
    tmp_path: Path,
) -> None:
    """Reject pack path, object list, and identity inconsistencies.

    Args:
        catalog_path: Published catalog document path.
        tmp_path: Isolated filesystem root.

    """
    bad_pack: dict[str, Json] = document(path=catalog_path)
    packs: dict[str, Json] = cast(
        typ="dict[str, Json]",
        val=bad_pack["packs"],
    )
    pack_id: str = sorted(packs)[0]
    cast(typ="dict[str, Json]", val=packs[pack_id])["path"] = "elsewhere.zst"
    fix_root(doc=bad_pack)
    with pytest.raises(
        expected_exception=FormatError,
        match="Invalid pack path",
    ):
        load_doc(doc=bad_pack, tmp_path=tmp_path)
    empty_pack: dict[str, Json] = document(path=catalog_path)
    cast(
        typ="dict[str, Json]",
        val=cast(typ="dict[str, Json]", val=empty_pack["packs"])[pack_id],
    )["objects"] = []
    fix_root(doc=empty_pack)
    with pytest.raises(
        expected_exception=FormatError,
        match="Invalid pack object list",
    ):
        load_doc(doc=empty_pack, tmp_path=tmp_path)
    dropped: dict[str, Json] = document(path=catalog_path)
    _ = cast(typ="dict[str, Json]", val=dropped["packs"]).pop(pack_id)
    fix_root(doc=dropped)
    with pytest.raises(
        expected_exception=FormatError,
        match="Unpacked catalog objects",
    ):
        load_doc(doc=dropped, tmp_path=tmp_path)
    identity_bad: dict[str, Json] = document(path=catalog_path)
    cast(
        typ="dict[str, Json]",
        val=cast(typ="dict[str, Json]", val=identity_bad["packs"])[pack_id],
    )["shard"] = "zz"
    fix_root(doc=identity_bad)
    with pytest.raises(
        expected_exception=FormatError,
        match="Pack logical identity mismatch",
    ):
        load_doc(doc=identity_bad, tmp_path=tmp_path)


def verify_catalog_metadata_rejections(
    *,
    catalog_path: Path,
    tmp_path: Path,
    recipes: dict[str, Recipe],
) -> None:
    """Reject dependency, environment, and tool metadata violations.

    Args:
        catalog_path: Published catalog document path.
        tmp_path: Isolated filesystem root.
        recipes: Published recipes by name.

    """

    def mutated(
        *,
        name: str,
        metadata: dict[str, Json] | None = None,
        entries: list[Entry] | None = None,
    ) -> dict[str, Json]:
        """Rewrite a recipe entry inside the stored catalog.

        Args:
            name: Recipe name to replace.
            metadata: Replacement metadata, if any.
            entries: Replacement entries, if any.

        Returns:
            The rewritten catalog document.

        """
        doc: dict[str, Json] = document(path=catalog_path)
        recipe: Recipe = recipes[name]
        cooked: Recipe = recook(
            recipe=recipe,
            metadata=metadata,
            entries=entries,
        )
        cast(typ="dict[str, Json]", val=doc["components"])[name] = cast(
            typ="Json",
            val=dict(cooked),
        )
        fix_root(doc=doc)
        return doc

    bad_requires: list[dict[str, Json]] = [
        {"requires": ["ghost"]},
        {"requires": ["one"]},
    ]
    for metadata in bad_requires:
        with pytest.raises(
            expected_exception=FormatError,
            match="Missing or cyclic",
        ):
            load_doc(
                doc=mutated(name="one", metadata=metadata),
                tmp_path=tmp_path,
            )
    bad_env: dict[str, Json] = mutated(
        name="one",
        metadata={"env": {"1BAD": ["x"]}},
    )
    with pytest.raises(
        expected_exception=FormatError,
        match="Invalid environment variable",
    ):
        load_doc(doc=bad_env, tmp_path=tmp_path)
    escaped_env: dict[str, Json] = mutated(
        name="one",
        metadata={"env": {"PATH": ["../../outside"]}},
    )
    with pytest.raises(
        expected_exception=FormatError,
        match="escapes component cache",
    ):
        load_doc(doc=escaped_env, tmp_path=tmp_path)
    verify_catalog_tool_and_link_rejections(
        mutated=mutated,
        tmp_path=tmp_path,
        recipes=recipes,
    )


def verify_catalog_tool_and_link_rejections(
    *,
    mutated: "Callable[..., dict[str, Json]]",
    tmp_path: Path,
    recipes: dict[str, Recipe],
) -> None:
    """Reject tool-schema and dependency-link violations in catalogs.

    Args:
        mutated: Helper producing mutated catalog documents.
        tmp_path: Isolated filesystem root.
        recipes: Published recipes by name.

    """
    bad_module: dict[str, Json] = mutated(
        name="one",
        metadata={
            "tools": {"bad": {"kind": "python-module", "target": "9bad"}},
        },
    )
    with pytest.raises(
        expected_exception=FormatError,
        match="Invalid Python module target",
    ):
        load_doc(doc=bad_module, tmp_path=tmp_path)
    bad_kind: dict[str, Json] = mutated(
        name="one",
        metadata={"tools": {"bad": {"kind": "alien", "target": "x"}}},
    )
    with pytest.raises(
        expected_exception=FormatError,
        match="Unsupported tool kind",
    ):
        load_doc(doc=bad_kind, tmp_path=tmp_path)
    duplicate: dict[str, Json] = mutated(
        name="one",
        metadata={"tools": {"dup": {"kind": "probe", "target": "x"}}},
    )
    second: Recipe = recook(
        recipe=recipes["two"],
        metadata={"tools": {"dup": {"kind": "probe", "target": "y"}}},
    )
    cast(typ="dict[str, Json]", val=duplicate["components"])["two"] = cast(
        typ="Json",
        val=dict(second),
    )
    fix_root(doc=duplicate)
    with pytest.raises(
        expected_exception=FormatError,
        match="Duplicate tool name",
    ):
        load_doc(doc=duplicate, tmp_path=tmp_path)
    one_recipe: Recipe = recipes["one"]
    for target, match in (
        ("/etc/passwd", "Absolute link target"),
        ("../two/data.txt", "not a component dependency"),
    ):
        linked_entries: list[Entry] = [
            *one_recipe["entries"],
            cast(
                typ="Entry",
                val={
                    "path": "zz-bad",
                    "type": "symlink",
                    "mode": 0o777,
                    "target": target,
                },
            ),
        ]
        with pytest.raises(expected_exception=FormatError, match=match):
            load_doc(
                doc=mutated(name="one", entries=linked_entries),
                tmp_path=tmp_path,
            )


class _ShortStream:
    """Readable stream that always returns EOF."""

    @staticmethod
    def read(size: int = -1) -> bytes:
        """Return empty data for any read.

        Args:
            size: Requested byte count.

        Returns:
            Empty payload.

        """
        del size
        return b""


def test_partition_coercion_and_path_rejections(tmp_path: Path) -> None:
    """Exercise scalar, JSON, and path safety rejections.

    Args:
        tmp_path: Isolated filesystem root.

    """
    module: ModuleType = import_module(
        name="stationer.partition.store",
    )
    for value in ("../x", "/abs", "C:\\x", "a//b", "a/./b", "a\\b", ""):
        with pytest.raises(
            expected_exception=FormatError,
            match="Unsafe path",
        ):
            _ = safe_path(value=value)
    expect(
        actual=safe_path(value="a/b"),
        expected=PurePosixPath("a/b"),
    )
    with pytest.raises(
        expected_exception=FormatError,
        match="Unsafe component name",
    ):
        _ = component_name(value="a/b")
    target: Path = tmp_path / "target"
    target.mkdir()
    linked: Path = tmp_path / "linked"
    linked.symlink_to(target=target)
    with pytest.raises(
        expected_exception=FormatError,
        match="root is a symlink",
    ):
        _ = destination(root=linked, relative="x")
    inner: Path = tmp_path / "inner"
    inner.mkdir()
    (inner / "sub").symlink_to(target=target)
    with pytest.raises(
        expected_exception=FormatError,
        match="Symlink destination parent",
    ):
        _ = destination(root=inner, relative="sub/x")
    with pytest.raises(expected_exception=FormatError, match="Invalid JSON"):
        _private_result(owner=module, name="_json", kind=object, data=b"{")
    with pytest.raises(
        expected_exception=FormatError,
        match="Expected JSON object",
    ):
        _private_result(owner=module, name="_mapping", kind=dict, value=[])
    with pytest.raises(
        expected_exception=FormatError,
        match="Expected string",
    ):
        _private_result(owner=module, name="_string", kind=str, value=1)
    for bad in (-1, True, "x"):
        with pytest.raises(
            expected_exception=FormatError,
            match="nonnegative integer",
        ):
            _private_result(owner=module, name="_integer", kind=int, value=bad)
    with pytest.raises(expected_exception=FormatError, match="string array"):
        _private_result(owner=module, name="_strings", kind=list, value={})
    with pytest.raises(expected_exception=FormatError, match="SHA-256"):
        _private_result(owner=module, name="_digest", kind=str, value="zz")
    with pytest.raises(
        expected_exception=FormatError,
        match="Invalid chunking configuration",
    ):
        _ = ChunkingConfig(minimum_bytes=10, target_bytes=5)
    with pytest.raises(
        expected_exception=FormatError,
        match="Impossible pack field size",
    ):
        _private_result(
            owner=module,
            name="_read_exact",
            kind=bytes,
            source=_ShortStream(),
            size=-1,
        )
    with pytest.raises(expected_exception=FormatError, match="Truncated pack"):
        _private_result(
            owner=module,
            name="_read_exact",
            kind=bytes,
            source=_ShortStream(),
            size=4,
        )


def test_partition_pack_codec(tmp_path: Path) -> None:
    """Verify pack framing and reject corrupt pack contents.

    Args:
        tmp_path: Isolated filesystem root.

    """
    objects: ObjectStore = ObjectStore(root=tmp_path / "objects")
    digest: str = objects.put(data=b"chunk-payload")
    pack: Path = tmp_path / "packs" / "ab" / "good.zst"
    PackCodec.encode(path=pack, objects=[digest], store=objects)
    decoded: ObjectStore = ObjectStore(root=tmp_path / "decoded")
    PackCodec.decode(
        path=pack,
        expected={digest: len(b"chunk-payload")},
        store=decoded,
    )
    check(
        condition=decoded.valid(digest=digest),
        reason="Decoded pack digest invalid.",
    )
    with pytest.raises(
        expected_exception=FormatError,
        match="Invalid source object",
    ):
        PackCodec.encode(
            path=tmp_path / "packs" / "cd" / "bad.zst",
            objects=["0" * 64],
            store=objects,
        )
    verify_pack_decode_rejections(tmp_path=tmp_path, digest=digest)


def verify_pack_decode_rejections(*, tmp_path: Path, digest: str) -> None:
    """Reject malformed magic, headers, objects, and membership.

    Args:
        tmp_path: Isolated filesystem root.
        digest: Digest of the previously encoded object.

    """
    magic: Path = tmp_path / "streams" / "magic.zst"
    write_stream(path=magic, payload=b"WRONG!")
    with pytest.raises(
        expected_exception=FormatError,
        match="Invalid pack magic",
    ):
        PackCodec.decode(path=magic, expected={})
    short: Path = tmp_path / "streams" / "short.zst"
    write_stream(path=short, payload=Constants.magic + b"\x00\x00\x00\x00")
    with pytest.raises(
        expected_exception=FormatError,
        match="Impossible pack header length",
    ):
        PackCodec.decode(path=short, expected={})
    payload: bytes = b"data"
    header: bytes = canonical(
        value={"sha256": "1" * 64, "size": len(payload)},
    )
    framed: bytes = (
        Constants.magic + struct_pack(">I", len(header)) + header + payload
    )
    alien: Path = tmp_path / "streams" / "alien.zst"
    write_stream(path=alien, payload=framed)
    with pytest.raises(
        expected_exception=FormatError,
        match="Duplicate or unexpected",
    ):
        PackCodec.decode(path=alien, expected={})
    tampered: Path = tmp_path / "streams" / "tampered.zst"
    write_stream(path=tampered, payload=framed)
    with pytest.raises(
        expected_exception=FormatError,
        match="Pack object hash mismatch",
    ):
        PackCodec.decode(
            path=tampered,
            expected={"1" * 64: len(payload)},
        )
    missing: Path = tmp_path / "streams" / "missing.zst"
    write_stream(path=missing, payload=Constants.magic)
    with pytest.raises(
        expected_exception=FormatError,
        match="Missing or unsorted",
    ):
        PackCodec.decode(
            path=missing,
            expected={digest: len(b"chunk-payload")},
        )


def test_partition_store_verify_and_recover(tmp_path: Path) -> None:
    """Recover objects from packs and reject corrupted pack bytes.

    Args:
        tmp_path: Isolated filesystem root.

    """
    objects: ObjectStore = ObjectStore(root=tmp_path / "cache" / "objects")
    recipe: Recipe = scan_recipe(
        objects=objects,
        root=component_tree(root=tmp_path),
        name="core",
    )
    store: PartitionStore = PartitionStore(cache=tmp_path / "cache")
    dist: Path = tmp_path / "dist"
    catalog: Catalog = store.build(
        recipes={"core": recipe},
        output=dist,
        platform="test",
    )
    digest: str = file_entry(recipe=recipe).get("chunks", [""])[0]
    store.objects.path(digest=digest).unlink()
    store.recover(catalog=catalog, root=dist, recipes={"core": recipe})
    check(
        condition=store.objects.valid(digest=digest),
        reason="Recovered object invalid.",
    )
    ghost_entry: Entry = cast(
        typ="Entry",
        val={**file_entry(recipe=recipe), "chunks": ["0" * 64]},
    )
    ghost: Recipe = recook(
        recipe=recipe,
        entries=[
            ghost_entry if entry["type"] == "file" else entry
            for entry in recipe["entries"]
        ],
    )
    with pytest.raises(
        expected_exception=FormatError,
        match="missing from previous catalog",
    ):
        store.recover(catalog=catalog, root=dist, recipes={"core": ghost})
    pack: Path = next(dist.rglob(pattern="*.zst"))
    _ = pack.write_bytes(data=pack.read_bytes() + b"x")
    with pytest.raises(
        expected_exception=FormatError,
        match="Compressed pack hash/size",
    ):
        PartitionStore.verify(catalog=catalog, root=dist)
    store.objects.path(digest=digest).unlink()
    with pytest.raises(
        expected_exception=FormatError,
        match="Recovery pack mismatch",
    ):
        store.recover(catalog=catalog, root=dist, recipes={"core": recipe})


def test_partition_materialize_rejections(tmp_path: Path) -> None:
    """Reject every reconstructed-tree integrity mismatch.

    Args:
        tmp_path: Isolated filesystem root.

    """
    objects: ObjectStore = ObjectStore(root=tmp_path / "objects")
    source: Path = component_tree(root=tmp_path)
    recipe: Recipe = scan_recipe(
        objects=objects,
        root=source,
        name="core",
    )

    def fresh() -> Path:
        """Materialize a fresh tree rooted below ``tmp_path``.

        Returns:
            The new materialized root.

        """
        index: int = len(list(tmp_path.glob(pattern="m-*")))
        root: Path = tmp_path / f"m-{index}" / "core"
        Materializer(store=objects).materialize(recipe=recipe, root=root)
        return root

    verify_tree(recipe=recipe, root=fresh())
    escaped_entry: Entry = cast(
        typ="Entry",
        val={
            "path": "empty/out",
            "type": "symlink",
            "mode": 0o777,
            "target": "../../../outside",
        },
    )
    escaped: Recipe = recook(
        recipe=recipe,
        entries=sorted(
            [*recipe["entries"], escaped_entry],
            key=lambda entry: entry["path"],
        ),
    )
    with pytest.raises(
        expected_exception=FormatError,
        match="escapes component cache",
    ):
        Materializer(store=objects).materialize(
            recipe=escaped,
            root=tmp_path / "esc" / "x",
        )
    verify_tree_rejection_battery(recipe=recipe, fresh=fresh)
    broken: Recipe = recook(
        recipe=recipe,
        entries=[
            cast(
                typ="Entry",
                val={
                    **file_entry(recipe=recipe),
                    "sha256": "1" * 64,
                    "chunks": ["0" * 64],
                },
            )
            if entry["path"] == file_entry(recipe=recipe)["path"]
            else entry
            for entry in recipe["entries"]
        ],
    )
    with pytest.raises(
        expected_exception=FormatError,
        match="Invalid materialization object",
    ):
        Materializer(store=objects).materialize(
            recipe=broken,
            root=tmp_path / "bm" / "x",
        )


def verify_tree_rejection_battery(
    *,
    recipe: Recipe,
    fresh: "Callable[[], Path]",
) -> None:
    """Corrupt materialized trees in every distinguishable way.

    Args:
        recipe: Recipe whose trees are verified.
        fresh: Callable materializing a fresh component root.

    """
    missing: Path = fresh()
    (missing / "bin" / "run").unlink()
    with pytest.raises(
        expected_exception=FormatError,
        match="entry list mismatch",
    ):
        verify_tree(recipe=recipe, root=missing)
    extra: Path = fresh()
    _ = (extra / "stray.txt").write_text(data="x", encoding="utf-8")
    with pytest.raises(
        expected_exception=FormatError,
        match="entry list mismatch",
    ):
        verify_tree(recipe=recipe, root=extra)
    verify_tree(recipe=recipe, root=extra, allow_extra=True)
    (extra / "data.txt").unlink()
    with pytest.raises(
        expected_exception=FormatError,
        match="entry list mismatch",
    ):
        verify_tree(recipe=recipe, root=extra, allow_extra=True)
    verify_tree_content_rejection_battery(recipe=recipe, fresh=fresh)
    verify_tree_link_rejection_battery(recipe=recipe, fresh=fresh)


def verify_tree_content_rejection_battery(
    *,
    recipe: Recipe,
    fresh: "Callable[[], Path]",
) -> None:
    """Corrupt file contents and modes in every distinguishable way.

    Args:
        recipe: Recipe whose trees are verified.
        fresh: Callable materializing a fresh component root.

    """
    resized: Path = fresh()
    _ = (resized / "alias.txt").write_bytes(data=b"short")
    with pytest.raises(expected_exception=FormatError, match="file mismatch"):
        verify_tree(recipe=recipe, root=resized)
    same_size: Path = fresh()
    original: bytes = (same_size / "alias.txt").read_bytes()
    _ = (same_size / "alias.txt").write_bytes(data=b"X" + original[1:])
    with pytest.raises(expected_exception=FormatError, match="file mismatch"):
        verify_tree(recipe=recipe, root=same_size)
    verify_tree(recipe=recipe, root=same_size, contents=False)
    moded: Path = fresh()
    target: Path = moded / "alias.txt"
    target.chmod(mode=S_IMODE(target.stat().st_mode) ^ 0o111)
    with pytest.raises(expected_exception=FormatError, match="mode mismatch"):
        verify_tree(recipe=recipe, root=moded)


def verify_tree_link_rejection_battery(
    *,
    recipe: Recipe,
    fresh: "Callable[[], Path]",
) -> None:
    """Corrupt link entries in every distinguishable way.

    Args:
        recipe: Recipe whose trees are verified.
        fresh: Callable materializing a fresh component root.

    """
    retargeted: Path = fresh()
    (retargeted / "link").unlink()
    (retargeted / "link").symlink_to(target="bin/run")
    with pytest.raises(
        expected_exception=FormatError,
        match="Symlink mismatch",
    ):
        verify_tree(recipe=recipe, root=retargeted)
    not_link: Path = fresh()
    (not_link / "link").unlink()
    _ = (not_link / "link").write_text(data="x", encoding="utf-8")
    with pytest.raises(
        expected_exception=FormatError,
        match="Symlink mismatch",
    ):
        verify_tree(recipe=recipe, root=not_link)
    not_file: Path = fresh()
    (not_file / "alias.txt").unlink()
    (not_file / "alias.txt").symlink_to(target="bin/run")
    with pytest.raises(
        expected_exception=FormatError,
        match="Regular file mismatch",
    ):
        verify_tree(recipe=recipe, root=not_file)
    not_dir: Path = fresh()
    (not_dir / "empty").rmdir()
    _ = (not_dir / "empty").write_text(data="x", encoding="utf-8")
    with pytest.raises(
        expected_exception=FormatError,
        match="Directory mismatch",
    ):
        verify_tree(recipe=recipe, root=not_dir)
    broken_link: Path = fresh()
    hardlinked: Path = broken_link / "alias.txt"
    hardlinked.unlink()
    _ = hardlinked.write_bytes(
        data=(broken_link / "data.txt").read_bytes(),
    )
    with pytest.raises(
        expected_exception=FormatError,
        match="Hardlink mismatch",
    ):
        verify_tree(recipe=recipe, root=broken_link)


def test_partition_update_and_rollback(tmp_path: Path) -> None:
    """Publish deltas, apply directory and ZIP updates, and roll back.

    Args:
        tmp_path: Isolated filesystem root.

    """
    objects: ObjectStore = ObjectStore(root=tmp_path / "cache" / "objects")
    source: Path = component_tree(root=tmp_path / "v1")
    recipe: Recipe = scan_recipe(objects=objects, root=source, name="core")
    store: PartitionStore = PartitionStore(cache=tmp_path / "cache")
    dist: Path = tmp_path / "dist"
    baseline: Catalog = store.build(
        recipes={"core": recipe},
        output=dist,
        platform="test",
    )
    changed: Path = component_tree(root=tmp_path / "v2")
    _ = (changed / "data.txt").write_bytes(data=b"new payload" * 100)
    recipe2: Recipe = scan_recipe(objects=objects, root=changed, name="core")
    dist2: Path = tmp_path / "dist2"
    current: Catalog = store.build(
        recipes={"core": recipe2},
        output=dist2,
        platform="test",
    )
    dist_baseline: Path = tmp_path / "dist-baseline"
    shutil_copytree(src=dist, dst=dist_baseline)
    update: Path = tmp_path / "update"
    manifest: dict[str, Json] = write_delta(
        baseline=baseline,
        current=current,
        source=dist2,
        output=update,
    )
    expect(
        actual=manifest["baseline_root"],
        expected=baseline["root_sha256"],
    )
    applied: Catalog = apply_delta(root=dist, update=update)
    expect(
        actual=applied["root_sha256"],
        expected=current["root_sha256"],
    )
    reverted: Catalog = rollback(
        root=dist,
        digest=baseline["root_sha256"],
    )
    expect(
        actual=reverted["root_sha256"],
        expected=baseline["root_sha256"],
    )
    with pytest.raises(expected_exception=FormatError, match="SHA-256"):
        _ = rollback(root=dist, digest="not-a-digest")
    artifact: Path = tmp_path / "update.zip"
    zip_update(update=update, artifact=artifact)
    zipped: Catalog = apply_update(root=dist, artifact=artifact)
    expect(
        actual=zipped["root_sha256"],
        expected=current["root_sha256"],
    )
    verify_update_rejections(
        tmp_path=tmp_path,
        dist=dist_baseline,
        update=update,
    )


def verify_update_rejections(
    *,
    tmp_path: Path,
    dist: Path,
    update: Path,
) -> None:
    """Reject malformed directory and streamed ZIP updates.

    Args:
        tmp_path: Isolated filesystem root.
        dist: Baseline distribution root.
        update: Valid update artifact directory.

    """
    stray: Path = tmp_path / "upd-stray"
    shutil_copytree(src=update, dst=stray)
    _ = (stray / "stray.txt").write_text(data="x", encoding="utf-8")
    with pytest.raises(
        expected_exception=FormatError,
        match="entry list mismatch",
    ):
        _ = apply_delta(root=dist, update=stray)
    linked: Path = tmp_path / "upd-linked"
    shutil_copytree(src=update, dst=linked, symlinks=True)
    (linked / "catalog.json").unlink()
    (linked / "catalog.json").symlink_to(
        target=tmp_path / "dist" / "catalog.json",
    )
    with pytest.raises(expected_exception=FormatError, match="symlink"):
        _ = apply_delta(root=dist, update=linked)
    tampered: Path = tmp_path / "upd-tampered"
    shutil_copytree(src=update, dst=tampered)
    manifest: dict[str, Json] = document(path=tampered / "update.json")
    manifest["baseline_root"] = "0" * 64
    _ = (tampered / "update.json").write_bytes(
        data=canonical(value=manifest),
    )
    with pytest.raises(
        expected_exception=FormatError,
        match="manifest mismatch",
    ):
        _ = apply_delta(root=dist, update=tampered)
    tampered2: Path = tmp_path / "upd-tampered2"
    shutil_copytree(src=update, dst=tampered2)
    manifest2: dict[str, Json] = document(path=tampered2 / "update.json")
    manifest2["pack_hashes"] = {}
    _ = (tampered2 / "update.json").write_bytes(
        data=canonical(value=manifest2),
    )
    fix_update_bytes(update=tampered2)
    with pytest.raises(
        expected_exception=FormatError,
        match="pack hashes mismatch",
    ):
        _ = apply_delta(root=dist, update=tampered2)
    corrupted: Path = tmp_path / "upd-corrupt"
    shutil_copytree(src=update, dst=corrupted)
    pack: Path = next(corrupted.rglob(pattern="*.zst"))
    _ = pack.write_bytes(data=b"garbage")
    fix_update_bytes(update=corrupted)
    with pytest.raises(
        expected_exception=FormatError,
        match="pack corruption",
    ):
        _ = apply_delta(root=dist, update=corrupted)


def test_partition_zip_update_rejections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reject traversal, duplicates, links, and oversized ZIP members.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Isolated filesystem root.

    """
    objects: ObjectStore = ObjectStore(root=tmp_path / "cache" / "objects")
    recipe: Recipe = scan_recipe(
        objects=objects,
        root=component_tree(root=tmp_path),
        name="core",
    )
    dist: Path = tmp_path / "dist"
    _ = publish(
        cache=tmp_path / "cache",
        recipes={"core": recipe},
        output=dist,
    )

    def archive(members: list[tuple[str, bytes, int]]) -> Path:
        """Write a zip archive with explicit member attributes.

        Args:
            members: ``(name, data, external_attr)`` tuples to write.

        Returns:
            The created archive path.

        """
        index: int = len(list(tmp_path.glob(pattern="bad-*.zip")))
        path: Path = tmp_path / f"bad-{index}.zip"
        with ZipFile(file=path, mode="w") as zip_file:
            for name, data, external in members:
                info: ZipInfo = ZipInfo(filename=name)
                info.external_attr = external
                with zip_file.open(name=info, mode="w") as member:
                    _ = member.write(data)
        return path

    traversal: Path = archive(members=[("../evil", b"x", 0)])
    with pytest.raises(expected_exception=FormatError, match="Unsafe path"):
        _ = apply_update(root=dist, artifact=traversal)
    duplicate: Path = archive(
        members=[
            ("catalog.json", b"{}", 0),
            ("catalog.json", b"{}", 0),
        ],
    )
    with pytest.raises(
        expected_exception=FormatError,
        match="Duplicate or linked",
    ):
        _ = apply_update(root=dist, artifact=duplicate)
    linked: Path = archive(
        members=[("catalog.json", b"{}", 0o120777 << 16)],
    )
    with pytest.raises(
        expected_exception=FormatError,
        match="Duplicate or linked",
    ):
        _ = apply_update(root=dist, artifact=linked)
    unexpected: Path = archive(members=[("evil.txt", b"x", 0)])
    with pytest.raises(
        expected_exception=FormatError,
        match="Unexpected update member",
    ):
        _ = apply_update(root=dist, artifact=unexpected)
    monkeypatch.setattr(
        target=Constants,
        name="max_update_member",
        value=1,
    )
    oversized: Path = archive(members=[("catalog.json", b"{}", 0)])
    with pytest.raises(
        expected_exception=FormatError,
        match="Oversized update member",
    ):
        _ = apply_update(root=dist, artifact=oversized)


def test_partition_garbage_collect(tmp_path: Path) -> None:
    """Remove unreachable packs while preserving dry-run behavior.

    Args:
        tmp_path: Isolated filesystem root.

    """
    kept: str = "a" * 64
    stale: str = "b" * 64
    root: Path = tmp_path / "store"
    for digest in (kept, stale):
        pack: Path = root / "packs" / digest[:2] / f"{digest}.zst"
        pack.parent.mkdir(parents=True, exist_ok=True)
        _ = pack.write_bytes(data=b"x")
    retained: Catalog = cast(
        typ="Catalog",
        val={"packs": {kept: {}}},
    )
    dry: list[str] = garbage_collect(
        root=root,
        retained=[retained],
        dry_run=True,
    )
    expect(actual=dry, expected=[stale])
    check(
        condition=(root / "packs" / stale[:2] / f"{stale}.zst").is_file(),
        reason="Stale pack missing before collection.",
    )
    removed: list[str] = garbage_collect(
        root=root,
        retained=[retained],
        dry_run=False,
    )
    expect(actual=removed, expected=[stale])
    check(
        condition=not (root / "packs" / stale[:2] / f"{stale}.zst").exists(),
        reason="Stale pack survived collection.",
    )


def test_catalog_has_no_exactus_strings(tmp_path: Path) -> None:
    """Keep the ported schema free of the source project's name.

    Args:
        tmp_path: Isolated filesystem root.

    """
    objects: ObjectStore = ObjectStore(root=tmp_path / "cache" / "objects")
    recipe: Recipe = scan_recipe(
        objects=objects,
        root=component_tree(root=tmp_path),
        name="core",
    )
    catalog: Catalog = publish(
        cache=tmp_path / "cache",
        recipes={"core": recipe},
        output=tmp_path / "dist",
    )
    encoded: bytes = canonical(value=cast(typ="Json", val=catalog))
    check(
        condition=b"exactus" not in encoded,
        reason="Catalog still references the source project.",
    )
