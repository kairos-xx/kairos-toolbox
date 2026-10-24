# Copyright (c) 2026 Project contributors.
"""Install, inspect, and remove the District control-plane integration."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib import import_module
from json import dumps, loads
from os import environ, pathsep
from os import name as os_name
from pathlib import Path
from platform import system
from re import Pattern
from re import compile as re_compile
from select import select
from shlex import quote
from shutil import copy2, copytree, ignore_patterns, rmtree, which
from sys import argv, executable, stdin, stdout
from typing import TYPE_CHECKING, Final, Protocol, TypedDict, cast, final
from zipfile import is_zipfile

from .claude import ClaudeHooks
from .claude import Constants as ClaudeConstants
from .codex import CodexHooks
from .codex import Constants as CodexConstants
from .cursor import Constants as CursorConstants
from .cursor import CursorHooks
from .devin import Constants as DevinConstants
from .devin import DevinHooks
from .lifecycle import LifecycleInstaller
from .mcp_registration import McpRegistration
from .process import run
from .product import Product
from .startup import (
    StartupState,
    WebStartup,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from subprocess import CompletedProcess
    from types import ModuleType
    from typing import TextIO
type TerminalAttributes = list[int | bytes | list[bytes]]


@final
class _Termios(Protocol):
    """Describe the POSIX terminal operations used by the wizard.

    Attributes:
        TCSADRAIN: Drain-output terminal update mode.

    """

    TCSADRAIN: int

    def tcgetattr(self, fd: int, /) -> TerminalAttributes:
        """Read terminal attributes.

        Args:
            fd: Terminal file descriptor.

        Returns:
            Current terminal attributes.

        """
        ...

    def tcsetattr(
        self,
        fd: int,
        when: int,
        attributes: TerminalAttributes,
        /,
    ) -> None:
        """Restore terminal attributes.

        Args:
            fd: Terminal file descriptor.
            when: Update timing mode.
            attributes: Terminal attributes to restore.

        """
        ...


class _Tty(Protocol):
    """Describe the POSIX tty operation used by the wizard."""

    def setcbreak(self, fd: int, when: int = 2, /) -> None:
        """Put a terminal into cbreak mode.

        Args:
            fd: Terminal file descriptor.
            when: Update timing mode.

        """
        ...


class Constants:
    """Group immutable installer configuration and terminal formatting.

    Attributes:
        module: Module.
        default_alias: Default alias.
        state_directory: State directory.
        manifest_name: Manifest name.
        launcher_marker: Launcher marker.
        routing_begin: Routing begin.
        routing_end: Routing end.
        alias_pattern: Alias pattern.
        uri_app_name: URI handler app bundle name.
        default_payload: Default archive payload file name.
        url_scheme: Url scheme.
        launch_services: Launch services.
        minimum_python: Minimum python.
        reset: Reset.
        bold: Bold.
        dim: Dim.
        green: Green.
        cyan: Cyan.
        yellow: Yellow.
        red: Red.

    """

    module: Final[str] = "district"
    default_alias: Final[str] = "district"
    state_directory: Final[str] = ".district"
    manifest_name: Final[str] = "install.json"
    default_payload: Final[str] = "district.pyz"
    launcher_marker: Final[str] = "# managed by district"
    routing_begin: Final[str] = "# >>> district automatic agent terminal >>>"
    routing_end: Final[str] = "# <<< district automatic agent terminal <<<"
    alias_pattern: Final[Pattern[str]] = re_compile(
        pattern=r"[A-Za-z0-9][A-Za-z0-9._+-]*",
    )
    uri_app_name: Final[str] = "DistrictURI.app"
    url_scheme: Final[str] = "district"
    launch_services: Final[Path] = (
        Path("/System/Library/Frameworks/CoreServices.framework/Frameworks")
        / "LaunchServices.framework/Support/lsregister"
    )
    minimum_python: Final[tuple[int, int]] = (3, 13)
    reset: Final[str] = "\033[0m"
    bold: Final[str] = "\033[1m"
    dim: Final[str] = "\033[2m"
    green: Final[str] = "\033[32m"
    cyan: Final[str] = "\033[36m"
    yellow: Final[str] = "\033[33m"
    red: Final[str] = "\033[31m"


@dataclass(frozen=True, slots=True, kw_only=True)
class Layout:
    """Resolved paths and interpreter for one installation.

    Attributes:
        home: Home.
        bin_dir: Bin dir.
        lib_dir: Lib dir.
        alias: Alias.
        mode: Mode.
        source_root: Source root; the running archive in ``pyz`` mode.
        python: Python.
        payload: Archive file name under ``lib_dir`` in ``pyz`` mode.

    """

    home: Path
    bin_dir: Path
    lib_dir: Path
    alias: str
    mode: str
    source_root: Path
    python: str
    payload: str = Constants.default_payload

    @property
    def pyz_path(self) -> Path:
        """Return the installed self-contained archive path.

        Returns:
            The resulting value.

        """
        return self.lib_dir / self.payload

    @property
    def launcher(self) -> Path:
        """Return the launcher script path for the selected alias.

        Returns:
            The resulting value.

        """
        suffix: str = ".cmd" if system() == "Windows" else ""
        return self.bin_dir / f"{self.alias}{suffix}"

    @property
    def state_dir(self) -> Path:
        """Return the per-user District Terminal state directory.

        Returns:
            The resulting value.

        """
        return self.home / Constants.state_directory

    @property
    def manifest_path(self) -> Path:
        """Return the installation manifest path.

        Returns:
            The resulting value.

        """
        return self.state_dir / Constants.manifest_name

    @property
    def import_root(self) -> Path:
        """Return the directory placed on PYTHONPATH by the launcher.

        Returns:
            The resulting value.

        """
        return self.lib_dir if self.mode == "copy" else self.source_root

    @property
    def bowman_import_root(self) -> Path:
        """Return the Bowman package import root.

        Returns:
            Bowman import root.

        """
        if self.mode == "copy":
            return self.lib_dir
        return self.source_root

    @property
    def uri_desktop(self) -> Path:
        """Return the Linux desktop URI-handler path.

        Returns:
            Desktop entry path.

        """
        return (
            self.home
            / ".local"
            / "share"
            / "applications"
            / "district-bowman.desktop"
        )

    @property
    def uri_app(self) -> Path:
        """Return the user-level URI handler bundle path.

        Returns:
            The resulting value.

        """
        return self.home / "Applications" / Constants.uri_app_name


class _InstallStep(Protocol):
    """Keyword-only component installation step signature."""

    def __call__(self, *, layout: Layout) -> str:
        """Install one component and return a detail line.

        Args:
            layout: Layout.

        Returns:
            The resulting value.

        """
        ...


class _RemoveStep(Protocol):
    """Keyword-only component removal step signature."""

    def __call__(self, *, layout: Layout) -> None:
        """Remove one component's owned artifacts.

        Args:
            layout: Layout.

        """
        ...


class _StateCheck(Protocol):
    """Keyword-only component state check signature."""

    def __call__(self, *, layout: Layout) -> bool:
        """Return whether one component is currently installed.

        Args:
            layout: Layout.

        Returns:
            The resulting value.

        """
        ...


@dataclass(frozen=True, slots=True)
class Component:
    """One selectable installation unit and its lifecycle handlers.

    Attributes:
        key: Key.
        title: Title.
        summary: Summary.
        available: Available.
        selected: Selected.
        required: Required.
        install: Install.
        remove: Remove.
        installed: Installed.

    """

    key: str
    title: str
    summary: str
    available: bool
    selected: bool
    required: bool
    install: _InstallStep | None
    remove: _RemoveStep | None
    installed: _StateCheck


class Reporter:
    """Emit styled installer output respecting quiet and colour settings."""

    def __init__(
        self,
        *,
        colour: bool,
        quiet: bool = False,
        stream: TextIO | None = None,
    ) -> None:
        """Initialize output styling switches.

        Args:
            colour: Colour.
            quiet: Quiet.
            stream: Stream.

        """
        self._colour: bool = colour
        self._quiet: bool = quiet
        self._stream: TextIO = stdout if stream is None else stream

    def paint(self, *, text: str, style: str) -> str:
        """Wrap text in an ANSI style when colour output is enabled.

        Args:
            text: Text.
            style: Style.

        Returns:
            The resulting value.

        """
        return f"{style}{text}{Constants.reset}" if self._colour else text

    def line(self, *, text: str) -> None:
        """Write one plain line unless quiet output was requested.

        Args:
            text: Text.

        """
        if not self._quiet:
            _ = self._stream.write(text + "\n")

    def step(self, *, title: str, detail: str) -> None:
        """Write one aligned component progress line.

        Args:
            title: Title.
            detail: Detail.

        """
        marker: str = self.paint(text="●", style=Constants.green)
        name: str = self.paint(text=title, style=Constants.bold)
        self.line(text=f"  {marker} {name:<24} {self._dim(text=detail)}")

    def pending(self, *, title: str, detail: str) -> None:
        """Write one aligned skipped/disabled component line.

        Args:
            title: Title.
            detail: Detail.

        """
        marker: str = self.paint(text="○", style=Constants.dim)
        self.line(text=f"  {marker} {title:<24} {self._dim(text=detail)}")

    def note(self, *, text: str) -> None:
        """Write one dim informational line.

        Args:
            text: Text.

        """
        self.line(text=self._dim(text=f"  {text}"))

    def warn(self, *, text: str) -> None:
        """Write one yellow warning line.

        Args:
            text: Text.

        """
        marker: str = self.paint(text="!", style=Constants.yellow)
        self.line(text=f"  {marker} {text}")

    def done(self, *, text: str) -> None:
        """Write one green completion line.

        Args:
            text: Text.

        """
        marker: str = self.paint(
            text="✔",
            style=Constants.green + Constants.bold,
        )
        self.line(text=f"  {marker} {text}")

    def _dim(self, *, text: str) -> str:
        """Dim text when colour output is enabled.

        Args:
            text: Text.

        Returns:
            The resulting value.

        """
        return self.paint(text=text, style=Constants.dim)


def write_launcher(*, layout: Layout) -> None:
    """Write the executable launcher script for the selected alias.

    Args:
        layout: Layout.

    """
    launcher: Path = layout.launcher
    layout.bin_dir.mkdir(parents=True, exist_ok=True)
    if layout.mode == "pyz":
        _write_pyz_launcher(layout=layout)
        return
    if system() == "Windows":
        _ = launcher.write_text(
            data=(
                "@echo off\r\n"
                f"rem managed by district\r\n"
                f'set "PYTHONPATH={layout.import_root};{
                    layout.bowman_import_root
                };%PYTHONPATH%"\r\n'
                f'"{layout.python}" -m {Constants.module} %*\r\n'
            ),
            encoding="utf-8",
        )
        return
    _ = launcher.write_text(
        data=(
            "#!/bin/sh\n"
            f"{Constants.launcher_marker}\n"
            f"PYTHONPATH={
                quote(
                    s=pathsep.join(
                        (
                            str(object=layout.import_root),
                            str(object=layout.bowman_import_root),
                        )
                    )
                )
            } "
            f"exec {quote(s=layout.python)} "
            f'-m {Constants.module} "$@"\n'
        ),
        encoding="utf-8",
    )
    launcher.chmod(mode=0o755)


def _write_pyz_launcher(*, layout: Layout) -> None:
    """Write a launcher that runs the installed archive directly.

    Args:
        layout: Layout.

    """
    launcher: Path = layout.launcher
    if system() == "Windows":
        _ = launcher.write_text(
            data=(
                "@echo off\r\n"
                "rem managed by district\r\n"
                f'"{layout.python}" "{layout.pyz_path}" %*\r\n'
            ),
            encoding="utf-8",
        )
        return
    _ = launcher.write_text(
        data=(
            "#!/bin/sh\n"
            f"{Constants.launcher_marker}\n"
            f"exec {quote(s=layout.python)} "
            f'{quote(s=str(object=layout.pyz_path))} "$@"\n'
        ),
        encoding="utf-8",
    )
    launcher.chmod(mode=0o755)


def _archive_above(*, path: Path) -> Path | None:
    """Return the first enclosing path that is a zip file.

    Args:
        path: Path to walk upwards from.

    Returns:
        The enclosing archive, or None.

    """
    return next(
        (
            candidate
            for candidate in path.parents
            if candidate.is_file() and is_zipfile(filename=candidate)
        ),
        None,
    )


def running_archive(*, anchor: Path | None = None) -> Path | None:
    """Return the ``.pyz`` archive this code is executing from.

    An archive extracts itself into a cache before importing, so the module
    path alone is not enough; the executed script (``sys.argv[0]``) is also
    consulted when no explicit anchor is given.

    Args:
        anchor: Module path to inspect; defaults to this file and argv.

    Returns:
        The enclosing zip archive, or None when running from a checkout.

    """
    if anchor is not None:
        return _archive_above(path=anchor)
    found: Path | None = _archive_above(path=Path(__file__))
    if found is not None:
        return found
    script: Path = Path(argv[0]) if argv else Path()
    if script.is_file() and is_zipfile(filename=script):
        return script.resolve()
    return None


def owned_launcher(*, layout: Layout) -> bool:
    """Return whether the launcher file is one written by this installer.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    launcher: Path = layout.launcher
    if not launcher.is_file():
        return False
    return Constants.launcher_marker in launcher.read_text(
        encoding="utf-8",
        errors="replace",
    )


def install_launcher(*, layout: Layout) -> str:
    """Copy sources when needed and write the launcher script.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    if layout.mode == "pyz":
        layout.lib_dir.mkdir(parents=True, exist_ok=True)
        if layout.source_root.resolve() != layout.pyz_path.resolve():
            _ = copy2(src=layout.source_root, dst=layout.pyz_path)
    if layout.mode == "copy":
        target: Path = layout.lib_dir / "district"
        if target.exists():
            rmtree(path=target)
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = copytree(
            src=layout.source_root / "district",
            dst=target,
            ignore=ignore_patterns(
                "__pycache__",
                "*.pyc",
                ".mypy_cache",
                ".ruff_cache",
            ),
        )
        bowman_source: Path = layout.source_root / "bowman"
        if bowman_source.is_dir():
            bowman_target: Path = layout.lib_dir / "bowman"
            if bowman_target.exists():
                rmtree(path=bowman_target)
            _ = copytree(
                src=bowman_source,
                dst=bowman_target,
                ignore=ignore_patterns(
                    "__pycache__",
                    "*.pyc",
                    ".mypy_cache",
                    ".ruff_cache",
                ),
            )
    write_launcher(layout=layout)
    return str(object=layout.launcher)


def _launcher_installed(*, layout: Layout) -> bool:
    """Return whether the managed launcher exists.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    return owned_launcher(layout=layout)


def remove_launcher(*, layout: Layout) -> None:
    """Remove the managed launcher only when it carries our marker.

    Args:
        layout: Layout.

    """
    launcher: Path = layout.launcher
    if owned_launcher(layout=layout):
        launcher.unlink()
    if layout.mode == "pyz":
        layout.pyz_path.unlink(missing_ok=True)
    if layout.mode == "copy":
        target: Path = layout.lib_dir / "district"
        if target.exists():
            rmtree(path=target)
        bowman_target: Path = layout.lib_dir / "bowman"
        if bowman_target.exists():
            rmtree(path=bowman_target)


def install_tmux(*, layout: Layout) -> str:
    """Install tmux through an available package manager when missing.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    del layout
    present: str | None = which(cmd="tmux")
    if present is not None:
        return present
    if (brew := which(cmd="brew")) is not None:
        _ = run(args=(brew, "install", "tmux"), check=True)
        return "installed with Homebrew"
    managers: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("apt-get", ("apt-get", "install", "-y", "tmux")),
        ("dnf", ("dnf", "install", "-y", "tmux")),
        ("pacman", ("pacman", "-S", "--noconfirm", "tmux")),
    )
    for manager, command in managers:
        if which(cmd=manager) is None:
            continue
        _ = run(args=("sudo", *command), check=True)
        return f"installed with {manager}"
    return "unavailable; install tmux manually"


def tmux_installed(*, layout: Layout) -> bool:
    """Return whether a tmux executable is resolvable.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    del layout
    return which(cmd="tmux") is not None


def install_zsh(*, layout: Layout) -> str:
    """Install the lifecycle block into ~/.zshrc.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    path: Path = layout.home / ".zshrc"
    LifecycleInstaller.install_file(
        path=path,
        command=str(object=layout.launcher),
    )
    return str(object=path)


def install_bash(*, layout: Layout) -> str:
    """Install the lifecycle block into ~/.bashrc.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    path: Path = layout.home / ".bashrc"
    LifecycleInstaller.install_file(
        path=path,
        command=str(object=layout.launcher),
    )
    return str(object=path)


def _powershell_profile(*, home: Path) -> Path:
    """Return the default current-user PowerShell profile path.

    Args:
        home: Home.

    Returns:
        The resulting value.

    """
    return (
        home / "Documents" / "PowerShell" / "Microsoft.PowerShell_profile.ps1"
    )


def install_powershell(*, layout: Layout) -> str:
    """Install the lifecycle block into the PowerShell profile.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    path: Path = _powershell_profile(home=layout.home)
    LifecycleInstaller.install_powershell(
        path=path,
        command=str(object=layout.launcher),
    )
    return str(object=path)


def _lifecycle_installed(*, path: Path) -> bool:
    """Return whether a startup file contains the lifecycle marker.

    Args:
        path: Path.

    Returns:
        The resulting value.

    """
    return path.is_file() and LifecycleInstaller.begin in path.read_text(
        encoding="utf-8",
        errors="replace",
    )


def zsh_installed(*, layout: Layout) -> bool:
    """Return whether ~/.zshrc carries the lifecycle block.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    return _lifecycle_installed(path=layout.home / ".zshrc")


def bash_installed(*, layout: Layout) -> bool:
    """Return whether ~/.bashrc carries the lifecycle block.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    return _lifecycle_installed(path=layout.home / ".bashrc")


def powershell_installed(*, layout: Layout) -> bool:
    """Return whether the PowerShell profile carries the lifecycle block.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    return _lifecycle_installed(
        path=_powershell_profile(home=layout.home),
    )


def remove_zsh(*, layout: Layout) -> None:
    """Remove the owned lifecycle block from ~/.zshrc.

    Args:
        layout: Layout.

    """
    LifecycleInstaller.remove_file(path=layout.home / ".zshrc")


def remove_bash(*, layout: Layout) -> None:
    """Remove the owned lifecycle block from ~/.bashrc.

    Args:
        layout: Layout.

    """
    LifecycleInstaller.remove_file(path=layout.home / ".bashrc")


def remove_powershell(*, layout: Layout) -> None:
    """Remove the owned lifecycle block from the PowerShell profile.

    Args:
        layout: Layout.

    """
    LifecycleInstaller.remove_file(
        path=_powershell_profile(home=layout.home),
    )


def routing_block(*, launcher: Path) -> str:
    """Return a POSIX-shell automatic agent routing block.

    Args:
        launcher: Launcher.

    Returns:
        The resulting value.

    """
    command: str = quote(s=str(object=launcher))
    return (
        f"{Constants.routing_begin}\n"
        'if [ -n "${PS1:-}" ] && [ -z "${DISTRICT_SESSION_ID:-}" ] && '
        '[ -z "${DISTRICT_AUTO_AGENT_ACTIVE:-}" ]; then\n'
        f"  if {command} --is-agent-shell >/dev/null 2>&1; then\n"
        "    export DISTRICT_AUTO_AGENT_ACTIVE=1\n"
        f"    exec {command} --agent\n"
        "  fi\n"
        "fi\n"
        f"{Constants.routing_end}\n"
    )


def install_block(*, path: Path, block: str, begin: str, end: str) -> None:
    """Install or replace one marker-delimited block in a startup file.

    Args:
        path: Path.
        block: Block.
        begin: Begin.
        end: End.

    """
    remove_block(path=path, begin=begin, end=end)
    existing: str = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        data=existing.rstrip() + "\n\n" + block,
        encoding="utf-8",
    )


def remove_block(*, path: Path, begin: str, end: str) -> None:
    """Remove one marker-delimited block from a startup file.

    Args:
        path: Path.
        begin: Begin.
        end: End.

    """
    if not path.is_file():
        return
    existing: str = path.read_text(encoding="utf-8")
    start: int = existing.find(begin)
    finish: int = existing.find(end)
    if start < 0 or finish < start:
        return
    finish += len(end)
    cleaned: str = (
        existing[:start].rstrip() + "\n" + existing[finish:].lstrip("\n")
    ).strip()
    _ = path.write_text(
        data=(cleaned + "\n") if cleaned else "",
        encoding="utf-8",
    )


def routing_files(*, layout: Layout) -> tuple[Path, ...]:
    """Return the POSIX startup files that receive the routing block.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    return (layout.home / ".zshrc", layout.home / ".bashrc")


def install_routing(*, layout: Layout) -> str:
    """Install automatic agent routing in the POSIX startup files.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    block: str = routing_block(launcher=layout.launcher)
    for path in routing_files(layout=layout):
        install_block(
            path=path,
            block=block,
            begin=Constants.routing_begin,
            end=Constants.routing_end,
        )
    return " · ".join(
        str(object=path) for path in routing_files(layout=layout)
    )


def routing_installed(*, layout: Layout) -> bool:
    """Return whether the routing block exists in a POSIX startup file.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    return any(
        path.is_file()
        and Constants.routing_begin
        in path.read_text(encoding="utf-8", errors="replace")
        for path in routing_files(layout=layout)
    )


def remove_routing(*, layout: Layout) -> None:
    """Remove the routing block from every POSIX startup file.

    Args:
        layout: Layout.

    """
    for path in routing_files(layout=layout):
        remove_block(
            path=path,
            begin=Constants.routing_begin,
            end=Constants.routing_end,
        )


def install_claude(*, layout: Layout) -> str:
    """Install the Claude Code hook entries.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    return str(
        object=ClaudeHooks.install(
            home=layout.home,
            launcher=layout.launcher,
        ),
    )


def install_codex(*, layout: Layout) -> str:
    """Install the Codex hook entries.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    return str(
        object=CodexHooks.install(
            home=layout.home,
            launcher=layout.launcher,
        ),
    )


def install_cursor(*, layout: Layout) -> str:
    """Install the Cursor hook entries.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    return str(
        object=CursorHooks.install(
            home=layout.home,
            launcher=layout.launcher,
        ),
    )


def install_devin(*, layout: Layout) -> str:
    """Install the Devin hook entries.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    return str(
        object=DevinHooks.install(
            home=layout.home,
            launcher=layout.launcher,
        ),
    )


def hook_installed(*, path: Path, marker: str) -> bool:
    """Return whether an agent config file contains our hook command.

    Args:
        path: Path.
        marker: Marker.

    Returns:
        The resulting value.

    """
    return path.is_file() and marker in path.read_text(
        encoding="utf-8",
        errors="replace",
    )


def install_mcp(*, layout: Layout) -> str:
    """Register the District MCP server with Claude Desktop and Claude Code.

    Args:
        layout: Layout.

    Returns:
        The edited configuration files, or a note when the CLI was used.

    """
    edited: tuple[Path, ...] = McpRegistration.install(
        home=layout.home,
        launcher=layout.launcher,
    )
    return ", ".join(map(str, edited)) or "claude mcp (user scope)"


def remove_mcp(*, layout: Layout) -> None:
    """Remove the District MCP server registration from Claude clients.

    Args:
        layout: Layout.

    """
    McpRegistration.uninstall(home=layout.home)


def mcp_installed(*, layout: Layout) -> bool:
    """Return whether a Claude client has the District MCP server registered.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    return McpRegistration.installed(home=layout.home)


def claude_installed(*, layout: Layout) -> bool:
    """Return whether the Claude hook is installed.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    return hook_installed(
        path=ClaudeHooks.config_path(home=layout.home),
        marker=ClaudeConstants.command_marker,
    )


def codex_installed(*, layout: Layout) -> bool:
    """Return whether the Codex hook is installed.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    return hook_installed(
        path=CodexHooks.config_path(home=layout.home),
        marker=CodexConstants.command_marker,
    )


def cursor_installed(*, layout: Layout) -> bool:
    """Return whether the Cursor hook is installed.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    return hook_installed(
        path=CursorHooks.config_path(home=layout.home),
        marker=CursorConstants.command_marker,
    )


def devin_installed(*, layout: Layout) -> bool:
    """Return whether the Devin hook is installed.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    return hook_installed(
        path=DevinHooks.config_path(home=layout.home),
        marker=DevinConstants.command_marker,
    )


def remove_claude(*, layout: Layout) -> None:
    """Remove the Claude hook entries.

    Args:
        layout: Layout.

    """
    ClaudeHooks.uninstall(home=layout.home)


def remove_codex(*, layout: Layout) -> None:
    """Remove the Codex hook entries.

    Args:
        layout: Layout.

    """
    CodexHooks.uninstall(home=layout.home)


def remove_cursor(*, layout: Layout) -> None:
    """Remove the Cursor hook entries.

    Args:
        layout: Layout.

    """
    CursorHooks.uninstall(home=layout.home)


def remove_devin(*, layout: Layout) -> None:
    """Remove the Devin hook entries.

    Args:
        layout: Layout.

    """
    DevinHooks.uninstall(home=layout.home)


def _install_web(*, layout: Layout) -> str:
    """Enable the per-user web dashboard startup entry.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    state: StartupState = WebStartup(home=layout.home).enable(
        command=(str(object=layout.launcher), "--web", "--no-browser"),
    )
    return f"{state.mechanism} · {state.location}"


def _web_installed(*, layout: Layout) -> bool:
    """Return whether the web dashboard startup entry exists.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    return WebStartup(home=layout.home).state().enabled


def _remove_web(*, layout: Layout) -> None:
    """Disable and remove the web dashboard startup entry.

    Args:
        layout: Layout.

    """
    _ = WebStartup(home=layout.home).disable()


def install_uri_handler(*, layout: Layout) -> str:
    """Register the district URI scheme with the native OS mechanism.

    Args:
        layout: Layout.

    Returns:
        Registered handler location.

    """
    platform_name: str = system()
    if platform_name == "Darwin":
        return _install_macos_uri_handler(layout=layout)
    if platform_name == "Linux":
        return _install_linux_uri_handler(layout=layout)
    if platform_name == "Windows":
        return _install_windows_uri_handler(layout=layout)
    return "unsupported"


def handler_command(*, layout: Layout, uri_argument: str) -> str:
    """Return the command used by an OS URI registration.

    The installed launcher already knows how to start District Terminal in
    every install mode (a checkout, a copy, or a ``.pyz``), so the handler
    simply
    calls it instead of rebuilding import paths.

    Args:
        layout: Layout.
        uri_argument: Platform URI placeholder.

    Returns:
        Handler command.

    """
    if system() == "Windows":
        return (
            f'cmd.exe /d /s /c ""{layout.launcher}" --handle-uri '
            f'"{uri_argument}""'
        )
    return (
        f"{quote(s=str(object=layout.launcher))} --handle-uri {uri_argument}"
    )


def _install_macos_uri_handler(*, layout: Layout) -> str:
    """Register a macOS application bundle for district URIs.

    Args:
        layout: Resolved installation layout.

    Returns:
        The created application bundle path.

    """
    app: Path = layout.uri_app
    if app.exists():
        rmtree(path=app)
    app.parent.mkdir(parents=True, exist_ok=True)
    shell_command: str = handler_command(
        layout=layout,
        uri_argument="",
    )
    apple_command: str = shell_command.replace("\\", "\\\\").replace(
        '"',
        '\\"',
    )
    script: str = (
        "on open location theURL\n"
        f'  do shell script "{apple_command}" & quoted form of theURL\n'
        "end open location\n"
    )
    layout.state_dir.mkdir(parents=True, exist_ok=True)
    source: Path = layout.state_dir / "uri-handler.applescript"
    _ = source.write_text(data=script, encoding="utf-8")
    _ = run(
        args=(
            "/usr/bin/osacompile",
            "-o",
            str(object=app),
            str(object=source),
        ),
        check=True,
    )
    source.unlink()
    plist_path: Path = app / "Contents" / "Info.plist"
    additions: tuple[str, ...] = (
        "Add :CFBundleURLTypes array",
        "Add :CFBundleURLTypes:0 dict",
        "Add :CFBundleURLTypes:0:CFBundleURLName string District Bowman",
        "Add :CFBundleURLTypes:0:CFBundleURLSchemes array",
        "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 "
        f"string {Constants.url_scheme}",
    )
    for entry in additions:
        _ = run(
            args=(
                "/usr/libexec/PlistBuddy",
                "-c",
                entry,
                str(object=plist_path),
            ),
            check=True,
        )
    if Constants.launch_services.is_file():
        _ = run(
            args=(
                str(object=Constants.launch_services),
                "-f",
                str(object=app),
            ),
            check=False,
        )
    return str(object=app)


def _install_linux_uri_handler(*, layout: Layout) -> str:
    """Register an XDG desktop handler for district URIs.

    Args:
        layout: Resolved installation layout.

    Returns:
        The created desktop entry path.

    """
    desktop: Path = layout.uri_desktop
    desktop.parent.mkdir(parents=True, exist_ok=True)
    command: str = handler_command(layout=layout, uri_argument="%u")
    _ = desktop.write_text(
        data=(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=District Terminal Bowman\n"
            f"Exec={command}\n"
            "NoDisplay=true\n"
            f"MimeType=x-scheme-handler/{Constants.url_scheme};\n"
        ),
        encoding="utf-8",
    )
    updater: str | None = which(cmd="update-desktop-database")
    if updater is not None:
        _ = run(args=(updater, str(object=desktop.parent)), check=False)
    xdg_mime: str | None = which(cmd="xdg-mime")
    if xdg_mime is not None:
        _ = run(
            args=(
                xdg_mime,
                "default",
                desktop.name,
                f"x-scheme-handler/{Constants.url_scheme}",
            ),
            check=False,
        )
    return str(object=desktop)


def _install_windows_uri_handler(*, layout: Layout) -> str:
    """Register a per-user Windows URL Protocol handler.

    Args:
        layout: Resolved installation layout.

    Returns:
        The registered registry root path.

    """
    root: str = rf"HKCU\Software\Classes\{Constants.url_scheme}"
    command: str = handler_command(layout=layout, uri_argument="%1")
    entries: tuple[tuple[str, str, str], ...] = (
        (root, "", f"URL:{Constants.url_scheme}"),
        (root, "URL Protocol", ""),
        (root + r"\shell\open\command", "", command),
    )
    for key, name, value in entries:
        args: tuple[str, ...] = (
            "reg.exe",
            "ADD",
            key,
            "/ve" if not name else "/v",
        )
        if name:
            args += (name,)
        args += ("/d", value, "/f")
        _ = run(args=args, check=True)
    return root


def uri_handler_installed(*, layout: Layout) -> bool:
    """Return whether the platform URI handler is installed.

    Args:
        layout: Resolved installation layout.

    Returns:
        Whether a URI handler is currently registered.

    """
    if layout.uri_app.exists():
        return True
    platform_name: str = system()
    if platform_name == "Darwin":
        return False
    if platform_name == "Linux":
        return layout.uri_desktop.is_file()
    if platform_name == "Windows":
        root: str = rf"HKCU\Software\Classes\{Constants.url_scheme}"
        return (
            run(
                args=("reg.exe", "QUERY", root),
                check=False,
                capture_output=True,
            ).returncode
            == 0
        )
    return False


def remove_uri_handler(*, layout: Layout) -> None:
    """Unregister the native District Terminal URI handler.

    Args:
        layout: Resolved installation layout.

    """
    app: Path = layout.uri_app
    if app.exists() and system() != "Darwin":
        rmtree(path=app)
    platform_name: str = system()
    if platform_name == "Darwin":
        if app.exists() and Constants.launch_services.is_file():
            _ = run(
                args=(
                    str(object=Constants.launch_services),
                    "-u",
                    str(object=app),
                ),
                check=False,
            )
        if app.exists():
            rmtree(path=app)
        return
    if platform_name == "Linux":
        layout.uri_desktop.unlink(missing_ok=True)
        return
    if platform_name == "Windows":
        root: str = rf"HKCU\Software\Classes\{Constants.url_scheme}"
        _ = run(args=("reg.exe", "DELETE", root, "/f"), check=False)


def components(*, layout: Layout) -> tuple[Component, ...]:
    """Build the ordered component table for one layout.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    posix: bool = system() != "Windows"
    agent_dirs: dict[str, Path] = {
        "claude": layout.home / ".claude",
        "codex": layout.home / ".codex",
        "cursor": layout.home / ".cursor",
        "devin": layout.home / ".config" / "devin",
    }
    return (
        Component(
            key="launcher",
            title="Launcher",
            summary=str(object=layout.launcher),
            available=True,
            selected=True,
            required=True,
            install=install_launcher,
            remove=remove_launcher,
            installed=_launcher_installed,
        ),
        Component(
            key="tmux",
            title="tmux backend",
            summary=(
                "persistent session backend"
                if posix
                else "ConPTY backend on Windows"
            ),
            available=posix,
            selected=posix,
            required=False,
            install=install_tmux,
            remove=None,
            installed=tmux_installed,
        ),
        Component(
            key="zsh",
            title="Zsh lifecycle",
            summary=str(object=layout.home / ".zshrc"),
            available=posix,
            selected=posix,
            required=False,
            install=install_zsh,
            remove=remove_zsh,
            installed=zsh_installed,
        ),
        Component(
            key="bash",
            title="Bash lifecycle",
            summary=str(object=layout.home / ".bashrc"),
            available=posix,
            selected=posix,
            required=False,
            install=install_bash,
            remove=remove_bash,
            installed=bash_installed,
        ),
        Component(
            key="powershell",
            title="PowerShell lifecycle",
            summary=str(object=_powershell_profile(home=layout.home)),
            available=True,
            selected=system() == "Windows",
            required=False,
            install=install_powershell,
            remove=remove_powershell,
            installed=powershell_installed,
        ),
        Component(
            key="routing",
            title="Agent routing",
            summary="interactive agent shells enter district --agent",
            available=posix,
            selected=posix,
            required=False,
            install=install_routing,
            remove=remove_routing,
            installed=routing_installed,
        ),
        Component(
            key="claude",
            title="Claude hooks",
            summary=str(
                object=ClaudeHooks.config_path(home=layout.home),
            ),
            available=True,
            selected=agent_dirs["claude"].exists(),
            required=False,
            install=install_claude,
            remove=remove_claude,
            installed=claude_installed,
        ),
        Component(
            key="mcp",
            title="Claude MCP server",
            summary=(
                f"{McpRegistration.claude_desktop_path(home=layout.home)}"
                " · claude mcp (user scope)"
            ),
            available=True,
            selected=(
                agent_dirs["claude"].exists()
                or McpRegistration.claude_desktop_path(
                    home=layout.home,
                ).parent.exists()
            ),
            required=False,
            install=install_mcp,
            remove=remove_mcp,
            installed=mcp_installed,
        ),
        Component(
            key="codex",
            title="Codex hooks",
            summary=str(object=CodexHooks.config_path(home=layout.home)),
            available=True,
            selected=agent_dirs["codex"].exists(),
            required=False,
            install=install_codex,
            remove=remove_codex,
            installed=codex_installed,
        ),
        Component(
            key="cursor",
            title="Cursor hooks",
            summary=str(object=CursorHooks.config_path(home=layout.home)),
            available=True,
            selected=agent_dirs["cursor"].exists(),
            required=False,
            install=install_cursor,
            remove=remove_cursor,
            installed=cursor_installed,
        ),
        Component(
            key="devin",
            title="Devin hooks",
            summary=str(object=DevinHooks.config_path(home=layout.home)),
            available=True,
            selected=agent_dirs["devin"].exists(),
            required=False,
            install=install_devin,
            remove=remove_devin,
            installed=devin_installed,
        ),
        Component(
            key="web",
            title="Web autostart",
            summary="dashboard at login · district --web --no-browser",
            available=True,
            selected=False,
            required=False,
            install=_install_web,
            remove=_remove_web,
            installed=_web_installed,
        ),
        Component(
            key="uri",
            title="Bowman URI handler",
            summary=f"{Constants.url_scheme}:// native OS handler",
            available=True,
            selected=True,
            required=False,
            install=install_uri_handler,
            remove=remove_uri_handler,
            installed=uri_handler_installed,
        ),
    )


def windows_root(*, home: Path) -> Path:
    """Return the per-user Windows installation directory.

    Args:
        home: Home.

    Returns:
        The resulting value.

    """
    base: Path = Path(
        environ.get(
            "LOCALAPPDATA",
            str(object=home / "AppData" / "Local"),
        ),
    )
    return base / "district"


def default_bin_dir(*, home: Path) -> Path:
    """Return the default per-user launcher directory.

    Args:
        home: Home.

    Returns:
        The resulting value.

    """
    if system() == "Windows":
        return windows_root(home=home) / "bin"
    return home / ".local" / "bin"


def default_lib_dir(*, home: Path) -> Path:
    """Return the default frozen-copy package directory.

    Args:
        home: Home.

    Returns:
        The resulting value.

    """
    if system() == "Windows":
        return windows_root(home=home) / "lib"
    return home / ".local" / "lib"


def validated_alias(*, alias: str) -> str:
    """Validate a requested launcher basename.

    Args:
        alias: Alias.

    Returns:
        The resulting value.

    Raises:
        ValueError: When the operation cannot complete.

    """
    if Constants.alias_pattern.fullmatch(string=alias) is None:
        msg: str = (
            "Alias must be a command basename containing only letters, "
            "digits, '.', '_', '+', or '-'."
        )
        raise ValueError(msg)
    return alias


class ManifestData(TypedDict, total=False):
    """Persisted installation manifest fields.

    Attributes:
        schema: Schema.
        installed_at: Installed at.
        mode: Mode.
        alias: Alias.
        bin_dir: Bin dir.
        lib_dir: Lib dir.
        source: Source.
        python: Python.
        components: Components.
        version: Installed District Terminal version.
        payload: Archive file name in ``pyz`` mode.

    """

    schema: int
    installed_at: str
    mode: str
    alias: str
    bin_dir: str
    lib_dir: str
    source: str
    python: str
    components: list[str]
    version: str
    payload: str


def read_manifest(*, layout: Layout) -> ManifestData:
    """Read the installation manifest when present.

    Args:
        layout: Layout.

    Returns:
        The resulting value.

    """
    path: Path = layout.manifest_path
    if not path.is_file():
        return ManifestData()
    try:
        data: object = loads(s=path.read_text(encoding="utf-8"))
    except ValueError:
        return ManifestData()
    if not isinstance(data, dict):
        return ManifestData()
    return cast(typ="ManifestData", val=data)


def write_manifest(
    *,
    layout: Layout,
    keys: frozenset[str],
    version: str | None = None,
) -> None:
    """Write the installation manifest describing the applied layout.

    Args:
        layout: Layout.
        keys: Keys.
        version: Installed version; defaults to the running version.

    """
    layout.state_dir.mkdir(parents=True, exist_ok=True)
    _ = layout.manifest_path.write_text(
        data=dumps(
            obj={
                "schema": 2,
                "installed_at": datetime.now(tz=UTC).isoformat(),
                "mode": layout.mode,
                "alias": layout.alias,
                "bin_dir": str(object=layout.bin_dir),
                "lib_dir": str(object=layout.lib_dir),
                "source": str(object=layout.source_root),
                "python": layout.python,
                "components": sorted(keys),
                "version": version or Product.version,
                "payload": layout.payload,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def manifest_layout(*, home: Path) -> Layout | None:
    """Rebuild a layout from a previously written manifest.

    Args:
        home: Home.

    Returns:
        The resulting value.

    """
    probe: Layout = Layout(
        home=home,
        bin_dir=default_bin_dir(home=home),
        lib_dir=default_lib_dir(home=home),
        alias=Constants.default_alias,
        mode="live",
        source_root=Path.cwd(),
        python=executable,
    )
    data: ManifestData = read_manifest(layout=probe)
    if not data:
        return None
    return Layout(
        home=home,
        bin_dir=Path(str(object=data.get("bin_dir", probe.bin_dir))),
        lib_dir=Path(str(object=data.get("lib_dir", probe.lib_dir))),
        alias=str(object=data.get("alias", probe.alias)),
        mode=str(object=data.get("mode", probe.mode)),
        source_root=Path(str(object=data.get("source", probe.source_root))),
        python=str(object=data.get("python", probe.python)),
        payload=str(object=data.get("payload", probe.payload)),
    )


def read_key() -> str:
    """Read one keypress, translating arrow escape sequences.

    Returns:
        The resulting value.

    """
    if os_name == "nt":
        try:
            msvcrt: ModuleType = import_module(name="msvcrt")
        except ImportError:
            return ""
        first: str = msvcrt.getwch()
        if first in {"\x00", "\xe0"}:
            second: str = msvcrt.getwch()
            return {"P": "down", "H": "up"}.get(second, "")
        return first
    if not stdin.isatty():
        return stdin.read(1)
    module: _Termios = cast(
        typ="_Termios",
        val=import_module(name="termios"),
    )
    tty: _Tty = cast(
        typ="_Tty",
        val=import_module(name="tty"),
    )
    descriptor: int = stdin.fileno()
    saved: TerminalAttributes = module.tcgetattr(descriptor)
    try:
        tty.setcbreak(descriptor)
        char: str = stdin.read(1)
        if char != "\x1b":
            return char
        sequence: str = char
        ready, _, _ = select([stdin], [], [], 0.02)
        while ready and len(sequence) < len("\x1b[B"):
            sequence += stdin.read(1)
            ready, _, _ = select([stdin], [], [], 0.02)
        return {"\x1b[A": "up", "\x1b[B": "down"}.get(sequence, "escape")
    finally:
        module.tcsetattr(descriptor, module.TCSADRAIN, saved)


def wizard_frame(
    *,
    layout: Layout,
    rows: tuple[Component, ...],
    selected: frozenset[str],
    cursor: int,
) -> str:
    """Render one wizard checklist frame.

    Args:
        layout: Layout.
        rows: Rows.
        selected: Selected.
        cursor: Cursor.

    Returns:
        The resulting value.

    """
    rule: str = "─" * 62
    lines: list[str] = [
        "",
        f"  {Constants.bold}District Terminal install{Constants.reset} "
        f"{Constants.dim}· mode {layout.mode} · alias {layout.alias} "
        f"· {layout.bin_dir}{Constants.reset}",
        f"  {rule}",
    ]
    for index, component in enumerate(iterable=rows):
        pointer: str = "▸" if index == cursor else " "
        mark: str = "●" if component.key in selected else "○"
        colour: str = (
            Constants.cyan if component.key in selected else Constants.dim
        )
        lines.append(
            f"   {pointer} {colour}{mark}{Constants.reset}  "
            f"{component.title:<22} "
            f"{Constants.dim}{component.summary}{Constants.reset}",
        )
    lines.extend(
        (
            f"  {rule}",
            f"  {Constants.dim}↑/↓ or j/k move · space toggle · "
            f"a alias · m mode · enter install · q quit{Constants.reset}",
            "",
        ),
    )
    return "\n".join(lines)


def prompt_alias(*, current: str) -> str:
    """Prompt for a launcher alias outside raw input mode.

    Args:
        current: Current.

    Returns:
        The resulting value.

    """
    try:
        value: str = input(f"  alias [{current}]: ").strip()
    except EOFError:
        return current
    if not value:
        return current
    try:
        return validated_alias(alias=value)
    except ValueError as error:
        _ = stdout.write(f"  {Constants.yellow}{error}{Constants.reset}\n")
        return current


def wizard_toggle(*, component: Component, selected: set[str]) -> None:
    """Toggle one non-required component in the selection set.

    Args:
        component: Component.
        selected: Selected.

    """
    if component.required:
        return
    if component.key in selected:
        selected.discard(component.key)
    else:
        selected.add(component.key)


def wizard_edit(*, layout: Layout, key: str) -> Layout:
    """Apply an alias or mode edit key to the pending layout.

    Args:
        layout: Layout.
        key: Key.

    Returns:
        The resulting value.

    """
    if key == "a":
        return replace(
            layout,
            alias=prompt_alias(current=layout.alias),
        )
    if layout.mode == "pyz":
        return layout
    return replace(
        layout,
        mode="copy" if layout.mode == "live" else "live",
    )


def wizard(
    *,
    layout: Layout,
    components: tuple[Component, ...],
) -> tuple[Layout, frozenset[str]] | None:
    """Run the interactive checklist wizard.

    Args:
        layout: Layout.
        components: Components.

    Returns:
        The resulting value.

    """
    selected: set[str] = {
        component.key
        for component in components
        if component.selected and component.available
    }
    visible: list[Component] = [
        component for component in components if component.available
    ]
    cursor: int = 0
    current: Layout = layout
    while True:
        frame: str = wizard_frame(
            layout=current,
            rows=tuple(visible),
            selected=frozenset(selected),
            cursor=cursor,
        )
        _ = stdout.write("\033[2J\033[H" + frame)
        stdout.flush()
        key: str = read_key()
        if key in {"down", "j"}:
            cursor = min(cursor + 1, len(visible) - 1)
        elif key in {"up", "k"}:
            cursor = max(cursor - 1, 0)
        elif key == " ":
            wizard_toggle(component=visible[cursor], selected=selected)
        elif key in {"a", "m"}:
            current = wizard_edit(layout=current, key=key)
        elif key in {"\r", "\n"}:
            selected.add("launcher")
            return current, frozenset(selected)
        elif key in {"q", "Q", "escape", "\x03"}:
            return None


class Installer:
    """Coordinate layout resolution, installation, removal, and status."""

    def __init__(
        self,
        *,
        source_root: Path,
        home: Path,
        reporter: Reporter,
    ) -> None:
        """Initialize installer context.

        Args:
            source_root: Source root.
            home: Home.
            reporter: Reporter.

        """
        self._source_root: Path = source_root
        self._home: Path = home
        self._reporter: Reporter = reporter

    @staticmethod
    def components(*, layout: Layout) -> tuple[Component, ...]:
        """Return the component table for a layout.

        Args:
            layout: Layout.

        Returns:
            The resulting value.

        """
        return components(layout=layout)

    def layout(
        self,
        *,
        alias: str | None,
        mode: str,
        bin_dir: Path | None,
        lib_dir: Path | None,
    ) -> Layout:
        """Resolve the effective layout for the requested options.

        Args:
            alias: Alias.
            mode: Mode.
            bin_dir: Bin dir.
            lib_dir: Lib dir.

        Returns:
            The resulting value.

        """
        previous: Layout | None = manifest_layout(home=self._home)
        return Layout(
            home=self._home,
            bin_dir=(
                bin_dir
                if bin_dir is not None
                else (
                    previous.bin_dir
                    if previous is not None
                    else default_bin_dir(home=self._home)
                )
            ),
            lib_dir=(
                lib_dir
                if lib_dir is not None
                else (
                    previous.lib_dir
                    if previous is not None
                    else default_lib_dir(home=self._home)
                )
            ),
            alias=validated_alias(
                alias=(
                    alias
                    if alias is not None
                    else (
                        previous.alias
                        if previous is not None
                        else Constants.default_alias
                    )
                ),
            ),
            mode=mode,
            source_root=self._source_root,
            python=self.resolve_python(),
        )

    def install(
        self,
        *,
        layout: Layout,
        selected: frozenset[str],
        dry_run: bool,
        force: bool,
    ) -> int:
        """Apply the selected components and record the manifest.

        Args:
            layout: Layout.
            selected: Selected.
            dry_run: Dry run.
            force: Force.

        Returns:
            The resulting value.

        Raises:
            RuntimeError: When the operation cannot complete.

        """
        if not self._supported_platform():
            msg: str = "District Terminal supports macOS, Linux, and Windows."
            raise RuntimeError(msg)
        self.check_conflict(layout=layout, force=force)
        applied: list[str] = []
        for component in self.components(layout=layout):
            if not component.available:
                continue
            if component.key not in selected:
                self._reporter.pending(
                    title=component.title,
                    detail=component.summary,
                )
                continue
            if dry_run:
                state: str = (
                    "refresh" if component.installed(layout=layout) else "new"
                )
                self._reporter.note(
                    text=f"would install {component.title} "
                    f"({state}) · {component.summary}",
                )
                applied.append(component.key)
                continue
            detail: str = (
                component.install(layout=layout)
                if component.install is not None
                else component.summary
            )
            self._reporter.step(title=component.title, detail=detail)
            applied.append(component.key)
        if dry_run:
            self._reporter.note(text="dry run · no changes written")
            return 0
        write_manifest(layout=layout, keys=frozenset(applied))
        self._print_hints(layout=layout)
        return 0

    def uninstall(self, *, purge: bool, dry_run: bool) -> int:
        """Remove every owned component recorded or detected.

        Args:
            purge: Purge.
            dry_run: Dry run.

        Returns:
            The resulting value.

        """
        layout: Layout = manifest_layout(home=self._home) or Layout(
            home=self._home,
            bin_dir=default_bin_dir(home=self._home),
            lib_dir=default_lib_dir(home=self._home),
            alias=Constants.default_alias,
            mode="live",
            source_root=self._source_root,
            python=executable,
        )
        removed: list[str] = []
        for component in self.components(layout=layout):
            if component.remove is None:
                continue
            owned: bool = component.installed(layout=layout)
            if not owned and component.key != "launcher":
                continue
            if dry_run:
                self._reporter.note(
                    text=f"would remove {component.title} "
                    f"· {component.summary}",
                )
                continue
            component.remove(layout=layout)
            self._reporter.step(
                title=f"{component.title} removed",
                detail=component.summary,
            )
            removed.append(component.key)
        if not dry_run:
            if layout.manifest_path.is_file():
                layout.manifest_path.unlink()
            if purge and layout.state_dir.exists():
                rmtree(path=layout.state_dir)
                self._reporter.step(
                    title="State purged",
                    detail=str(object=layout.state_dir),
                )
        self._reporter.done(
            text=(
                "uninstall plan complete"
                if dry_run
                else f"uninstalled · {len(removed)} components removed"
            ),
        )
        return 0

    def status(self, *, layout: Layout) -> int:
        """Render the installation and environment state table.

        Args:
            layout: Layout.

        Returns:
            The resulting value.

        """
        title: str = self._reporter.paint(
            text="District Terminal install status",
            style=Constants.bold,
        )
        meta: str = self._reporter.paint(
            text=f"· mode {layout.mode} · alias {layout.alias}",
            style=Constants.dim,
        )
        self._reporter.line(text=f"\n  {title} {meta}")
        self._reporter.line(text=f"  {'─' * 62}")
        missing: int = 0
        for component in self.components(layout=layout):
            if not component.available:
                continue
            if component.installed(layout=layout):
                self._reporter.step(
                    title=component.title,
                    detail=component.summary,
                )
            else:
                missing += 1
                self._reporter.pending(
                    title=component.title,
                    detail=component.summary,
                )
        self._reporter.line(text=f"  {'─' * 62}")
        self._environment(layout=layout)
        if missing:
            self._reporter.note(
                text=f"{missing} components not installed; "
                "run install.py to add them",
            )
        self._reporter.line(text="")
        return 0

    def check_conflict(self, *, layout: Layout, force: bool) -> None:
        """Stop when the alias resolves to a foreign executable.

        Args:
            layout: Layout.
            force: Force.

        Raises:
            RuntimeError: When the operation cannot complete.

        """
        resolved: str | None = which(cmd=layout.alias)
        if resolved is None:
            return
        foreign: bool = Path(
            resolved,
        ).resolve() != layout.launcher.resolve() or not owned_launcher(
            layout=layout,
        )
        if foreign and not force:
            msg: str = (
                f"Alias '{layout.alias}' conflicts with an existing "
                f"command at {resolved}. Re-run with --force to replace "
                "it or choose another --alias."
            )
            raise RuntimeError(msg)
        if foreign:
            self._reporter.warn(
                text=f"forcing '{layout.alias}' over {resolved}",
            )

    def resolve_python(self) -> str:
        """Choose the interpreter the launcher should pin.

        Returns:
            The resulting value.

        """
        candidates: tuple[Path, ...] = (
            self._source_root / ".venv" / "bin" / "python",
            Path(executable),
        )
        bundled: bool = self._source_root.is_file()
        probe: str = (
            ("import sys;" if bundled else "import sys,psutil;")
            + "sys.exit(0 if sys.version_info>="
            + f"{Constants.minimum_python} else 1)"
        )
        for candidate in candidates:
            if not candidate.is_file():
                continue
            result: CompletedProcess[str] = run(
                args=(str(object=candidate), "-c", probe),
                check=False,
                capture_output=True,
            )
            if result.returncode == 0:
                return str(object=candidate)
        self._reporter.warn(
            text="no interpreter with Python 3.13 and psutil found; "
            f"using {executable}",
        )
        return executable

    @staticmethod
    def _supported_platform() -> bool:
        """Return whether the current OS is supported.

        Returns:
            The resulting value.

        """
        return system() in {"Darwin", "Linux", "Windows"}

    def _print_hints(self, *, layout: Layout) -> None:
        """Print the post-install usage hints.

        Args:
            layout: Layout.

        """
        self._reporter.done(text="installed")
        self._reporter.note(
            text="restart your shell or run: exec $SHELL",
        )
        self._reporter.line(text="")
        for hint in (
            f"{layout.alias} --status",
            f"{layout.alias} --watch",
            f"{layout.alias} --list",
            f"{layout.alias} --web",
        ):
            painted: str = self._reporter.paint(
                text=hint,
                style=Constants.cyan,
            )
            self._reporter.line(text=f"    {painted}")
        self._reporter.line(text="")

    def _environment(self, *, layout: Layout) -> None:
        """Render interpreter, backend, and state facts.

        Args:
            layout: Layout.

        """
        resolved: str | None = which(cmd=layout.alias)
        resolution: str = (
            f"resolves to {resolved}"
            if resolved is not None
            else "not on PATH"
        )
        self._reporter.step(title="PATH", detail=resolution)
        self._reporter.step(title="Python", detail=layout.python)
        if layout.manifest_path.is_file():
            self._reporter.step(
                title="Manifest",
                detail=str(object=layout.manifest_path),
            )
        else:
            self._reporter.pending(
                title="Manifest",
                detail="no install recorded",
            )
        if layout.state_dir.exists():
            self._reporter.step(
                title="State",
                detail=str(object=layout.state_dir),
            )


def resolve_selection(
    *,
    components: tuple[Component, ...],
    preset: str,
    with_keys: frozenset[str],
    without_keys: frozenset[str],
) -> frozenset[str]:
    """Combine preset, additions, and exclusions into a selection.

    Args:
        components: Components.
        preset: Preset.
        with_keys: With keys.
        without_keys: Without keys.

    Returns:
        The resulting value.

    Raises:
        ValueError: When the operation cannot complete.

    """
    known: frozenset[str] = frozenset(
        component.key for component in components
    )
    unknown: frozenset[str] = (with_keys | without_keys) - known
    if unknown:
        msg: str = (
            f"Unknown components: {', '.join(sorted(unknown))}. "
            f"Valid keys: {', '.join(sorted(known))}."
        )
        raise ValueError(msg)
    available: frozenset[str] = frozenset(
        component.key for component in components if component.available
    )
    if preset == "minimal":
        base: set[str] = {"launcher"}
    elif preset == "full":
        base = set(available)
    else:
        base = {
            component.key
            for component in components
            if component.available and component.selected
        }
    selected: set[str] = (base | (with_keys & available)) - without_keys
    if "launcher" in without_keys:
        msg = "The launcher component is required."
        raise ValueError(msg)
    selected.add("launcher")
    return frozenset(selected)


def csv(*, value: str | None) -> frozenset[str]:
    """Split a comma-separated component list into a key set.

    Args:
        value: Value.

    Returns:
        The resulting value.

    """
    if not value:
        return frozenset()
    return frozenset(
        entry.strip() for entry in value.split(sep=",") if entry.strip()
    )


def _use_colour() -> bool:
    """Return whether ANSI styling should be emitted.

    Returns:
        The resulting value.

    """
    return stdout.isatty() and environ.get("NO_COLOR") is None


def resolve_mode(*, requested: str | None, archive: Path | None) -> str:
    """Choose the install mode from the request and execution context.

    Args:
        requested: Explicit ``--mode`` value, if any.
        archive: Running ``.pyz`` archive, if any.

    Returns:
        ``live``, ``copy`` or ``pyz``.

    Raises:
        ValueError: When the request cannot be honoured in this context.

    """
    if archive is None:
        if requested == "pyz":
            msg: str = (
                "pyz mode installs the running archive; "
                "run this command from a district .pyz file."
            )
            raise ValueError(msg)
        return requested or "live"
    if requested not in {None, "pyz"}:
        msg = (
            f"{requested} mode needs a source checkout; "
            "an archive installs in pyz mode."
        )
        raise ValueError(msg)
    return "pyz"


def _add_install_arguments(*, parser: ArgumentParser) -> None:
    """Register the District Terminal install option arguments.

    Args:
        parser: Parser.

    """
    _ = parser.add_argument("--alias", metavar="NAME")
    _ = parser.add_argument("--bin-dir", metavar="PATH", type=Path)
    _ = parser.add_argument("--lib-dir", metavar="PATH", type=Path)
    _ = parser.add_argument(
        "--mode",
        choices=("live", "copy", "pyz"),
        default=None,
        help="live points the launcher at this checkout; "
        "copy freezes it under --lib-dir; pyz installs the running "
        "self-contained archive (chosen automatically from a .pyz)",
    )
    _ = parser.add_argument(
        "--preset",
        choices=("minimal", "default", "full"),
        default="default",
    )
    _ = parser.add_argument("--with", dest="with_keys", metavar="LIST")
    _ = parser.add_argument("--without", dest="without_keys", metavar="LIST")
    _ = parser.add_argument("--yes", action="store_true")
    _ = parser.add_argument("--force", action="store_true")
    _ = parser.add_argument("--dry-run", action="store_true")
    _ = parser.add_argument("--quiet", action="store_true")


def main(
    *,
    arguments: tuple[str, ...] | None = None,
    stream: TextIO | None = None,
) -> int:
    """Install, inspect, plan, or uninstall the District Terminal integration.

    Args:
        arguments: Arguments.
        stream: Stream.

    Returns:
        The resulting value.

    """
    parser: ArgumentParser = ArgumentParser(
        prog="install.py",
        description=(
            "Install the District Terminal control plane: launcher, "
            "agent hooks, shell lifecycle, routing, web autostart."
        ),
    )
    add_subparser: Callable[..., ArgumentParser] = parser.add_subparsers(
        dest="action",
    ).add_parser
    install_parser: ArgumentParser = add_subparser(
        name="install",
        help="install components",
    )
    _add_install_arguments(parser=install_parser)
    plan_parser: ArgumentParser = add_subparser(
        name="plan",
        help="print the resolved plan without changes",
    )
    _add_install_arguments(parser=plan_parser)
    uninstall_parser: ArgumentParser = add_subparser(
        name="uninstall",
        help="remove owned components",
    )
    _ = uninstall_parser.add_argument("--purge", action="store_true")
    _ = uninstall_parser.add_argument("--yes", action="store_true")
    _ = uninstall_parser.add_argument("--dry-run", action="store_true")
    _ = uninstall_parser.add_argument("--quiet", action="store_true")
    status_parser: ArgumentParser = add_subparser(
        name="status",
        help="show component and environment state",
    )
    _ = status_parser.add_argument("--alias", metavar="NAME")
    _ = status_parser.add_argument("--bin-dir", metavar="PATH", type=Path)
    _ = status_parser.add_argument("--quiet", action="store_true")
    parsers: tuple[ArgumentParser, ...] = (
        install_parser,
        plan_parser,
        uninstall_parser,
        status_parser,
    )
    for item in parsers:
        _ = item.add_argument("--home", metavar="PATH", type=Path)
    raw: tuple[str, ...] = tuple(argv[1:]) if arguments is None else arguments
    actions: frozenset[str] = frozenset(
        {"install", "plan", "uninstall", "status"},
    )
    prefixed: list[str] = list(raw)
    if not prefixed or (
        prefixed[0] not in actions and prefixed[0] not in {"-h", "--help"}
    ):
        prefixed.insert(0, "install")
    namespace: Namespace = parser.parse_args(args=prefixed)
    action: str = cast(typ="str", val=namespace.action)
    home: Path = (
        cast(typ="Path | None", val=getattr(namespace, "home", None))
        or Path.home()
    )
    reporter: Reporter = Reporter(
        colour=_use_colour(),
        quiet=cast(typ="bool", val=getattr(namespace, "quiet", False)),
        stream=stream,
    )
    archive: Path | None = running_archive()
    installer: Installer = Installer(
        source_root=(
            archive
            if archive is not None
            else Path(__file__).resolve().parents[2]
        ),
        home=home,
        reporter=reporter,
    )
    try:
        if action == "uninstall":
            return installer.uninstall(
                purge=cast(typ="bool", val=namespace.purge),
                dry_run=cast(typ="bool", val=namespace.dry_run),
            )
        mode: str = resolve_mode(
            requested=cast(
                typ="str | None",
                val=getattr(namespace, "mode", None),
            ),
            archive=archive,
        )
        layout: Layout = installer.layout(
            alias=cast(typ="str | None", val=namespace.alias),
            mode=mode,
            bin_dir=cast(typ="Path | None", val=namespace.bin_dir),
            lib_dir=cast(
                typ="Path | None",
                val=getattr(namespace, "lib_dir", None),
            ),
        )
        if action == "status":
            return installer.status(layout=layout)
        components: tuple[Component, ...] = installer.components(
            layout=layout,
        )
        selected: frozenset[str] = resolve_selection(
            components=components,
            preset=cast(typ="str", val=namespace.preset),
            with_keys=csv(
                value=cast(typ="str | None", val=namespace.with_keys),
            ),
            without_keys=csv(
                value=cast(typ="str | None", val=namespace.without_keys),
            ),
        )
        dry_run: bool = cast(typ="bool", val=namespace.dry_run)
        if action == "plan":
            return installer.install(
                layout=layout,
                selected=selected,
                dry_run=True,
                force=cast(typ="bool", val=namespace.force),
            )
        yes: bool = cast(typ="bool", val=namespace.yes)
        interactive: bool = (
            stdin.isatty() and stdout.isatty() and not yes and not dry_run
        )
        if interactive:
            outcome: tuple[Layout, frozenset[str]] | None = wizard(
                layout=layout,
                components=components,
            )
            if outcome is None:
                reporter.note(text="aborted; no changes written")
                return 1
            layout, selected = outcome
        return installer.install(
            layout=layout,
            selected=selected,
            dry_run=dry_run,
            force=cast(typ="bool", val=namespace.force),
        )
    except (RuntimeError, ValueError) as error:
        reporter.warn(text=str(object=error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
