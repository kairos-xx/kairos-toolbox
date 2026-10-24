# Copyright (c) 2026 Project contributors.
"""Tests for the partition launcher runtime."""

from __future__ import annotations

from multiprocessing import get_context
from os import pathsep as os_pathsep
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, cast

import pytest
from stationer.partition import launcher as launcher_module
from stationer.partition.launcher import (
    Runtime,
    cache_lock,
    closure,
    main,
)
from stationer.partition.store import (
    FormatError,
    ObjectStore,
    Recipe,
)

from tests.tools.stationer.helpers import check, expect
from tests.tools.stationer.partition.test_store import (
    component_tree,
    publish,
    scan_recipe,
)

if TYPE_CHECKING:
    from multiprocessing.context import SpawnContext
    from multiprocessing.process import BaseProcess

    from stationer.partition.store import Json


def _private(owner: object, *, name: str, **arguments: object) -> object:
    """Call a private helper by name for boundary coverage.

    Args:
        owner: Module or instance holding the callable.
        name: Callable name, including its leading underscore.
        arguments: Keyword arguments forwarded to the callable.

    Returns:
        Callable result.

    """
    return getattr(owner, name)(**arguments)


def _lock_worker(root: str, tag: str, output: str) -> None:
    """Append tagged lines to ``output`` under the shared cache lock.

    Args:
        root: Locked directory shared by both workers.
        tag: Per-worker line prefix.
        output: Shared file receiving the appends.

    """
    path: Path = Path(output)
    with (
        cache_lock(root=Path(root)),
        path.open(mode="a", encoding="utf-8") as stream,
    ):
        for index in range(20):
            _ = stream.write(f"{tag}-{index}\n")


def _catalog_with_tool(
    *,
    tmp_path: Path,
    metadata: dict[str, object],
    name: str = "core",
) -> Path:
    """Publish one component plus tool metadata, return the catalog dir.

    Args:
        tmp_path: Per-test temporary directory.
        metadata: Component metadata to record in the recipe.
        name: Component name.

    Returns:
        Directory holding ``catalog.json`` and the pack store.

    """
    objects: ObjectStore = ObjectStore(root=tmp_path / "build" / "objects")
    recipe: Recipe = scan_recipe(
        objects=objects,
        root=component_tree(root=tmp_path),
        name=name,
        metadata=cast(typ="dict[str, Json]", val=metadata),
    )
    output: Path = tmp_path / "dist"
    _ = publish(
        cache=tmp_path / "build",
        recipes={name: recipe},
        output=output,
    )
    return output


def test_closure_orders_dependencies_first() -> None:
    """Return dependency-first resolution for a linear chain."""
    expect(
        actual=closure(
            components={
                "a": ("b",),
                "b": ("c",),
                "c": (),
            },
            requested=("a",),
        ),
        expected=("c", "b", "a"),
    )


def test_closure_is_deterministic_and_rejects_cycles() -> None:
    """Order diamonds deterministically and reject cycles and ghosts."""
    expect(
        actual=closure(
            components={
                "a": ("c", "b"),
                "b": ("d",),
                "c": ("d",),
                "d": (),
            },
            requested=("a",),
        ),
        expected=("d", "b", "c", "a"),
    )
    with pytest.raises(
        expected_exception=FormatError,
        match="Missing/cyclic component",
    ):
        closure(
            components={"x": ("y",), "y": ("x",)},
            requested=("x",),
        )
    with pytest.raises(
        expected_exception=FormatError,
        match="Missing/cyclic component",
    ):
        closure(components={}, requested=("ghost",))


def test_cache_lock_serializes_two_processes(tmp_path: Path) -> None:
    """Keep appended lines intact when two processes share one lock.

    Args:
        tmp_path: Per-test temporary directory.

    """
    root: Path = tmp_path / "locked"
    output: Path = tmp_path / "lines.txt"
    context: SpawnContext = get_context(method="spawn")
    workers: list[BaseProcess] = [
        context.Process(
            target=_lock_worker,
            args=(str(object=root), tag, str(object=output)),
        )
        for tag in ("left", "right")
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=60)
        expect(actual=worker.exitcode, expected=0)
    lines: list[str] = output.read_text(encoding="utf-8").splitlines()
    expect(actual=len(lines), expected=40)
    left: list[str] = [line for line in lines if line.startswith("left-")]
    right: list[str] = [line for line in lines if line.startswith("right-")]
    expect(actual=left, expected=[f"left-{i}" for i in range(20)])
    expect(actual=right, expected=[f"right-{i}" for i in range(20)])
    check(
        condition=left + right == lines or right + left == lines,
        reason="Lines from the two workers interleaved.",
    )


def test_cache_root_falls_back_when_shared_root_unwritable(
    tmp_path: Path,
) -> None:
    """Select the per-owner root when the shared candidate is unusable.

    Args:
        tmp_path: Per-test temporary directory.

    """
    base: Path = tmp_path / "cache"
    base.mkdir(parents=True)
    identity: str = "ab" * 32
    _ = (base / identity).write_bytes(data=b"not-a-directory")
    root: object = _private(
        owner=launcher_module,
        name="_select_cache_root",
        base=base,
        identity=identity,
    )
    owner: object = _private(owner=launcher_module, name="_cache_owner")
    expect(actual=root, expected=base / f"{identity}.{owner}")


def test_main_replaces_process_via_injected_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatch a catalog tool through the process-replacement seam.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Fixture pointing the runtime cache at ``tmp_path``.

    """
    monkeypatch.setenv(
        name="KAIROS_TOOLS_CACHE",
        value=str(object=tmp_path / "cache"),
    )
    output: Path = _catalog_with_tool(
        tmp_path=tmp_path,
        metadata={
            "tools": {
                "run": {"kind": "binary", "target": "bin/run"},
            },
        },
    )
    recorded: dict[str, object] = {}

    def fake_replace(
        file: str,
        args: list[str],
        env: dict[str, str],
    ) -> NoReturn:
        """Capture the replacement invocation instead of exec'ing.

        Args:
            file: Executable chosen by the launcher.
            args: Argument vector for the tool.
            env: Dependency-aware environment.

        Raises:
            SystemExit: Always, matching ``execvpe`` semantics.

        """
        recorded["file"] = file
        recorded["args"] = list(args)
        recorded["env"] = dict(env)
        raise SystemExit(0)

    with pytest.raises(expected_exception=SystemExit) as captured:
        _ = main(
            catalog_path=output / "catalog.json",
            arguments=("run", "--flag"),
            replace_process=fake_replace,
        )
    expect(actual=captured.value.code, expected=0)
    invoked: str = cast(typ="str", val=recorded["file"])
    check(
        condition=Path(invoked).is_file(),
        reason="Launcher did not point at a materialized binary.",
    )
    check(
        condition=invoked.endswith("bin/run"),
        reason=f"Unexpected tool path: {invoked}",
    )
    argv: list[str] = cast(typ="list[str]", val=recorded["args"])
    expect(actual=argv, expected=[invoked, "--flag"])


def test_runtime_reuses_ready_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Materialize a component once and reuse the verified tree.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Fixture pointing the runtime cache at ``tmp_path``.

    """
    monkeypatch.setenv(
        name="KAIROS_TOOLS_CACHE",
        value=str(object=tmp_path / "cache"),
    )
    output: Path = _catalog_with_tool(tmp_path=tmp_path, metadata={})
    runtime: Runtime = Runtime(catalog_path=output / "catalog.json")
    first: Path = runtime.ensure(name="core")
    second: Path = runtime.ensure(name="core")
    expect(actual=first, expected=second)
    check(
        condition=(first / "data.txt").is_file(),
        reason="Materialized component is missing files.",
    )
    env: dict[str, str] = runtime.environment(name="core")
    check(
        condition=env.get("PYTHONDONTWRITEBYTECODE") == "1",
        reason="Dependency environment lost bytecode suppression.",
    )


def test_environment_merges_dependency_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Merge declared env entries dependency-first.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Fixture pointing the runtime cache at ``tmp_path``.

    """
    monkeypatch.setenv(
        name="KAIROS_TOOLS_CACHE",
        value=str(object=tmp_path / "cache"),
    )
    objects: ObjectStore = ObjectStore(root=tmp_path / "build" / "objects")
    base_tree: Path = tmp_path / "base-src"
    (base_tree / "libs").mkdir(parents=True)
    _ = (base_tree / "libs" / "lib.so").write_bytes(data=b"so")
    base: Recipe = scan_recipe(
        objects=objects,
        root=base_tree,
        name="base",
        metadata=cast(
            typ="dict[str, Json]",
            val={"env": {"LD_LIBRARY_PATH": ["libs"]}},
        ),
    )
    tool_tree: Path = tmp_path / "tool-src"
    (tool_tree / "lib").mkdir(parents=True)
    _ = (tool_tree / "lib" / "x.so").write_bytes(data=b"so")
    tool: Recipe = scan_recipe(
        objects=objects,
        root=tool_tree,
        name="tool",
        metadata=cast(
            typ="dict[str, Json]",
            val={
                "requires": ["base"],
                "env": {"LD_LIBRARY_PATH": ["lib"]},
            },
        ),
    )
    output: Path = tmp_path / "dist"
    _ = publish(
        cache=tmp_path / "build",
        recipes={"tool": tool, "base": base},
        output=output,
    )
    runtime: Runtime = Runtime(catalog_path=output / "catalog.json")
    env: dict[str, str] = runtime.environment(name="tool")
    parts: list[str] = env["LD_LIBRARY_PATH"].split(sep=os_pathsep)
    expect(actual=len(parts), expected=2)
    check(
        condition="base" in parts[0],
        reason="Dependency env entry must come first.",
    )
