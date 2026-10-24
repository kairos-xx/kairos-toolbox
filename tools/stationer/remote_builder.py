# Copyright (c) 2026 Project contributors.
"""Build on a remote native worker that pulls the job from this host.

PyInstaller cannot cross-compile, so each target operating system needs a
machine of its own. Instead of pushing to the worker over SSH, this backend
serves one authenticated job and lets a freshly started worker (a VM, a
container, a colleague's laptop) pull it with a single pasted command. The
host verifies everything the worker sends back before trusting it.
"""

from __future__ import annotations

from dataclasses import dataclass
from http.client import HTTPConnection, HTTPResponse
from os import kill, name
from pathlib import Path
from secrets import token_hex
from shutil import move, rmtree
from signal import SIGTERM
from socket import AF_INET, AF_INET6, SOCK_STREAM, socket
from subprocess import CompletedProcess, TimeoutExpired
from subprocess import run as subprocess_run
from sys import platform as sys_platform
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic, sleep
from tomllib import load
from typing import TYPE_CHECKING, Final, cast, final

from tools.conjure import QemuRunnerError, SettingsLoader, UtmRunner
from tools.stationer.builder import (
    ArtifactUtilities,
    BuildError,
    BuildResult,
    SourceArchive,
)
from tools.stationer.conjure import (
    Credentials,
    JsonObject,
    JsonValue,
    MaterializedArtifact,
    Payload,
    RemoteSpec,
    RemoteStep,
    ResultBundle,
)
from tools.stationer.installer_support import (
    PyzPayload,
    deb_postinst,
    ensure_python_ps1,
    ensure_python_sh,
    make_pyz_source,
    pkg_postinstall,
    shim_cmd,
    shim_sh,
)
from tools.stationer.progress import ProgressTracker
from tools.stationer.remote_exposure import (
    Charter,
    ExposureConstants,
    ExposureError,
    shorten_url,
    start_charter,
)
from tools.stationer.remote_server import (
    JobHandler,
    JobServer,
    JobState,
    ServerConstants,
)
from tools.stationer.remote_workers import posix_worker, windows_worker
from tools.stationer.sessions import open_remote_vm

if TYPE_CHECKING:
    from collections.abc import Callable

    from tools.conjure import CommandResult, Settings
    from tools.stationer.builder import (
        PlatformName,
        ProjectConfig,
    )
    from tools.stationer.progress import ProgressSink
    from tools.stationer.sessions import JobSession, VmOpener


@final
class RemoteConstants:
    """Group remote-backend constants.

    Attributes:
        targets: Operating systems a remote worker can build for.
        inno_setup: Download URL for the Inno Setup installer.
        keys: Accepted ``[remote]`` manifest keys.
        poll_seconds: Server wake-up interval while waiting for the worker.
        cpus: Accepted ``architecture`` values.
        max_port: Highest valid TCP port.
        scripts: Worker script name per target.
        architectures: Worker-reported CPU names mapped to artifact names.
        install_weight: Progress weight of the dependency install step.
        freeze_weight: Progress weight of the PyInstaller step.
        process_wait_seconds: Maximum wait for a prior server to release
            its port.
        process_poll_seconds: Poll interval while waiting for port release.
        probe_timeout_seconds: Timeout for probing a prior server identity.

    """

    targets: Final[tuple[str, ...]] = ("windows", "linux", "macos")
    inno_setup: Final[str] = (
        "https://github.com/jrsoftware/issrc/releases/download/"
        "is-6_7_3/innosetup-6.7.3.exe"
    )
    keys: Final[frozenset[str]] = frozenset(
        {
            "bind",
            "port",
            "worker_host",
            "expose",
            "ngrok_authtoken",
            "short",
            "python_version",
            "python_bootstrap",
            "architecture",
            "timeout_seconds",
            "vm",
            "vm_attach",
            "local",
        },
    )
    poll_seconds: Final[float] = 0.5
    cpus: Final[frozenset[str]] = frozenset({"native", "arm64", "amd64"})
    max_port: Final[int] = 65_535
    scripts: Final[dict[str, str]] = {
        "windows": "install.ps1",
        "linux": "install-linux.sh",
        "macos": "install-macos.sh",
    }
    architectures: Final[dict[str, str]] = {
        "x64": "amd64",
        "x86_64": "amd64",
        "amd64": "amd64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    install_weight: Final[int] = 5
    freeze_weight: Final[int] = 4
    process_wait_seconds: Final[float] = 5.0
    process_poll_seconds: Final[float] = 0.05
    probe_timeout_seconds: Final[float] = 0.5


def _field[T: (str, int, bool)](
    *,
    values: JsonObject,
    key: str,
    default: T,
) -> T:
    """Read one typed ``[remote]`` value.

    Args:
        values: The ``[remote]`` table.
        key: Key to read.
        default: Value used when the key is absent; its type is required.

    Returns:
        The configured or default value.

    Raises:
        BuildError: If the configured value has the wrong type.

    """
    value: JsonValue = values.get(key, default)
    if type(value) is not type(default):
        msg: str = f"[remote] {key} must be of type {type(default).__name__}."
        raise BuildError(msg)
    return cast(typ="T", val=value)


def _optional_text(*, values: JsonObject, key: str) -> str | None:
    """Read one optional string ``[remote]`` value.

    Args:
        values: The ``[remote]`` table.
        key: Key to read.

    Returns:
        The text, or ``None`` when absent.

    Raises:
        BuildError: If the value is present but not a string.

    """
    value: JsonValue = values.get(key)
    if value is not None and not isinstance(value, str):
        msg: str = f"[remote] {key} must be a string."
        raise BuildError(msg)
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class RemoteSettings:
    """Resolved settings of the remote backend.

    Defaults keep the job server on the loopback interface; reaching a
    worker on another machine needs an explicit ``bind`` or an ``expose``
    charter.

    Attributes:
        bind: Local interface the job server listens on.
        port: Local port; ``0`` picks a free one.
        worker_host: Address the worker uses to reach this host.
        expose: ``direct``, ``ngrok``, ``pinggy``, ``tunnl`` or ``runlocal``.
        ngrok_authtoken: Explicit ngrok authtoken.
        short: ``None`` for no short URL, ``""`` for a random one, or an alias.
        python_version: Interpreter version bootstrapped on Windows workers.
        python_bootstrap: Whether a worker may install Python itself.
        architecture: ``native``, ``arm64`` or ``amd64``: the CPU the
            Windows executable is built for.
        timeout_seconds: Maximum wall-clock build duration.
        vm: ``None`` prints the bootstrap command for you to paste. A name
            boots that VM from the ``conjure`` registry, and ``""`` boots
            the one ``[tool.conjure]`` defines, then runs the command in
            it over SSH.
        vm_attach: Use the already-running guest that ``[tool.conjure]``
            describes (SSH user, key and port) instead of booting one.
        local: Run the worker script on this machine instead of a remote
            one; the target must match the host's operating system.

    """

    bind: str = "127.0.0.1"
    port: int = 8765
    worker_host: str = "10.0.2.2"
    expose: str = "direct"
    ngrok_authtoken: str | None = None
    short: str | None = None
    python_version: str = "3.13.9"
    python_bootstrap: bool = True
    architecture: str = "native"
    timeout_seconds: int = 1800
    vm: str | None = None
    vm_attach: bool = False
    local: bool = False

    @classmethod
    def load(cls, *, manifest: Path) -> RemoteSettings:
        """Read the optional ``[remote]`` table of a manifest.

        Args:
            manifest: Release-builder manifest path.

        Returns:
            Defaults overlaid with the manifest's values.

        Raises:
            BuildError: If a key is unknown, mistyped or out of range.

        """
        with manifest.open(mode="rb") as stream:
            document: JsonObject = load(stream)
        table: JsonValue = document.get("remote", {})
        if not isinstance(table, dict):
            msg: str = "[remote] must be a table."
            raise BuildError(msg)
        values: JsonObject = table
        unknown: list[str] = sorted(set(values) - RemoteConstants.keys)
        if unknown:
            msg = f"Unknown [remote] keys: {', '.join(unknown)}"
            raise BuildError(msg)
        defaults: RemoteSettings = cls()
        settings: RemoteSettings = cls(
            bind=_field(values=values, key="bind", default=defaults.bind),
            port=_field(values=values, key="port", default=defaults.port),
            worker_host=_field(
                values=values,
                key="worker_host",
                default=defaults.worker_host,
            ),
            expose=_field(
                values=values,
                key="expose",
                default=defaults.expose,
            ),
            ngrok_authtoken=_optional_text(
                values=values,
                key="ngrok_authtoken",
            ),
            short=_optional_text(values=values, key="short"),
            python_version=_field(
                values=values,
                key="python_version",
                default=defaults.python_version,
            ),
            python_bootstrap=_field(
                values=values,
                key="python_bootstrap",
                default=defaults.python_bootstrap,
            ),
            architecture=_field(
                values=values,
                key="architecture",
                default=defaults.architecture,
            ),
            timeout_seconds=_field(
                values=values,
                key="timeout_seconds",
                default=defaults.timeout_seconds,
            ),
            vm=_optional_text(values=values, key="vm"),
            vm_attach=_field(
                values=values,
                key="vm_attach",
                default=defaults.vm_attach,
            ),
            local=_field(
                values=values,
                key="local",
                default=defaults.local,
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        """Check ranges and allowed values.

        Raises:
            BuildError: If any setting is out of range.

        """
        problems: list[str] = []
        if not 0 <= self.port <= RemoteConstants.max_port:
            problems.append(
                f"port must be from 0 to {RemoteConstants.max_port}",
            )
        if self.expose not in ExposureConstants.backends:
            problems.append(
                "expose must be one of "
                + ", ".join(sorted(ExposureConstants.backends)),
            )
        if self.architecture not in RemoteConstants.cpus:
            problems.append(
                "architecture must be one of "
                + ", ".join(sorted(RemoteConstants.cpus)),
            )
        if self.timeout_seconds < 1:
            problems.append("timeout_seconds must be positive")
        if problems:
            msg: str = "Invalid [remote] settings: " + "; ".join(problems)
            raise BuildError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class RemotePackaging:
    """Metadata and payload baked into the installer a worker produces.

    Attributes:
        maintainer: Debian package maintainer for a Linux worker.
        description: Debian package description for a Linux worker.
        publisher: Inno Setup publisher name for a Windows worker.
        pyz: ``.pyz`` built on the worker and bundled into the installer;
            ``None`` produces an executable-only installer.

    """

    maintainer: str = ""
    description: str = ""
    publisher: str = ""
    pyz: PyzPayload | None = None


def pyz_step(*, project: ProjectConfig, pyz: PyzPayload) -> RemoteStep:
    """Plan the step that builds the ``.pyz`` inside the worker.

    Args:
        project: Project configuration.
        pyz: Bundled ``.pyz`` description.

    Returns:
        The ``make_pyz.py`` invocation producing ``{dist}/pyz/<archive>``.

    """
    return RemoteStep(
        message="building the pyz archive",
        argv=(
            "{venv_python}",
            "{work}/make_pyz.py",
            "--python",
            "{venv_python}",
            "--source",
            "{source}",
            "--bootstrap",
            f"{{source}}/{pyz.bootstrap}",
            "--output",
            f"{{dist}}/pyz/{pyz.archive}",
            "--",
            *project.pip_args,
            *(
                f"{{source}}/.local-deps/{path.name}"
                for path in project.local_dependencies
            ),
        ),
        cwd="work",
    )


def remote_steps(
    *,
    project: ProjectConfig,
    target: str,
    packaging: RemotePackaging | None = None,
) -> tuple[RemoteStep, ...]:
    """Plan the commands a worker runs to freeze and package ``project``.

    The payload holds the project at its root and its local dependencies
    under ``.local-deps/``; dependencies are installed before the project.
    After PyInstaller, the target's native installer is built too: Windows
    workers fetch and run Inno Setup, Linux workers run ``dpkg-deb`` and
    macOS workers run ``pkgbuild``.

    Args:
        project: Project configuration.
        target: ``windows``, ``linux`` or ``macos``.
        packaging: Installer metadata and optional bundled ``.pyz``.

    Returns:
        Create a venv, upgrade pip, install, freeze, build the ``.pyz``,
        then package.

    """
    pyz: PyzPayload | None = packaging.pyz if packaging else None
    pip: tuple[str, ...] = (
        "{venv_python}",
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
    )
    installs: tuple[str, ...] = (
        *(f".local-deps/{path.name}" for path in project.local_dependencies),
        ".",
    )
    entrypoint: str = project.entrypoint.relative_to(project.root).as_posix()
    steps: list[RemoteStep] = [
        RemoteStep(
            message="creating an isolated virtual environment",
            argv=("{python}", "-m", "venv", "{venv}"),
            cwd="work",
        ),
        RemoteStep(
            message="upgrading pip",
            argv=(*pip, "--upgrade", "pip"),
            cwd="work",
        ),
        RemoteStep(
            message="installing PyInstaller, dependencies and the project",
            # ``raw`` is only requested after pip was upgraded above, since
            # older bundled pips reject it; the host reads the byte counts.
            argv=(
                *pip,
                "--progress-bar",
                "raw",
                "pyinstaller",
                *project.pip_args,
                *installs,
            ),
            weight=RemoteConstants.install_weight,
        ),
        RemoteStep(
            message="building the native executable",
            argv=(
                "{venv_python}",
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "--onefile",
                *(() if project.console else ("--windowed",)),
                "--name",
                project.name,
                "--distpath",
                "{dist}",
                "--workpath",
                "{work}/pyinstaller-work",
                "--specpath",
                "{work}/spec",
                *project.pyinstaller_args,
                entrypoint,
            ),
            weight=RemoteConstants.freeze_weight,
        ),
    ]
    if pyz is not None:
        steps.append(pyz_step(project=project, pyz=pyz))
    steps.extend(
        packaging_steps(project=project, target=target, packaging=packaging),
    )
    return tuple(steps)


def packaging_steps(
    *,
    project: ProjectConfig,
    target: str,
    packaging: RemotePackaging | None = None,
) -> tuple[RemoteStep, ...]:
    """Plan the target's installer steps after the freeze.

    Args:
        project: Project configuration.
        target: ``windows``, ``linux`` or ``macos``.
        packaging: Installer metadata and optional bundled ``.pyz``.

    Returns:
        The packaging steps; empty for an unknown target.

    """
    pyz: PyzPayload | None = packaging.pyz if packaging else None
    name: str = project.name
    match target:
        case "windows":
            return (
                RemoteStep(
                    message="downloading Inno Setup",
                    argv=(
                        "curl.exe",
                        "-fsSL",
                        "-o",
                        "{work}\\innosetup.exe",
                        RemoteConstants.inno_setup,
                    ),
                    cwd="work",
                ),
                RemoteStep(
                    message="installing Inno Setup silently",
                    argv=(
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        (
                            "& '{work}\\innosetup.exe' /VERYSILENT "
                            "/SUPPRESSMSGBOXES /NORESTART /SP- "
                            "'/DIR={work}\\inno'; $tries = 300; "
                            "while (-not (Test-Path "
                            "'{work}\\inno\\ISCC.exe')) { "
                            "if (--$tries -le 0) { throw 'Inno Setup "
                            "produced no ISCC.exe' }; "
                            "Start-Sleep -Seconds 1 }"
                        ),
                    ),
                    cwd="work",
                ),
                RemoteStep(
                    message="compiling the setup installer",
                    argv=(
                        "{work}\\inno\\ISCC.exe",
                        "/Qp",
                        "{work}\\installer.iss",
                    ),
                    cwd="work",
                ),
            )
        case "linux":
            pyz_steps: tuple[RemoteStep, ...] = (
                (
                    RemoteStep(
                        message="copying the pyz into the package",
                        argv=(
                            "cp",
                            f"{{dist}}/pyz/{pyz.archive}",
                            f"{{work}}/pkg/usr/local/lib/{name}/{pyz.bundled}",
                        ),
                        cwd="work",
                    ),
                    RemoteStep(
                        message="marking package scripts executable",
                        argv=(
                            "chmod",
                            "755",
                            f"{{work}}/pkg/usr/local/bin/{name}-pyz",
                            f"{{work}}/pkg/usr/local/lib/{name}/"
                            "ensure-python.sh",
                            "{work}/pkg/DEBIAN/postinst",
                        ),
                        cwd="work",
                    ),
                )
                if pyz is not None
                else ()
            )
            return (
                RemoteStep(
                    message="staging the package root",
                    argv=(
                        "mkdir",
                        "-p",
                        "{work}/pkg/usr/local/bin",
                        f"{{work}}/pkg/usr/local/lib/{name}",
                        "{dist}/installer",
                    ),
                    cwd="work",
                ),
                RemoteStep(
                    message="copying the executable into the package",
                    argv=(
                        "cp",
                        f"{{dist}}/{name}",
                        f"{{work}}/pkg/usr/local/bin/{name}",
                    ),
                    cwd="work",
                ),
                *pyz_steps,
                RemoteStep(
                    message="packaging the DEB installer",
                    argv=(
                        "dpkg-deb",
                        "--build",
                        "--root-owner-group",
                        "{work}/pkg",
                        f"{{dist}}/installer/{name}.deb",
                    ),
                    cwd="work",
                ),
            )
        case "macos":
            pyz_steps = (
                (
                    RemoteStep(
                        message="copying the pyz into the package",
                        argv=(
                            "cp",
                            f"{{dist}}/pyz/{pyz.archive}",
                            f"{{work}}/pkg-root/usr/local/lib/{name}/"
                            f"{pyz.bundled}",
                        ),
                        cwd="work",
                    ),
                    RemoteStep(
                        message="marking package scripts executable",
                        argv=(
                            "chmod",
                            "755",
                            f"{{work}}/pkg-root/usr/local/bin/{name}-pyz",
                            "{work}/scripts/postinstall",
                        ),
                        cwd="work",
                    ),
                )
                if pyz is not None
                else ()
            )
            return (
                RemoteStep(
                    message="staging the package root",
                    argv=(
                        "mkdir",
                        "-p",
                        "{work}/pkg-root/usr/local/bin",
                        f"{{work}}/pkg-root/usr/local/lib/{name}",
                        "{work}/scripts",
                        "{dist}/installer",
                    ),
                    cwd="work",
                ),
                RemoteStep(
                    message="copying the executable into the package",
                    argv=(
                        "cp",
                        f"{{dist}}/{name}",
                        f"{{work}}/pkg-root/usr/local/bin/{name}",
                    ),
                    cwd="work",
                ),
                *pyz_steps,
                RemoteStep(
                    message="packaging the PKG installer",
                    argv=(
                        "pkgbuild",
                        "--root",
                        "{work}/pkg-root",
                        *(
                            ("--scripts", "{work}/scripts")
                            if pyz is not None
                            else ()
                        ),
                        "--identifier",
                        project.identifier,
                        "--version",
                        project.version,
                        "--install-location",
                        "/",
                        f"{{dist}}/installer/{name}.pkg",
                    ),
                    cwd="work",
                ),
            )
        case _:
            return ()


def remote_files(
    *,
    project: ProjectConfig,
    target: str,
    packaging: RemotePackaging,
) -> dict[str, str]:
    """Render the extra files a worker needs before its steps run.

    Args:
        project: Project configuration.
        target: ``windows``, ``linux`` or ``macos``.
        packaging: Installer metadata and optional bundled ``.pyz``.

    Returns:
        ``{work}``-relative file names mapped to their content; names and
        content may use the step placeholders such as ``{architecture}``.

    """
    pyz: PyzPayload | None = packaging.pyz
    name: str = project.name
    libdir: str = f"usr/local/lib/{name}"
    shared: dict[str, str] = (
        {
            "make_pyz.py": make_pyz_source(),
        }
        if pyz is not None
        else {}
    )
    if target == "windows":
        files: dict[str, str] = {
            "installer.iss": _inno_script(
                project=project,
                publisher=packaging.publisher or project.name,
                pyz=pyz,
            ),
        }
        if pyz is not None:
            files["ensure-python.ps1"] = ensure_python_ps1()
            files[f"{name}-pyz.cmd"] = shim_cmd(name=name, pyz=pyz)
        return {**files, **shared}
    if target == "linux":
        files = {
            "pkg/DEBIAN/control": (
                f"Package: {name}\n"
                f"Version: {project.version}\n"
                "Architecture: {architecture}\n"
                + (
                    "Depends: python3, curl, ca-certificates\n"
                    if pyz is not None
                    else ""
                )
                + f"Maintainer: {packaging.maintainer}\n"
                f"Description: {packaging.description}\n"
            ),
        }
        if pyz is not None:
            files["pkg/DEBIAN/postinst"] = deb_postinst(name=name)
            files[f"pkg/{libdir}/ensure-python.sh"] = ensure_python_sh()
            files[f"pkg/usr/local/bin/{name}-pyz"] = shim_sh(
                name=name,
                pyz=pyz,
            )
        return {**files, **shared}
    if target == "macos":
        files = {}
        if pyz is not None:
            files["scripts/postinstall"] = pkg_postinstall(name=name)
            files[f"pkg-root/{libdir}/ensure-python.sh"] = ensure_python_sh()
            files[f"pkg-root/usr/local/bin/{name}-pyz"] = shim_sh(
                name=name,
                pyz=pyz,
            )
        return {**files, **shared}
    return shared


def _inno_script(
    *,
    project: ProjectConfig,
    publisher: str,
    pyz: PyzPayload | None = None,
) -> str:
    """Render the Inno Setup script a Windows worker compiles.

    ``{{`` produces a literal Inno brace, so ``AppId`` keeps its ``{id}``
    braces and Inno constants such as ``{app}`` stay untouched; the worker
    expands the ``{dist}``, ``{work}`` and ``{architecture}`` placeholders
    instead.

    Args:
        project: Project configuration.
        publisher: Publisher name in the setup.
        pyz: ``.pyz`` payload bundled into the installer when present.

    Returns:
        The ``.iss`` script text.

    """
    identifier: str = project.identifier
    name: str = project.name
    pyz_lines: str = ""
    if pyz is not None:
        pyz_lines = (
            f'Source: "{{dist}}\\pyz\\{pyz.archive}"; '
            f'DestDir: "{{app}}"; DestName: "{pyz.bundled}"; '
            "Flags: ignoreversion\n"
            f'Source: "{{work}}\\{name}-pyz.cmd"; '
            'DestDir: "{app}"\n'
            'Source: "{work}\\ensure-python.ps1"; DestDir: "{app}"\n'
            "\n"
            "[Run]\n"
            'Filename: "powershell.exe"; '
            'Parameters: "-NoProfile -ExecutionPolicy Bypass -File '
            '""{app}\\ensure-python.ps1"" ""{app}"""; '
            "Flags: runhidden waituntilterminated; "
            'StatusMsg: "Checking for a Python interpreter..."\n'
        )
    return (
        "[Setup]\n"
        f"AppId={{{{{identifier}}}}}\n"
        f"AppName={name}\n"
        f"AppVersion={project.version}\n"
        f"AppPublisher={publisher}\n"
        f"DefaultDirName={{autopf}}\\{name}\n"
        f"DefaultGroupName={name}\n"
        "OutputDir={dist}\\installer\n"
        f"OutputBaseFilename={name}-setup\n"
        "ArchitecturesAllowed={architecture}\n"
        "ArchitecturesInstallIn64BitMode={architecture}\n"
        "\n"
        "[Files]\n"
        f'Source: "{{dist}}\\{name}.exe"; DestDir: "{{app}}"; '
        "Flags: ignoreversion\n"
        f"{pyz_lines}"
    )


def primary_artifact(
    *,
    name: str,
    result: ResultBundle,
) -> MaterializedArtifact:
    """Pick the frozen executable among the returned files.

    Args:
        name: Project (executable) name.
        result: Verified result.

    Returns:
        The top-level ``name`` or ``name.exe`` artifact, else the first
        artifact with that file name at any depth.

    Raises:
        BuildError: If the worker returned no such executable.

    """
    names: set[str] = {name, f"{name}.exe"}
    matches: list[MaterializedArtifact] = [
        artifact
        for artifact in result.artifacts
        if artifact.path.name in names
    ]
    if not matches:
        msg: str = f"The worker returned no executable named {name}."
        raise BuildError(msg)
    return min(matches, key=lambda artifact: len(artifact.path.parts))


def installer_name(
    *,
    target: str,
    project: str,
    version: str,
    architecture: str,
) -> str:
    """Name an installer's release file the way local builds do.

    Args:
        target: Operating system the worker built for.
        project: Project name.
        version: Project version.
        architecture: Canonical CPU architecture.

    Returns:
        ``<name>-<version>-<os>-<arch>-setup.exe`` on Windows,
        ``<name>_<version>_<arch>.deb`` on Linux and
        ``<name>-<version>-macos-<arch>.pkg`` on macOS.

    """
    if target == "windows":
        return f"{project}-{version}-windows-{architecture}-setup.exe"
    if target == "linux":
        return f"{project}_{version}_{architecture}.deb"
    return f"{project}-{version}-macos-{architecture}.pkg"


@final
class WorkerLaunch:
    """Run the worker bootstrap command in a VM on a background thread.

    The command blocks until the worker finishes, while the host has to keep
    serving the job, so it runs beside the server loop.
    """

    def __init__(
        self,
        *,
        session: JobSession,
        command: tuple[str, ...],
        timeout_seconds: float,
        log_path: str,
        script_path: str,
    ) -> None:
        """Prepare the launch without starting it.

        Args:
            session: Started VM session.
            command: Command vector to run in the guest.
            timeout_seconds: Longest the command may run.
            log_path: Guest file the command's output is redirected to.
            script_path: Guest file the bootstrap script is downloaded to.

        """
        self._log_path: Final[str] = log_path
        self._script_path: Final[str] = script_path
        self._session: Final[JobSession] = session
        self._command: Final[tuple[str, ...]] = command
        self._timeout: Final[float] = timeout_seconds
        self._failure: str | None = None
        self._thread: Final[Thread] = Thread(target=self._run, daemon=True)

    def start(self) -> None:
        """Start running the command."""
        self._thread.start()

    def failure(self) -> str | None:
        """Report a failed launch.

        Returns:
            A description when the command failed, else ``None``.

        """
        return self._failure

    def log_tail(self) -> str:
        """Fetch the end of the worker's output log from the guest.

        Runners such as UTM's guest agent start a program without capturing
        its output, so the command writes to a file that is fetched here
        when a build fails or times out.

        Returns:
            The last lines of the log, or a note when it cannot be read.

        """
        with TemporaryDirectory(prefix="stationer-log-") as temporary:
            target: Path = Path(temporary) / "worker.log"
            try:
                self._session.download(
                    source=self._log_path,
                    destination=target,
                )
                lines: list[str] = target.read_text(
                    encoding="utf-8",
                    errors="replace",
                ).splitlines()
            except (QemuRunnerError, OSError):
                return "(no worker output could be fetched from the guest)"
        return "Worker output (end of log):\n" + "\n".join(lines[-30:])

    def cleanup(self) -> None:
        """Delete the bootstrap script and the output log from the guest.

        The command removes the script itself once it ends, but is killed
        with its shell on a timeout, and nothing removes the log, which
        must outlive the run so a failure can still be diagnosed. Call this
        after the log was read. Deleting is best effort: a file the killed
        command still holds open, or a guest that is gone, is not an error.
        """
        try:
            _ = self._session.run(
                command=(
                    "cmd.exe",
                    "/d",
                    "/c",
                    "del",
                    "/f",
                    "/q",
                    self._script_path,
                    self._log_path,
                ),
                timeout_seconds=30.0,
            )
        except (QemuRunnerError, OSError, TimeoutExpired):
            return

    def _run(self) -> None:
        """Run the command and record how it ended."""
        try:
            result: CommandResult = self._session.run(
                command=self._command,
                timeout_seconds=self._timeout,
            )
        except (QemuRunnerError, OSError, TimeoutExpired) as error:
            self._failure = str(object=error)
            return
        if result.return_code != 0:
            output: str = (result.stderr or result.stdout).strip()
            self._failure = (
                f"the command started in the VM exited with status "
                f"{result.return_code}:\n{output}"
            )


class RemoteBuilder:
    """Run one pull-worker build and verify what comes back."""

    def __init__(
        self,
        *,
        say: Callable[[str], None],
        open_vm: VmOpener = open_remote_vm,
        job_settings: Settings | None = None,
        progress: ProgressSink | None = None,
    ) -> None:
        """Initialize the builder.

        Args:
            say: Sink for instructions and live worker progress.
            open_vm: Creates the VM session when a VM is configured.
            job_settings: Resolved ``conjure`` settings; loaded from the
                nearest ``pyproject.toml`` when a VM is used and this is
                omitted.
            progress: Receives structured progress updates, which the worker
                reports as step events and forwarded step output; without
                it only the plain step messages reach ``say``.

        """
        self._progress: Final[ProgressSink | None] = progress
        self._say: Final[Callable[[str], None]] = say
        self._open_vm: Final[VmOpener] = open_vm
        self._job_settings: Final[Settings | None] = job_settings

    def build(
        self,
        *,
        project: ProjectConfig,
        settings: RemoteSettings,
        target: str,
        output: Path,
        packaging: RemotePackaging | None = None,
    ) -> BuildResult:
        """Serve the job, wait for the worker, and verify its result.

        Args:
            project: Project configuration.
            settings: Resolved remote settings.
            target: ``windows``, ``linux`` or ``macos``.
            output: Output root; artifacts land in ``output/<target>``.
            packaging: Installer metadata and optional bundled ``.pyz``.

        Returns:
            The verified build result (executable and installer).

        Raises:
            BuildError: On an unknown target, charter failure, worker failure,
                timeout, or an invalid result.

        """
        packaging = packaging or RemotePackaging()
        if target not in RemoteConstants.targets:
            msg: str = f"Unknown remote target: {target}"
            raise BuildError(msg)
        started: float = monotonic()
        with TemporaryDirectory(prefix="stationer-remote-") as temporary:
            archive: Path = Path(temporary) / "payload.zip"
            SourceArchive.create(project=project, destination=archive)
            spec: RemoteSpec = RemoteSpec(
                steps=remote_steps(
                    project=project,
                    target=target,
                    packaging=packaging,
                ),
                files=remote_files(
                    project=project,
                    target=target,
                    packaging=packaging,
                ),
                python_version=settings.python_version,
                python_bootstrap=settings.python_bootstrap,
                architecture=settings.architecture,
                timeout_seconds=settings.timeout_seconds,
            )
            state: JobState = JobState(
                spec=spec,
                credentials=Credentials.create(),
                payload=Payload.of(path=archive),
                output=Path(temporary) / "result",
                public_url="",
                target=target,
                done=Event(),
                say=lambda message: self._say(f"[worker] {message}"),
                track=(
                    ProgressTracker(
                        weights=tuple(step.weight for step in spec.steps),
                        sink=self._progress,
                    )
                    if self._progress is not None
                    else None
                ),
            )
            worker_log: str = self._serve(
                state=state,
                settings=settings,
                target=target,
            )
            placed: dict[str, Path] | None = self._place(
                state=state,
                output=output,
                target=target,
                project=project.name,
            )
        if state.error is not None:
            msg = f"Remote worker failed:\n{state.error}"
            if worker_log:
                msg = f"{msg}\n{worker_log}"
            raise BuildError(msg)
        if state.result is None or placed is None:
            msg = "The worker returned no validated result bundle."
            raise BuildError(msg)
        if state.track is not None:
            state.track.finish()
        primary: MaterializedArtifact = primary_artifact(
            name=project.name,
            result=state.result,
        )
        architecture: str = RemoteConstants.architectures.get(
            state.result.architecture.lower(),
            state.result.architecture.lower(),
        )
        root: Path = state.output.resolve()
        frozen: Path = placed[primary.path.relative_to(root).as_posix()]
        executable: Path = frozen.parent / (
            f"{project.name}-{project.version}-{target}-{architecture}"
            f"{frozen.suffix}"
        )
        _ = frozen.replace(target=executable)
        installer_path: Path | None = None
        installer_sha256: str = ""
        for artifact in state.result.artifacts:
            relative: str = artifact.path.relative_to(root).as_posix()
            if not relative.startswith("installer/"):
                continue
            produced: Path = placed[relative]
            installer_path = produced.parent / installer_name(
                target=target,
                project=project.name,
                version=project.version,
                architecture=architecture,
            )
            _ = produced.replace(target=installer_path)
            installer_sha256 = artifact.sha256
            break
        return BuildResult(
            platform=cast(typ="PlatformName", val=target),
            architecture=architecture,
            executable=executable,
            installer=installer_path,
            executable_sha256=primary.sha256,
            installer_sha256=installer_sha256,
            duration_seconds=monotonic() - started,
            abi=ArtifactUtilities.abi(platform_name=target),
        )

    @classmethod
    def _clear_previous_server(cls, *, bind: str, port: int) -> None:
        """Kill a listener only when it is this stationer server.

        Args:
            bind: Address on which the stationer server should bind.
            port: TCP port that must be available for the new server.

        Raises:
            BuildError: If the existing listener cannot be identified as a
                stationer server, cannot be terminated, or does not
                release the port in time.

        """
        if port == 0 or cls._port_is_free(bind=bind, port=port):
            return
        probe_deadline: float = (
            monotonic() + RemoteConstants.process_wait_seconds
        )
        pid: int | None = cls._listener_pid(port=port)
        while pid is None and monotonic() < probe_deadline:
            sleep(RemoteConstants.process_poll_seconds)
            if cls._port_is_free(bind=bind, port=port):
                return
            pid = cls._listener_pid(port=port)
        if pid is None:
            msg: str = (
                f"Port {port} is occupied, but its listener "
                "PID could not be identified."
            )
            raise BuildError(
                msg,
            )
        command: str = cls._process_command(pid=pid)
        if not cls._same_server(bind=bind, port=port, pid=pid):
            detail: str = command or "unknown process"
            msg = (
                f"Port {port} is already used by PID {pid}, which is not a "
                f"verified stationer server: {detail}"
            )
            raise BuildError(
                msg,
            )
        cls._terminate_listener(pid=pid)
        deadline: float = monotonic() + RemoteConstants.process_wait_seconds
        while monotonic() < deadline:
            if cls._port_is_free(bind=bind, port=port):
                return
            sleep(RemoteConstants.process_poll_seconds)
        msg = f"Previous stationer PID {pid} did not release port {port}."
        raise BuildError(
            msg,
        )

    @classmethod
    def _same_server(cls, *, bind: str, port: int, pid: int) -> bool:
        """Verify HTTP identity and process metadata for an old server.

        Args:
            bind: Address on which the existing server is expected to listen.
            port: TCP port of the existing listener.
            pid: Process identifier of the existing listener.

        Returns:
            Whether both the HTTP identity and process metadata identify the
            listener as a stationer server.

        """
        host: str = "127.0.0.1" if bind in {"", "0." * 4, "::"} else bind
        connection: HTTPConnection = HTTPConnection(
            host=host,
            port=port,
            timeout=RemoteConstants.probe_timeout_seconds,
        )
        try:
            connection.request(
                method="GET",
                url=ServerConstants.identity_path,
            )
            response: HTTPResponse = connection.getresponse()
            identity: bytes = response.read(
                len(ServerConstants.identity) + 1,
            )
        except (OSError, TimeoutError):
            return False
        finally:
            connection.close()
        if identity != ServerConstants.identity:
            return False
        command: str = cls._process_command(pid=pid).lower()
        return bool(command) and (
            "stationer" in command or "remote_builder" in command
        )

    @staticmethod
    def _place(
        *,
        state: JobState,
        output: Path,
        target: str,
        project: str,
    ) -> dict[str, Path] | None:
        """Move a verified result into the project's artifact folders.

        The worker only reports its architecture with the result, so the
        result is unpacked into a scratch folder and moved once it is known
        to be valid; a failed build therefore never touches ``output``.
        Artifacts under ``installer/`` or ``pyz/`` go to the matching
        sibling folder; everything else lands in ``pyinstaller/``.

        Args:
            state: The finished job.
            output: Build output root.
            target: Operating system the worker built for.
            project: Project name.

        Returns:
            Result-relative artifact paths mapped to where each file was
            placed, or ``None`` for a failed job.

        """
        if state.error is not None or state.result is None:
            return None
        architecture: str = RemoteConstants.architectures.get(
            state.result.architecture.lower(),
            state.result.architecture.lower(),
        )
        package: Path = ArtifactUtilities.folder(
            output=output,
            platform_name=target,
            architecture=architecture,
            project=project,
        )
        folders: dict[str, Path] = {
            "pyinstaller": ArtifactUtilities.pyinstaller_folder(
                package=package,
            ),
            "installer": ArtifactUtilities.installer_folder(package=package),
            "pyz": ArtifactUtilities.pyz_folder(package=package),
        }
        root: Path = state.output.resolve()
        used: set[Path] = {
            folders.get(
                (
                    artifact.path.relative_to(root).parts[0]
                    if len(artifact.path.relative_to(root).parts) > 1
                    else "pyinstaller"
                ),
                folders["pyinstaller"],
            )
            for artifact in state.result.artifacts
        }
        for folder in used:
            rmtree(path=folder, ignore_errors=True)
            folder.mkdir(parents=True, exist_ok=True)
        placed: dict[str, Path] = {}
        for artifact in state.result.artifacts:
            relative: Path = artifact.path.relative_to(root)
            top: str = (
                relative.parts[0] if len(relative.parts) > 1 else "pyinstaller"
            )
            destination: Path = (
                folders.get(top, folders["pyinstaller"]) / relative.name
            )
            _ = move(src=artifact.path, dst=destination)
            placed[relative.as_posix()] = destination
        return placed

    @staticmethod
    def _wait(
        *,
        server: JobServer,
        timeout_seconds: int,
        launch: WorkerLaunch | None = None,
    ) -> None:
        """Serve requests until the job finishes or time runs out.

        Args:
            server: The job server.
            timeout_seconds: Maximum wait.
            launch: Worker command running in a VM; its failure ends the wait.

        Raises:
            BuildError: If the worker never finishes in time, or the command
                launched in the VM fails.

        """
        deadline: float = monotonic() + timeout_seconds
        server.timeout = RemoteConstants.poll_seconds
        while not server.state.done.is_set() and monotonic() < deadline:
            server.handle_request()
            failure: str | None = (
                launch.failure() if launch is not None else None
            )
            if failure is not None and not server.state.done.is_set():
                msg: str = f"Worker launch failed: {failure}"
                raise BuildError(msg)
        if not server.state.done.is_set():
            msg = f"Remote build timed out after {timeout_seconds} seconds."
            raise BuildError(msg)

    @staticmethod
    def _command(*, target: str, url: str) -> str:
        """Compose the one-line command a person pastes on a worker.

        Args:
            target: Operating system the worker builds for.
            url: Bootstrap URL.

        Returns:
            The bootstrap command for the worker's shell.

        """
        return (
            f"curl.exe -fsSL {url} | powershell.exe -NoProfile -Command -"
            if target == "windows"
            else f"curl -fsSL {url} | bash"
        )

    @staticmethod
    def _listener_pid(*, port: int) -> int | None:
        """Return the PID listening on a TCP port, when discoverable.

        Args:
            port: TCP port whose listener should be identified.

        Returns:
            The listener process identifier, or ``None`` when no process can
            be identified.

        """
        if port == 0:
            return None
        if name == "nt":
            command: tuple[str, ...] = (
                "powershell.exe",
                "-NoProfile",
                "-Command",
                (
                    "$c=Get-NetTCPConnection -State Listen -LocalPort "
                    f"{port} -ErrorAction SilentlyContinue | "
                    "Select-Object -First 1; if($c){$c.OwningProcess}"
                ),
            )
        else:
            command = ("lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t")
        try:
            result: CompletedProcess[str] = subprocess_run(
                args=command,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return None
        first: str = (
            result.stdout.strip().splitlines()[0]
            if result.stdout.strip()
            else ""
        )
        return int(first) if first.isdigit() else None

    @staticmethod
    def _process_command(*, pid: int) -> str:
        """Return the listener command line using native OS facilities.

        Args:
            pid: Process identifier whose command line should be inspected.

        Returns:
            The process command line, or an empty string when it cannot be
            retrieved.

        """
        if name == "nt":
            command: tuple[str, ...] = (
                "powershell.exe",
                "-NoProfile",
                "-Command",
                (
                    f"$p=Get-CimInstance Win32_Process -Filter "
                    f"'ProcessId={pid}'; "
                    "if($p){$p.ExecutablePath; $p.CommandLine}"
                ),
            )
        else:
            command = ("ps", "-p", str(object=pid), "-o", "command=")
        try:
            result: CompletedProcess[str] = subprocess_run(
                args=command,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return ""
        return result.stdout.strip()

    @staticmethod
    def _port_is_free(*, bind: str, port: int) -> bool:
        """Return whether the configured address can currently be bound.

        Args:
            bind: Local address to test.
            port: TCP port to test.

        Returns:
            Whether a TCP socket can bind to the requested address and port.

        """
        if port == 0:
            return True
        family: int = AF_INET
        host: str = bind
        if ":" in bind:
            family = AF_INET6
        probe: socket = socket(family=family, type=SOCK_STREAM)
        try:
            probe.bind((host, port))
        except OSError:
            return False
        finally:
            probe.close()
        return True

    @staticmethod
    def _terminate_listener(*, pid: int) -> None:
        """Terminate a verified previous server process.

        Args:
            pid: Process identifier of the listener to terminate.

        Raises:
            BuildError: If the operating system cannot terminate the process.

        """
        if name == "nt":
            try:
                result: CompletedProcess[str] = subprocess_run(
                    args=("taskkill.exe", "/PID", str(object=pid), "/T", "/F"),
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except OSError as error:
                msg: str = f"Could not terminate PID {pid}: {error}"
                raise BuildError(msg) from error
            if result.returncode != 0:
                detail: str = result.stderr.strip() or result.stdout.strip()
                msg = f"Could not terminate PID {pid}: {detail}"
                raise BuildError(msg)
            return
        try:
            kill(pid, SIGTERM)
        except OSError as error:
            msg = f"Could not terminate PID {pid}: {error}"
            raise BuildError(msg) from error

    @staticmethod
    def _gateway(*, session: JobSession | None) -> str | None:
        """Return the address a guest uses to reach this host, if it differs.

        Args:
            session: The started VM session, if the worker runs in a VM.

        Returns:
            The host's address on a UTM guest's network, or ``None`` for
            guests that use the configured worker host.

        """
        if isinstance(session, UtmRunner):
            return session.host_address()
        return None

    def _serve(
        self,
        *,
        state: JobState,
        settings: RemoteSettings,
        target: str,
    ) -> str:
        """Serve the job until it finishes, running the worker if asked.

        Args:
            state: The job to serve.
            settings: Resolved remote settings.
            target: Operating system the worker builds for.

        Returns:
            The end of the worker's guest log when it reported a failure in
            a VM, else an empty string.

        Raises:
            BuildError: On a charter or VM failure, a failed launch, or a
                timeout.

        """
        server: JobServer | None = None
        charter: Charter | None = None
        session: JobSession | None = None
        launch: WorkerLaunch | None = None
        worker_log: str = ""
        try:
            if not settings.local and (
                settings.vm is not None or settings.vm_attach
            ):
                session = self._start_vm(settings=settings, target=target)
            # A guest on a host-only network (UTM) reaches this machine at
            # its own address on that network, not at QEMU's 10.0.2.2, and
            # only a listener bound there is reachable from it.
            gateway: str | None = self._gateway(session=session)
            bind: str = gateway or settings.bind
            self._clear_previous_server(bind=bind, port=settings.port)
            server = JobServer((bind, settings.port), JobHandler)
            server.state = state
            charter = start_charter(
                backend=settings.expose,
                port=server.server_address[1],
                authtoken=settings.ngrok_authtoken,
            )
            state.public_url = (
                charter.public_url
                if charter is not None
                else (
                    f"http://{gateway or settings.worker_host}:"
                    f"{server.server_address[1]}"
                )
            )
            launch = self._start_worker(
                state=state,
                settings=settings,
                target=target,
                session=session,
            )
            self._wait(
                server=server,
                timeout_seconds=settings.timeout_seconds,
                launch=launch,
            )
            if state.error is not None and launch is not None:
                worker_log = launch.log_tail()
        except BuildError as error:
            if launch is None:
                raise
            msg: str = f"{error}\n{launch.log_tail()}"
            raise BuildError(msg) from error
        except (ExposureError, QemuRunnerError) as error:
            raise BuildError(str(object=error)) from error
        finally:
            if server is not None:
                server.server_close()
            if charter is not None:
                charter.close()
            if launch is not None:
                launch.cleanup()
            if session is not None:
                session.close()
        return worker_log

    def _launch_local(self, *, state: JobState, target: str) -> None:
        """Run the rendered worker script on this machine in a thread.

        The worker still pulls the payload and pushes its result bundle
        over the loopback job server, so verification, reporting and
        cleanup are identical to a remote run; only the machine changes.

        Args:
            state: The job being served.
            target: Operating system the worker builds for.

        Raises:
            BuildError: If the host's operating system is not ``target``.

        """
        host: str = (
            "windows"
            if name == "nt"
            else "macos"
            if sys_platform == "darwin"
            else "linux"
        )
        if host != target:
            msg: str = (
                f"--local builds {target} on a {target} machine; "
                f"this host is {host}."
            )
            raise BuildError(msg)
        script: str = (
            windows_worker(
                base_url=state.public_url,
                credentials=state.credentials,
                payload=state.payload,
                spec=state.spec,
            )
            if target == "windows"
            else posix_worker(
                base_url=state.public_url,
                credentials=state.credentials,
                payload=state.payload,
                spec=state.spec,
                os_name=target,
            )
        )
        runner: TemporaryDirectory[str] = TemporaryDirectory(
            prefix="stationer-local-",
        )
        suffix: str = ".ps1" if target == "windows" else ".sh"
        path: Path = Path(runner.name) / f"worker{suffix}"
        path.write_text(data=script, encoding="utf-8")
        argv: tuple[str, ...] = (
            (
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(object=path),
            )
            if target == "windows"
            else ("bash", str(object=path))
        )

        def run() -> None:
            """Run the worker script and report an unreported death."""
            try:
                completed: CompletedProcess[bytes] = subprocess_run(
                    args=argv,
                    check=False,
                )
                if completed.returncode != 0 and not state.done.is_set():
                    state.error = (
                        f"the local worker exited with "
                        f"{completed.returncode} without a report"
                    )
            except OSError as error:
                if not state.done.is_set():
                    state.error = f"the local worker never ran: {error}"
            finally:
                runner.cleanup()
                state.done.set()

        self._say(f"Running the {target} worker on this machine.")
        Thread(target=run, daemon=True).start()

    def _start_worker(
        self,
        *,
        state: JobState,
        settings: RemoteSettings,
        target: str,
        session: JobSession | None,
    ) -> WorkerLaunch | None:
        """Get a worker to pull the job: locally, in the VM, or by a human.

        Args:
            state: The job being served; its public URL is already set.
            settings: Resolved remote settings.
            target: Operating system the worker builds for.
            session: The started VM session, if the worker runs in a VM.

        Returns:
            The VM launch, or ``None`` when the worker runs locally or a
            person has been told which command to paste.

        """
        url: str = self._url(
            target=target,
            state=state,
            short=settings.short,
        )
        if settings.local:
            self._launch_local(state=state, target=target)
            return None
        if session is None:
            self._say(f"Run this on the {target} worker:")
            self._say(f"  {self._command(target=target, url=url)}")
            return None
        return self._run_in_vm(session=session, settings=settings, url=url)

    def _start_vm(
        self,
        *,
        settings: RemoteSettings,
        target: str,
    ) -> JobSession:
        """Start the VM that will run the worker.

        Args:
            settings: Resolved remote settings selecting the VM.
            target: Operating system the worker builds for.

        Returns:
            The started VM session.

        Raises:
            BuildError: If the VM is misconfigured or fails to start, or the
                target is not Windows.

        """
        if target != "windows":
            msg: str = (
                "Running the worker in a VM needs --remote windows: the "
                "conjure transports target Windows guests."
            )
            raise BuildError(msg)
        try:
            job: Settings = (
                self._job_settings
                if self._job_settings is not None
                else SettingsLoader.load()
            )
            session: JobSession = self._open_vm(
                vm=settings.vm or "",
                attach=settings.vm_attach,
                settings=job,
            )
        except ValueError as error:
            raise BuildError(str(object=error)) from error
        try:
            self._say("[vm] starting the VM and waiting for it to answer")
            session.start()
        except QemuRunnerError as error:
            session.close()
            raise BuildError(str(object=error)) from error
        return session

    def _run_in_vm(
        self,
        *,
        session: JobSession,
        settings: RemoteSettings,
        url: str,
    ) -> WorkerLaunch:
        """Run the worker command inside a started VM.

        The script is saved and run with ``-File`` rather than piped into
        ``powershell -Command -``, which dies without output in a service
        context such as a VM guest agent. The command's exit status is the
        fetch-and-run status, so a guest that cannot reach this host fails
        the launch at once instead of leaving the build waiting out its
        timeout.

        Args:
            session: The started VM session.
            settings: Resolved remote settings.
            url: Bootstrap URL the guest downloads the worker from.

        Returns:
            The running launch.

        """
        stem: str = f"C:\\Windows\\Temp\\stationer-{token_hex(nbytes=4)}"
        log: str = f"{stem}.log"
        script: str = f"{stem}.ps1"
        fetch: str = (
            f"curl.exe -fsSL -o {script} {url} && powershell.exe "
            f"-NoProfile -ExecutionPolicy Bypass -File {script}"
        )
        launch: WorkerLaunch = WorkerLaunch(
            session=session,
            command=(
                "cmd.exe",
                "/d",
                "/s",
                "/c",
                f"({fetch}) > {log} 2>&1 && (del /q {script}) "
                f"|| (del /q {script} & exit /b 1)",
            ),
            timeout_seconds=settings.timeout_seconds,
            log_path=log,
            script_path=script,
        )
        launch.start()
        self._say("[vm] the worker command was started inside the VM")
        return launch

    def _url(
        self,
        *,
        target: str,
        state: JobState,
        short: str | None,
    ) -> str:
        """Compose the URL a worker downloads its bootstrap script from.

        Args:
            target: Operating system the worker builds for.
            state: Job state, holding the public URL and secrets.
            short: ``None`` for no short URL, ``""`` random, or an alias.

        Returns:
            The bootstrap URL, shortened when requested.

        """
        url: str = (
            f"{state.public_url}/{state.credentials.bootstrap}/"
            f"{RemoteConstants.scripts[target]}"
        )
        if short is not None:
            url = shorten_url(url=url, alias=short or None)
            self._say("note: the short URL service can see this bootstrap URL")
        return url
