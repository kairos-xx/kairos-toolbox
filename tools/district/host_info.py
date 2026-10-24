# Copyright (c) 2026 Project contributors.
"""Collect facts about the machine a district runs on, for the overview."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from platform import mac_ver, node, release
from sys import version_info
from typing import TYPE_CHECKING, Final, Protocol, cast, final

from psutil import (
    Error as PsutilError,
)
from psutil import (
    Process,
    boot_time,
    cpu_count,
    cpu_percent,
    disk_usage,
    getloadavg,
    process_iter,
    swap_memory,
    virtual_memory,
)

from .daemon import DistrictDaemon
from .pairing import LanNetwork
from .platform import RuntimePlatform
from .product import Product, state_directory
from .storage import DistrictDatabase

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from psutil._ntuples import sdiskusage, sswap, svmem

    from .json_config import JsonValue


class BatteryReading(Protocol):
    """Describe the psutil battery fields district consumes."""

    @property
    def percent(self) -> float:
        """Return the charge, 0 to 100.

        Returns:
            Charge percentage.

        """
        ...

    @property
    def secsleft(self) -> int:
        """Return the estimated seconds of charge left.

        Returns:
            Seconds, or a negative code when unknown or unlimited.

        """
        ...

    @property
    def power_plugged(self) -> bool | None:
        """Return whether mains power is connected.

        Returns:
            True or False, or None when unknown.

        """
        ...


@final
class HostConstants:
    """Group host-information constants.

    Attributes:
        database_name: File name of the district database.
        percent_places: Decimal places kept for percentages.
        unknown_seconds: Battery time-left codes below this mean "unknown".

    """

    database_name: Final[str] = "district.db"
    percent_places: Final[int] = 1
    unknown_seconds: Final[int] = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class CpuFacts:
    """Describe processor capacity and current load.

    Attributes:
        logical: Logical core count.
        physical: Physical core count, when the platform reports it.
        percent: Mean utilisation across cores, 0 to 100.
        per_core: Utilisation of each logical core, 0 to 100.
        load: One, five and fifteen minute load averages, when reported.

    """

    logical: int
    physical: int | None
    percent: float
    per_core: tuple[float, ...]
    load: tuple[float, float, float] | None


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryFacts:
    """Describe physical memory and swap.

    Attributes:
        total: Installed memory in bytes.
        used: Memory in use in bytes.
        available: Memory that can be given out without swapping.
        percent: Share of memory in use, 0 to 100.
        swap_total: Swap size in bytes.
        swap_used: Swap in use in bytes.

    """

    total: int
    used: int
    available: int
    percent: float
    swap_total: int
    swap_used: int


@dataclass(frozen=True, slots=True, kw_only=True)
class DiskFacts:
    """Describe the volume that holds the district state.

    Attributes:
        path: Directory the figures were measured on.
        total: Volume size in bytes.
        used: Bytes in use.
        free: Bytes available.
        percent: Share of the volume in use, 0 to 100.

    """

    path: str
    total: int
    used: int
    free: int
    percent: float


@dataclass(frozen=True, slots=True, kw_only=True)
class BatteryFacts:
    """Describe the battery of a portable machine.

    Attributes:
        percent: Charge, 0 to 100.
        plugged: Whether mains power is connected, when known.
        seconds_left: Estimated seconds of charge left, when known.

    """

    percent: float
    plugged: bool | None
    seconds_left: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class NetworkFacts:
    """Describe one private network address of the host.

    Attributes:
        interface: Interface name.
        address: IPv4 address on that interface.
        network: The network the address belongs to.

    """

    interface: str
    address: str
    network: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessFacts:
    """Describe one of the machine's largest processes.

    Attributes:
        pid: Process id.
        name: Executable name.
        rss_bytes: Resident memory in bytes.

    """

    pid: int
    name: str
    rss_bytes: int


@dataclass(frozen=True, slots=True, kw_only=True)
class DistrictFacts:
    """Describe this district installation.

    Attributes:
        version: Installed District Terminal version.
        daemon_running: Whether the session daemon answers.
        database_bytes: Size of the state database.
        schema_version: Version of the database schema.
        state_directory: Where district keeps its state.

    """

    version: str
    daemon_running: bool
    database_bytes: int
    schema_version: int
    state_directory: str


@dataclass(frozen=True, slots=True, kw_only=True)
class HostSnapshot:
    """Describe the host at one moment.

    Attributes:
        hostname: Machine name.
        system: Operating system name.
        system_version: Operating system release.
        architecture: CPU architecture.
        python: Interpreter version running district.
        uptime_seconds: Seconds since the machine started.
        cpu: Processor facts.
        memory: Memory facts.
        disk: Disk facts.
        battery: Battery facts, or None on a machine without one.
        networks: Private network addresses.
        processes: Largest processes by resident memory.
        district: Facts about this district installation.

    """

    hostname: str
    system: str
    system_version: str
    architecture: str
    python: str
    uptime_seconds: float
    cpu: CpuFacts
    memory: MemoryFacts
    disk: DiskFacts
    battery: BatteryFacts | None
    networks: tuple[NetworkFacts, ...]
    processes: tuple[ProcessFacts, ...]
    district: DistrictFacts

    def as_json(self) -> dict[str, JsonValue]:
        """Render the snapshot in the dashboard's camelCase JSON style.

        Returns:
            A JSON-compatible mapping.

        """
        battery: BatteryFacts | None = self.battery
        return {
            "hostname": self.hostname,
            "system": self.system,
            "systemVersion": self.system_version,
            "architecture": self.architecture,
            "python": self.python,
            "uptimeSeconds": self.uptime_seconds,
            "cpu": {
                "logical": self.cpu.logical,
                "physical": self.cpu.physical,
                "percent": self.cpu.percent,
                "perCore": list(self.cpu.per_core),
                "load": None if self.cpu.load is None else list(self.cpu.load),
            },
            "memory": {
                "totalBytes": self.memory.total,
                "usedBytes": self.memory.used,
                "availableBytes": self.memory.available,
                "percent": self.memory.percent,
                "swapTotalBytes": self.memory.swap_total,
                "swapUsedBytes": self.memory.swap_used,
            },
            "disk": {
                "path": self.disk.path,
                "totalBytes": self.disk.total,
                "usedBytes": self.disk.used,
                "freeBytes": self.disk.free,
                "percent": self.disk.percent,
            },
            "battery": (
                None
                if battery is None
                else {
                    "percent": battery.percent,
                    "plugged": battery.plugged,
                    "secondsLeft": battery.seconds_left,
                }
            ),
            "networks": [
                {
                    "interface": item.interface,
                    "address": item.address,
                    "network": item.network,
                }
                for item in self.networks
            ],
            "processes": [
                {
                    "pid": item.pid,
                    "name": item.name,
                    "rssBytes": item.rss_bytes,
                }
                for item in self.processes
            ],
            "district": {
                "version": self.district.version,
                "daemonRunning": self.district.daemon_running,
                "databaseBytes": self.district.database_bytes,
                "schemaVersion": self.district.schema_version,
                "stateDirectory": self.district.state_directory,
            },
        }


@final
class HostInfo:
    """Gather a :class:`HostSnapshot` from the operating system."""

    @classmethod
    def collect(
        cls,
        *,
        home: Path,
        process_limit: int,
        now: float,
    ) -> HostSnapshot:
        """Measure the host.

        CPU utilisation is measured since the previous call, so the first
        call after the process starts reports zero.

        Args:
            home: Home directory whose district state is described.
            process_limit: How many of the largest processes to list.
            now: Current time as seconds since the epoch, injected so the
                uptime is reproducible.

        Returns:
            The snapshot.

        """
        runtime: RuntimePlatform = RuntimePlatform.detect()
        return HostSnapshot(
            hostname=node(),
            system=runtime.os,
            system_version=mac_ver()[0] or release(),
            architecture=runtime.architecture,
            python=(
                f"{version_info.major}.{version_info.minor}"
                f".{version_info.micro}"
            ),
            uptime_seconds=round(
                number=now - boot_time(),
                ndigits=HostConstants.percent_places,
            ),
            cpu=cls._cpu(),
            memory=cls._memory(),
            disk=cls._disk(home=home),
            battery=cls._battery(),
            networks=tuple(
                NetworkFacts(
                    interface=item.interface,
                    address=item.address,
                    network=str(object=item.network),
                )
                for item in LanNetwork.addresses()
            ),
            processes=cls._processes(limit=process_limit),
            district=cls._district(home=home),
        )

    @staticmethod
    def _cpu() -> CpuFacts:
        """Measure processor load.

        Returns:
            Core counts, utilisation and load averages.

        """
        per_core: list[float] = [
            round(number=value, ndigits=HostConstants.percent_places)
            for value in cpu_percent(interval=None, percpu=True)
        ]
        logical: int = cpu_count(logical=True) or len(per_core) or 1
        try:
            load: tuple[float, float, float] | None = getloadavg()
        except OSError:
            load = None
        return CpuFacts(
            logical=logical,
            physical=cpu_count(logical=False),
            percent=round(
                number=sum(per_core) / len(per_core) if per_core else 0.0,
                ndigits=HostConstants.percent_places,
            ),
            per_core=tuple(per_core),
            load=load,
        )

    @staticmethod
    def _memory() -> MemoryFacts:
        """Measure physical memory and swap.

        Returns:
            Memory figures in bytes.

        """
        memory: svmem = virtual_memory()
        swap: sswap = swap_memory()
        return MemoryFacts(
            total=memory.total,
            used=memory.total - memory.available,
            available=memory.available,
            percent=round(
                number=(memory.total - memory.available)
                * 100.0
                / memory.total,
                ndigits=HostConstants.percent_places,
            ),
            swap_total=swap.total,
            swap_used=swap.used,
        )

    @staticmethod
    def _disk(*, home: Path) -> DiskFacts:
        """Measure the volume holding the district state.

        Args:
            home: Home directory.

        Returns:
            Disk figures in bytes.

        """
        usage: sdiskusage = disk_usage(path=str(object=home))
        return DiskFacts(
            path=str(object=home),
            total=usage.total,
            used=usage.used,
            free=usage.free,
            percent=round(
                number=usage.percent,
                ndigits=HostConstants.percent_places,
            ),
        )

    @staticmethod
    def _battery() -> BatteryFacts | None:
        """Read the battery, when the machine has one.

        Returns:
            Battery facts, or None on a machine without a battery.

        """
        reader: Callable[[], BatteryReading | None] = cast(
            typ="Callable[[], BatteryReading | None]",
            val=import_module(name="psutil").sensors_battery,
        )
        battery: BatteryReading | None = reader()
        if battery is None:
            return None
        seconds: int = battery.secsleft
        return BatteryFacts(
            percent=round(
                number=battery.percent,
                ndigits=HostConstants.percent_places,
            ),
            plugged=battery.power_plugged,
            seconds_left=(
                seconds if seconds > HostConstants.unknown_seconds else None
            ),
        )

    @staticmethod
    def _processes(*, limit: int) -> tuple[ProcessFacts, ...]:
        """List the processes using the most resident memory.

        A process that exits or denies access mid-scan is skipped.

        Args:
            limit: Maximum rows to return.

        Returns:
            Processes ordered from largest to smallest.

        """
        found: list[ProcessFacts] = []
        process: Process
        for process in process_iter():
            try:
                found.append(
                    ProcessFacts(
                        pid=process.pid,
                        name=process.name(),
                        rss_bytes=process.memory_info().rss,
                    ),
                )
            except PsutilError:
                continue
        found.sort(key=lambda item: item.rss_bytes, reverse=True)
        return tuple(found[:limit])

    @staticmethod
    def _district(*, home: Path) -> DistrictFacts:
        """Describe the district installation under ``home``.

        Args:
            home: Home directory.

        Returns:
            Version, daemon state and database facts.

        """
        directory: Path = state_directory(home=home)
        database: Path = directory / HostConstants.database_name
        return DistrictFacts(
            version=Product.version,
            daemon_running=DistrictDaemon.ping(home=home),
            database_bytes=database.stat().st_size if database.exists() else 0,
            schema_version=DistrictDatabase(home=home).schema_version(),
            state_directory=str(object=directory),
        )
