# Copyright (c) 2026 Project contributors.
"""Architecture guard: layered imports and the execution boundary."""

from __future__ import annotations

from ast import (
    Import,
    ImportFrom,
    Module,
)
from ast import (
    parse as ast_parse,
)
from ast import (
    walk as ast_walk,
)
from pathlib import Path
from re import finditer as re_finditer
from tomllib import loads as tomllib_loads

from tests.tools.stationer.helpers import check, expect


def _root() -> Path:
    """Return the repository root.

    Returns:
        The kairos-toolbox checkout root.

    """
    return Path(__file__).resolve().parents[3]


def _imported_modules(*, path: Path) -> set[str]:
    """Collect the modules one file imports, statically and lazily.

    Args:
        path: Python source file to scan.

    Returns:
        Imported module names plus ``import_module`` string targets.

    """
    tree: Module = ast_parse(source=path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast_walk(node=tree):
        match node:
            case Import(names=names):
                modules.update(alias.name for alias in names)
            case ImportFrom(module=module, level=0):
                modules.add(module or "")
            case _:
                pass
    text: str = path.read_text(encoding="utf-8")
    for match in re_finditer(
        pattern=r'import_module\(name="([^"]+)"',
        string=text,
    ):
        modules.add(match.group(1))
    return modules


def _allowlist() -> dict[str, set[str]]:
    """Read the architecture allowlist.

    Returns:
        Offender file to allowed execution-boundary patterns.

    """
    path: Path = (
        _root() / "tools" / "stationer" / "architecture_allowlist.toml"
    )
    data: dict[str, list[dict[str, str]]] = tomllib_loads(
        path.read_text(encoding="utf-8"),
    )
    return {
        entry["file"]: set(
            entry.get("patterns") or [entry["pattern"]],
        )
        for entry in data["offenders"]
    }


def _allowed_imports() -> dict[str, set[str]]:
    """Read the sanctioned cross-product import seams.

    Returns:
        Offender file to permitted sibling module prefixes.

    """
    path: Path = (
        _root() / "tools" / "stationer" / "architecture_allowlist.toml"
    )
    data: dict[str, list[dict[str, str]]] = tomllib_loads(
        path.read_text(encoding="utf-8"),
    )
    return {
        entry["file"]: set(
            entry.get("modules") or [entry["module"]],
        )
        for entry in data.get("imports", [])
    }


def _stationer_sources() -> list[Path]:
    """List owned stationer sources.

    Returns:
        Every Python file under ``tools/stationer``.

    """
    return sorted((_root() / "tools" / "stationer").rglob(pattern="*.py"))


def test_partition_layer_imports_nothing_heavy() -> None:
    """``tools.stationer.partition`` never imports heavier layers."""
    forbidden: tuple[str, ...] = (
        "tools.stationer.builder",
        "tools.stationer.cli",
        "tools.stationer.sessions",
        "tools.stationer.engine",
        "tools.stationer.publisher",
        "tools.stationer.remote",
        "tools.conjure",
        "tools.district",
        "tools.bowman",
        "tools.charter",
        "stationer.builder",
        "stationer.cli",
        "stationer.sessions",
        "stationer.engine",
        "stationer.publisher",
        "stationer.remote",
    )
    for path in sorted(
        (_root() / "tools" / "stationer" / "partition").rglob(pattern="*.py"),
    ):
        for module in _imported_modules(path=path):
            for bad in forbidden:
                check(
                    condition=not module.startswith(bad),
                    reason=f"{path.name} imports {module} ({bad})",
                )


def test_stationer_does_not_import_sibling_products() -> None:
    """Stationer modules never reach other product packages."""
    siblings: frozenset[str] = frozenset(
        {
            "district",
            "bowman",
            "charter",
            "maire",
            "evocator",
        },
    )
    allowed: dict[str, set[str]] = _allowed_imports()
    for path in _stationer_sources():
        file: str = str(object=path.relative_to(_root()))
        for module in _imported_modules(path=path):
            parts: list[str] = module.split(sep=".")
            head: str = (
                parts[0]
                if parts[0] != "tools"
                else (parts[1] if len(parts) > 1 else "")
            )
            if head not in siblings:
                continue
            check(
                condition=any(
                    module.startswith(seam)
                    for seam in allowed.get(file, set())
                ),
                reason=f"{file} imports sibling product {module}",
            )


def test_execution_boundary_matches_allowlist() -> None:
    """Direct execution sites match the allowlist exactly."""
    execution_marks: tuple[str, ...] = (
        "subprocess_run(",
        "subprocess.run(",
        "from subprocess import",
        "import subprocess",
        "Popen(",
        "qemu-system",
        "utmctl",
    )
    allowed: dict[str, set[str]] = _allowlist()
    seen: dict[str, set[str]] = {}
    for path in _stationer_sources():
        text: str = path.read_text(encoding="utf-8")
        marks: set[str] = {mark for mark in execution_marks if mark in text}
        if marks:
            seen[str(object=path.relative_to(_root()))] = marks
    expect(actual=seen.keys(), expected=allowed.keys())
    for file, marks in seen.items():
        for mark in marks:
            check(
                condition=mark in allowed[file],
                reason=f"{file} gained unallowed mark {mark!r}",
            )
