# Copyright (c) 2026 Project contributors.
"""Behavioral coverage for the standalone extension installer."""

from __future__ import annotations

from argparse import Namespace
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from runpy import run_path
from sys import modules
from typing import TYPE_CHECKING, Final

import pytest

from tests.conftest import sibling_tree

if TYPE_CHECKING:
    from _frozen_importlib import ModuleSpec
    from pathlib import Path
    from types import ModuleType
    from typing import Protocol

    class Editor(Protocol):
        """Describe the editor records produced by the installer module.

        Attributes:
            identifier: Stable CLI/editor identifier.
            label: Human-readable product name.
            command: Discovered executable, when available.
            extensions: User extension directory, when known.

        """

        identifier: str
        label: str
        command: str | None
        extensions: Path | None

    class Installer(Protocol):
        """Describe the installer instances produced by the module."""

        def install(self, *, editor: Editor) -> None:
            """Install into one editor.

            Args:
                editor: Destination editor.

            """

        def uninstall(self, *, editor: Editor) -> None:
            """Uninstall from one editor.

            Args:
                editor: Destination editor.

            """


paths: Final = sibling_tree()


def expect(*, actual: object, expected: object) -> None:
    """Fail the test when two values differ.

    Args:
        actual: Actual.
        expected: Expected.

    Raises:
        AssertionError: When the assertion fails.

    """
    if actual != expected:
        msg: str = f"expected {expected!r}; got {actual!r}"
        raise AssertionError(msg)


def check(*, condition: bool, reason: str) -> None:
    """Fail the test when a condition does not hold.

    Args:
        condition: Condition.
        reason: Reason.

    Raises:
        AssertionError: When the assertion fails.

    """
    if not condition:
        raise AssertionError(reason)


def _installer_module() -> ModuleType:
    """Load this project's standalone installer.

    Returns:
        Loaded installer module.

    Raises:
        RuntimeError: If the module cannot be loaded.

    """
    name: str = (
        f"covered_{paths.project.name.replace(chr(45), chr(95))}_installer"
    )
    loader: SourceFileLoader = SourceFileLoader(
        fullname=name,
        path=str(object=paths.installer),
    )
    spec: ModuleSpec | None = spec_from_loader(name=name, loader=loader)
    if spec is None:
        msg: str = "Unable to load installer module"
        raise RuntimeError(msg)
    module: ModuleType = module_from_spec(spec=spec)
    modules[name] = module
    loader.exec_module(module=module)
    return module


def _which(*, cmd: str) -> str | None:
    """Resolve only the ``code`` executable.

    Args:
        cmd: Command name to resolve.

    Returns:
        Executable path for ``code``; otherwise ``None``.

    """
    return "/usr/bin/code" if cmd == "code" else None


def test_installer_behaviour(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise selection, discovery, installation, and removal.

    Args:
        tmp_path: Temporary project and editor home.
        monkeypatch: Runtime dependency overrides.

    """
    module: ModuleType = _installer_module()
    project: Path = tmp_path / "project"
    source: Path = project / "src"
    source.mkdir(parents=True)
    _ = (source / "package.json").write_text(
        data='{"name":"covered-extension","version":"1.2.3"}',
        encoding="utf-8",
    )
    _ = (source / "extension.js").write_text(
        data="module.exports = {};",
        encoding="utf-8",
    )
    installer: Installer = module.Installer(project=project)
    extensions: Path = tmp_path / "extensions"
    editor: Editor = module.Editor(
        identifier="code",
        label="Code",
        command="code",
        extensions=extensions,
    )
    monkeypatch.setattr(
        target=module,
        name="input",
        value=lambda: "a",
        raising=False,
    )
    expect(
        actual=module.Installer.select(editors=(editor,)),
        expected=(editor,),
    )
    monkeypatch.setattr(
        target=module,
        name="input",
        value=lambda: "1",
        raising=False,
    )
    expect(
        actual=module.Installer.select(editors=(editor,)),
        expected=(editor,),
    )
    monkeypatch.setattr(
        target=module,
        name="input",
        value=lambda: "9",
        raising=False,
    )
    with pytest.raises(
        expected_exception=ValueError,
        match="Invalid editor selection",
    ):
        _ = module.Installer.select(editors=(editor,))
    installer.install(editor=editor)
    target: Path = extensions / "local.covered-extension-1.2.3"
    check(
        condition=(target / "extension.js").is_file(),
        reason="installed payload missing",
    )
    installer.install(editor=editor)
    installer.uninstall(editor=editor)
    check(condition=not target.exists(), reason="stale install remains")
    installer.uninstall(editor=editor)
    missing: Editor = module.Editor(
        identifier="missing",
        label="Missing",
        command=None,
        extensions=None,
    )
    with pytest.raises(
        expected_exception=RuntimeError,
        match="No extension directory",
    ):
        installer.install(editor=missing)
    installer.uninstall(editor=missing)


def test_discover_and_main_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise discovery plus noninteractive main branches.

    Args:
        tmp_path: Temporary home.
        monkeypatch: Runtime dependency overrides.

    """
    module: ModuleType = _installer_module()
    monkeypatch.setattr(
        target=module.Path,
        name="home",
        value=lambda: tmp_path,
    )
    monkeypatch.setattr(target=module, name="which", value=_which)
    editors: tuple[Editor, ...] = module.Installer.discover()
    check(condition=bool(editors), reason="no editors discovered")
    expect(actual=editors[0].identifier, expected="code")
    arguments: Namespace = Namespace(list=True, editor=[], uninstall=False)
    monkeypatch.setattr(
        target=module,
        name="_arguments",
        value=lambda: arguments,
    )
    monkeypatch.setattr(
        target=module.Installer,
        name="discover",
        value=staticmethod(lambda: editors),
    )
    expect(actual=module.main(), expected=0)


def test_main_argument_and_entrypoint_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise real argument parsing and the remaining main branches.

    Args:
        tmp_path: Temporary home.
        monkeypatch: Runtime dependency overrides.

    """
    module: ModuleType = _installer_module()
    monkeypatch.setattr(
        target=module.Path,
        name="home",
        value=lambda: tmp_path,
    )
    monkeypatch.setattr(target=module, name="which", value=_which)
    editors: tuple[Editor, ...] = module.Installer.discover()
    check(condition=bool(editors), reason="no editors discovered")
    monkeypatch.setattr(
        target=modules["sys"],
        name="argv",
        value=["install.py", "--list"],
    )
    expect(actual=module.main(), expected=0)
    monkeypatch.setattr(
        target=module.Installer,
        name="discover",
        value=staticmethod(lambda: ()),
    )
    monkeypatch.setattr(
        target=modules["sys"],
        name="argv",
        value=["install.py"],
    )
    expect(actual=module.main(), expected=1)
    monkeypatch.setattr(
        target=module.Installer,
        name="discover",
        value=staticmethod(lambda: editors),
    )
    monkeypatch.setattr(
        target=modules["sys"],
        name="argv",
        value=["install.py", "--editor", "code"],
    )
    expect(actual=module.main(), expected=0)
    monkeypatch.setattr(
        target=modules["sys"],
        name="argv",
        value=["install.py", "--editor", "code", "--uninstall"],
    )
    expect(actual=module.main(), expected=0)
    monkeypatch.setattr(
        target=modules["sys"],
        name="argv",
        value=["install.py", "--editor", "missing"],
    )
    with pytest.raises(expected_exception=ValueError, match="not detected"):
        _ = module.main()
    monkeypatch.setattr(
        target=modules["sys"],
        name="argv",
        value=["install.py", "--list"],
    )
    with pytest.raises(expected_exception=SystemExit):
        run_path(
            path_name=str(object=paths.installer),
            run_name="__main__",
        )
