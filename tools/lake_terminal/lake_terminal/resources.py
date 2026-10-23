# Copyright (c) 2026 Project contributors.
"""Portable per-session process and resource telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, Final, NamedTuple, Protocol, cast

from .process import run

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from subprocess import CompletedProcess
    from types import ModuleType

    from .models import SessionEntry


class NetworkAddress(NamedTuple):
    """Represent one typed network endpoint.

    Attributes:
        ip: IP address.
        port: TCP or UDP port.

    """

    ip: str
    port: int


class NetworkConnection(NamedTuple):
    """Represent the psutil connection fields consumed by Lake Terminal.

    Attributes:
        fd: Socket file descriptor.
        family: Address family.
        type: Socket type.
        laddr: Local endpoint.
        raddr: Remote endpoint.
        status: Connection status.
        pid: Owning process id.

    """

    fd: int
    family: int
    type: int
    laddr: NetworkAddress | tuple[()]
    raddr: NetworkAddress | tuple[()]
    status: str
    pid: int | None = None


class IoCounters(NamedTuple):
    """Represent process I/O counters consumed by Lake Terminal.

    Attributes:
        read_count: Read operations.
        write_count: Write operations.
        read_bytes: Bytes read.
        write_bytes: Bytes written.
        read_chars: Characters read.
        write_chars: Characters written.

    """

    read_count: int
    write_count: int
    read_bytes: int
    write_bytes: int
    read_chars: int
    write_chars: int


class IoCountersMethod(Protocol):
    """Describe the psutil process I/O counter operation."""

    def __call__(self) -> IoCounters:
        """Return process I/O counters.

        Returns:
            Process I/O counters.

        """
        ...


class MemoryInfo(NamedTuple):
    """Represent the psutil memory fields consumed by Lake Terminal.

    Attributes:
        rss: Resident set size.
        vms: Virtual memory size.

    """

    rss: int
    vms: int


class ProcessProtocol(Protocol):
    """Describe the psutil process API surface consumed by Lake Terminal.

    Attributes:
        pid: Process id.

    """

    pid: int

    def __init__(self, pid: int) -> None:
        """Bind to a running process.

        Args:
            pid: Process id.

        """
        ...

    def children(self, *, recursive: bool) -> list[ProcessProtocol]:
        """Return child processes.

        Args:
            recursive: Whether to include the whole tree.

        Returns:
            Child processes.

        """
        ...

    def oneshot(self) -> AbstractContextManager[None]:
        """Return a cached single-shot inspection context.

        Returns:
            Context manager enabling psutil result caching.

        """
        ...

    def net_connections(self, *, kind: str) -> list[NetworkConnection]:
        """Return process network connections.

        Args:
            kind: Socket family selector.

        Returns:
            Process network connections.

        """
        ...

    def cmdline(self) -> list[str]:
        """Return the process command line.

        Returns:
            Command-line argument vector.

        """
        ...

    def ppid(self) -> int:
        """Return the parent process id.

        Returns:
            Parent process id.

        """
        ...

    def name(self) -> str:
        """Return the process name.

        Returns:
            Process name.

        """
        ...

    def cpu_percent(self, *, interval: float | None) -> float:
        """Return process CPU utilisation.

        Args:
            interval: Measurement interval.

        Returns:
            CPU percentage.

        """
        ...

    def memory_info(self) -> MemoryInfo:
        """Return process memory counters.

        Returns:
            Memory counters.

        """
        ...


_psutil: ModuleType = import_module(name="psutil")
AccessDenied: type[Exception] = cast(
    typ="type[Exception]",
    val=_psutil.AccessDenied,
)
NoSuchProcess: type[Exception] = cast(
    typ="type[Exception]",
    val=_psutil.NoSuchProcess,
)
Process: type[ProcessProtocol] = cast(
    typ="type[ProcessProtocol]",
    val=_psutil.Process,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessResource:
    """Resource snapshot for one process in a managed tree.

    Attributes:
        pid: Pid.
        parent_pid: Parent pid.
        name: Name.
        command: Command.
        cpu_percent: Cpu percent.
        rss_bytes: Rss bytes.
        vms_bytes: Vms bytes.
        read_bytes: Read bytes.
        write_bytes: Write bytes.
        connections: Connections.
        listening_ports: Listening ports.

    """

    pid: int
    parent_pid: int
    name: str
    command: str
    cpu_percent: float
    rss_bytes: int
    vms_bytes: int
    read_bytes: int
    write_bytes: int
    connections: tuple[str, ...]
    listening_ports: tuple[int, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceSnapshot:
    """Aggregated resources for one Lake session.

    Attributes:
        processes: Processes.
        cpu_percent: Cpu percent.
        rss_bytes: Rss bytes.
        vms_bytes: Vms bytes.
        read_bytes: Read bytes.
        write_bytes: Write bytes.
        connections: Connections.
        listening_ports: Listening ports.
        partial: Partial.
        network_bytes_supported: Network bytes supported.

    """

    processes: tuple[ProcessResource, ...]
    cpu_percent: float
    rss_bytes: int
    vms_bytes: int
    read_bytes: int
    write_bytes: int
    connections: tuple[str, ...]
    listening_ports: tuple[int, ...]
    partial: bool
    network_bytes_supported: bool = False

    @property
    def process_count(self) -> int:
        """Return the number of observed processes.

        Returns:
            The resulting value.

        """
        return len(self.processes)


class ResourceCollector:
    """Collect process-tree telemetry without inventing unavailable metrics.

    Attributes:
        socket_kinds: Socket kinds.

    """

    socket_kinds: Final[frozenset[str]] = frozenset(
        {"LISTEN", "ESTABLISHED"},
    )

    @classmethod
    def collect(cls, *, entry: SessionEntry) -> ResourceSnapshot:
        """Collect one resource snapshot rooted at the registered process.

        Args:
            entry: Entry.

        Returns:
            The resulting value.

        """
        root_pid: int = cls.root_pid(entry=entry)
        if root_pid <= 0:
            return cls.empty()
        partial: bool = False
        processes: list[ProcessResource] = []
        try:
            root: ProcessProtocol = Process(pid=root_pid)
            children: list[ProcessProtocol] = root.children(recursive=True)
            candidates: list[ProcessProtocol] = [root, *children]
        except (AccessDenied, NoSuchProcess, ProcessLookupError):
            return cls.empty(partial=True)
        for process in candidates:
            try:
                process_resource: ProcessResource = cls.process_resource(
                    process=process,
                )
                processes.append(process_resource)
            except (AccessDenied, NoSuchProcess, ProcessLookupError):
                partial = True
        all_connections: tuple[str, ...] = tuple(
            sorted(
                {value for item in processes for value in item.connections},
            ),
        )
        all_ports: tuple[int, ...] = tuple(
            sorted(
                {
                    value
                    for item in processes
                    for value in item.listening_ports
                },
            ),
        )
        cpu_percent: float = sum(item.cpu_percent for item in processes)
        rss_bytes: int = sum(item.rss_bytes for item in processes)
        vms_bytes: int = sum(item.vms_bytes for item in processes)
        read_bytes: int = sum(item.read_bytes for item in processes)
        write_bytes: int = sum(item.write_bytes for item in processes)
        return ResourceSnapshot(
            processes=tuple(processes),
            cpu_percent=cpu_percent,
            rss_bytes=rss_bytes,
            vms_bytes=vms_bytes,
            read_bytes=read_bytes,
            write_bytes=write_bytes,
            connections=all_connections,
            listening_ports=all_ports,
            partial=partial,
        )

    @classmethod
    def process_resource(
        cls,
        *,
        process: ProcessProtocol,
    ) -> ProcessResource:
        """Collect resource information for one process.

        Args:
            process: Process to inspect.

        Returns:
            Resource information for the process.

        """

        def empty_io() -> IoCounters:
            """Return zeroed counters on platforms without I/O metrics.

            Returns:
                Process I/O counters.

            """
            return IoCounters(
                read_count=0,
                write_count=0,
                read_bytes=0,
                write_bytes=0,
                read_chars=0,
                write_chars=0,
            )

        with process.oneshot():
            io_counters: IoCountersMethod = cast(
                typ="IoCountersMethod",
                val=getattr(process, "io_counters", empty_io),
            )
            io: IoCounters = io_counters()
            sockets: list[NetworkConnection] = process.net_connections(
                kind="inet",
            )
            connections: tuple[str, ...] = cls.connections(
                sockets=sockets,
            )
            ports: tuple[int, ...] = cls.listening_ports(
                sockets=sockets,
            )
            command_parts: list[str] = process.cmdline()
            return ProcessResource(
                pid=process.pid,
                parent_pid=process.ppid(),
                name=process.name(),
                command=" ".join(command_parts),
                cpu_percent=process.cpu_percent(interval=None),
                rss_bytes=(memory := process.memory_info()).rss,
                vms_bytes=memory.vms,
                read_bytes=io.read_bytes,
                write_bytes=io.write_bytes,
                connections=connections,
                listening_ports=ports,
            )

    @classmethod
    def connections(
        cls,
        *,
        sockets: list[NetworkConnection],
    ) -> tuple[str, ...]:
        """Return active socket endpoints.

        Args:
            sockets: Process network connections.

        Returns:
            Formatted active socket endpoints.

        """
        connections: list[str] = []
        for socket in sockets:
            if socket.status not in cls.socket_kinds or not socket.laddr:
                continue
            local_address: NetworkAddress = socket.laddr
            local: str = f"{local_address.ip}:{local_address.port}"
            if socket.raddr:
                remote_address: NetworkAddress = socket.raddr
                remote: str = f" -> {remote_address.ip}:{remote_address.port}"
            else:
                remote = ""
            connections.append(f"{local}{remote}")
        return tuple(sorted(connections))

    @staticmethod
    def empty(*, partial: bool = False) -> ResourceSnapshot:
        """Return an empty explicit telemetry snapshot.

        Args:
            partial: Partial.

        Returns:
            The resulting value.

        """
        return ResourceSnapshot(
            processes=(),
            cpu_percent=0.0,
            rss_bytes=0,
            vms_bytes=0,
            read_bytes=0,
            write_bytes=0,
            connections=(),
            listening_ports=(),
            partial=partial,
        )

    @staticmethod
    def listening_ports(
        *,
        sockets: list[NetworkConnection],
    ) -> tuple[int, ...]:
        """Return ports on which processes are listening.

        Args:
            sockets: Process network connections.

        Returns:
            Sorted unique listening ports.

        """
        ports: set[int] = set()
        for socket in sockets:
            if socket.status != "LISTEN" or not socket.laddr:
                continue
            local_address: NetworkAddress = socket.laddr
            ports.add(local_address.port)
        return tuple(sorted(ports))

    @staticmethod
    def root_pid(*, entry: SessionEntry) -> int:
        """Resolve the process root owned by the managed terminal.

        Args:
            entry: Entry.

        Returns:
            The resulting value.

        """
        if entry.backend != "tmux" or not entry.session:
            return entry.pid
        try:
            result: CompletedProcess[str] = run(
                args=(
                    "tmux",
                    "display-message",
                    "-p",
                    "-t",
                    entry.session,
                    "#{pane_pid}",
                ),
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return entry.pid
        value: str = (result.stdout or "").strip()
        return (
            int(value)
            if result.returncode == 0 and value.isdigit()
            else entry.pid
        )
