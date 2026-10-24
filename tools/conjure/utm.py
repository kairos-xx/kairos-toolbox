"""Discover UTM-managed QEMU VMs and derive standalone QEMU commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from platform import system
from plistlib import load as load_plist
from re import Match, Pattern
from re import compile as compile_pattern
from shlex import join as shell_join
from shlex import split as shell_split
from shutil import which
from subprocess import CompletedProcess, run
from typing import Final

type PlistValue = (
    str
    | int
    | float
    | bool
    | bytes
    | datetime
    | list[PlistValue]
    | dict[str, PlistValue]
)


class DependencyKind(StrEnum):
    """Classify a dependency found in a UTM-generated QEMU command.

    Attributes:
        FILE: A file such as a disk image.
        DIRECTORY: A directory such as the firmware data directory.
        SOCKET: A Unix socket path.
        UTM_RUNTIME: A UTM-owned channel that has no standalone equivalent.

    """

    FILE = "file"
    DIRECTORY = "directory"
    SOCKET = "socket"
    UTM_RUNTIME = "utm_runtime"


@dataclass(frozen=True, slots=True, kw_only=True)
class UtmDependency:
    """Describe one resource required by a captured QEMU launch.

    Attributes:
        kind: Resource classification.
        value: Path or option value of the resource.
        option: QEMU option that referenced it.
        exists: Whether it exists now, or ``None`` when not checkable.

    """

    kind: DependencyKind
    value: str
    option: str
    exists: bool | None


@dataclass(frozen=True, slots=True, kw_only=True)
class UtmDrive:
    """Describe one QEMU drive argument.

    Attributes:
        identifier: Drive ``id``, if any.
        media: Drive ``media``, if any.
        path: Backing file, if any.
        readonly: Whether the drive is read-only.
        raw: The raw ``-drive`` value.

    """

    identifier: str | None
    media: str | None
    path: Path | None
    readonly: bool
    raw: str


@dataclass(frozen=True, slots=True, kw_only=True)
class UtmVm:
    """Describe a discovered UTM QEMU virtual machine.

    Attributes:
        name: VM name.
        bundle: ``.utm`` bundle, if located.
        config: Bundle ``config.plist``, if present.
        executable: QEMU system executable.
        architecture: QEMU architecture suffix.
        uuid: VM UUID, if known.
        machine: ``-machine`` value.
        accelerator: ``-accel`` value.
        cpu: ``-cpu`` value.
        memory_mib: Guest memory in MiB.
        cpus: Virtual CPU count.
        qemu_data_dir: ``-L`` firmware directory.
        drives: Attached drives.
        dependencies: Resources the launch needs.
        captured_argv: Exact argv UTM used.
        standalone_argv: Normalized argv for independent startup.
        running_pid: PID of the running process, if any.
        fidelity_warnings: Differences introduced by normalization.

    """

    name: str
    bundle: Path | None
    config: Path | None
    executable: Path
    architecture: str
    uuid: str | None
    machine: str | None
    accelerator: str | None
    cpu: str | None
    memory_mib: int | None
    cpus: int | None
    qemu_data_dir: Path | None
    drives: tuple[UtmDrive, ...]
    dependencies: tuple[UtmDependency, ...]
    captured_argv: tuple[str, ...]
    standalone_argv: tuple[str, ...]
    running_pid: int | None
    fidelity_warnings: tuple[str, ...]

    @property
    def captured_command(self) -> str:
        """Return the shell-rendered captured command.

        Returns:
            The captured argv quoted for a POSIX shell.

        """
        return shell_join(split_command=self.captured_argv)

    @property
    def standalone_command(self) -> str:
        """Return the shell-rendered normalized standalone command.

        Returns:
            The standalone argv quoted for a POSIX shell.

        """
        return shell_join(split_command=self.standalone_argv)


@dataclass(frozen=True, slots=True, kw_only=True)
class UtmBundle:
    """Describe one UTM bundle located on disk.

    Attributes:
        path: The ``.utm`` directory.
        config: Its ``config.plist``, if present.
        name: VM name.
        uuid: VM UUID, if known.
        architecture: Guest architecture, if known.

    """

    path: Path
    config: Path | None
    name: str
    uuid: str | None
    architecture: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class _Process:
    """Represent one process row used during QEMU discovery.

    Attributes:
        pid: Process identifier.
        argv: Process argument vector.

    """

    pid: int
    argv: tuple[str, ...]


class UtmQemuExtractor:
    """Discover UTM QEMU VMs and derive launchable standalone commands.

    Running VMs are reconstructed from the actual QEMU process argv, which is
    the authoritative effective configuration; stopped UTM bundles are located
    and their plist metadata is inspected.

    Attributes:
        UTM_DOCUMENTS: Home-relative folders that hold ``.utm`` bundles.
        UTM_APPS: Locations of the UTM application.
        QEMU_PREFIX: Executable-name prefix of QEMU system emulators.
        LAUNCHER: Name of UTM's helper that hosts the emulator framework.
        FRAMEWORK_PREFIX: Prefix of UTM's bundled emulator binaries.
        FRAMEWORK_SUFFIX: Suffix of UTM's bundled emulator binaries.
        UTM_MARKERS: Argument fragments that identify UTM-owned channels.
        UTM_SPICE_CHARS: Chardev prefixes of UTM's SPICE channels.
        PATH_PATTERN: Pattern extracting the file path of a ``-drive``.

    """

    UTM_DOCUMENTS: Final[tuple[str, ...]] = (
        "Library/Containers/com.utmapp.UTM/Data/Documents",
        "Library/Containers/com.utmapp.UTM-Setapp/Data/Documents",
        "Documents",
    )
    UTM_APPS: Final[tuple[str, ...]] = (
        "/Applications/UTM.app",
        "Applications/UTM.app",
        "/Applications/Setapp/UTM.app",
    )
    QEMU_PREFIX: Final[str] = "qemu-system-"
    LAUNCHER: Final[str] = "QEMULauncher"
    FRAMEWORK_PREFIX: Final[str] = "qemu-"
    FRAMEWORK_SUFFIX: Final[str] = "-softmmu"
    UTM_MARKERS: Final[tuple[str, ...]] = (
        "com.utmapp.",
        "org.qemu.monitor.qmp",
        "com.utmapp.terminal.",
        "org.qemu.guest_agent",
        "com.redhat.spice.",
        "org.spice-space.webdav.",
    )
    UTM_SPICE_CHARS: Final[tuple[str, ...]] = ("spiceport", "spicevmc")
    PATH_PATTERN: Final[Pattern[str]] = compile_pattern(
        pattern=r"(?:file\.filename|file|path)=([^,]+)",
    )

    def __init__(
        self,
        *,
        home: Path | None = None,
        process_rows: tuple[tuple[int, tuple[str, ...]], ...] | None = None,
    ) -> None:
        """Initialize the extractor.

        Args:
            home: Home directory to inspect.
            process_rows: Optional deterministic process snapshot for tests.

        """
        self._home: Path = home if home is not None else Path.home()
        self._process_rows: tuple[tuple[int, tuple[str, ...]], ...] | None = (
            process_rows
        )

    @classmethod
    def normalize_for_standalone(
        cls,
        *,
        argv: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Remove UTM-owned ephemeral integration from a QEMU argv.

        Hardware, disks, networking, acceleration, machine identity, clock
        and ordinary devices are preserved. UTM SPICE/QMP channels, terminal
        channels, USB redirection, SPICE audio, guest integration, WebDAV and
        UTM-owned TPM wiring are removed.

        Args:
            argv: Captured QEMU argv.

        Returns:
            QEMU argv suitable for independent headless startup.

        """
        normalized: list[str] = []
        index: int = 0
        removed: set[str] = set()
        while index < len(argv):
            flag: str = argv[index]
            value: str | None = (
                argv[index + 1]
                if index + 1 < len(argv) and flag.startswith("-")
                else None
            )
            if flag == "-S":
                index += 1
                continue
            if value is not None and cls._dropped(
                flag=flag,
                value=value,
                removed=removed,
            ):
                index += 2
                continue
            normalized.append(flag)
            if value is not None:
                normalized.append(value)
                index += 2
            else:
                index += 1
        if "-display" not in normalized and "-nographic" not in normalized:
            normalized.extend(("-display", "default"))
        return tuple(normalized)

    def available(self) -> bool:
        """Report whether this host is macOS with UTM installed.

        Returns:
            ``True`` on macOS when a UTM app or its documents exist.

        """
        if system() != "Darwin":
            return False
        apps: tuple[Path, ...] = tuple(
            Path(app) if app.startswith("/") else self._home / app
            for app in self.UTM_APPS
        )
        containers: tuple[Path, ...] = tuple(
            self._home / relative for relative in self.UTM_DOCUMENTS[:2]
        )
        return any(path.is_dir() for path in (*apps, *containers))

    def discover(self) -> tuple[UtmVm, ...]:
        """Return all running UTM-managed QEMU VMs that can be reconstructed.

        Returns:
            Discovered VMs sorted by name and PID.

        """
        bundles: tuple[UtmBundle, ...] = self.locate_bundles()
        return tuple(
            sorted(
                (
                    self._from_process(process=process, bundles=bundles)
                    for process in self._qemu_processes()
                    if self._looks_like_utm(process=process)
                ),
                key=lambda vm: (vm.name.casefold(), vm.running_pid or -1),
            ),
        )

    def locate_bundles(self) -> tuple[UtmBundle, ...]:
        """Locate UTM bundles, including stopped virtual machines.

        Returns:
            Located bundles sorted by path.

        """
        paths: set[Path] = set()
        for relative_root in self.UTM_DOCUMENTS:
            root: Path = self._home / relative_root
            if root.is_dir():
                paths.update(
                    path
                    for path in root.glob(pattern="*.utm")
                    if path.is_dir()
                )
        return tuple(
            self._bundle_from_path(path=path) for path in sorted(paths)
        )

    def inspect_running(self, *, pid: int) -> UtmVm:
        """Inspect one running QEMU process by PID.

        Args:
            pid: QEMU process identifier.

        Returns:
            Reconstructed virtual-machine launch specification.

        Raises:
            LookupError: If the PID is not a QEMU process.

        """
        bundles: tuple[UtmBundle, ...] = self.locate_bundles()
        for process in self._qemu_processes():
            if process.pid == pid and self._is_qemu(process=process):
                return self._from_process(process=process, bundles=bundles)
        msg: str = f"QEMU process {pid} was not found."
        raise LookupError(msg)

    def from_command(self, *, command: str, pid: int | None = None) -> UtmVm:
        """Parse a QEMU command such as one copied from UTM diagnostics.

        Args:
            command: Shell-rendered QEMU command.
            pid: Optional source process identifier.

        Returns:
            Parsed virtual-machine specification.

        """
        return self.from_argv(
            argv=tuple(shell_split(s=command, posix=True)),
            pid=pid,
        )

    def from_argv(
        self,
        *,
        argv: tuple[str, ...],
        pid: int | None = None,
    ) -> UtmVm:
        """Build a VM specification directly from captured QEMU argv.

        Args:
            argv: Exact QEMU argv, including the executable.
            pid: Optional source process identifier.

        Returns:
            Parsed virtual-machine specification.

        Raises:
            ValueError: If argv does not identify QEMU.

        """
        argv = self._emulator_argv(argv=argv)
        if not argv or not self._is_emulator(name=Path(argv[0]).name):
            msg: str = "argv does not start with a QEMU system emulator."
            raise ValueError(msg)
        return self._from_process(
            process=_Process(pid=pid or -1, argv=argv),
            bundles=self.locate_bundles(),
            pid_override=pid,
        )

    @classmethod
    def _dropped(cls, *, flag: str, value: str, removed: set[str]) -> bool:
        """Decide whether an option and its value are UTM-owned.

        Args:
            flag: Option name.
            value: Option value.
            removed: Chardev ids already dropped; extended here.

        Returns:
            ``True`` when the option must be removed.

        """
        if flag in {"-spice", "-tpmdev"}:
            return True
        if flag == "-audiodev":
            return value.startswith("spice,")
        if flag == "-chardev" and cls._utm_chardev(value=value):
            identifier: str | None = cls._field(value=value, name="id")
            if identifier is not None:
                removed.add(identifier)
            return True
        if flag in {"-mon", "-serial"}:
            return any(identifier in value for identifier in removed)
        return flag == "-device" and cls._utm_device(
            value=value,
            removed_chardevs=frozenset(removed),
        )

    @classmethod
    def _utm_chardev(cls, *, value: str) -> bool:
        """Return whether a chardev belongs to UTM/SPICE integration.

        Args:
            value: ``-chardev`` value.

        Returns:
            ``True`` for UTM-owned channels.

        """
        return (
            value.startswith(cls.UTM_SPICE_CHARS)
            or "name=usbredir" in value
            or any(marker in value for marker in cls.UTM_MARKERS)
            or (value.startswith("socket,") and ".swtpm" in value)
        )

    @classmethod
    def _utm_device(
        cls,
        *,
        value: str,
        removed_chardevs: frozenset[str],
    ) -> bool:
        """Return whether a device depends on removed UTM integration.

        Args:
            value: ``-device`` value.
            removed_chardevs: Ids of chardevs that were removed.

        Returns:
            ``True`` when the device must be removed.

        """
        chardev: str | None = cls._field(value=value, name="chardev")
        serial_port: bool = value.startswith("virtserialport,") and (
            chardev in removed_chardevs
            or any(marker in value for marker in cls.UTM_MARKERS)
        )
        return (
            value.startswith("usb-redir,")
            or serial_port
            or cls._field(value=value, name="tpmdev") is not None
            or cls._field(value=value, name="audiodev") == "audio0"
        )

    @classmethod
    def _fidelity_warnings(cls, *, argv: tuple[str, ...]) -> tuple[str, ...]:
        """Describe semantic differences introduced by normalization.

        Args:
            argv: Captured argv.

        Returns:
            Human-readable warnings.

        """
        warnings: list[str] = []
        if cls._option(argv=argv, option="-tpmdev") is not None:
            warnings.append(
                "TPM wiring was removed from standalone_argv. Preserve the "
                "original TPM state and run a compatible swtpm endpoint "
                "before re-enabling it; this can matter for BitLocker and "
                "Windows device identity.",
            )
        if cls._option(argv=argv, option="-spice") is not None:
            warnings.append(
                "UTM SPICE display, clipboard, audio, USB redirection, guest "
                "agent and WebDAV channels are not part of standalone_argv.",
            )
        if "-S" in argv:
            warnings.append(
                "UTM launched QEMU paused with -S and resumes it through "
                "QMP; standalone_argv removes -S so the guest boots at once.",
            )
        return tuple(warnings)

    @classmethod
    def _plist_strings(cls, *, path: Path) -> dict[str, str]:
        """Read string leaves from a property list.

        Args:
            path: Plist file (XML or binary).

        Returns:
            The first string value of each key.

        """
        with path.open(mode="rb") as stream:
            document: PlistValue = load_plist(fp=stream)
        values: dict[str, str] = {}
        cls._collect(node=document, values=values)
        return values

    @classmethod
    def _collect(cls, *, node: PlistValue, values: dict[str, str]) -> None:
        """Gather string-valued keys from a decoded plist.

        Args:
            node: Decoded plist node.
            values: Receives the first string value of each key.

        """
        if isinstance(node, dict):
            for key, child in node.items():
                if isinstance(child, str):
                    values.setdefault(key, child)
                else:
                    cls._collect(node=child, values=values)
        elif isinstance(node, list):
            for child in node:
                cls._collect(node=child, values=values)

    @classmethod
    def _drives(cls, *, argv: tuple[str, ...]) -> tuple[UtmDrive, ...]:
        """Extract QEMU drive definitions.

        Args:
            argv: Process argv.

        Returns:
            One record per ``-drive``.

        """
        drives: list[UtmDrive] = []
        for value in cls._option_values(argv=argv, option="-drive"):
            found: Match[str] | None = cls.PATH_PATTERN.search(string=value)
            drives.append(
                UtmDrive(
                    identifier=cls._field(value=value, name="id"),
                    media=cls._field(value=value, name="media"),
                    path=(
                        Path(found.group(1)).expanduser()
                        if found is not None
                        else None
                    ),
                    readonly="readonly=on" in value,
                    raw=value,
                ),
            )
        return tuple(drives)

    @classmethod
    def _dependencies(
        cls,
        *,
        argv: tuple[str, ...],
    ) -> tuple[UtmDependency, ...]:
        """Extract file, directory, socket and UTM runtime dependencies.

        Args:
            argv: Process argv.

        Returns:
            The dependencies found.

        """
        found: list[UtmDependency] = []
        data_dir: Path | None = cls._path_option(argv=argv, option="-L")
        if data_dir is not None:
            found.append(
                UtmDependency(
                    kind=DependencyKind.DIRECTORY,
                    value=str(object=data_dir),
                    option="-L",
                    exists=data_dir.is_dir(),
                ),
            )
        found.extend(
            UtmDependency(
                kind=DependencyKind.FILE,
                value=str(object=drive.path),
                option="-drive",
                exists=drive.path.is_file(),
            )
            for drive in cls._drives(argv=argv)
            if drive.path is not None
        )
        for value in cls._option_values(argv=argv, option="-chardev"):
            path_value: str | None = cls._field(value=value, name="path")
            if path_value is not None:
                found.append(
                    UtmDependency(
                        kind=DependencyKind.SOCKET,
                        value=path_value,
                        option="-chardev",
                        exists=Path(path_value).expanduser().exists(),
                    ),
                )
            if cls._utm_chardev(value=value):
                found.append(
                    UtmDependency(
                        kind=DependencyKind.UTM_RUNTIME,
                        value=value,
                        option="-chardev",
                        exists=None,
                    ),
                )
        spice: str | None = cls._option(argv=argv, option="-spice")
        if spice is not None:
            found.append(
                UtmDependency(
                    kind=DependencyKind.UTM_RUNTIME,
                    value=spice,
                    option="-spice",
                    exists=None,
                ),
            )
        return tuple(found)

    @classmethod
    def _path_option(
        cls,
        *,
        argv: tuple[str, ...],
        option: str,
    ) -> Path | None:
        """Return a path-valued QEMU option.

        Args:
            argv: Process argv.
            option: Option name.

        Returns:
            The expanded path, or ``None``.

        """
        value: str | None = cls._option(argv=argv, option=option)
        return Path(value).expanduser() if value is not None else None

    @classmethod
    def _integer_option(
        cls,
        *,
        argv: tuple[str, ...],
        option: str,
    ) -> int | None:
        """Return an integer-valued QEMU option.

        Args:
            argv: Process argv.
            option: Option name.

        Returns:
            The integer, or ``None``.

        """
        value: str | None = cls._option(argv=argv, option=option)
        return int(value) if value is not None and value.isdigit() else None

    @classmethod
    def _smp_cpus(cls, *, argv: tuple[str, ...]) -> int | None:
        """Extract the effective CPU count from ``-smp``.

        Args:
            argv: Process argv.

        Returns:
            The CPU count, or ``None``.

        """
        value: str | None = cls._option(argv=argv, option="-smp")
        if value is None:
            return None
        cpus: str = (
            cls._field(value=value, name="cpus")
            or value.split(
                sep=",",
                maxsplit=1,
            )[0]
        )
        return int(cpus) if cpus.isdigit() else None

    @classmethod
    def _emulator_argv(cls, *, argv: tuple[str, ...]) -> tuple[str, ...]:
        """Drop UTM's launcher so ``argv[0]`` is the emulator.

        The App Store build runs QEMU through ``QEMULauncher <emulator>
        <qemu args>``; a standalone build runs ``qemu-system-*`` directly.

        Args:
            argv: Process argv.

        Returns:
            The argv starting at the emulator.

        """
        if len(argv) > 1 and Path(argv[0]).name == cls.LAUNCHER:
            return argv[1:]
        return argv

    @classmethod
    def _is_emulator(cls, *, name: str) -> bool:
        """Tell whether an executable name is a QEMU system emulator.

        Args:
            name: Executable file name.

        Returns:
            ``True`` for ``qemu-system-*`` and UTM's ``qemu-*-softmmu``.

        """
        return name.startswith(cls.QEMU_PREFIX) or (
            name.startswith(cls.FRAMEWORK_PREFIX)
            and name.endswith(cls.FRAMEWORK_SUFFIX)
        )

    @classmethod
    def _architecture(cls, *, name: str) -> str:
        """Extract the guest architecture from an emulator name.

        Args:
            name: Executable file name.

        Returns:
            The architecture, such as ``aarch64``.

        """
        return (
            name.removeprefix(cls.QEMU_PREFIX)
            .removeprefix(cls.FRAMEWORK_PREFIX)
            .removesuffix(cls.FRAMEWORK_SUFFIX)
        )

    @staticmethod
    def _read_process_rows() -> tuple[tuple[int, tuple[str, ...]], ...]:
        """Read the local process table without invoking a shell.

        Returns:
            ``(pid, argv)`` rows.

        """
        result: CompletedProcess[str] = run(
            args=("ps", "-axo", "pid=,command="),
            capture_output=True,
            check=True,
            text=True,
        )
        rows: list[tuple[int, tuple[str, ...]]] = []
        for line in result.stdout.splitlines():
            stripped: str = line.strip()
            if not stripped:
                continue
            pid_text, _, command = stripped.partition(" ")
            if "qemu-" not in command:
                continue
            try:
                argv: tuple[str, ...] = tuple(
                    shell_split(s=command, posix=True),
                )
            except ValueError:
                continue
            if argv:
                rows.append((int(pid_text), argv))
        return tuple(rows)

    @staticmethod
    def _field(*, value: str, name: str) -> str | None:
        """Extract a comma-separated QEMU key/value field.

        Args:
            value: Option value.
            name: Field name.

        Returns:
            The field value, or ``None``.

        """
        prefix: str = f"{name}="
        return next(
            (
                field[len(prefix) :]
                for field in value.split(sep=",")
                if field.startswith(prefix)
            ),
            None,
        )

    @staticmethod
    def _match_bundle(
        *,
        argv: tuple[str, ...],
        uuid: str | None,
        bundles: tuple[UtmBundle, ...],
    ) -> UtmBundle | None:
        """Match process argv to its UTM bundle.

        Args:
            argv: Process argv.
            uuid: VM UUID from argv.
            bundles: Located bundles.

        Returns:
            The matching bundle, or ``None``.

        """
        joined: str = " ".join(argv)
        return next(
            (
                bundle
                for bundle in bundles
                if (uuid is not None and bundle.uuid == uuid)
                or str(object=bundle.path) in joined
            ),
            None,
        )

    @staticmethod
    def _option(*, argv: tuple[str, ...], option: str) -> str | None:
        """Return the first value following a QEMU option.

        Args:
            argv: Process argv.
            option: Option name.

        Returns:
            The value, or ``None``.

        """
        try:
            index: int = argv.index(option)
        except ValueError:
            return None
        return argv[index + 1] if index + 1 < len(argv) else None

    @staticmethod
    def _option_values(
        *,
        argv: tuple[str, ...],
        option: str,
    ) -> tuple[str, ...]:
        """Return every value following a repeated QEMU option.

        Args:
            argv: Process argv.
            option: Option name.

        Returns:
            All values, in order.

        """
        return tuple(
            argv[index + 1]
            for index, flag in enumerate(iterable=argv[:-1])
            if flag == option
        )

    def _from_process(
        self,
        *,
        process: _Process,
        bundles: tuple[UtmBundle, ...],
        pid_override: int | None = None,
    ) -> UtmVm:
        """Create a VM model from one QEMU process.

        Args:
            process: Process row.
            bundles: Located bundles.
            pid_override: PID to report instead of the process PID.

        Returns:
            The VM model.

        """
        argv: tuple[str, ...] = process.argv
        executable: Path = Path(argv[0]).expanduser()
        architecture: str = self._architecture(name=executable.name)
        uuid: str | None = self._option(argv=argv, option="-uuid")
        bundle: UtmBundle | None = self._match_bundle(
            argv=argv,
            uuid=uuid,
            bundles=bundles,
        )
        return UtmVm(
            name=self._option(argv=argv, option="-name") or architecture,
            bundle=bundle.path if bundle is not None else None,
            config=bundle.config if bundle is not None else None,
            executable=self._resolve_executable(
                executable=executable,
                architecture=architecture,
            ),
            architecture=architecture,
            uuid=uuid,
            machine=self._option(argv=argv, option="-machine"),
            accelerator=self._option(argv=argv, option="-accel"),
            cpu=self._option(argv=argv, option="-cpu"),
            memory_mib=self._integer_option(argv=argv, option="-m"),
            cpus=self._smp_cpus(argv=argv),
            qemu_data_dir=self._path_option(argv=argv, option="-L"),
            drives=self._drives(argv=argv),
            dependencies=self._dependencies(argv=argv),
            captured_argv=argv,
            standalone_argv=self.normalize_for_standalone(argv=argv),
            running_pid=(
                pid_override
                if pid_override is not None
                else (process.pid if process.pid >= 0 else None)
            ),
            fidelity_warnings=self._fidelity_warnings(argv=argv),
        )

    def _resolve_executable(
        self,
        *,
        executable: Path,
        architecture: str,
    ) -> Path:
        """Resolve the QEMU binary when argv[0] is only a name.

        Args:
            executable: ``argv[0]`` as captured.
            architecture: QEMU architecture suffix.

        Returns:
            An existing executable when one can be found, else ``executable``.

        """
        if executable.is_absolute() and executable.is_file():
            return executable
        name: str = f"{self.QEMU_PREFIX}{architecture}"
        on_path: str | None = which(cmd=name)
        if on_path is not None:
            return Path(on_path)
        roots: tuple[Path, ...] = tuple(
            Path(app) if app.startswith("/") else self._home / app
            for app in self.UTM_APPS
        )
        return next(
            (
                candidate
                for root in roots
                if root.is_dir()
                for candidate in root.rglob(pattern=name)
                if candidate.is_file()
            ),
            executable,
        )

    def _qemu_processes(self) -> tuple[_Process, ...]:
        """Return QEMU process rows.

        Returns:
            Rows with a non-empty argv.

        """
        rows: tuple[tuple[int, tuple[str, ...]], ...] = (
            self._process_rows
            if self._process_rows is not None
            else self._read_process_rows()
        )
        return tuple(
            _Process(pid=pid, argv=self._emulator_argv(argv=argv))
            for pid, argv in rows
            if argv and argv[0]
        )

    def _looks_like_utm(self, *, process: _Process) -> bool:
        """Return whether a QEMU process appears to be UTM-owned.

        Args:
            process: Process row.

        Returns:
            ``True`` for QEMU processes carrying UTM markers.

        """
        return self._is_qemu(process=process) and any(
            marker in argument
            for argument in process.argv
            for marker in self.UTM_MARKERS
        )

    def _is_qemu(self, *, process: _Process) -> bool:
        """Return whether a process executable is QEMU.

        Args:
            process: Process row.

        Returns:
            ``True`` for ``qemu-system-*`` executables.

        """
        return bool(process.argv) and self._is_emulator(
            name=Path(process.argv[0]).name,
        )

    def _bundle_from_path(self, *, path: Path) -> UtmBundle:
        """Inspect basic metadata from a UTM bundle.

        Args:
            path: The ``.utm`` directory.

        Returns:
            The bundle description.

        """
        config: Path = path / "config.plist"
        values: dict[str, str] = (
            self._plist_strings(path=config) if config.is_file() else {}
        )
        return UtmBundle(
            path=path,
            config=config if config.is_file() else None,
            name=values.get("Name", path.stem),
            uuid=values.get("UUID"),
            architecture=values.get("Architecture"),
        )
