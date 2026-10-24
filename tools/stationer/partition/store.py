# Copyright (c) 2026 Project contributors.
"""Deterministic, streaming content-addressed filesystem bundles.

Acquisition is deliberately outside this module. Catalogs describe logical
component trees; packs contain objects with exact component membership.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
from json import dumps as json_dumps
from json import loads as json_loads
from os import fsync as os_fsync
from os import link as os_link
from os import stat_result as os_stat_result
from pathlib import Path, PurePosixPath
from re import fullmatch as re_fullmatch
from re import match as re_match
from shutil import copyfile as shutil_copyfile
from shutil import copyfileobj as shutil_copyfileobj
from stat import S_IMODE, S_ISDIR, S_ISLNK, S_ISREG
from struct import pack as struct_pack
from struct import unpack as struct_unpack
from tempfile import TemporaryDirectory
from typing import (
    IO,
    TYPE_CHECKING,
    BinaryIO,
    Final,
    Literal,
    NotRequired,
    TypedDict,
    cast,
    final,
)
from uuid import uuid4
from zipfile import ZipFile

from .zstd_portable import Zstd, ZstdOptions

if TYPE_CHECKING:
    from _hashlib import HASH
if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
type Json = None | bool | int | float | str | list[Json] | dict[str, Json]


@final
class Constants:
    """Group module-level constants.

    Attributes:
        magic: Grouped module constant.
        block: Grouped module constant.
        max_update_member: Grouped module constant.
        max_header: Grouped module constant.
        max_mode: Grouped module constant.
        format: Grouped module constant.
        gear: Grouped module constant.

    """

    magic: Final[bytes] = b"PTFS1\n"
    block: Final[int] = 1024 * 1024
    max_update_member: Final[int] = 256 * 1024 * 1024
    max_header: Final[int] = 4096
    max_mode: Final[int] = 511
    format: Final[str] = "partitioned-filesystem-v1"
    gear: Final[tuple[int, ...]] = tuple(
        int.from_bytes(
            bytes=sha256(data=bytes([byte])).digest()[:8],
            byteorder="big",
        )
        for byte in range(256)
    )


class FormatError(ValueError):
    """Invalid or corrupted partitioned filesystem data."""


class Entry(TypedDict):
    """Portable recipe entry; unused fields are absent.

    Attributes:
        path: Path recorded by this structure.
        type: Type recorded by this structure.
        mode: Mode recorded by this structure.
        size: Size recorded by this structure.
        sha256: Sha256 recorded by this structure.
        chunks: Chunks recorded by this structure.
        target: Target recorded by this structure.

    """

    path: str
    type: Literal["directory", "file", "symlink", "hardlink"]
    mode: int
    size: NotRequired[int]
    sha256: NotRequired[str]
    chunks: NotRequired[list[str]]
    target: NotRequired[str]


class Recipe(TypedDict):
    """One immutable logical component and its acquisition metadata.

    Attributes:
        name: Name recorded by this structure.
        metadata: Metadata recorded by this structure.
        entries: Entries recorded by this structure.
        tree_sha256: Tree sha256 recorded by this structure.
        recipe_sha256: Recipe sha256 recorded by this structure.
        logical_bytes: Logical bytes recorded by this structure.

    """

    name: str
    metadata: dict[str, Json]
    entries: list[Entry]
    tree_sha256: str
    recipe_sha256: str
    logical_bytes: int


class ObjectRecord(TypedDict):
    """Unique chunk location and exact component membership.

    Attributes:
        size: Size recorded by this structure.
        members: Members recorded by this structure.
        pack: Pack recorded by this structure.

    """

    size: int
    members: list[str]
    pack: str


class PackRecord(TypedDict):
    """Deterministic shard and compressed transport identity.

    Attributes:
        path: Path recorded by this structure.
        sha256: Sha256 recorded by this structure.
        bytes: Bytes recorded by this structure.
        members: Members recorded by this structure.
        membership_sha256: Membership sha256 recorded by this structure.
        shard: Shard recorded by this structure.
        objects: Objects recorded by this structure.

    """

    path: str
    sha256: str
    bytes: int
    members: list[str]
    membership_sha256: str
    shard: str
    objects: list[str]


class Catalog(TypedDict):
    """Versioned canonical distribution identity.

    Attributes:
        schema_version: Schema version recorded by this structure.
        format: Format recorded by this structure.
        platform: Platform recorded by this structure.
        chunking: Chunking recorded by this structure.
        pack_target_bytes: Pack target bytes recorded by this structure.
        components: Components recorded by this structure.
        objects: Objects recorded by this structure.
        packs: Packs recorded by this structure.
        root_sha256: Root sha256 recorded by this structure.

    """

    schema_version: int
    format: str
    platform: str
    chunking: dict[str, Json]
    pack_target_bytes: int
    components: dict[str, Recipe]
    objects: dict[str, ObjectRecord]
    packs: dict[str, PackRecord]
    root_sha256: str


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """Versioned Gear CDC parameters and whole-small-file policy.

    Attributes:
        minimum_bytes: Minimum bytes recorded by this structure.
        target_bytes: Target bytes recorded by this structure.
        maximum_bytes: Maximum bytes recorded by this structure.
        small_file_bytes: Small file bytes recorded by this structure.

    """

    minimum_bytes: int = 65536
    target_bytes: int = 262144
    maximum_bytes: int = 1048576
    small_file_bytes: int = 65536

    def document(self) -> dict[str, Json]:
        """Return the canonical, fingerprintable policy.

        Returns:
            Return the canonical, fingerprintable policy.

        """
        return {
            "algorithm": "gear-v1",
            "minimum_bytes": self.minimum_bytes,
            "target_bytes": self.target_bytes,
            "maximum_bytes": self.maximum_bytes,
            "small_file_bytes": self.small_file_bytes,
        }

    def chunks(self, source: BinaryIO, size: int) -> Iterator[bytes]:
        """Yield bounded chunks, resynchronizing after inserted content.

        Args:
            source: Verified source data.
            size: Expected byte length.


        Yields:
            Successive bounded entries from the operation.

        """
        if size <= self.small_file_bytes:
            yield source.read(self.small_file_bytes + 1)
            return
        chunk: bytearray = bytearray()
        rolling: int = 0
        mask: int = self.target_bytes - 1
        for block in iter(lambda: source.read(Constants.block), b""):
            for byte in block:
                chunk.append(byte)
                rolling = (rolling << 1) + Constants.gear[byte] & (1 << 64) - 1
                length: int = len(chunk)
                if length >= self.minimum_bytes and (
                    rolling & mask == 0 or length >= self.maximum_bytes
                ):
                    yield bytes(chunk)
                    chunk.clear()
                    rolling = 0
        if chunk:
            yield bytes(chunk)

    def __post_init__(self) -> None:
        """Reject impossible boundaries and non-power-of-two targets.

        Raises:
            FormatError: When the operation detects invalid input or state.

        """
        if (
            not 0
            < self.minimum_bytes
            <= self.target_bytes
            <= self.maximum_bytes
            <= 16 * Constants.block
            or self.target_bytes & self.target_bytes - 1
            or (not 0 <= self.small_file_bytes <= self.maximum_bytes)
        ):
            msg: str = "Invalid chunking configuration"
            raise FormatError(msg)


def canonical(value: Json) -> bytes:
    """Encode portable JSON without timestamps or incidental whitespace.

    Args:
        value: Value for this operation.


    Returns:
        Encode portable JSON without timestamps or incidental whitespace.

    """
    return json_dumps(
        obj=value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode(encoding="utf-8")


def identity(value: Json) -> str:
    """Hash a canonical logical identity.

    Args:
        value: Value for this operation.


    Returns:
        Hash a canonical logical identity.

    """
    return sha256(data=canonical(value=value)).hexdigest()


def hash_file(path: Path) -> str:
    """Hash a large file with bounded memory.

    Args:
        path: Filesystem path to inspect or publish.


    Returns:
        Hash a large file with bounded memory.

    """
    digest: HASH = sha256()
    with path.open(mode="rb") as source:
        for block in iter(lambda: source.read(Constants.block), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_bytes(path: Path, data: bytes) -> None:
    """Publish bytes using a unique sibling and atomic replacement.

    Args:
        path: Filesystem path to inspect or publish.
        data: Input bytes or canonical document.

    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path = path.with_name(name=f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open(mode="xb") as sink:
            sink.write(data)
            sink.flush()
            os_fsync(fd=sink.fileno())
        temporary.replace(target=path)
    finally:
        temporary.unlink(missing_ok=True)


def safe_path(value: str) -> PurePosixPath:
    """Reject traversal, aliases, drive paths, backslashes, and NULs.

    Args:
        value: Value for this operation.


    Returns:
        Reject traversal, aliases, drive paths, backslashes, and NULs.

    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    path: PurePosixPath = PurePosixPath(value)
    if (
        not value
        or "\x00" in value
        or "\\" in value
        or path.is_absolute()
        or re_match(pattern="^[A-Za-z]:", string=value)
        or any(part in {"", ".", ".."} for part in value.split(sep="/"))
        or (path.as_posix() != value)
    ):
        msg: str = f"Unsafe path: {value!r}"
        raise FormatError(msg)
    return path


def component_name(value: str) -> str:
    """Validate a single portable component identifier.

    Args:
        value: Value for this operation.


    Returns:
        Validate a single portable component identifier.

    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    if len(safe_path(value=value).parts) != 1:
        msg: str = f"Unsafe component name: {value!r}"
        raise FormatError(msg)
    return value


def destination(root: Path, relative: str) -> Path:
    """Resolve a destination without traversing symlink parents.

    Args:
        root: Filesystem root for this operation.
        relative: Relative for this operation.


    Returns:
        Resolve a destination without traversing symlink parents.

    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    path: Path = root.joinpath(*safe_path(value=relative).parts)
    if root.is_symlink():
        msg: str = "Destination root is a symlink"
        raise FormatError(msg)
    for parent in (path, *path.parents):
        if parent == root.parent:
            break
        if parent.is_symlink():
            msg = f"Symlink destination parent: {parent}"
            raise FormatError(msg)
    return path


def tree_identity(entries: list[Entry]) -> str:
    """Hash portable tree structure independently of chunk boundaries.

    Args:
        entries: Entries for this operation.


    Returns:
        Hash portable tree structure independently of chunk boundaries.

    """
    structure: list[Json] = []
    for entry in entries:
        record: dict[str, Json] = cast(typ="dict[str, Json]", val=dict(entry))
        record.pop("chunks", None)
        structure.append(record)
    return identity(value=structure)


def recipe_identity(recipe: Recipe) -> str:
    """Hash all recipe data except its self hash.

    Args:
        recipe: Logical filesystem recipe.


    Returns:
        Hash all recipe data except its self hash.

    """
    body: dict[str, Json] = cast(typ="dict[str, Json]", val=dict(recipe))
    body.pop("recipe_sha256", None)
    return identity(value=body)


class ObjectStore:
    """Disk-backed verified chunks, atomically created under SHA-256."""

    def __init__(self, root: Path) -> None:
        """Initialize a disposable object cache.

        Args:
            root: Filesystem root for this operation.

        """
        self.root = root
        self.created = 0
        self.reused = 0

    def path(self, digest: str) -> Path:
        """Return a safe address after validating its hash syntax.

        Args:
            digest: Expected SHA-256 content identity.


        Returns:
            Return a safe address after validating its hash syntax.

        """
        _digest(value=digest)
        return destination(root=self.root, relative=f"{digest[:2]}/{digest}")

    def valid(self, digest: str, size: int | None = None) -> bool:
        """Verify cached content before reusing it.

        Args:
            digest: Expected SHA-256 content identity.
            size: Expected byte length.


        Returns:
            Verify cached content before reusing it.

        """
        path: Path = self.path(digest=digest)
        return (
            path.is_file()
            and (size is None or path.stat().st_size == size)
            and (hash_file(path=path) == digest)
        )

    def put(self, data: bytes) -> str:
        """Store one bounded chunk; repair corrupt disposable caches.

        Args:
            data: Input bytes or canonical document.


        Returns:
            Store one bounded chunk; repair corrupt disposable caches.

        """
        digest: str = sha256(data=data).hexdigest()
        if self.valid(digest=digest, size=len(data)):
            self.reused += 1
        else:
            atomic_bytes(path=self.path(digest=digest), data=data)
            self.created += 1
        return digest


class RecipeScanner:
    """Scan deterministic trees while writing streaming CDC objects."""

    def __init__(
        self,
        objects: ObjectStore,
        chunking: ChunkingConfig | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """Configure object storage, chunk boundaries, and live counters.

        Args:
            objects: Objects for this operation.
            chunking: Chunking for this operation.
            progress: Progress for this operation.

        """
        self.objects = objects
        self.chunking = chunking or ChunkingConfig()
        self.progress = progress

    def scan(self, root: Path, name: str, metadata: dict[str, Json]) -> Recipe:
        """Preserve dirs, modes, links, and full hashes without large reads.

        Args:
            root: Filesystem root for this operation.
            name: Component or resource name.
            metadata: Component tools, dependencies, and environment metadata.


        Returns:
            Preserve dirs, modes, links, and full hashes without large reads.

        Raises:
            FormatError: When the operation detects invalid input or state.

        """
        component_name(value=name)
        entries: list[Entry] = []
        inodes: dict[tuple[int, int], Entry] = {}
        logical: int = 0
        count: int = 0
        for path in sorted(
            root.rglob(pattern="*"),
            key=lambda member: member.relative_to(root).as_posix(),
        ):
            relative: str = path.relative_to(root).as_posix()
            safe_path(value=relative)
            info: os_stat_result = path.lstat()
            mode: int = S_IMODE(info.st_mode)
            entry: Entry
            if S_ISLNK(info.st_mode):
                entry = {
                    "path": relative,
                    "type": "symlink",
                    "mode": mode,
                    "target": path.readlink().as_posix(),
                }
            elif S_ISDIR(info.st_mode):
                entry = {"path": relative, "type": "directory", "mode": mode}
            elif S_ISREG(info.st_mode):
                inode: tuple[int, int] = (info.st_dev, info.st_ino)
                first: Entry | None = (
                    inodes.get(inode) if info.st_nlink > 1 else None
                )
                logical += info.st_size
                if first is not None:
                    entry = {
                        "path": relative,
                        "type": "hardlink",
                        "mode": mode,
                        "target": first["path"],
                    }
                else:
                    entry = self._regular_file(
                        path=path,
                        relative=relative,
                        info=info,
                    )
                    inodes[inode] = entry
                count += 1
                if self.progress is not None:
                    self.progress(count, logical)
            else:
                msg: str = f"Unsupported filesystem entry: {path}"
                raise FormatError(msg)
            entries.append(entry)
        recipe: Recipe = {
            "name": name,
            "metadata": metadata,
            "entries": entries,
            "tree_sha256": tree_identity(entries=entries),
            "recipe_sha256": "",
            "logical_bytes": logical,
        }
        recipe["recipe_sha256"] = recipe_identity(recipe=recipe)
        return recipe

    def _regular_file(
        self,
        path: Path,
        relative: str,
        info: os_stat_result,
    ) -> Entry:
        """Stream hashes/chunks and detect concurrent source mutation.

        Args:
            path: Filesystem path to inspect or publish.
            relative: Relative for this operation.
            info: Info for this operation.


        Returns:
            Stream hashes/chunks and detect concurrent source mutation.

        Raises:
            FormatError: When the operation detects invalid input or state.

        """
        mode: int = S_IMODE(info.st_mode)
        digest: HASH = sha256()
        chunks: list[str] = []
        size: int = 0
        with path.open(mode="rb") as source:
            for chunk in self.chunking.chunks(
                source=source,
                size=info.st_size,
            ):
                if not chunk:
                    continue
                digest.update(chunk)
                size += len(chunk)
                chunks.append(self.objects.put(data=chunk))
        after: os_stat_result = path.lstat()
        if size != info.st_size or (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
            after.st_mode,
        ) != (info.st_size, info.st_mtime_ns, info.st_ino, info.st_mode):
            msg: str = f"File changed during scan: {path}"
            raise FormatError(msg)
        return {
            "path": relative,
            "type": "file",
            "mode": mode,
            "size": size,
            "sha256": digest.hexdigest(),
            "chunks": chunks,
        }


def memberships(recipes: dict[str, Recipe]) -> dict[str, list[str]]:
    """Compute exact content references, independently of dependencies.

    Args:
        recipes: Recipes for this operation.


    Returns:
        Compute exact content references, independently of dependencies.

    """
    refs: dict[str, set[str]] = defaultdict(set)
    for name, recipe in sorted(recipes.items()):
        for entry in recipe["entries"]:
            for digest in entry.get("chunks", []):
                members: set[str] = refs[digest]
                members.add(name)
    return {digest: sorted(names) for digest, names in sorted(refs.items())}


def shards(
    objects: list[str],
    sizes: dict[str, int],
    target: int,
    prefix: str = "",
) -> Iterator[tuple[str, list[str]]]:
    """Split oversized groups recursively by stable hexadecimal prefixes.

    Args:
        objects: Objects for this operation.
        sizes: Sizes for this operation.
        target: Target for this operation.
        prefix: Prefix for this operation.


    Yields:
        Successive bounded entries from the operation.

    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    if target <= 0:
        msg: str = "Pack target must be positive"
        raise FormatError(msg)
    if sum(sizes[digest] for digest in objects) <= target or len(objects) <= 1:
        if objects:
            yield (prefix, sorted(objects))
        return
    groups: dict[str, list[str]] = defaultdict(list)
    for digest in objects:
        groups[digest[: len(prefix) + 1]].append(digest)
    for group, digests in sorted(groups.items()):
        yield from shards(
            objects=digests,
            sizes=sizes,
            target=target,
            prefix=group,
        )


def pack_identity(
    members: list[str],
    prefix: str,
    objects: list[str],
    sizes: dict[str, int],
) -> str:
    """Compute logical pack identity before compression.

    Args:
        members: Members for this operation.
        prefix: Prefix for this operation.
        objects: Objects for this operation.
        sizes: Sizes for this operation.


    Returns:
        Compute logical pack identity before compression.

    """
    return identity(
        value=cast(
            typ="Json",
            val={
                "schema": 1,
                "members": members,
                "shard": prefix,
                "objects": [[digest, sizes[digest]] for digest in objects],
            },
        ),
    )


def _read_exact(source: Zstd, size: int) -> bytes:
    """Read one bounded field, rejecting truncation and absurd lengths.

    Args:
        source: Verified source data.
        size: Expected byte length.


    Returns:
        Read one bounded field, rejecting truncation and absurd lengths.

    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    if not 0 <= size <= 16 * Constants.block:
        msg: str = "Impossible pack field size"
        raise FormatError(msg)
    data: bytearray = bytearray()
    while len(data) < size:
        block: bytes = source.read(size=size - len(data))
        if not block:
            msg = "Truncated pack"
            raise FormatError(msg)
        data.extend(block)
    return bytes(data)


class PackCodec:
    """Deterministic framed Zstandard packs; no host tar metadata."""

    @staticmethod
    def encode(path: Path, objects: list[str], store: ObjectStore) -> None:
        """Stream sorted verified objects with bounded framing.

        Args:
            path: Filesystem path to inspect or publish.
            objects: Objects for this operation.
            store: Content-addressed object store.


        Raises:
            FormatError: When the operation detects invalid input or state.

        """
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path = path.with_name(
            name=f".{path.name}.{uuid4().hex}.tmp",
        )
        try:
            with Zstd(
                file=temporary,
                options=ZstdOptions(level=19, threads=1),
            ) as sink:
                sink.write(Constants.magic)
                for digest in objects:
                    source_path: Path = store.path(digest=digest)
                    if not store.valid(digest=digest):
                        msg: str = f"Invalid source object: {digest}"
                        raise FormatError(msg)
                    header: bytes = canonical(
                        value={
                            "sha256": digest,
                            "size": source_path.stat().st_size,
                        },
                    )
                    sink.write(struct_pack(">I", len(header)))
                    sink.write(header)
                    with source_path.open(mode="rb") as source:
                        for block in iter(
                            lambda: source.read(Constants.block),
                            b"",
                        ):
                            sink.write(block)
            temporary.replace(target=path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def decode(
        path: Path,
        expected: dict[str, int],
        store: ObjectStore | None = None,
    ) -> None:
        """Verify every record, with optional decoded-object cache recovery.

        Args:
            path: Filesystem path to inspect or publish.
            expected: Expected object hashes and sizes.
            store: Content-addressed object store.


        Raises:
            FormatError: When the operation detects invalid input or state.

        """
        seen: list[str] = []
        source: Zstd
        with Zstd(file=path, mode="r") as source:
            if (
                _read_exact(source=source, size=len(Constants.magic))
                != Constants.magic
            ):
                msg: str = "Invalid pack magic"
                raise FormatError(msg)
            while first := source.read(size=1):
                length: int = struct_unpack(
                    ">I",
                    first + _read_exact(source=source, size=3),
                )[0]
                if not 0 < length <= Constants.max_header:
                    msg = "Impossible pack header length"
                    raise FormatError(msg)
                header: dict[str, Json] = _mapping(
                    value=_json(data=_read_exact(source=source, size=length)),
                )
                digest: str = _digest(
                    value=_string(value=header.get("sha256")),
                )
                size: int = _integer(value=header.get("size"))
                if digest in seen or expected.get(digest) != size:
                    msg = "Duplicate or unexpected pack object"
                    raise FormatError(msg)
                data: bytes = _read_exact(source=source, size=size)
                if sha256(data=data).hexdigest() != digest:
                    msg = "Pack object hash mismatch"
                    raise FormatError(msg)
                if store is not None:
                    store.put(data=data)
                seen.append(digest)
        if seen != sorted(expected):
            msg = "Missing or unsorted pack objects"
            raise FormatError(msg)


def _json(data: bytes) -> Json:
    """Read JSON as untrusted typed data.

    Args:
        data: Input bytes or canonical document.


    Returns:
        Read JSON as untrusted typed data.

    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    try:
        return cast(typ="Json", val=json_loads(s=data))
    except ValueError as exc:
        msg: str = "Invalid JSON"
        raise FormatError(msg) from exc


def _mapping(value: Json) -> dict[str, Json]:
    """Require a JSON object.

    Args:
        value: Value for this operation.


    Returns:
        Require a JSON object.

    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    if not isinstance(value, dict):
        msg: str = "Expected JSON object"
        raise FormatError(msg)
    return value


def _string(value: Json) -> str:
    """Require a JSON string.

    Args:
        value: Value for this operation.


    Returns:
        Require a JSON string.

    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    if not isinstance(value, str):
        msg: str = "Expected string"
        raise FormatError(msg)
    return value


def _integer(value: Json) -> int:
    """Require a nonnegative integer, excluding bool.

    Args:
        value: Value for this operation.


    Returns:
        Require a nonnegative integer, excluding bool.

    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    if type(value) is not int or value < 0:
        msg: str = "Expected nonnegative integer"
        raise FormatError(msg)
    return value


def _strings(value: Json) -> list[str]:
    """Require an ordered string array.

    Args:
        value: Value for this operation.


    Returns:
        Require an ordered string array.

    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    if not isinstance(value, list):
        msg: str = "Expected string array"
        raise FormatError(msg)
    return [_string(value=item) for item in value]


def _digest(value: str) -> str:
    """Require a lowercase SHA-256 digest.

    Args:
        value: Value for this operation.


    Returns:
        Require a lowercase SHA-256 digest.

    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    if re_fullmatch(pattern="[0-9a-f]{64}", string=value) is None:
        msg: str = "Invalid SHA-256 address"
        raise FormatError(msg)
    return value


def _validate_entry(entry: Entry, entries: dict[str, Entry]) -> int:
    """Validate one typed recipe entry and return its logical bytes.

    Args:
        entry: Entry for this operation.
        entries: Entries for this operation.


    Returns:
        Validate one typed recipe entry and return its logical bytes.

    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    kind: str = entry["type"]
    if kind == "file":
        _digest(value=entry.get("sha256", ""))
        for digest in entry.get("chunks", []):
            _digest(value=digest)
        return entry.get("size", 0)
    if kind == "hardlink":
        linked: Entry | None = entries.get(
            safe_path(value=entry.get("target", "")).as_posix(),
        )
        if linked is None or linked["type"] != "file":
            msg: str = "Hardlink must reference a regular file"
            raise FormatError(msg)
        if linked["mode"] != entry["mode"]:
            msg = "Incompatible hardlink modes"
            raise FormatError(msg)
        return linked.get("size", 0)
    if kind == "symlink":
        target: str = entry.get("target", "")
        if not target or "\x00" in target or "\\" in target:
            msg = "Invalid symlink target"
            raise FormatError(msg)
    elif kind != "directory":
        msg = "Unknown recipe entry type"
        raise FormatError(msg)
    return 0


def validate_recipe(recipe: Recipe) -> None:
    """Validate identity, entry paths, types, and safe link topology.

    Args:
        recipe: Logical filesystem recipe.


    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    component_name(value=recipe["name"])
    if recipe_identity(recipe=recipe) != recipe["recipe_sha256"]:
        msg: str = "Recipe hash mismatch"
        raise FormatError(msg)
    if tree_identity(entries=recipe["entries"]) != recipe["tree_sha256"]:
        msg = "Tree hash mismatch"
        raise FormatError(msg)
    entries: dict[str, Entry] = {
        entry["path"]: entry for entry in recipe["entries"]
    }
    if len(entries) != len(recipe["entries"]):
        msg = "Duplicate recipe path"
        raise FormatError(msg)
    if list(entries) != sorted(entries):
        msg = "Unsorted recipe paths"
        raise FormatError(msg)
    logical: int = 0
    for entry in recipe["entries"]:
        path: PurePosixPath = safe_path(value=entry["path"])
        for parent in path.parents:
            if str(object=parent) != "." and (
                parent.as_posix() not in entries
                or entries[parent.as_posix()]["type"] != "directory"
            ):
                msg = "Non-directory recipe parent"
                raise FormatError(msg)
        if not 0 <= entry["mode"] <= Constants.max_mode:
            msg = "Unsafe file mode"
            raise FormatError(msg)
        logical += _validate_entry(entry=entry, entries=entries)
    if logical != recipe["logical_bytes"]:
        msg = "Logical byte count mismatch"
        raise FormatError(msg)


def load_recipe(value: Json) -> Recipe:
    """Parse and validate a cached or distributed recipe.

    Args:
        value: Value for this operation.


    Returns:
        Parse and validate a cached or distributed recipe.

    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    doc: dict[str, Json] = _mapping(value=value)
    raw_entries: Json = doc.get("entries")
    if not isinstance(raw_entries, list):
        msg: str = "Missing recipe entries"
        raise FormatError(msg)
    entries: list[Entry] = []
    for raw in raw_entries:
        row: dict[str, Json] = _mapping(value=raw)
        kind: str = _string(value=row.get("type"))
        entry: Entry = {
            "path": _string(value=row.get("path")),
            "type": cast(
                typ="Literal['directory', 'file', 'symlink', 'hardlink']",
                val=kind,
            ),
            "mode": _integer(value=row.get("mode")),
        }
        if kind == "file":
            entry.update(
                size=_integer(value=row.get("size")),
                sha256=_string(value=row.get("sha256")),
                chunks=_strings(value=row.get("chunks")),
            )
        elif kind in {"symlink", "hardlink"}:
            entry["target"] = _string(value=row.get("target"))
        entries.append(entry)
    recipe: Recipe = {
        "name": _string(value=doc.get("name")),
        "metadata": _mapping(value=doc.get("metadata")),
        "entries": entries,
        "logical_bytes": _integer(value=doc.get("logical_bytes")),
        "tree_sha256": _string(value=doc.get("tree_sha256")),
        "recipe_sha256": _string(value=doc.get("recipe_sha256")),
    }
    validate_recipe(recipe=recipe)
    return recipe


def catalog_identity(catalog: Catalog) -> str:
    """Hash the full catalog body excluding its self hash.

    Args:
        catalog: Verified filesystem catalog.


    Returns:
        Hash the full catalog body excluding its self hash.

    """
    body: dict[str, Json] = cast(typ="dict[str, Json]", val=dict(catalog))
    body.pop("root_sha256", None)
    return identity(value=body)


def _load_objects(
    raw: dict[str, Json],
    config: ChunkingConfig,
) -> dict[str, ObjectRecord]:
    """Parse bounded object metadata.

    Args:
        raw: Raw for this operation.
        config: Deterministic chunking policy.


    Returns:
        Parse bounded object metadata.

    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    objects: dict[str, ObjectRecord] = {}
    for digest, value in raw.items():
        row: dict[str, Json] = _mapping(value=value)
        objects[_digest(value=digest)] = {
            "size": _integer(value=row.get("size")),
            "members": _strings(value=row.get("members")),
            "pack": _digest(value=_string(value=row.get("pack"))),
        }
        if objects[digest]["size"] > config.maximum_bytes:
            msg: str = "Object exceeds chunk size policy"
            raise FormatError(msg)
    return objects


def _load_packs(
    raw: dict[str, Json],
    objects: dict[str, ObjectRecord],
) -> dict[str, PackRecord]:
    """Parse and validate canonical pack references and logical identities.

    Args:
        raw: Raw for this operation.
        objects: Objects for this operation.


    Returns:
        Parse and validate canonical pack references and logical identities.

    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    packs: dict[str, PackRecord] = {}
    covered: set[str] = set()
    sizes: dict[str, int] = {
        digest: row["size"] for digest, row in objects.items()
    }
    for pack_id, value in raw.items():
        _digest(value=pack_id)
        row: dict[str, Json] = _mapping(value=value)
        pack: PackRecord = {
            "path": _string(value=row.get("path")),
            "sha256": _digest(value=_string(value=row.get("sha256"))),
            "bytes": _integer(value=row.get("bytes")),
            "members": _strings(value=row.get("members")),
            "membership_sha256": _digest(
                value=_string(value=row.get("membership_sha256")),
            ),
            "shard": _string(value=row.get("shard")),
            "objects": _strings(value=row.get("objects")),
        }
        if pack["path"] != f"packs/{pack_id[:2]}/{pack_id}.zst":
            msg: str = "Invalid pack path"
            raise FormatError(msg)
        if not pack["objects"] or pack["objects"] != sorted(
            set(pack["objects"]),
        ):
            msg = "Invalid pack object list"
            raise FormatError(msg)
        if any(
            digest not in objects or digest in covered
            for digest in pack["objects"]
        ):
            msg = "Unknown or multiply packed object"
            raise FormatError(msg)
        if (
            pack["members"] != sorted(set(pack["members"]))
            or identity(value=cast(typ="Json", val=pack["members"]))
            != pack["membership_sha256"]
            or re_fullmatch(pattern="[0-9a-f]{0,64}", string=pack["shard"])
            is None
            or any(
                not digest.startswith(pack["shard"])
                for digest in pack["objects"]
            )
            or (
                pack_identity(
                    members=pack["members"],
                    prefix=pack["shard"],
                    objects=pack["objects"],
                    sizes=sizes,
                )
                != pack_id
            )
        ):
            msg = "Pack logical identity mismatch"
            raise FormatError(msg)
        for digest in pack["objects"]:
            if (
                objects[digest]["pack"] != pack_id
                or objects[digest]["members"] != pack["members"]
            ):
                msg = "Object/pack membership mismatch"
                raise FormatError(msg)
        covered.update(pack["objects"])
        packs[pack_id] = pack
    if covered != set(objects):
        msg = "Unpacked catalog objects"
        raise FormatError(msg)
    return packs


def _validate_file_sizes(
    recipes: dict[str, Recipe],
    objects: dict[str, ObjectRecord],
) -> None:
    """Check reconstructed lengths against the object table.

    Args:
        recipes: Recipes for this operation.
        objects: Objects for this operation.


    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    for recipe in recipes.values():
        for entry in recipe["entries"]:
            if entry["type"] == "file" and sum(
                objects[digest]["size"] for digest in entry.get("chunks", [])
            ) != entry.get("size", 0):
                msg: str = "File chunk sizes mismatch"
                raise FormatError(msg)


def _dependency_closure(
    recipes: dict[str, Recipe],
    name: str,
    active: frozenset[str] = frozenset(),
) -> set[str]:
    """Validate required components and reject cyclic metadata.

    Args:
        recipes: Recipes for this operation.
        name: Component or resource name.
        active: Active for this operation.


    Returns:
        Validate required components and reject cyclic metadata.

    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    if name not in recipes or name in active:
        msg: str = "Missing or cyclic component dependency"
        raise FormatError(msg)
    result: set[str] = {name}
    for required in _strings(
        value=recipes[name]["metadata"].get("requires", []),
    ):
        result.update(
            _dependency_closure(
                recipes=recipes,
                name=component_name(value=required),
                active=active | {name},
            ),
        )
    return result


def _link_target(
    name: str,
    relative: str,
    target: str,
    allowed: set[str] | None,
) -> None:
    """Allow sibling dependency links while rejecting cache-root escapes.

    Args:
        name: Component or resource name.
        relative: Relative for this operation.
        target: Target for this operation.
        allowed: Allowed for this operation.


    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    if PurePosixPath(target).is_absolute() or re_match(
        pattern="^[A-Za-z]:",
        string=target,
    ):
        msg: str = "Absolute link target"
        raise FormatError(msg)
    parts: list[str] = [name, *PurePosixPath(relative).parent.parts]
    for part in target.split(sep="/"):
        if part == "..":
            if not parts:
                msg = "Link escapes component cache"
                raise FormatError(msg)
            parts.pop()
        elif part not in {"", "."}:
            parts.append(part)
    if not parts or (allowed is not None and parts[0] not in allowed):
        msg = "Link target is not a component dependency"
        raise FormatError(msg)


def _validate_links(recipe: Recipe) -> None:
    """Reject escaping links even for directly supplied recipes.

    Args:
        recipe: Logical filesystem recipe.

    """
    for entry in recipe["entries"]:
        if entry["type"] == "symlink":
            _link_target(
                name=recipe["name"],
                relative=entry["path"],
                target=entry.get("target", ""),
                allowed=None,
            )


def _validate_metadata(recipes: dict[str, Recipe]) -> None:
    """Validate dependency closure, environment paths, and tool targets.

    Args:
        recipes: Recipes for this operation.


    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    seen: set[str] = set()
    for name, recipe in recipes.items():
        allowed: set[str] = _dependency_closure(recipes=recipes, name=name)
        for entry in recipe["entries"]:
            if entry["type"] == "symlink":
                _link_target(
                    name=name,
                    relative=entry["path"],
                    target=entry.get("target", ""),
                    allowed=allowed,
                )
        env: dict[str, Json] = _mapping(
            value=recipe["metadata"].get("env", {}),
        )
        for variable, values in env.items():
            if (
                re_fullmatch(pattern="[A-Za-z_][A-Za-z_0-9]*", string=variable)
                is None
            ):
                msg: str = "Invalid environment variable"
                raise FormatError(msg)
            for value in _strings(value=values):
                _link_target(
                    name=name,
                    relative="environment",
                    target=value,
                    allowed=None,
                )
        tools: dict[str, Json] = _mapping(
            value=recipe["metadata"].get("tools", {}),
        )
        for tool, value in tools.items():
            _validate_tool(tool=tool, spec=_mapping(value=value), seen=seen)


def _validate_tool(tool: str, spec: dict[str, Json], seen: set[str]) -> None:
    """Validate the original four dispatch kinds without allowing paths out.

    Args:
        tool: Tool for this operation.
        spec: Spec for this operation.
        seen: Seen for this operation.


    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    component_name(value=tool)
    if tool in seen:
        msg: str = "Duplicate tool name"
        raise FormatError(msg)
    seen.add(tool)
    kind: str = _string(value=spec.get("kind"))
    target: str = _string(value=spec.get("target"))
    if kind in {"binary", "js-entry"}:
        safe_path(value=target)
    elif kind == "python-module":
        if (
            re_fullmatch(
                pattern="[A-Za-z_]\\w*(?:\\.[A-Za-z_]\\w*)*",
                string=target,
            )
            is None
        ):
            msg = "Invalid Python module target"
            raise FormatError(msg)
    elif kind != "probe":
        msg = "Unsupported tool kind"
        raise FormatError(msg)
    if "module" in spec:
        _string(value=spec["module"])


def load_catalog(path: Path) -> Catalog:
    """Validate schema, integrity hierarchy, membership, and all paths.

    Args:
        path: Filesystem path to inspect or publish.


    Returns:
        Validate schema, integrity hierarchy, membership, and all paths.

    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    doc: dict[str, Json] = _mapping(value=_json(data=path.read_bytes()))
    if doc.get("schema_version") != 1 or doc.get("format") != Constants.format:
        msg: str = "Unsupported partition catalog schema"
        raise FormatError(msg)
    body: dict[str, Json] = dict(doc)
    root: str = _digest(value=_string(value=body.pop("root_sha256", None)))
    if identity(value=body) != root:
        msg = "Catalog root hash mismatch"
        raise FormatError(msg)
    recipes: dict[str, Recipe] = {
        component_name(value=name): load_recipe(value=value)
        for name, value in _mapping(value=doc.get("components")).items()
    }
    for name, recipe in recipes.items():
        if name != recipe["name"]:
            msg = "Component name mismatch"
            raise FormatError(msg)
    chunking: dict[str, Json] = _mapping(value=doc.get("chunking"))
    if chunking.get("algorithm") != "gear-v1":
        msg = "Unknown chunker"
        raise FormatError(msg)
    config: ChunkingConfig = ChunkingConfig(
        minimum_bytes=_integer(value=chunking.get("minimum_bytes")),
        target_bytes=_integer(value=chunking.get("target_bytes")),
        maximum_bytes=_integer(value=chunking.get("maximum_bytes")),
        small_file_bytes=_integer(value=chunking.get("small_file_bytes")),
    )
    objects: dict[str, ObjectRecord] = _load_objects(
        raw=_mapping(value=doc.get("objects")),
        config=config,
    )
    packs: dict[str, PackRecord] = _load_packs(
        raw=_mapping(value=doc.get("packs")),
        objects=objects,
    )
    if memberships(recipes=recipes) != {
        digest: row["members"] for digest, row in objects.items()
    }:
        msg = "Catalog membership mismatch"
        raise FormatError(msg)
    _validate_file_sizes(recipes=recipes, objects=objects)
    _validate_metadata(recipes=recipes)
    catalog: Catalog = {
        "schema_version": 1,
        "format": Constants.format,
        "platform": _string(value=doc.get("platform")),
        "chunking": config.document(),
        "pack_target_bytes": _integer(value=doc.get("pack_target_bytes")),
        "components": recipes,
        "objects": objects,
        "packs": packs,
        "root_sha256": root,
    }
    if catalog_identity(catalog=catalog) != root:
        msg = "Noncanonical catalog fields"
        raise FormatError(msg)
    return catalog


class PartitionStore:
    """Build, verify, and recover canonical packs from persistent caches."""

    def __init__(
        self,
        cache: Path,
        chunking: ChunkingConfig | None = None,
    ) -> None:
        """Initialize build caches; these are never distributed wholesale.

        Args:
            cache: Persistent build or runtime cache.
            chunking: Chunking for this operation.

        """
        self.cache = cache
        self.chunking = chunking or ChunkingConfig()
        self.objects = ObjectStore(root=cache / "objects")
        self.packs_built = 0
        self.packs_reused = 0

    @staticmethod
    def verify(
        catalog: Catalog,
        root: Path,
        store: ObjectStore | None = None,
    ) -> None:
        """Verify transport hashes and every framed object before trust.

        Args:
            catalog: Verified filesystem catalog.
            root: Filesystem root for this operation.
            store: Content-addressed object store.


        Raises:
            FormatError: When the operation detects invalid input or state.

        """
        for pack in catalog["packs"].values():
            path: Path = destination(root=root, relative=pack["path"])
            if (
                path.stat().st_size != pack["bytes"]
                or hash_file(path=path) != pack["sha256"]
            ):
                msg: str = "Compressed pack hash/size mismatch"
                raise FormatError(msg)
            PackCodec.decode(
                path=path,
                expected={
                    digest: catalog["objects"][digest]["size"]
                    for digest in pack["objects"]
                },
                store=store,
            )

    def build(
        self,
        recipes: dict[str, Recipe],
        output: Path,
        platform: str,
        pack_target: int = 64 * Constants.block,
        progress: Callable[[int, int, int], None] | None = None,
    ) -> Catalog:
        """Plan exact membership and stable shards; reuse verified packs.

        Args:
            recipes: Recipes for this operation.
            output: Destination for generated artifacts.
            platform: Platform for this operation.
            pack_target: Pack target for this operation.
            progress: Progress callback receiving built packs, reused
                packs, and the total pack count.


        Returns:
            Plan exact membership and stable shards; reuse verified packs.

        Raises:
            FormatError: When the operation detects invalid input or state.

        """
        if pack_target <= 0:
            msg: str = "Pack target must be positive"
            raise FormatError(msg)
        for recipe in recipes.values():
            validate_recipe(recipe=recipe)
        refs: dict[str, list[str]] = memberships(recipes=recipes)
        sizes: dict[str, int] = {
            digest: self.objects.path(digest=digest).stat().st_size
            for digest in refs
        }
        groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for digest, names in refs.items():
            groups[tuple(names)].append(digest)
        catalog: Catalog = {
            "schema_version": 1,
            "format": Constants.format,
            "platform": platform,
            "chunking": self.chunking.document(),
            "pack_target_bytes": pack_target,
            "components": recipes,
            "objects": {},
            "packs": {},
            "root_sha256": "",
        }
        planned: list[tuple[list[str], str, list[str]]] = [
            (list(names), prefix, group)
            for names, digests in sorted(groups.items())
            for prefix, group in shards(
                objects=digests,
                sizes=sizes,
                target=pack_target,
            )
        ]
        total_packs: int = len(planned)
        for members, prefix, group in planned:
            pack_id: str = pack_identity(
                members=members,
                prefix=prefix,
                objects=group,
                sizes=sizes,
            )
            relative: str = f"packs/{pack_id[:2]}/{pack_id}.zst"
            cached: Path = self.cache / relative
            expected: dict[str, int] = {
                digest: sizes[digest] for digest in group
            }
            try:
                PackCodec.decode(path=cached, expected=expected)
                reused: bool = True
            except (OSError, RuntimeError, ValueError):
                reused = False
            if reused:
                self.packs_reused += 1
            else:
                PackCodec.encode(
                    path=cached,
                    objects=group,
                    store=self.objects,
                )
                PackCodec.decode(path=cached, expected=expected)
                self.packs_built += 1
            path: Path = output / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil_copyfile(src=cached, dst=path)
            catalog["packs"][pack_id] = {
                "path": relative,
                "sha256": hash_file(path=path),
                "bytes": path.stat().st_size,
                "members": members,
                "membership_sha256": identity(
                    value=cast(typ="Json", val=members),
                ),
                "shard": prefix,
                "objects": group,
            }
            for digest in group:
                catalog["objects"][digest] = {
                    "size": sizes[digest],
                    "members": members,
                    "pack": pack_id,
                }
            if progress is not None:
                progress(self.packs_built, self.packs_reused, total_packs)
        catalog["root_sha256"] = catalog_identity(catalog=catalog)
        atomic_bytes(
            path=output / "catalog.json",
            data=canonical(value=cast(typ="Json", val=catalog)),
        )
        _ = load_catalog(path=output / "catalog.json")
        return catalog

    def recover(
        self,
        catalog: Catalog,
        root: Path,
        recipes: dict[str, Recipe],
    ) -> None:
        """Recover local objects from verified previous packs.

        Args:
            catalog: Verified filesystem catalog.
            root: Filesystem root for this operation.
            recipes: Recipes for this operation.


        Raises:
            FormatError: When the operation detects invalid input or state.

        """
        required: dict[str, list[str]] = memberships(recipes=recipes)
        packs: set[str] = set()
        for digest in required:
            record: ObjectRecord | None = catalog["objects"].get(digest)
            if record is None:
                msg: str = "Cached recipe missing from previous catalog"
                raise FormatError(msg)
            if not self.objects.valid(digest=digest, size=record["size"]):
                packs.add(record["pack"])
        for pack_id in sorted(packs):
            pack: PackRecord = catalog["packs"][pack_id]
            path: Path = destination(root=root, relative=pack["path"])
            if (
                hash_file(path=path) != pack["sha256"]
                or path.stat().st_size != pack["bytes"]
            ):
                msg = "Recovery pack mismatch"
                raise FormatError(msg)
            PackCodec.decode(
                path=path,
                expected={
                    digest: catalog["objects"][digest]["size"]
                    for digest in pack["objects"]
                },
                store=self.objects,
            )


class Materializer:
    """Safely reconstruct immutable files with exact-file hardlink reuse."""

    def __init__(self, store: ObjectStore) -> None:
        """Initialize verified objects and optional duplicate-file index.

        Args:
            store: Content-addressed object store.

        """
        self.store = store
        self.files: dict[tuple[str, int], Path] = {}

    def materialize(self, recipe: Recipe, root: Path) -> None:
        """Create dirs/files/hardlinks first, symlinks last; verify output.

        Args:
            recipe: Logical filesystem recipe.
            root: Filesystem root for this operation.

        """
        validate_recipe(recipe=recipe)
        root.mkdir(parents=True, exist_ok=False)
        _validate_links(recipe=recipe)
        entries: list[Entry] = recipe["entries"]
        directories: list[Entry] = [
            entry for entry in entries if entry["type"] == "directory"
        ]
        for entry in directories:
            destination(root=root, relative=entry["path"]).mkdir()
        for entry in entries:
            if entry["type"] != "file":
                continue
            self._file(entry=entry, root=root)
        for entry in entries:
            if entry["type"] == "hardlink":
                os_link(
                    src=destination(
                        root=root,
                        relative=entry.get("target", ""),
                    ),
                    dst=destination(root=root, relative=entry["path"]),
                )
        for entry in entries:
            if entry["type"] == "symlink":
                path: Path = destination(root=root, relative=entry["path"])
                path.symlink_to(target=entry.get("target", ""))
        for entry in reversed(directories):
            destination(root=root, relative=entry["path"]).chmod(
                mode=entry["mode"],
            )
        verify_tree(recipe=recipe, root=root)

    def _file(self, entry: Entry, root: Path) -> None:
        """Write a verified file or hardlink a compatible duplicate.

        Args:
            entry: Entry for this operation.
            root: Filesystem root for this operation.


        Raises:
            FormatError: When the operation detects invalid input or state.

        """
        path: Path = destination(root=root, relative=entry["path"])
        key: tuple[str, int] = (entry.get("sha256", ""), entry["mode"])
        existing: Path | None = self.files.get(key)
        if (
            existing is not None
            and existing.is_file()
            and (hash_file(path=existing) == key[0])
        ):
            try:
                os_link(src=existing, dst=path)
            except OSError:
                existing = None
        else:
            existing = None
        if existing is None:
            digest: HASH = sha256()
            size: int = 0
            source: IO[bytes]
            sink: IO[bytes]
            with path.open(mode="xb") as sink:
                for chunk in entry.get("chunks", []):
                    if not self.store.valid(digest=chunk):
                        msg: str = "Invalid materialization object"
                        raise FormatError(msg)
                    with self.store.path(digest=chunk).open(
                        mode="rb",
                    ) as source:
                        for block in iter(
                            lambda: source.read(Constants.block),
                            b"",
                        ):
                            sink.write(block)
                            digest.update(block)
                            size += len(block)
            if size != entry.get("size", 0) or digest.hexdigest() != key[0]:
                msg = "Reconstructed file hash/size mismatch"
                raise FormatError(msg)
            path.chmod(mode=entry["mode"])
        self.files[key] = path


def verify_tree(
    recipe: Recipe,
    root: Path,
    *,
    contents: bool = True,
    allow_extra: bool = False,
) -> None:
    """Check reconstructed sizes, modes, links, hashes, and extra paths.

    Args:
        recipe: Logical filesystem recipe.
        root: Filesystem root for this operation.
        contents: Also rehash every file against its recorded digest;
            disable for the fast structural check of an already
            marker-attested tree.
        allow_extra: Tolerate paths not present in the recipe. Missing
            recipe entries always fail; extras are files the consumer
            wrote after publication, such as runtime validation
            markers.


    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    actual: set[str] = {
        path.relative_to(root).as_posix() for path in root.rglob(pattern="*")
    }
    expected: list[str] = [entry["path"] for entry in recipe["entries"]]
    if allow_extra:
        if not set(expected).issubset(actual):
            msg: str = "Materialized entry list mismatch"
            raise FormatError(msg)
    elif sorted(actual) != expected:
        msg = "Materialized entry list mismatch"
        raise FormatError(msg)
    for entry in recipe["entries"]:
        _verify_tree_entry(entry=entry, root=root, contents=contents)


def _verify_tree_entry(entry: Entry, root: Path, *, contents: bool) -> None:
    """Verify one reconstructed entry without following unexpected links.

    Args:
        entry: Entry for this operation.
        root: Filesystem root for this operation.
        contents: Rehash regular files against their recorded digests.


    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    path: Path = root / entry["path"]
    info: os_stat_result = path.lstat()
    kind: str = entry["type"]
    if kind == "symlink":
        if not S_ISLNK(
            info.st_mode,
        ) or path.readlink().as_posix() != entry.get("target", ""):
            msg: str = "Symlink mismatch"
            raise FormatError(msg)
    elif kind == "directory":
        if not S_ISDIR(info.st_mode):
            msg = "Directory mismatch"
            raise FormatError(msg)
    else:
        if not S_ISREG(info.st_mode):
            msg = "Regular file mismatch"
            raise FormatError(msg)
        if kind == "file":
            if info.st_size != entry.get("size", 0) or (
                contents and hash_file(path=path) != entry.get("sha256", "")
            ):
                msg = "Materialized file mismatch"
                raise FormatError(msg)
        elif not path.samefile(other_path=root / entry.get("target", "")):
            msg = "Hardlink mismatch"
            raise FormatError(msg)
    if kind != "symlink" and S_IMODE(info.st_mode) != entry["mode"]:
        msg = "Materialized mode mismatch"
        raise FormatError(msg)


def write_delta(
    baseline: Catalog,
    current: Catalog,
    source: Path,
    output: Path,
) -> dict[str, Json]:
    """Write only new packs plus a self-verifying target catalog/update.

    Args:
        baseline: Baseline for this operation.
        current: Current for this operation.
        source: Verified source data.
        output: Destination for generated artifacts.


    Returns:
        Write only new packs plus a self-verifying target catalog/update.

    """
    output.mkdir(parents=True, exist_ok=False)
    missing: list[str] = sorted(set(current["packs"]) - set(baseline["packs"]))
    removed: list[str] = sorted(set(baseline["packs"]) - set(current["packs"]))
    for pack_id in missing:
        pack: PackRecord = current["packs"][pack_id]
        target: Path = output / pack["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil_copyfile(
            src=destination(root=source, relative=pack["path"]),
            dst=target,
        )
    atomic_bytes(
        path=output / "catalog.json",
        data=canonical(value=cast(typ="Json", val=current)),
    )
    manifest: dict[str, Json] = {
        "schema_version": 1,
        "baseline_root": baseline["root_sha256"],
        "target_root": current["root_sha256"],
        "missing_packs": cast(typ="Json", val=missing),
        "removed_packs": cast(typ="Json", val=removed),
        "pack_hashes": {
            pack_id: current["packs"][pack_id]["sha256"] for pack_id in missing
        },
        "update_bytes": 0,
    }
    payload_bytes: int = sum(
        path.stat().st_size
        for path in output.rglob(pattern="*")
        if path.is_file()
    )
    while True:
        data: bytes = canonical(value=manifest)
        total: int = payload_bytes + len(data)
        if manifest["update_bytes"] == total:
            break
        manifest["update_bytes"] = total
    atomic_bytes(path=output / "update.json", data=data)
    return manifest


def apply_delta(root: Path, update: Path) -> Catalog:
    """Verify an update fully, install immutable packs, switch catalog last.

    Args:
        root: Filesystem root for this operation.
        update: Update for this operation.


    Returns:
        Verify an update fully, install immutable packs, switch catalog last.

    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    baseline: Catalog = load_catalog(path=root / "catalog.json")
    current: Catalog = load_catalog(path=update / "catalog.json")
    manifest: dict[str, Json] = _mapping(
        value=_json(data=(update / "update.json").read_bytes()),
    )
    missing: list[str] = sorted(set(current["packs"]) - set(baseline["packs"]))
    expected_files: set[str] = {"catalog.json", "update.json"} | {
        current["packs"][pack_id]["path"] for pack_id in missing
    }
    actual_files: set[str] = set()
    for path in update.rglob(pattern="*"):
        if path.is_symlink():
            msg: str = "Update contains a symlink"
            raise FormatError(msg)
        if path.is_file():
            actual_files.add(path.relative_to(update).as_posix())
    if actual_files != expected_files:
        msg = "Update entry list mismatch"
        raise FormatError(msg)
    if (
        manifest.get("schema_version") != 1
        or manifest.get("baseline_root") != baseline["root_sha256"]
        or manifest.get("target_root") != current["root_sha256"]
        or (_strings(value=manifest.get("missing_packs")) != missing)
        or (
            _strings(value=manifest.get("removed_packs"))
            != sorted(set(baseline["packs"]) - set(current["packs"]))
        )
        or (
            manifest.get("update_bytes")
            != sum(
                path.stat().st_size
                for path in update.rglob(pattern="*")
                if path.is_file()
            )
        )
    ):
        msg = "Update baseline/target/manifest mismatch"
        raise FormatError(msg)
    hashes: dict[str, Json] = _mapping(value=manifest.get("pack_hashes"))
    if hashes != {
        pack_id: current["packs"][pack_id]["sha256"] for pack_id in missing
    }:
        msg = "Update pack hashes mismatch"
        raise FormatError(msg)
    for pack_id, pack in current["packs"].items():
        source: Path = update if pack_id in missing else root
        path: Path = destination(root=source, relative=pack["path"])
        if (
            path.stat().st_size != pack["bytes"]
            or hash_file(path=path) != pack["sha256"]
        ):
            msg = "Update pack corruption"
            raise FormatError(msg)
        PackCodec.decode(
            path=path,
            expected={
                digest: current["objects"][digest]["size"]
                for digest in pack["objects"]
            },
        )
    for pack_id in missing:
        relative: str = current["packs"][pack_id]["path"]
        target: Path = destination(root=root, relative=relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path = target.with_name(
            name=f".{target.name}.{uuid4().hex}.tmp",
        )
        try:
            shutil_copyfile(
                src=destination(root=update, relative=relative),
                dst=temporary,
            )
            temporary.replace(target=target)
        finally:
            temporary.unlink(missing_ok=True)
    atomic_bytes(
        path=root / "catalogs" / f"{baseline['root_sha256']}.json",
        data=canonical(value=cast(typ="Json", val=baseline)),
    )
    atomic_bytes(
        path=root / "catalogs" / f"{current['root_sha256']}.json",
        data=canonical(value=cast(typ="Json", val=current)),
    )
    atomic_bytes(
        path=root / "catalog.json",
        data=canonical(value=cast(typ="Json", val=current)),
    )
    return current


def rollback(root: Path, digest: str) -> Catalog:
    """Restore a retained catalog after verifying all reachable packs.

    Args:
        root: Filesystem root for this operation.
        digest: Expected SHA-256 content identity.


    Returns:
        Restore a retained catalog after verifying all reachable packs.

    """
    catalog: Catalog = load_catalog(
        path=destination(
            root=root,
            relative=f"catalogs/{_digest(value=digest)}.json",
        ),
    )
    PartitionStore.verify(catalog=catalog, root=root)
    atomic_bytes(
        path=root / "catalog.json",
        data=canonical(value=cast(typ="Json", val=catalog)),
    )
    return catalog


def garbage_collect(
    root: Path,
    retained: list[Catalog],
    *,
    dry_run: bool = True,
) -> list[str]:
    """Explicit reachability GC; normal updates never remove old packs.

    Args:
        root: Filesystem root for this operation.
        retained: Retained for this operation.
        dry_run: Dry run for this operation.


    Returns:
        Explicit reachability GC; normal updates never remove old packs.

    """
    reachable: set[str] = {
        pack_id for catalog in retained for pack_id in catalog["packs"]
    }
    removed: list[str] = []
    for path in sorted((root / "packs").glob(pattern="*/*.zst")):
        if path.stem not in reachable:
            removed.append(path.stem)
            if not dry_run:
                path.unlink()
    return removed


def deep_verify(catalog: Catalog, root: Path) -> None:
    """Reconstruct every tree in disposable scratch storage.

    Args:
        catalog: Verified filesystem catalog.
        root: Filesystem root for this operation.

    """
    with TemporaryDirectory(prefix="partition-verify-") as temporary:
        base: Path = Path(temporary)
        objects: ObjectStore = ObjectStore(root=base / "objects")
        PartitionStore.verify(catalog=catalog, root=root, store=objects)
        materializer: Materializer = Materializer(store=objects)
        for name, recipe in sorted(catalog["components"].items()):
            materializer.materialize(
                recipe=recipe,
                root=base / "components" / name,
            )


def apply_update(root: Path, artifact: Path) -> Catalog:
    """Apply a directory or safely streamed ZIP update artifact.

    Args:
        root: Filesystem root for this operation.
        artifact: Artifact for this operation.


    Returns:
        Apply a directory or safely streamed ZIP update artifact.

    """
    if artifact.is_dir():
        return apply_delta(root=root, update=artifact)
    with TemporaryDirectory(prefix="partition-update-") as temporary:
        folder: Path = Path(temporary)
        _extract_update(artifact=artifact, folder=folder)
        return apply_delta(root=root, update=folder)


def _extract_update(artifact: Path, folder: Path) -> None:
    """Reject ZIP traversal, symlink entries, duplicates, and excess files.

    Args:
        artifact: Artifact for this operation.
        folder: Folder for this operation.


    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    seen: set[str] = set()
    with ZipFile(file=artifact) as archive:
        for info in archive.infolist():
            name: str = info.filename
            safe_path(value=name)
            if name in seen or S_ISLNK(info.external_attr >> 16):
                msg: str = "Duplicate or linked ZIP update member"
                raise FormatError(msg)
            seen.add(name)
            if (
                name not in {"catalog.json", "update.json"}
                and re_fullmatch(
                    pattern="packs/[0-9a-f]{2}/[0-9a-f]{64}\\.zst",
                    string=name,
                )
                is None
            ):
                msg = "Unexpected update member"
                raise FormatError(msg)
            if info.file_size > Constants.max_update_member:
                msg = "Oversized update member"
                raise FormatError(msg)
            path: Path = destination(root=folder, relative=name)
            path.parent.mkdir(parents=True, exist_ok=True)
            source: IO[bytes]
            sink: IO[bytes]
            with (
                archive.open(name=info) as source,
                path.open(mode="xb") as sink,
            ):
                shutil_copyfileobj(
                    fsrc=source,
                    fdst=sink,
                    length=Constants.block,
                )
