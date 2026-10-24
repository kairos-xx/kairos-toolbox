# Copyright (c) 2026 Project contributors.
"""Offline dependency-aware runtime for partitioned filesystem tools."""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from importlib import import_module
from os import environ as os_environ
from os import execvpe as os_execvpe
from os import name as os_name
from os import pathsep as os_pathsep
from pathlib import Path
from shutil import rmtree as shutil_rmtree
from subprocess import call
from sys import argv as sys_argv
from sys import executable as sys_executable
from sys import platform as sys_platform
from tempfile import gettempdir
from time import monotonic, sleep
from typing import (
    TYPE_CHECKING,
    BinaryIO,
    Final,
    NoReturn,
    Protocol,
    TextIO,
    cast,
    final,
)
from uuid import uuid4

from .store import (
    FormatError,
    Materializer,
    ObjectStore,
    PackCodec,
    PartitionStore,
    apply_update,
    atomic_bytes,
    canonical,
    component_name,
    deep_verify,
    destination,
    hash_file,
    load_catalog,
    rollback,
    verify_tree,
)

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping

    from .store import Catalog, Json, PackRecord, Recipe


def closure(
    *,
    components: Mapping[str, tuple[str, ...]],
    requested: tuple[str, ...],
) -> tuple[str, ...]:
    """Resolve dependencies once, in deterministic dependency-first order.

    Args:
        components: Component name to its required component names.
        requested: Components to resolve, in request order.


    Returns:
        Resolved components, dependencies first, deduplicated.

    """
    resolved: list[str] = []
    active: set[str] = set()

    def visit(component: str) -> None:
        """Visit one dependency and reject missing or cyclic references.

        Args:
            component: Component for this operation.

        Raises:
            FormatError: When input or state does not satisfy the contract.

        """
        if component in resolved:
            return
        if component in active or component not in components:
            msg: str = f"Missing/cyclic component dependency: {component}"
            raise FormatError(msg)
        active.add(component)
        for dependency in sorted(components[component]):
            visit(component=dependency)
        active.remove(component)
        resolved.append(component)

    for name in requested:
        visit(component=name)
    return tuple(resolved)


def _requires(catalog: Catalog) -> dict[str, tuple[str, ...]]:
    """Project catalog component metadata into a dependency mapping.

    Args:
        catalog: Verified filesystem catalog.

    Returns:
        Component name to its required component names.

    """
    return {
        name: tuple(
            cast(
                typ="list[str]",
                val=recipe["metadata"].get("requires", []),
            ),
        )
        for name, recipe in catalog["components"].items()
    }


@final
class _PosixLock(Protocol):
    """Typed interface to the optional POSIX locking module.

    Attributes:
        LOCK_EX: Exclusive lock flag.
        LOCK_NB: Non-blocking flag.
        LOCK_UN: Unlock flag.

    """

    LOCK_EX: int
    LOCK_NB: int
    LOCK_UN: int

    def flock(self, stream: BinaryIO, operation: int, /) -> None:
        """Lock or unlock a file descriptor.

        Args:
            stream: Stream for this operation.
            operation: Operation for this operation.

        """


@final
class _WindowsLock(Protocol):
    """Typed interface to the optional Windows locking module.

    Attributes:
        LK_NBLCK: Non-blocking lock flag.
        LK_UNLCK: Unlock flag.

    """

    LK_NBLCK: int
    LK_UNLCK: int

    def locking(self, descriptor: int, operation: int, count: int, /) -> None:
        """Lock or unlock bytes at the current file offset.

        Args:
            descriptor: Descriptor for this operation.
            operation: Operation for this operation.
            count: Count for this operation.

        """


class _PosixUid(Protocol):
    """Typed interface to the optional POSIX user identifier."""

    def getuid(self) -> int:
        """Return the real user identifier of the calling process.

        Returns:
            Real user identifier of the calling process.

        """
        ...


def _cache_owner() -> str:
    """Tag one cache writer so fallback roots stay per-owner.

    POSIX identifies writers by real user identifier, which is what
    diverges when a shared cache directory was populated by ``sudo``
    or a different account. Other platforms hash the per-user home
    directory, which already separates writers.

    Returns:
        Short deterministic owner tag for fallback directory names.

    """
    if sys_platform == "win32":
        return (
            "h"
            + sha256(
                data=str(object=Path.home()).encode(),
            ).hexdigest()[:16]
        )
    module: _PosixUid = cast(
        typ="_PosixUid",
        val=import_module(name="os"),
    )
    return f"u{module.getuid()}"


def _usable_cache_root(root: Path) -> bool:
    """Whether ``root`` can host the catalog lock file for this process.

    A candidate qualifies only when it is not a symlink, the process
    can create it when absent, and ``.lock`` is or can become a
    regular file this process may open for writing. Anything else
    defers to the next deterministic candidate instead of failing the
    entire run.

    Args:
        root: Candidate catalog cache root.

    Returns:
        ``True`` when ``root`` can host the lock file.

    """
    if root.is_symlink():
        return False
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    if not root.is_dir():
        return False
    lock: Path = root / ".lock"
    if lock.is_symlink() or (lock.exists() and not lock.is_file()):
        return False
    try:
        with lock.open(mode="a+b"):
            pass
    except OSError:
        return False
    return True


def _cache_candidates(base: Path, identity: str) -> tuple[Path, ...]:
    """Order catalog cache roots: shared first, per-owner escapes next.

    Args:
        base: Preferred cache base directory.
        identity: Catalog root SHA-256.

    Returns:
        Deterministic candidate roots, most desirable first.

    """
    owner: str = _cache_owner()
    return (
        base / identity,
        base / f"{identity}.{owner}",
        Path(gettempdir()) / f"kairos-tools-{owner}" / identity,
    )


def _select_cache_root(base: Path, identity: str) -> Path:
    """Pick the first usable catalog cache root for this process.

    Args:
        base: Preferred cache base directory.
        identity: Catalog root SHA-256.

    Returns:
        First usable candidate catalog cache root.

    Raises:
        PermissionError: When no candidate can host the lock file.

    """
    candidates: tuple[Path, ...] = _cache_candidates(
        base=base,
        identity=identity,
    )
    for candidate in candidates:
        if _usable_cache_root(root=candidate):
            return candidate
    msg: str = (
        "No writable runtime cache for catalog "
        f"{identity}; tried: "
        + ", ".join(str(object=candidate) for candidate in candidates)
    )
    raise PermissionError(msg)


@contextmanager
def cache_lock(root: Path, timeout: float = 120.0) -> Generator[None]:
    """Serialize cache publication across processes without stale markers.

    Args:
        root: Filesystem root for this operation.
        timeout: Timeout for this operation.


    Yields:
        Successive bounded entries from the operation.

    Raises:
        FormatError: When the runtime cache lock is a symlink.
        TimeoutError: When the operation detects invalid input or state.

    """
    root.mkdir(parents=True, exist_ok=True)
    lock: Path = root / ".lock"
    if lock.is_symlink():
        msg: str = f"Runtime cache lock is a symlink: {lock}"
        raise FormatError(msg)
    with lock.open(mode="a+b") as stream:
        started: float = monotonic()
        if sys_platform == "win32":
            msvcrt: _WindowsLock = cast(
                typ="_WindowsLock",
                val=import_module(name="msvcrt"),
            )
            stream.write(b"\x00")
            stream.flush()
            while True:
                try:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if monotonic() - started > timeout:
                        msg = "Runtime cache lock timed out"
                        raise TimeoutError(msg) from None
                    sleep(0.1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl: _PosixLock = cast(
                typ="_PosixLock",
                val=import_module(name="fcntl"),
            )
            while True:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if monotonic() - started > timeout:
                        msg = "Runtime cache lock timed out"
                        raise TimeoutError(msg) from None
                    sleep(0.1)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


class Runtime:
    """Per-component atomic readiness, decoded objects, and environments."""

    def __init__(self, catalog_path: Path, cache: Path | None = None) -> None:
        """Load the catalog and select a writable cache for its root.

        The preferred ``<cache>/<catalog-sha>`` root is used when this
        process can lock it; otherwise a deterministic per-owner
        fallback under the same base, then a per-owner temporary
        base, is selected so cross-account cache collisions cannot
        abort the run.

        Args:
            catalog_path: Catalog path for this operation.
            cache: Persistent build or runtime cache.

        """
        self.catalog = load_catalog(path=catalog_path)
        self.source = catalog_path.parent
        base: Path = cache or Path(
            os_environ.get(
                "KAIROS_TOOLS_CACHE",
                str(
                    object=Path(
                        os_environ.get(
                            "XDG_CACHE_HOME",
                            str(object=Path.home() / ".cache"),
                        ),
                    )
                    / "kairos-tools",
                ),
            ),
        )
        self.root = _select_cache_root(
            base=base,
            identity=self.catalog["root_sha256"],
        )
        self.components = self.root / "components"
        self.objects = ObjectStore(root=self.root / "objects")

    @staticmethod
    def readiness(catalog: Catalog, recipe: Recipe) -> dict[str, Json]:
        """Return the identity required before reusing a component.

        Args:
            catalog: Verified filesystem catalog.
            recipe: Logical filesystem recipe.


        Returns:
            Return the identity required before reusing a component.

        """
        return {
            "format_version": 1,
            "catalog_root": catalog["root_sha256"],
            "component": recipe["name"],
            "recipe_sha256": recipe["recipe_sha256"],
            "tree_sha256": recipe["tree_sha256"],
        }

    def ensure(self, name: str) -> Path:
        """Materialize only a tool's closure; preserve valid prior trees.

        Args:
            name: Component or resource name.


        Returns:
            Materialize only a tool's closure; preserve valid prior trees.

        """
        names: tuple[str, ...] = closure(
            components=_requires(catalog=self.catalog),
            requested=(name,),
        )
        with cache_lock(root=self.root):
            self.components.mkdir(parents=True, exist_ok=True)
            for orphan in self.components.glob(pattern=".*.tmp.*"):
                if orphan.is_dir() and (not orphan.is_symlink()):
                    shutil_rmtree(path=orphan)
            materializer: Materializer = Materializer(store=self.objects)
            verified: set[str] = set()
            for component in names:
                recipe: Recipe = self.catalog["components"][component]
                target: Path = destination(
                    root=self.components,
                    relative=component_name(value=component),
                )
                ready: Path = destination(
                    root=self.root,
                    relative=f"ready/{component}.json",
                )
                marker: bytes = canonical(
                    value=self.readiness(catalog=self.catalog, recipe=recipe),
                )
                if component not in verified:
                    if self._ready(
                        recipe=recipe,
                        target=target,
                        ready=ready,
                        marker=marker,
                    ):
                        verified.add(component)
                    else:
                        self._recover_objects(recipe=recipe)
                        self._publish(
                            recipe=recipe,
                            target=target,
                            materializer=materializer,
                        )
                        verified.add(component)
                self._index_files(
                    recipe=recipe,
                    target=target,
                    materializer=materializer,
                )
            if (
                os_environ.get("KAIROS_TOOLS_OBJECT_CACHE", "keep")
                == "discard"
            ):
                shutil_rmtree(path=self.objects.root, ignore_errors=True)
        return self.components / name

    def environment(self, name: str) -> dict[str, str]:
        """Compose original environment semantics dependency-first.

        Args:
            name: Component or resource name.


        Returns:
            Compose original environment semantics dependency-first.

        Raises:
            FormatError: When the operation detects invalid input or state.

        """
        merged: dict[str, list[str]] = {}
        for component in closure(
            components=_requires(catalog=self.catalog),
            requested=(name,),
        ):
            entries: dict[str, list[str]] = cast(
                typ="dict[str, list[str]]",
                val=self.catalog["components"][component]["metadata"].get(
                    "env",
                    {},
                ),
            )
            for variable, values in entries.items():
                for value in values:
                    path: Path = (
                        self.components / component / value
                    ).resolve()
                    if not path.is_relative_to(self.components.resolve()):
                        msg: str = "Environment path escapes component cache"
                        raise FormatError(msg)
                    merged.setdefault(variable, []).append(str(object=path))
        env: dict[str, str] = os_environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        for variable, values in merged.items():
            if env.get(variable):
                values.append(env[variable])
            env[variable] = os_pathsep.join(dict.fromkeys(values))
        return env

    def tools(self) -> dict[str, tuple[str, dict[str, str]]]:
        """Discover preserved tool declarations from catalog metadata.

        Returns:
            Discover preserved tool declarations from catalog metadata.

        Raises:
            FormatError: When the operation detects invalid input or state.

        """
        tools: dict[str, tuple[str, dict[str, str]]] = {}
        for name, recipe in sorted(self.catalog["components"].items()):
            declared: dict[str, dict[str, str]] = cast(
                typ="dict[str, dict[str, str]]",
                val=recipe["metadata"].get("tools", {}),
            )
            for tool, spec in sorted(declared.items()):
                if tool in tools:
                    msg: str = "Duplicate tool name"
                    raise FormatError(msg)
                tools[tool] = (name, spec)
        return tools

    def command(
        self,
        tool: str,
        args: list[str],
    ) -> tuple[list[str], dict[str, str]]:
        """Resolve original Python/Node/binary/probe invocation semantics.

        Args:
            tool: Tool for this operation.
            args: Args for this operation.


        Returns:
            Resolve original Python/Node/binary/probe invocation semantics.

        Raises:
            FormatError: When the operation detects invalid input or state.

        """
        if tool not in self.tools():
            msg: str = f"Unsupported bundled tool: {tool}"
            raise FormatError(msg)
        name, spec = self.tools()[tool]
        root: Path = self.ensure(name=name)
        kind, target = (spec["kind"], spec["target"])
        command: list[str]
        if kind == "python-module":
            command = [sys_executable, "-m", target, *args]
        elif kind == "probe":
            command = [sys_executable, "-c", target]
        elif kind == "js-entry":
            node: Recipe = self.catalog["components"]["node"]
            node_tools: dict[str, dict[str, str]] = cast(
                typ="dict[str, dict[str, str]]",
                val=node["metadata"]["tools"],
            )
            node_root: Path = self.ensure(name="node")
            command = [
                str(object=node_root / node_tools["node"]["target"]),
                str(object=root / "js" / "node_modules" / target),
                *args,
            ]
        elif kind == "binary":
            candidate: Path = root / target
            if candidate.is_file():
                command = [str(object=candidate), *args]
            elif spec.get("module"):
                command = [sys_executable, "-m", spec["module"], *args]
            else:
                msg = f"Missing bundled binary: {target}"
                raise FormatError(msg)
        else:
            msg = f"Unsupported tool kind: {kind}"
            raise FormatError(msg)
        return (command, self.environment(name=name))

    @staticmethod
    def _ready(
        recipe: Recipe,
        target: Path,
        ready: Path,
        marker: bytes,
    ) -> bool:
        """Trust readiness only when the entire local tree still verifies.

        The marker attests a tree that already passed full content
        verification at publication, so the recurring check stays
        structural — paths, types, sizes, modes, and links. Set
        ``KAIROS_TOOLS_VERIFY=deep`` to rehash every file on each
        call, matching ``--verify-deep``.

        Args:
            recipe: Logical filesystem recipe.
            target: Target for this operation.
            ready: Ready for this operation.
            marker: Marker for this operation.


        Returns:
            Trust readiness only when the entire local tree still verifies.

        """
        if (
            not ready.is_file()
            or ready.read_bytes() != marker
            or (not target.is_dir())
        ):
            return False
        try:
            verify_tree(
                recipe=recipe,
                root=target,
                contents=os_environ.get("KAIROS_TOOLS_VERIFY") == "deep",
                allow_extra=True,
            )
        except (OSError, ValueError):
            return False
        return True

    @staticmethod
    def _index_files(
        recipe: Recipe,
        target: Path,
        materializer: Materializer,
    ) -> None:
        """Index only verified files for compatible hardlink sharing.

        Args:
            recipe: Logical filesystem recipe.
            target: Target for this operation.
            materializer: Verified component reconstruction context.

        """
        for entry in recipe["entries"]:
            if entry["type"] == "file":
                materializer.files[entry.get("sha256", ""), entry["mode"]] = (
                    target / entry["path"]
                )

    def _recover_objects(self, recipe: Recipe) -> None:
        """Decode only packs with missing/corrupt required objects.

        Args:
            recipe: Logical filesystem recipe.


        Raises:
            FormatError: When the operation detects invalid input or state.

        """
        required: set[str] = {
            digest
            for entry in recipe["entries"]
            for digest in entry.get("chunks", [])
        }
        packs: set[str] = {
            self.catalog["objects"][digest]["pack"]
            for digest in required
            if not self.objects.valid(
                digest=digest,
                size=self.catalog["objects"][digest]["size"],
            )
        }
        for pack_id in sorted(packs):
            pack: PackRecord = self.catalog["packs"][pack_id]
            path: Path = destination(root=self.source, relative=pack["path"])
            if (
                hash_file(path=path) != pack["sha256"]
                or path.stat().st_size != pack["bytes"]
            ):
                msg: str = "Runtime pack corruption"
                raise FormatError(msg)
            PackCodec.decode(
                path=path,
                expected={
                    digest: self.catalog["objects"][digest]["size"]
                    for digest in pack["objects"]
                },
                store=self.objects,
            )

    def _publish(
        self,
        recipe: Recipe,
        target: Path,
        materializer: Materializer,
    ) -> None:
        """Build a verified sibling before replacing the component tree.

        Args:
            recipe: Logical filesystem recipe.
            target: Target for this operation.
            materializer: Verified component reconstruction context.


        Raises:
            OSError: When publication fails and the previous tree is restored.

        """
        name: str = recipe["name"]
        ready: Path = destination(
            root=self.root,
            relative=f"ready/{name}.json",
        )
        marker: bytes = canonical(
            value=self.readiness(catalog=self.catalog, recipe=recipe),
        )
        temporary: Path = self.components / f".{name}.tmp.{uuid4().hex}"
        backup: Path = self.components / f".{name}.old.{uuid4().hex}"
        try:
            materializer.materialize(recipe=recipe, root=temporary)
            if target.exists():
                try:
                    target.rename(target=backup)
                except OSError:
                    shutil_rmtree(path=target)
            try:
                temporary.rename(target=target)
                atomic_bytes(path=ready, data=marker)
            except OSError:
                if target.exists():
                    shutil_rmtree(path=target)
                if backup.exists():
                    backup.rename(target=target)
                raise
            self._index_files(
                recipe=recipe,
                target=target,
                materializer=materializer,
            )
            if backup.exists():
                shutil_rmtree(path=backup)
        finally:
            if temporary.exists():
                shutil_rmtree(path=temporary)


def _write(text: str) -> None:
    """Write to the current stdout, including redirected host streams.

    Args:
        text: Text for this operation.

    """
    stream: TextIO = cast(typ="TextIO", val=import_module(name="sys").stdout)
    stream.write(text)


def _error(text: str) -> None:
    """Write diagnostics to the current redirected stderr.

    Args:
        text: Text for this operation.

    """
    stream: TextIO = cast(typ="TextIO", val=import_module(name="sys").stderr)
    stream.write(text)


@final
class Constants:
    """Group module-level constants.

    Attributes:
        maintenance_arguments: Grouped module constant.

    """

    maintenance_arguments: Final[int] = 2


def _maintenance(runtime: Runtime, args: tuple[str, ...]) -> None:
    """Apply verified updates or restore a retained catalog under a lock.

    Args:
        runtime: Runtime for this operation.
        args: Args for this operation.


    Raises:
        FormatError: When the operation detects invalid input or state.

    """
    if len(args) != Constants.maintenance_arguments:
        msg: str = "Update/rollback requires an artifact path or catalog root"
        raise FormatError(msg)
    with cache_lock(root=runtime.source):
        updated: Catalog = (
            apply_update(root=runtime.source, artifact=Path(args[1]))
            if args[0] == "--apply-update"
            else rollback(root=runtime.source, digest=args[1])
        )
    _write(text=updated["root_sha256"] + "\n")


class ProcessReplacement(Protocol):
    """Native process replacement boundary used by dispatch tests."""

    def __call__(
        self,
        file: str,
        args: list[str],
        env: dict[str, str],
    ) -> NoReturn:
        """Replace this process with the verified tool invocation.

        Args:
            file: Executable selected from validated catalog metadata.
            args: Argument vector passed directly without a shell.
            env: Dependency-aware execution environment.

        """
        ...


def main(
    *,
    catalog_path: Path,
    arguments: tuple[str, ...] | None = None,
    replace_process: ProcessReplacement = os_execvpe,
) -> int:
    """Dispatch metadata commands or replace the process with a tool.

    Args:
        catalog_path: Catalog path for this operation.
        arguments: Explicit command arguments.
        replace_process: Native POSIX replacement or a test boundary.


    Returns:
        Dispatch metadata commands or replace the process with a tool.

    """
    args: tuple[str, ...] = (
        tuple(sys_argv[1:]) if arguments is None else arguments
    )
    try:
        runtime: Runtime = Runtime(catalog_path=catalog_path)
        tool: str = args[0] if args else "--help"
        if tool in {"list-tools", "--list-tools", "--list"}:
            _write(text="\n".join(runtime.tools()) + "\n")
        elif tool == "--version":
            _write(text=runtime.catalog["root_sha256"] + "\n")
        elif tool in {"--verify", "--verify-deep"}:
            if tool == "--verify-deep":
                deep_verify(catalog=runtime.catalog, root=runtime.source)
            else:
                PartitionStore.verify(
                    catalog=runtime.catalog,
                    root=runtime.source,
                )
            _write(text="Verified " + runtime.catalog["root_sha256"] + "\n")
        elif tool in {"--help", "-h"}:
            _write(
                text=(
                    "usage: tools.py TOOL [ARGS...]\nOffline partitioned"
                    " filesystem launcher.\nlist-tools | --verify |"
                    " --verify-deep | --version\nCache: KAIROS_TOOLS_CACHE or"
                    " XDG_CACHE_HOME/kairos-tools\n"
                ),
            )
        elif tool in {"--apply-update", "--rollback"}:
            _maintenance(runtime=runtime, args=args)
        else:
            if tool not in runtime.tools():
                _error(text=f"Unsupported bundled tool: {tool}\n")
                return 127
            command, env = runtime.command(tool=tool, args=list(args[1:]))
            if os_name == "posix":
                replace_process(file=command[0], args=command, env=env)
            return call(args=command, env=env)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        _error(text=f"Bundle error: {exc}\n")
        return 1
    return 0
