# Copyright (c) 2026 Project contributors.
"""Tests for building native installers with ``tools.release_builder``."""

from __future__ import annotations

from ast import ImportFrom, Module, parse, walk
from typing import TYPE_CHECKING, Final, final

from tests.conftest import sibling_tree
from tools.lake_terminal.lake_terminal.product import Product
from tools.release_builder import BuildConfiguration, ManifestLoader

from .helpers import check, expect, present

if TYPE_CHECKING:
    from pathlib import Path


@final
class Constants:
    """Group module-level constants.

    Attributes:
        project: Grouped module constant.

    """

    project: Final[Path] = (
        sibling_tree().project.parent.parent / "tools" / "lake_terminal"
    )


def test_manifest_loads_without_a_windows_vm() -> None:
    """The shipped manifest builds macOS and Linux with no extra setup."""
    configuration: BuildConfiguration = ManifestLoader.load(
        manifest=Constants.project / "release-builder.toml",
        project_root=Constants.project,
        platforms=("macos", "linux"),
    )
    expect(actual=configuration.project.version, expected=Product.version)
    expect(actual=configuration.project.name, expected="lake")
    expect(actual=configuration.windows, expected=None)
    check(
        condition=configuration.project.entrypoint.is_file(),
        reason="entry script must exist",
    )


def test_manifest_has_no_second_copy_of_the_version() -> None:
    """The version is read from product.py, not duplicated."""
    text: str = (Constants.project / "release-builder.toml").read_text(
        encoding="utf-8",
    )
    check(condition="version_file" in text, reason="uses version_file")
    check(
        condition=not any(
            line.split(sep="=")[0].strip() == "version"
            for line in text.splitlines()
        ),
        reason="a static version would go stale after the first release",
    )


def test_frozen_build_collects_lazily_imported_modules() -> None:
    """PyInstaller cannot see ``import_module`` targets, so it must be told."""
    configuration: BuildConfiguration = ManifestLoader.load(
        manifest=Constants.project / "release-builder.toml",
        project_root=Constants.project,
        platforms=("macos",),
    )
    args: tuple[str, ...] = configuration.project.pyinstaller_args
    check(
        condition="--collect-submodules" in args and "lake_terminal" in args,
        reason=str(object=args),
    )
    check(condition="--collect-data" in args, reason="dashboard assets")


def test_entry_script_uses_absolute_imports() -> None:
    """A frozen entry script has no package, so relative imports fail."""
    tree: Module = parse(
        source=(Constants.project / "lake_entry.py").read_text(
            encoding="utf-8",
        ),
    )
    imports: list[ImportFrom] = [
        node for node in walk(node=tree) if isinstance(node, ImportFrom)
    ]
    expect(actual=[node.level for node in imports], expected=[0])
    imported: str = present(value=imports[0].module)
    expect(actual=imported, expected="lake_terminal.cli")
