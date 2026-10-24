# Copyright (c) 2026 Project contributors.
"""Tests for the District Terminal control-plane installer."""

from __future__ import annotations

from dataclasses import replace
from io import StringIO
from json import loads
from stat import S_IXUSR
from sys import executable
from typing import TYPE_CHECKING, Final, cast

import pytest
from district import installer as installer_module
from district import mcp_registration
from district.installer import (
    Component,
    Constants,
    Installer,
    Layout,
    ManifestData,
    Reporter,
    main,
)
from district.lifecycle import LifecycleInstaller
from district.mcp_registration import McpRegistration

from tests.conftest import sibling_tree

if TYPE_CHECKING:
    from pathlib import Path
WORKSPACE: Final[Path] = sibling_tree().project.parent


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


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """Return a scratch home directory.

    Args:
        tmp_path: Tmp path.

    Returns:
        The resulting value.

    """
    return tmp_path


@pytest.fixture
def layout(home: Path) -> Layout:
    """Return a layout rooted at the scratch home.

    Args:
        home: Home.

    Returns:
        The resulting value.

    """
    return Layout(
        home=home,
        bin_dir=home / ".local" / "bin",
        lib_dir=home / ".local" / "lib",
        alias="testshared",
        mode="live",
        source_root=WORKSPACE,
        python=executable,
    )


@pytest.fixture
def installer(home: Path, layout: Layout) -> Installer:
    """Return an installer bound to the scratch home.

    Args:
        home: Home.
        layout: Layout.

    Returns:
        The resulting value.

    """
    return Installer(
        source_root=layout.source_root,
        home=home,
        reporter=Reporter(colour=False),
    )


def install(
    *,
    installer: Installer,
    layout: Layout,
    keys: frozenset[str],
) -> int:
    """Install the requested components quietly.

    Args:
        installer: Installer.
        layout: Layout.
        keys: Keys.

    Returns:
        The resulting value.

    """
    return installer.install(
        layout=layout,
        selected=keys,
        dry_run=False,
        force=False,
    )


def test_launcher_path(*, layout: Layout) -> None:
    """Resolve the launcher path from bin dir and alias.

    Args:
        layout: Layout.

    """
    expect(actual=layout.launcher, expected=layout.bin_dir / "testshared")


def test_manifest_path(*, layout: Layout) -> None:
    """Resolve the manifest path under ~/.district.

    Args:
        layout: Layout.

    """
    expect(
        actual=layout.manifest_path,
        expected=layout.home / ".district" / "install.json",
    )


def test_import_root_modes(*, layout: Layout) -> None:
    """Resolve the import root per install mode.

    Args:
        layout: Layout.

    """
    expect(actual=layout.import_root, expected=layout.source_root)
    copied: Layout = replace(layout, mode="copy")
    expect(actual=copied.import_root, expected=copied.lib_dir)


@pytest.mark.parametrize(
    "alias",
    ["shared", "sh-ared", "sh.ared", "sh+ared", "sh2"],
)
def test_valid_aliases(*, installer: Installer, alias: str) -> None:
    """Accept documented alias characters.

    Args:
        installer: Installer.
        alias: Alias.

    """
    resolved: Layout = installer.layout(
        alias=alias,
        mode="live",
        bin_dir=None,
        lib_dir=None,
    )
    expect(actual=resolved.alias, expected=alias)


@pytest.mark.parametrize("alias", ["", "-lead", "a/b", "a b", "a;b"])
def test_invalid_aliases(*, installer: Installer, alias: str) -> None:
    """Reject separators and unsafe characters.

    Args:
        installer: Installer.
        alias: Alias.

    """
    with pytest.raises(expected_exception=ValueError, match="Alias"):
        _ = installer.layout(
            alias=alias,
            mode="live",
            bin_dir=None,
            lib_dir=None,
        )


def test_writes_marked_executable(
    *,
    installer: Installer,
    layout: Layout,
) -> None:
    """Write an executable launcher carrying the managed marker.

    Args:
        installer: Installer.
        layout: Layout.

    """
    result: int = install(
        installer=installer,
        layout=layout,
        keys=frozenset({"launcher"}),
    )
    expect(actual=result, expected=0)
    launcher: Path = layout.launcher
    content: str = launcher.read_text(encoding="utf-8")
    check(
        condition=Constants.launcher_marker in content,
        reason="launcher carries the managed marker",
    )
    check(condition=Constants.module in content, reason="module named")
    check(
        condition=str(object=layout.source_root) in content,
        reason="source root embedded",
    )
    check(
        condition=bool(launcher.stat().st_mode & S_IXUSR),
        reason="launcher is executable",
    )


def test_uninstall_preserves_foreign(
    *,
    installer: Installer,
    layout: Layout,
) -> None:
    """Keep a launcher lacking the managed marker on uninstall.

    Args:
        installer: Installer.
        layout: Layout.

    """
    result: int = install(
        installer=installer,
        layout=layout,
        keys=frozenset({"launcher"}),
    )
    expect(actual=result, expected=0)
    _ = layout.launcher.write_text(
        data="#!/bin/sh\necho foreign\n",
        encoding="utf-8",
    )
    expect(actual=installer.uninstall(purge=False, dry_run=False), expected=0)
    check(
        condition=layout.launcher.is_file(),
        reason="foreign launcher preserved",
    )


def test_uninstall_removes_owned(
    *,
    installer: Installer,
    layout: Layout,
) -> None:
    """Remove a managed launcher and the manifest.

    Args:
        installer: Installer.
        layout: Layout.

    """
    result: int = install(
        installer=installer,
        layout=layout,
        keys=frozenset({"launcher"}),
    )
    expect(actual=result, expected=0)
    expect(actual=installer.uninstall(purge=False, dry_run=False), expected=0)
    check(condition=not layout.launcher.exists(), reason="launcher removed")
    check(
        condition=not layout.manifest_path.exists(),
        reason="manifest removed",
    )


def test_zsh_lifecycle(*, installer: Installer, layout: Layout) -> None:
    """Install the lifecycle block referencing the launcher.

    Args:
        installer: Installer.
        layout: Layout.

    """
    keys: frozenset[str] = frozenset({"launcher", "zsh"})
    expect(
        actual=install(installer=installer, layout=layout, keys=keys),
        expected=0,
    )
    zshrc: Path = layout.home / ".zshrc"
    content: str = zshrc.read_text(encoding="utf-8")
    check(
        condition=LifecycleInstaller.begin in content,
        reason="lifecycle block installed",
    )
    check(
        condition=f"{layout.launcher} --lifecycle-start" in content,
        reason="lifecycle invokes the launcher",
    )
    expect(actual=installer.uninstall(purge=False, dry_run=False), expected=0)
    check(
        condition=LifecycleInstaller.begin
        not in zshrc.read_text(encoding="utf-8"),
        reason="lifecycle block removed",
    )


def test_agent_routing(*, installer: Installer, layout: Layout) -> None:
    """Install and remove the automatic agent routing block.

    Args:
        installer: Installer.
        layout: Layout.

    """
    keys: frozenset[str] = frozenset({"launcher", "routing"})
    expect(
        actual=install(installer=installer, layout=layout, keys=keys),
        expected=0,
    )
    zshrc: Path = layout.home / ".zshrc"
    content: str = zshrc.read_text(encoding="utf-8")
    check(
        condition=Constants.routing_begin in content,
        reason="routing block installed",
    )
    check(condition="--is-agent-shell" in content, reason="probe installed")
    check(condition="--agent" in content, reason="agent entry installed")
    expect(actual=installer.uninstall(purge=False, dry_run=False), expected=0)
    check(
        condition=Constants.routing_begin
        not in zshrc.read_text(encoding="utf-8"),
        reason="routing block removed",
    )


def test_agent_hooks(*, installer: Installer, layout: Layout) -> None:
    """Write and remove the Claude hook entries.

    Args:
        installer: Installer.
        layout: Layout.

    """
    keys: frozenset[str] = frozenset({"launcher", "claude"})
    expect(
        actual=install(installer=installer, layout=layout, keys=keys),
        expected=0,
    )
    settings: Path = layout.home / ".claude" / "settings.json"
    content: str = settings.read_text(encoding="utf-8")
    check(condition="--claude-hook" in content, reason="claude hook written")
    check(
        condition=str(object=layout.launcher) in content,
        reason="hook invokes the launcher",
    )
    expect(actual=installer.uninstall(purge=False, dry_run=False), expected=0)
    check(
        condition="--claude-hook" not in settings.read_text(encoding="utf-8"),
        reason="claude hook removed",
    )


def test_manifest_records_components(
    *,
    installer: Installer,
    layout: Layout,
) -> None:
    """Record applied components in the manifest.

    Args:
        installer: Installer.
        layout: Layout.

    """
    keys: frozenset[str] = frozenset({"launcher", "zsh"})
    expect(
        actual=install(installer=installer, layout=layout, keys=keys),
        expected=0,
    )
    manifest: ManifestData = cast(
        typ="ManifestData",
        val=loads(s=layout.manifest_path.read_text(encoding="utf-8")),
    )
    expect(
        actual=set(manifest.get("components", [])),
        expected={"launcher", "zsh"},
    )
    expect(actual=manifest.get("mode"), expected="live")
    expect(actual=manifest.get("alias"), expected="testshared")


def test_dry_run_writes_nothing(
    *,
    installer: Installer,
    layout: Layout,
) -> None:
    """Make no filesystem changes during a dry run.

    Args:
        installer: Installer.
        layout: Layout.

    """
    result: int = installer.install(
        layout=layout,
        selected=frozenset({"launcher"}),
        dry_run=True,
        force=False,
    )
    expect(actual=result, expected=0)
    check(
        condition=not layout.launcher.exists(),
        reason="dry run writes no launcher",
    )
    check(
        condition=not layout.manifest_path.exists(),
        reason="dry run writes no manifest",
    )


def test_minimal_plan_selects_launcher(
    *,
    installer: Installer,
    layout: Layout,
) -> None:
    """Select only the launcher under the minimal preset.

    Args:
        installer: Installer.
        layout: Layout.

    """
    components: tuple[Component, ...] = installer.components(layout=layout)
    buf: StringIO = StringIO()
    _ = Installer(
        source_root=layout.source_root,
        home=layout.home,
        reporter=Reporter(colour=False, stream=buf),
    ).install(
        layout=layout,
        selected=frozenset({"launcher"}),
        dry_run=True,
        force=False,
    )
    out: str = buf.getvalue()
    check(
        condition="would install Launcher" in out,
        reason="plan lists the launcher",
    )
    check(
        condition="would install Zsh" not in out,
        reason="plan excludes unselected components",
    )
    check(
        condition=len(components) > 0,
        reason="component table populated",
    )


def test_paint_without_colour() -> None:
    """Return unstyled text when colour is disabled."""
    reporter: Reporter = Reporter(colour=False)
    expect(actual=reporter.paint(text="x", style="y"), expected="x")


def test_quiet_suppresses_output() -> None:
    """Write nothing when quiet is enabled."""
    buf: StringIO = StringIO()
    reporter: Reporter = Reporter(colour=False, quiet=True, stream=buf)
    reporter.step(title="t", detail="d")
    reporter.done(text="d")
    expect(actual=buf.getvalue(), expected="")


def test_main_status(*, home: Path) -> None:
    """Render the status table for an uninstalled home.

    Args:
        home: Home.

    """
    buf: StringIO = StringIO()
    result: int = main(
        arguments=("status", "--home", str(object=home)),
        stream=buf,
    )
    expect(actual=result, expected=0)
    check(
        condition="District Terminal install status" in buf.getvalue(),
        reason="status header rendered",
    )


def test_main_plan_with_unique_alias(*, home: Path) -> None:
    """Print the plan without changing the filesystem.

    Args:
        home: Home.

    """
    buf: StringIO = StringIO()
    result: int = main(
        arguments=(
            "plan",
            "--home",
            str(object=home),
            "--alias",
            "testshared-plan",
        ),
        stream=buf,
    )
    expect(actual=result, expected=0)
    check(
        condition="would install Launcher" in buf.getvalue(),
        reason="plan lists the launcher",
    )
    check(
        condition=not (home / ".local" / "bin" / "testshared-plan").exists(),
        reason="plan writes nothing",
    )


def test_main_unknown_component_rejected(*, home: Path) -> None:
    """Return failure for unknown component keys.

    Args:
        home: Home.

    """
    result: int = main(
        arguments=(
            "plan",
            "--home",
            str(object=home),
            "--with",
            "nope",
        ),
        stream=StringIO(),
    )
    expect(actual=result, expected=2)


def test_main_install_and_uninstall_dry_run(*, home: Path) -> None:
    """Exercise install and uninstall dispatch without writing changes.

    Args:
        home: Home.

    """
    install_output: StringIO = StringIO()
    install_result: int = main(
        arguments=(
            "install",
            "--home",
            str(object=home),
            "--alias",
            "coverage-shared",
            "--dry-run",
            "--yes",
        ),
        stream=install_output,
    )
    expect(actual=install_result, expected=0)
    check(
        condition="would install Launcher" in install_output.getvalue(),
        reason="dry-run install renders its plan",
    )
    uninstall_output: StringIO = StringIO()
    uninstall_result: int = main(
        arguments=("uninstall", "--home", str(object=home), "--dry-run"),
        stream=uninstall_output,
    )
    expect(actual=uninstall_result, expected=0)


def test_main_implicit_install_and_invalid_alias(*, home: Path) -> None:
    """Exercise implicit install dispatch and installer error handling.

    Args:
        home: Home.

    """
    output: StringIO = StringIO()
    result: int = main(
        arguments=(
            "--home",
            str(object=home),
            "--alias",
            "coverage-shared",
            "--dry-run",
            "--yes",
        ),
        stream=output,
    )
    expect(actual=result, expected=0)
    invalid_output: StringIO = StringIO()
    invalid_result: int = main(
        arguments=(
            "plan",
            "--home",
            str(object=home),
            "--alias",
            "invalid alias",
        ),
        stream=invalid_output,
    )
    expect(actual=invalid_result, expected=2)


def no_cli(**_: object) -> None:
    """Report that the ``claude`` executable is not installed.

    Args:
        **_: Ignored lookup arguments.

    """
    return


def test_mcp_component_registers_and_removes_server(
    *,
    installer: Installer,
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Install and remove the Claude MCP registration as one component.

    Args:
        installer: Installer.
        layout: Layout.
        monkeypatch: Monkeypatch.

    """
    monkeypatch.setattr(
        target=mcp_registration,
        name="which",
        value=no_cli,
    )
    desktop: Path = McpRegistration.claude_desktop_path(home=layout.home)
    desktop.parent.mkdir(parents=True)
    component: Component = next(
        item
        for item in installer_module.components(layout=layout)
        if item.key == "mcp"
    )
    check(condition=component.available, reason="mcp component available")
    check(condition=component.selected, reason="selected when Claude exists")
    expect(
        actual=install(
            installer=installer,
            layout=layout,
            keys=frozenset({"launcher", "mcp"}),
        ),
        expected=0,
    )
    check(
        condition=McpRegistration.installed(home=layout.home),
        reason="registered",
    )
    check(condition=component.installed(layout=layout), reason="reported")
    component.remove(layout=layout) if component.remove else None
    check(
        condition=not McpRegistration.installed(home=layout.home),
        reason="removed",
    )


def test_live_launcher_path_contains_bowman(layout: Layout) -> None:
    """The directory added to ``PYTHONPATH`` must hold ``bowman``.

    Args:
        layout: Live layout rooted at the real ``tools`` directory.

    """
    check(
        condition=(layout.bowman_import_root / "bowman").is_dir(),
        reason=f"bowman missing under {layout.bowman_import_root}",
    )


def test_copy_install_bundles_bowman(layout: Layout) -> None:
    """A copy install carries ``bowman`` next to ``district``.

    Args:
        layout: Base layout rooted at the real ``tools`` directory.

    """
    copied: Layout = replace(layout, mode="copy")
    _ = installer_module.install_launcher(layout=copied)
    check(
        condition=(copied.lib_dir / "bowman").is_dir(),
        reason="bowman not copied",
    )
    check(
        condition=(copied.lib_dir / "district").is_dir(),
        reason="district not copied",
    )
