# Copyright (c) 2026 Project contributors.
"""Tests for the self-contained ``pyz`` install mode."""

from __future__ import annotations

from io import StringIO
from json import loads
from sys import executable
from typing import TYPE_CHECKING
from zipfile import ZipFile

import pytest
from district import installer as installer_module
from district.installer import (
    Layout,
    install_launcher,
    main,
    manifest_layout,
    owned_launcher,
    remove_launcher,
    running_archive,
    write_manifest,
)
from district.product import Product

from .helpers import check, expect, present

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    """Create a minimal zip standing in for a ``.pyz`` build.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Archive path.

    """
    path: Path = tmp_path / "dl" / "district.pyz"
    path.parent.mkdir()
    with ZipFile(file=path, mode="w") as bundle:
        bundle.writestr(zinfo_or_arcname="__main__.py", data="print('x')\n")
    return path


@pytest.fixture
def layout(tmp_path: Path, archive: Path) -> Layout:
    """Return a pyz layout rooted in scratch directories.

    Args:
        tmp_path: Pytest temporary directory.
        archive: Source archive.

    Returns:
        The layout.

    """
    return Layout(
        home=tmp_path / "home",
        bin_dir=tmp_path / "bin",
        lib_dir=tmp_path / "lib",
        alias="districtpyz",
        mode="pyz",
        source_root=archive,
        python=executable,
    )


def test_layout_payload_defaults(layout: Layout) -> None:
    """The payload name and path derive from the lib directory.

    Args:
        layout: Installed layout fixture.

    """
    expect(actual=layout.payload, expected="district.pyz")
    expect(
        actual=layout.pyz_path,
        expected=layout.lib_dir / "district.pyz",
    )


def test_pyz_launcher_execs_payload(layout: Layout) -> None:
    """The launcher runs the pinned interpreter on the archive.

    Args:
        layout: Installed layout fixture.

    """
    _ = install_launcher(layout=layout)
    text: str = layout.launcher.read_text(encoding="utf-8")
    check(
        condition=f'exec {executable} {layout.pyz_path} "$@"' in text,
        reason=text,
    )
    check(condition="PYTHONPATH" not in text, reason=text)
    check(condition=owned_launcher(layout=layout), reason="marker")


def test_pyz_install_copies_archive_and_remove_cleans(
    layout: Layout,
    archive: Path,
) -> None:
    """Install copies bytes; remove deletes launcher and payload.

    Args:
        layout: Installed layout fixture.
        archive: Prepared archive path.

    """
    _ = install_launcher(layout=layout)
    expect(
        actual=layout.pyz_path.read_bytes(),
        expected=archive.read_bytes(),
    )
    remove_launcher(layout=layout)
    check(condition=not layout.pyz_path.exists(), reason="payload gone")
    check(condition=not layout.launcher.exists(), reason="launcher gone")
    check(condition=archive.exists(), reason="source archive untouched")


def test_manifest_records_version_and_payload(layout: Layout) -> None:
    """The manifest stores the version and payload for updates.

    Args:
        layout: Installed layout fixture.

    """
    layout.state_dir.mkdir(parents=True)
    write_manifest(layout=layout, keys=frozenset({"launcher"}))
    raw: dict[str, object] = loads(
        s=layout.manifest_path.read_text(encoding="utf-8"),
    )
    expect(actual=raw["version"], expected=Product.version)
    expect(actual=raw["payload"], expected="district.pyz")
    expect(actual=raw["mode"], expected="pyz")
    rebuilt: Layout | None = manifest_layout(home=layout.home)
    rebuilt = present(value=rebuilt)
    expect(actual=rebuilt.mode, expected="pyz")
    expect(actual=rebuilt.payload, expected="district.pyz")


def test_manifest_without_payload_is_tolerated(layout: Layout) -> None:
    """Older manifests without ``payload`` still load.

    Args:
        layout: Installed layout fixture.

    """
    layout.state_dir.mkdir(parents=True)
    layout.manifest_path.write_text(
        data='{"mode": "copy", "alias": "x"}',
        encoding="utf-8",
    )
    rebuilt: Layout | None = manifest_layout(home=layout.home)
    rebuilt = present(value=rebuilt)
    expect(actual=rebuilt.payload, expected="district.pyz")


def test_running_archive_detects_zip_ancestor(archive: Path) -> None:
    """A module path inside a zip resolves to the archive.

    Args:
        archive: Prepared archive path.

    """
    inside: Path = archive / "district" / "installer.py"
    expect(actual=running_archive(anchor=inside), expected=archive)
    expect(
        actual=running_archive(anchor=archive.parent / "x.py"),
        expected=None,
    )


def test_main_auto_selects_pyz(
    tmp_path: Path,
    archive: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running from an archive installs in ``pyz`` mode by default.

    Args:
        tmp_path: Pytest temporary directory.
        archive: Prepared archive path.
        monkeypatch: Pytest monkeypatch fixture.

    """

    def fake_archive(**_: object) -> Path:
        """Return fake archive.

        Args:
            _: _.

        Returns:
            The resulting value.

        """
        return archive

    monkeypatch.setattr(
        target=installer_module,
        name="running_archive",
        value=fake_archive,
    )
    home: Path = tmp_path / "home"
    status: int = main(
        arguments=(
            "install",
            "--home",
            str(object=home),
            "--bin-dir",
            str(object=tmp_path / "bin"),
            "--lib-dir",
            str(object=tmp_path / "lib"),
            "--alias",
            "districtpyz",
            "--preset",
            "minimal",
            "--yes",
            "--quiet",
        ),
        stream=StringIO(),
    )
    expect(actual=status, expected=0)
    manifest: dict[str, object] = loads(
        s=(home / ".district" / "install.json").read_text(
            encoding="utf-8",
        ),
    )
    expect(actual=manifest["mode"], expected="pyz")
    check(
        condition=(tmp_path / "lib" / "district.pyz").is_file(),
        reason="payload installed",
    )


def test_main_rejects_live_mode_from_archive(
    tmp_path: Path,
    archive: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live/copy need a checkout, so they fail from an archive.

    Args:
        tmp_path: Pytest temporary directory.
        archive: Prepared archive path.
        monkeypatch: Pytest monkeypatch fixture.

    """

    def fake_archive(**_: object) -> Path:
        """Return fake archive.

        Args:
            _: _.

        Returns:
            The resulting value.

        """
        return archive

    monkeypatch.setattr(
        target=installer_module,
        name="running_archive",
        value=fake_archive,
    )
    status: int = main(
        arguments=(
            "install",
            "--mode",
            "live",
            "--home",
            str(object=tmp_path / "home"),
            "--yes",
            "--quiet",
        ),
        stream=StringIO(),
    )
    expect(actual=status, expected=2)


def test_main_rejects_pyz_mode_from_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--mode pyz`` needs a running archive.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """

    def no_archive(**_: object) -> Path | None:
        """Return no archive.

        Args:
            _: _.

        Returns:
            The resulting value.

        """
        return None

    monkeypatch.setattr(
        target=installer_module,
        name="running_archive",
        value=no_archive,
    )
    status: int = main(
        arguments=(
            "install",
            "--mode",
            "pyz",
            "--home",
            str(object=tmp_path / "home"),
            "--yes",
            "--quiet",
        ),
        stream=StringIO(),
    )
    expect(actual=status, expected=2)


def test_wizard_mode_key_keeps_pyz(layout: Layout) -> None:
    """The wizard's mode toggle never turns an archive install into live.

    Args:
        layout: Installed layout fixture.

    """
    edited: Layout = installer_module.wizard_edit(layout=layout, key="m")
    expect(actual=edited.mode, expected="pyz")
