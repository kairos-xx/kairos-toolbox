# Copyright (c) 2026 Project contributors.
"""Cross-platform runtime and capability detection."""

from __future__ import annotations

from dataclasses import dataclass
from os import environ
from platform import machine, system
from shutil import which
from typing import Final

from .agent_identity import AgentIdentity
from .conpty import WindowsConPty


@dataclass(frozen=True, slots=True, kw_only=True)
class Capabilities:
    """Describe capabilities available on the current execution host.

    Attributes:
        persistent_sessions: Persistent sessions.
        read_only_capture: Read only capture.
        process_tree: Process tree.
        clipboard: Clipboard.
        clickable_copy: Clickable copy.
        notifications: Notifications.
        editor_agent_bridge: Editor agent bridge.
        interactive_reconnect: Interactive reconnect.

    """

    persistent_sessions: bool
    read_only_capture: bool
    process_tree: bool
    clipboard: bool
    clickable_copy: bool
    notifications: bool
    editor_agent_bridge: bool
    interactive_reconnect: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimePlatform:
    """Normalized execution-host identity and capabilities.

    Attributes:
        os: Os.
        architecture: Architecture.
        shell_location: Shell.
        backend: Backend.
        capabilities: Capabilities.

    """

    os: str
    architecture: str
    shell_location: str
    backend: str
    capabilities: Capabilities

    @classmethod
    def detect(cls) -> RuntimePlatform:
        """Detect the current execution host.

        Returns:
            The resulting value.

        """
        raw_os: str = system().lower()
        os_name: str = {"darwin": "macos", "windows": "windows"}.get(
            raw_os,
            raw_os,
        )
        raw_arch: str = machine().lower()
        architecture: str = {
            "amd64": "x86_64",
            "x64": "x86_64",
            "x86_64": "x86_64",
            "arm64": "arm64",
            "aarch64": "arm64",
        }.get(raw_arch, raw_arch)
        tmux: bool = which(cmd="tmux") is not None and os_name != "windows"
        conpty: bool = (
            os_name == "windows" and WindowsConPty.capabilities().available
        )
        clipboard: bool = cls._clipboard_available(os_name=os_name)
        backend: str = "tmux" if tmux else "conpty" if conpty else "process"
        persistent: bool = tmux or conpty
        return RuntimePlatform(
            os=os_name,
            architecture=architecture,
            shell_location=environ.get("SHELL", environ.get("COMSPEC", "")),
            backend=backend,
            capabilities=Capabilities(
                persistent_sessions=persistent,
                read_only_capture=persistent,
                process_tree=True,
                clipboard=clipboard,
                clickable_copy=os_name == "macos",
                notifications=os_name in {"macos", "linux", "windows"},
                editor_agent_bridge=True,
                interactive_reconnect=persistent,
            ),
        )

    @classmethod
    def surface(cls) -> str:
        """Return normalized execution surface.

        Returns:
            The resulting value.

        """
        explicit: str = (
            environ.get("DISTRICT_AGENT_SURFACE", "").strip().lower()
        )
        if explicit:
            return explicit
        identity: AgentIdentity | None = AgentIdentity.from_environment()
        if identity is not None and identity.surface:
            return identity.surface
        return "vscode" if cls.editor() else "cli"

    @classmethod
    def editor(cls) -> str:
        """Return the detected editor identity when hosted by an editor.

        VS Code-compatible forks share every ``VSCODE_*`` variable, so the
        fork is read from the user-data directory embedded in the IPC hook
        path (``.../Application Support/Devin/...``) when available.

        Returns:
            The editor identity, or an empty string outside an editor.

        """
        if environ.get("VSCODE_PID") or environ.get("VSCODE_CWD"):
            return environ.get("DISTRICT_EDITOR") or cls._fork()
        return environ.get("DISTRICT_EDITOR", "")

    @staticmethod
    def _fork() -> str:
        """Identify the VS Code-compatible fork from its IPC hook path.

        Returns:
            The fork name, defaulting to ``vscode``.

        """
        hook: str = environ.get("VSCODE_IPC_HOOK", "").lower()
        segments: set[str] = set(hook.replace("\\", "/").split(sep="/"))
        for name in ("devin", "cursor", "windsurf", "vscodium"):
            if name in segments:
                return name
        return "vscode"

    @staticmethod
    def _clipboard_available(*, os_name: str) -> bool:
        """Return whether a native clipboard command is available.

        Args:
            os_name: Os name.

        Returns:
            The resulting value.

        """
        commands: Final[dict[str, tuple[str, ...]]] = {
            "macos": ("pbcopy",),
            "linux": ("wl-copy", "xclip", "xsel"),
            "windows": ("clip.exe", "clip"),
        }
        return any(
            which(cmd=command) is not None
            for command in commands.get(os_name, ())
        )
