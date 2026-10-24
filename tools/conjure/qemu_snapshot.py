# Copyright (c) 2026 Project contributors.
"""QEMU qcow2 and QMP snapshot management.

Two layers cover returning a VM to a known state:

* :class:`QemuSnapshotManager` drives ``qemu-img snapshot`` on an *offline*
  qcow2 disk: create a baseline, revert to it, list, prune.
* :class:`QmpClient` and :class:`QemuVmSnapshotManager` drive a *running* VM
  through the QEMU Machine Protocol (``savevm``/``loadvm``/``delvm``).

:meth:`QemuSnapshotManager.reset_to` is the building block ``QemuVm`` uses to
boot from, and leave behind, the original state of its disk.
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from json import JSONDecodeError, dumps, loads
from pathlib import Path
from re import Pattern
from re import compile as compile_pattern
from shutil import which
from socket import AF_UNIX, SOCK_STREAM, create_connection, socket
from subprocess import CalledProcessError, CompletedProcess, run
from sys import stdout
from typing import TYPE_CHECKING, Final, Self, TextIO, cast, final

from tools.conjure.errors import QemuRunnerError

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from types import TracebackType
type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)
type JsonObject = dict[str, JsonValue]
type SnapshotRows = list[Snapshot]


@final
class SnapshotConstants:
    """Group snapshot-management constants.

    Attributes:
        qemu_img: Default ``qemu-img`` executable name.
        name_pattern: Characters a snapshot name may contain.
        qmp_capabilities: QMP command that ends capability negotiation.
        human_monitor_command: QMP command that runs an HMP line.
        default_qmp_timeout: Seconds before a QMP socket operation fails.
        snapshot_fields: Columns of a ``qemu-img snapshot -l`` row.
        paused_states: Run states in which the VM is paused.
        stopped_states: Run states in which the VM has stopped for good.
        error_words: Words that mark a failed HMP command.

    """

    qemu_img: Final[str] = "qemu-img"
    name_pattern: Final[Pattern[str]] = compile_pattern(
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    qmp_capabilities: Final[str] = "qmp_capabilities"
    human_monitor_command: Final[str] = "human-monitor-command"
    default_qmp_timeout: Final[float] = 10.0
    snapshot_fields: Final[int] = 6
    paused_states: Final[frozenset[str]] = frozenset(
        {"paused", "prelaunch", "suspended"},
    )
    stopped_states: Final[frozenset[str]] = frozenset(
        {"shutdown", "guest-panicked"},
    )
    error_words: Final[tuple[str, ...]] = (
        "error",
        "failed",
        "not supported",
    )


class QemuSnapshotError(QemuRunnerError):
    """Report a snapshot-management failure."""


class SnapshotNotFoundError(QemuSnapshotError):
    """Report a snapshot that does not exist."""


class SnapshotExistsError(QemuSnapshotError):
    """Report a creation that would overwrite a snapshot."""


class UnsupportedImageError(QemuSnapshotError):
    """Report an image that cannot hold internal snapshots."""


class QmpError(QemuSnapshotError):
    """Report a QEMU Machine Protocol failure."""


class QmpCommandError(QmpError):
    """Report a QMP command that QEMU rejected."""


class QmpConnectionError(QmpError):
    """Report a QMP connection that could not be used."""


class SnapshotAction(StrEnum):
    """Name the supported snapshot actions.

    Attributes:
        create: Take a snapshot.
        delete: Remove a snapshot.
        revert: Restore a snapshot.

    """

    create = "create"
    delete = "delete"
    revert = "revert"


class VmRunState(StrEnum):
    """Name the relevant QEMU run states.

    Attributes:
        running: The guest CPUs are executing.
        paused: Execution is paused or not yet started.
        shutdown: The guest has stopped for good.
        unknown: Any other QEMU state.

    """

    running = "running"
    paused = "paused"
    shutdown = "shutdown"
    unknown = "unknown"


@dataclass(frozen=True, slots=True, kw_only=True)
class Snapshot:
    """Describe one internal qcow2 snapshot.

    Attributes:
        identifier: Numeric snapshot id.
        name: Snapshot tag.
        vm_size: Size of the saved VM state.
        date: Creation date and time.
        vm_clock: Guest clock at creation.

    """

    identifier: str
    name: str
    vm_size: str
    date: str
    vm_clock: str

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-compatible representation.

        Returns:
            The snapshot as plain text fields.

        """
        return {
            "identifier": self.identifier,
            "name": self.name,
            "vm_size": self.vm_size,
            "date": self.date,
            "vm_clock": self.vm_clock,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ImageInfo:
    """Describe a QEMU disk image.

    Attributes:
        path: Image file.
        format: Image format, such as ``qcow2``.
        virtual_size: Size the guest sees, in bytes.
        actual_size: Size on the host, in bytes.
        cluster_size: qcow2 cluster size, when reported.
        backing_file: Backing image, when there is one.
        encrypted: Whether the image is encrypted.

    """

    path: Path
    format: str
    virtual_size: int
    actual_size: int
    cluster_size: int | None
    backing_file: str | None
    encrypted: bool

    def to_dict(self) -> dict[str, str | int | bool | None]:
        """Return a JSON-compatible representation.

        Returns:
            The image metadata as plain values.

        """
        return {
            "path": str(object=self.path),
            "format": self.format,
            "virtual_size": self.virtual_size,
            "actual_size": self.actual_size,
            "cluster_size": self.cluster_size,
            "backing_file": self.backing_file,
            "encrypted": self.encrypted,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class CommandPlan:
    """Describe a ``qemu-img`` command without running it.

    Attributes:
        action: What the command does.
        snapshot: Snapshot the command targets.
        command: Complete argument vector.

    """

    action: SnapshotAction
    snapshot: str
    command: tuple[str, ...]

    def shell(self) -> str:
        """Return a readable shell form of the command.

        Returns:
            The argument vector joined by spaces.

        """
        return " ".join(self.command)


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationResult:
    """Describe a completed or planned disk operation.

    Attributes:
        action: What was done.
        snapshot: Snapshot it targeted.
        image: Disk image it ran against.
        command: Argument vector that was or would be run.
        changed: Whether the image was (or would be) modified.
        dry_run: Whether the command was only planned.

    """

    action: SnapshotAction
    snapshot: str
    image: Path
    command: tuple[str, ...]
    changed: bool
    dry_run: bool

    def to_dict(self) -> dict[str, str | bool | list[str]]:
        """Return a JSON-compatible representation.

        Returns:
            The result as plain values.

        """
        return {
            "action": self.action.value,
            "snapshot": self.snapshot,
            "image": str(object=self.image),
            "command": list(self.command),
            "changed": self.changed,
            "dry_run": self.dry_run,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class QmpEndpoint:
    """Describe a QMP transport endpoint.

    Attributes:
        unix_socket: Unix socket path, for a local VM.
        host: TCP host, for a networked monitor.
        port: TCP port, for a networked monitor.

    """

    unix_socket: Path | None = None
    host: str | None = None
    port: int | None = None

    def validate(self) -> None:
        """Check that exactly one transport is configured.

        Raises:
            QmpConnectionError: If both or neither transport is set, or the
                TCP form lacks its host or port.

        """
        unix_mode: bool = self.unix_socket is not None
        tcp_mode: bool = self.host is not None or self.port is not None
        if unix_mode == tcp_mode:
            msg: str = "Configure either unix_socket or host and port."
            raise QmpConnectionError(msg)
        if tcp_mode and (self.host is None or self.port is None):
            msg = "TCP QMP requires both host and port."
            raise QmpConnectionError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class QmpGreeting:
    """Describe the QMP server greeting.

    Attributes:
        major: QEMU major version.
        minor: QEMU minor version.
        micro: QEMU micro version.
        package: Distribution package string.
        capabilities: Capabilities the server offers.

    """

    major: int
    minor: int
    micro: int
    package: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class VmStatus:
    """Describe the current VM run state.

    Attributes:
        running: Whether the guest CPUs are executing.
        status: QEMU's run-state name.

    """

    running: bool
    status: str

    @property
    def state(self) -> VmRunState:
        """Return the normalized run state.

        Returns:
            Running, paused, shut down, or unknown.

        """
        if self.running:
            return VmRunState.running
        if self.status in SnapshotConstants.paused_states:
            return VmRunState.paused
        if self.status in SnapshotConstants.stopped_states:
            return VmRunState.shutdown
        return VmRunState.unknown


@dataclass(frozen=True, slots=True, kw_only=True)
class BlockDevice:
    """Describe one QMP block device.

    Attributes:
        device: Legacy device name.
        qdev: Device path, when known.
        node_name: Block node name, when inserted.
        inserted_file: File backing the medium, when inserted.
        removable: Whether the medium can be removed.
        locked: Whether the guest locked the medium.
        tray_open: Whether the tray is open.

    """

    device: str
    qdev: str | None
    node_name: str | None
    inserted_file: str | None
    removable: bool
    locked: bool
    tray_open: bool

    def to_dict(self) -> dict[str, str | bool | None]:
        """Return a JSON-compatible representation.

        Returns:
            The device as plain values.

        """
        return {
            "device": self.device,
            "qdev": self.qdev,
            "node_name": self.node_name,
            "inserted_file": self.inserted_file,
            "removable": self.removable,
            "locked": self.locked,
            "tray_open": self.tray_open,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class VmSnapshotResult:
    """Describe a QMP VM snapshot operation.

    Attributes:
        action: What was done.
        snapshot: Snapshot it targeted.
        monitor_output: Text the monitor answered with.
        resumed: Whether the VM was resumed afterwards.

    """

    action: SnapshotAction
    snapshot: str
    monitor_output: str
    resumed: bool


class SnapshotNames:
    """Validate snapshot names shared by both managers."""

    @staticmethod
    def validate(*, name: str) -> None:
        """Reject a name ``qemu-img`` or the monitor could misread.

        Args:
            name: Candidate snapshot name.

        Raises:
            QemuSnapshotError: If the name has unsupported characters.

        """
        if SnapshotConstants.name_pattern.fullmatch(string=name) is None:
            msg: str = (
                "Snapshot names may contain only letters, digits, "
                "'.', '_' and '-'."
            )
            raise QemuSnapshotError(msg)


class QmpClient:
    """Speak the QEMU Machine Protocol over a Unix or TCP socket."""

    def __init__(
        self,
        *,
        endpoint: QmpEndpoint,
        timeout: float = SnapshotConstants.default_qmp_timeout,
    ) -> None:
        """Initialize a client without connecting.

        Args:
            endpoint: Where the QMP server listens.
            timeout: Seconds before a socket operation fails.

        """
        endpoint.validate()
        self._endpoint: Final[QmpEndpoint] = endpoint
        self._timeout: Final[float] = timeout
        self._socket: socket | None = None
        self._reader: TextIO | None = None
        self._greeting: QmpGreeting | None = None
        self._command_id: int = 0

    @property
    def connected(self) -> bool:
        """Return whether the client owns a connected socket.

        Returns:
            ``True`` between :meth:`connect` and :meth:`close`.

        """
        return self._socket is not None

    @property
    def greeting(self) -> QmpGreeting:
        """Return the negotiated greeting.

        Returns:
            The server greeting.

        Raises:
            QmpConnectionError: If the client is not connected.

        """
        if self._greeting is None:
            msg: str = "QMP is not connected."
            raise QmpConnectionError(msg)
        return self._greeting

    def connect(self) -> Self:
        """Connect and negotiate QMP capabilities.

        Returns:
            This client.

        Raises:
            QmpConnectionError: If the server cannot be reached or does not
                speak QMP.

        """
        if self.connected:
            return self
        try:
            connection: socket
            if self._endpoint.unix_socket is not None:
                connection = socket(family=AF_UNIX, type=SOCK_STREAM)
                connection.settimeout(self._timeout)
                connection.connect(str(object=self._endpoint.unix_socket))
            else:
                connection = create_connection(
                    address=(
                        str(object=self._endpoint.host),
                        self._endpoint.port or 0,
                    ),
                    timeout=self._timeout,
                )
            self._socket = connection
            self._reader = connection.makefile(
                mode="r",
                encoding="utf-8",
                newline="\n",
            )
            self._greeting = self._parse_greeting(payload=self._read_message())
            _ = self.execute(command=SnapshotConstants.qmp_capabilities)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.close()
            raise QmpConnectionError(str(object=error)) from error
        return self

    def close(self) -> None:
        """Release every transport resource."""
        if self._reader is not None:
            self._reader.close()
            self._reader = None
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        self._greeting = None

    def execute(
        self,
        *,
        command: str,
        arguments: dict[str, str | int | bool] | None = None,
    ) -> JsonValue:
        """Run one QMP command.

        Args:
            command: QMP command name.
            arguments: Command arguments, if any.

        Returns:
            The command's ``return`` value.

        Raises:
            QmpConnectionError: If the client is not connected.
            QmpCommandError: If QEMU rejects the command.

        """
        if not self.connected:
            msg: str = "QMP is not connected."
            raise QmpConnectionError(msg)
        self._command_id += 1
        command_id: str = f"conjure-snapshot-{self._command_id}"
        payload: JsonObject = {"execute": command, "id": command_id}
        if arguments:
            payload["arguments"] = cast(typ="JsonValue", val=arguments)
        self._send(payload=payload)
        while True:
            response: JsonObject = self._read_message()
            if response.get("id") != command_id:
                continue
            if "error" in response:
                raise QmpCommandError(
                    dumps(obj=response["error"], sort_keys=True),
                )
            return response.get("return")

    def human_monitor_command(self, *, command_line: str) -> str:
        """Run one human-monitor line through QMP.

        Args:
            command_line: The monitor command.

        Returns:
            The monitor's textual answer.

        """
        result: JsonValue = self.execute(
            command=SnapshotConstants.human_monitor_command,
            arguments={"command-line": command_line},
        )
        return "" if result is None else str(object=result)

    def query_status(self) -> VmStatus:
        """Return the VM run state.

        Returns:
            The current status.

        Raises:
            QmpError: If the answer is malformed.

        """
        result: JsonValue = self.execute(command="query-status")
        if not isinstance(result, dict):
            msg: str = "query-status returned an invalid payload."
            raise QmpError(msg)
        running: JsonValue = result.get("running")
        status: JsonValue = result.get("status")
        if not isinstance(running, bool) or not isinstance(status, str):
            msg = "query-status payload is incomplete."
            raise QmpError(msg)
        return VmStatus(running=running, status=status)

    def query_block(self) -> tuple[BlockDevice, ...]:
        """Return the block devices QEMU sees.

        Returns:
            Every reported device.

        Raises:
            QmpError: If the answer is malformed.

        """
        result: JsonValue = self.execute(command="query-block")
        if not isinstance(result, list):
            msg: str = "query-block returned an invalid payload."
            raise QmpError(msg)
        return tuple(
            self._block_device(raw=item)
            for item in result
            if isinstance(item, dict)
        )

    def stop(self) -> VmStatus:
        """Pause guest execution.

        Returns:
            The status afterwards.

        """
        _ = self.execute(command="stop")
        return self.query_status()

    def cont(self) -> VmStatus:
        """Resume guest execution.

        Returns:
            The status afterwards.

        """
        _ = self.execute(command="cont")
        return self.query_status()

    def system_reset(self) -> None:
        """Ask QEMU to reset the machine."""
        _ = self.execute(command="system_reset")

    def system_powerdown(self) -> None:
        """Ask the guest to power down gracefully."""
        _ = self.execute(command="system_powerdown")

    def quit(self) -> None:
        """Ask QEMU to exit."""
        _ = self.execute(command="quit")

    def commands(self) -> tuple[str, ...]:
        """Return the QMP commands this QEMU advertises.

        Returns:
            Command names.

        Raises:
            QmpError: If the answer is malformed.

        """
        result: JsonValue = self.execute(command="query-commands")
        if not isinstance(result, list):
            msg: str = "query-commands returned an invalid payload."
            raise QmpError(msg)
        names: list[str] = []
        for item in result:
            name: JsonValue = (
                item.get("name") if isinstance(item, dict) else None
            )
            if isinstance(name, str):
                names.append(name)
        return tuple(names)

    def supports(self, *, command: str) -> bool:
        """Return whether QEMU advertises a command.

        Args:
            command: QMP command name.

        Returns:
            ``True`` when it is available.

        """
        return command in self.commands()

    def __enter__(self) -> Self:
        """Connect and return this client.

        Returns:
            This client.

        """
        return self.connect()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the connection.

        Args:
            exc_type: Propagated exception type, when an error occurred.
            exc_value: Propagated exception instance, when an error occurred.
            traceback: Propagated exception traceback, when an error occurred.

        """
        del exc_type, exc_value, traceback
        self.close()

    @classmethod
    def _parse_greeting(cls, *, payload: JsonObject) -> QmpGreeting:
        """Decode the server greeting.

        Args:
            payload: The first message the server sent.

        Returns:
            The typed greeting.

        Raises:
            QmpConnectionError: If the greeting is incomplete.

        """
        qmp: JsonValue = payload.get("QMP")
        version: JsonValue = (
            qmp.get("version") if isinstance(qmp, dict) else None
        )
        qemu: JsonValue = (
            version.get("qemu") if isinstance(version, dict) else None
        )
        if (
            not isinstance(qmp, dict)
            or not isinstance(version, dict)
            or not isinstance(qemu, dict)
        ):
            msg: str = "Missing QMP greeting."
            raise QmpConnectionError(msg)
        capabilities: JsonValue = qmp.get("capabilities", [])
        return QmpGreeting(
            major=cls._number(value=qemu.get("major")),
            minor=cls._number(value=qemu.get("minor")),
            micro=cls._number(value=qemu.get("micro")),
            package=str(object=version.get("package", "")),
            capabilities=(
                tuple(str(object=item) for item in capabilities)
                if isinstance(capabilities, list)
                else ()
            ),
        )

    @staticmethod
    def _block_device(*, raw: JsonObject) -> BlockDevice:
        """Convert one ``query-block`` entry.

        Args:
            raw: Decoded device entry.

        Returns:
            The typed device.

        """
        inserted_value: JsonValue = raw.get("inserted")
        inserted: JsonObject = (
            inserted_value if isinstance(inserted_value, dict) else {}
        )
        qdev: JsonValue = raw.get("qdev")
        node: JsonValue = inserted.get("node-name")
        file: JsonValue = inserted.get("file")
        return BlockDevice(
            device=str(object=raw.get("device", "")),
            qdev=None if qdev is None else str(object=qdev),
            node_name=None if node is None else str(object=node),
            inserted_file=None if file is None else str(object=file),
            removable=bool(raw.get("removable", False)),
            locked=bool(raw.get("locked", False)),
            tray_open=bool(raw.get("tray_open", False)),
        )

    @staticmethod
    def _number(*, value: JsonValue) -> int:
        """Read an integer version field.

        Args:
            value: Decoded field.

        Returns:
            The integer, or ``0`` when the field is not one.

        """
        return value if isinstance(value, int) else 0

    def _send(self, *, payload: JsonObject) -> None:
        """Write one command to the socket.

        Args:
            payload: Command object.

        Raises:
            QmpConnectionError: If the socket is gone.

        """
        connection: socket | None = self._socket
        if connection is None:
            msg: str = "QMP is not connected."
            raise QmpConnectionError(msg)
        data: bytes = (
            dumps(obj=payload, separators=(",", ":")) + "\r\n"
        ).encode(encoding="utf-8")
        try:
            connection.sendall(data)
        except OSError as error:
            raise QmpConnectionError(str(object=error)) from error

    def _read_message(self) -> JsonObject:
        """Read the next JSON object from the socket.

        Returns:
            The decoded message.

        Raises:
            QmpConnectionError: If the connection closes or breaks.

        """
        reader: TextIO | None = self._reader
        if reader is None:
            msg: str = "QMP is not connected."
            raise QmpConnectionError(msg)
        while True:
            try:
                line: str = reader.readline()
                if not line:
                    msg = "QMP connection closed."
                    raise QmpConnectionError(msg)
                payload: JsonValue = loads(s=line)
            except (OSError, JSONDecodeError) as error:
                raise QmpConnectionError(str(object=error)) from error
            if isinstance(payload, dict):
                return payload


class QemuVmSnapshotManager:
    """Manage the state of a *running* VM through QMP."""

    def __init__(self, *, client: QmpClient) -> None:
        """Initialize the manager.

        Args:
            client: Connected QMP client.

        """
        self._client: Final[QmpClient] = client

    @property
    def client(self) -> QmpClient:
        """Return the underlying client.

        Returns:
            The QMP client.

        """
        return self._client

    def status(self) -> VmStatus:
        """Return the VM run state.

        Returns:
            The current status.

        """
        return self._client.query_status()

    def block_devices(self) -> tuple[BlockDevice, ...]:
        """Return the attached block devices.

        Returns:
            Every device QEMU reports.

        """
        return self._client.query_block()

    def pause(self) -> VmStatus:
        """Pause the VM.

        Returns:
            The status afterwards.

        """
        return self._client.stop()

    def resume(self) -> VmStatus:
        """Resume the VM.

        Returns:
            The status afterwards.

        """
        return self._client.cont()

    def snapshots(self) -> str:
        """Return the monitor's snapshot listing.

        Returns:
            The ``info snapshots`` text.

        """
        return self._client.human_monitor_command(
            command_line="info snapshots",
        )

    def create(
        self,
        *,
        name: str,
        pause: bool = True,
        resume: bool = True,
    ) -> VmSnapshotResult:
        """Save the VM state with ``savevm``.

        Args:
            name: Snapshot name.
            pause: Pause the VM while it is saved.
            resume: Resume it afterwards.

        Returns:
            The operation result.

        """
        SnapshotNames.validate(name=name)
        return self._operation(
            action=SnapshotAction.create,
            name=name,
            monitor_command=f"savevm {name}",
            pause=pause,
            resume=resume,
        )

    def revert(
        self,
        *,
        name: str,
        pause: bool = True,
        resume: bool = True,
    ) -> VmSnapshotResult:
        """Restore the VM state with ``loadvm``.

        Args:
            name: Snapshot name.
            pause: Pause the VM while it is restored.
            resume: Resume it afterwards.

        Returns:
            The operation result.

        """
        SnapshotNames.validate(name=name)
        return self._operation(
            action=SnapshotAction.revert,
            name=name,
            monitor_command=f"loadvm {name}",
            pause=pause,
            resume=resume,
        )

    def restore(
        self,
        *,
        name: str,
        pause: bool = True,
        resume: bool = True,
    ) -> VmSnapshotResult:
        """Restore a snapshot; an alias for :meth:`revert`.

        Args:
            name: Snapshot name.
            pause: Pause the VM while it is restored.
            resume: Resume it afterwards.

        Returns:
            The operation result.

        """
        return self.revert(name=name, pause=pause, resume=resume)

    def delete(self, *, name: str) -> VmSnapshotResult:
        """Delete a VM snapshot with ``delvm``.

        Args:
            name: Snapshot name.

        Returns:
            The operation result.

        """
        SnapshotNames.validate(name=name)
        output: str = self._client.human_monitor_command(
            command_line=f"delvm {name}",
        )
        self._raise_for_monitor_error(output=output)
        return VmSnapshotResult(
            action=SnapshotAction.delete,
            snapshot=name,
            monitor_output=output,
            resumed=False,
        )

    @contextmanager
    def paused(self, *, resume: bool = True) -> Generator[VmStatus]:
        """Pause around a block and restore the running state after it.

        Args:
            resume: Resume the VM afterwards if this paused it.

        Yields:
            The status while paused.

        """
        before: VmStatus = self.status()
        changed: bool = before.running
        current: VmStatus = self.pause() if changed else before
        try:
            yield current
        finally:
            if changed and resume:
                _ = self.resume()

    @contextmanager
    def checkpoint(
        self,
        *,
        name: str,
        revert_on_error: bool = True,
        delete_on_success: bool = False,
    ) -> Generator[VmSnapshotResult]:
        """Save the VM around a block of work.

        Args:
            name: Snapshot name.
            revert_on_error: Restore the snapshot if the block raises.
            delete_on_success: Delete the snapshot if the block succeeds.

        Yields:
            The creation result.

        """
        created: VmSnapshotResult = self.create(name=name)
        try:
            yield created
        except BaseException:
            if revert_on_error:
                _ = self.revert(name=name)
            raise
        else:
            if delete_on_success:
                _ = self.delete(name=name)

    @staticmethod
    def _raise_for_monitor_error(*, output: str) -> None:
        """Turn a failure message of the monitor into an exception.

        Args:
            output: The monitor's answer.

        Raises:
            QmpCommandError: If the answer reports a failure.

        """
        normalized: str = output.lower()
        if any(word in normalized for word in SnapshotConstants.error_words):
            raise QmpCommandError(output.strip())

    def _operation(
        self,
        *,
        action: SnapshotAction,
        name: str,
        monitor_command: str,
        pause: bool,
        resume: bool,
    ) -> VmSnapshotResult:
        """Run a monitor snapshot command, pausing around it.

        Args:
            action: What the command does.
            name: Snapshot name.
            monitor_command: The HMP line to run.
            pause: Pause the VM first.
            resume: Resume it afterwards.

        Returns:
            The operation result.

        """
        should_pause: bool = pause and self.status().running
        if should_pause:
            _ = self.pause()
        resumed: bool = False
        output: str = ""
        try:
            output = self._client.human_monitor_command(
                command_line=monitor_command,
            )
            self._raise_for_monitor_error(output=output)
        finally:
            if should_pause and resume:
                _ = self.resume()
                resumed = True
        return VmSnapshotResult(
            action=action,
            snapshot=name,
            monitor_output=output,
            resumed=resumed,
        )


class QemuSnapshotManager:
    """Manage internal snapshots of one *offline* qcow2 image."""

    def __init__(
        self,
        *,
        image: Path,
        qemu_img: Path | None = None,
        dry_run: bool = False,
    ) -> None:
        """Initialize the manager and validate the image.

        Args:
            image: qcow2 disk image.
            qemu_img: ``qemu-img`` executable; searched on ``PATH`` if omitted.
            dry_run: Plan mutations instead of running them.

        """
        self._image: Final[Path] = image.expanduser().resolve()
        self._qemu_img: Final[Path] = self._resolve_qemu_img(qemu_img=qemu_img)
        self._dry_run: Final[bool] = dry_run
        self._history: list[OperationResult] = []
        self._validate_image()

    @property
    def image(self) -> Path:
        """Return the managed image.

        Returns:
            The resolved image path.

        """
        return self._image

    @property
    def qemu_img(self) -> Path:
        """Return the ``qemu-img`` executable.

        Returns:
            The resolved executable.

        """
        return self._qemu_img

    @property
    def dry_run(self) -> bool:
        """Return whether mutations are only planned.

        Returns:
            ``True`` in dry-run mode.

        """
        return self._dry_run

    @property
    def history(self) -> tuple[OperationResult, ...]:
        """Return the mutations made through this manager.

        Returns:
            Results in the order they ran.

        """
        return tuple(self._history)

    @property
    def count(self) -> int:
        """Return the number of snapshots.

        Returns:
            How many snapshots the image holds.

        """
        return len(self.list())

    @property
    def empty(self) -> bool:
        """Return whether the image holds no snapshots.

        Returns:
            ``True`` when there are none.

        """
        return not self.list()

    def qemu_img_version(self) -> str:
        """Return the ``qemu-img`` version line.

        Returns:
            The first line of ``qemu-img --version``.

        """
        return (
            self._run(arguments=("--version",)).stdout.splitlines()[0].strip()
        )

    def image_info(self) -> ImageInfo:
        """Return parsed image metadata.

        Returns:
            The image description.

        Raises:
            QemuSnapshotError: If ``qemu-img info`` returns invalid JSON.

        """
        raw: JsonValue = loads(
            s=self._run(
                arguments=("info", "--output=json", str(object=self._image)),
            ).stdout,
        )
        if not isinstance(raw, dict):
            msg: str = "qemu-img info returned invalid JSON."
            raise QemuSnapshotError(msg)
        specific: JsonValue = raw.get("format-specific")
        cluster: JsonValue = raw.get("cluster-size")
        backing: JsonValue = raw.get("backing-filename")
        return ImageInfo(
            path=self._image,
            format=str(object=raw.get("format", "")),
            virtual_size=self._size(value=raw.get("virtual-size")),
            actual_size=self._size(value=raw.get("actual-size")),
            cluster_size=cluster if isinstance(cluster, int) else None,
            backing_file=None if backing is None else str(object=backing),
            encrypted=(
                isinstance(specific, dict) and "encrypt" in dumps(obj=specific)
            ),
        )

    def list(
        self,
        *,
        name_contains: str | None = None,
        reverse: bool = False,
    ) -> tuple[Snapshot, ...]:
        """Return the snapshots, optionally filtered and reversed.

        Args:
            name_contains: Keep only names containing this text.
            reverse: Return newest first.

        Returns:
            The matching snapshots.

        """
        snapshots: list[Snapshot] = self._parse_snapshots(
            output=self._run(
                arguments=("snapshot", "-l", str(object=self._image)),
            ).stdout,
        )
        if name_contains is not None:
            snapshots = [
                snapshot
                for snapshot in snapshots
                if name_contains in snapshot.name
            ]
        if reverse:
            snapshots.reverse()
        return tuple(snapshots)

    def names(self) -> tuple[str, ...]:
        """Return the snapshot names.

        Returns:
            Names in the order ``qemu-img`` lists them.

        """
        return tuple(snapshot.name for snapshot in self.list())

    def identifiers(self) -> tuple[str, ...]:
        """Return the snapshot identifiers.

        Returns:
            Identifiers in the order ``qemu-img`` lists them.

        """
        return tuple(snapshot.identifier for snapshot in self.list())

    def exists(self, *, name: str) -> bool:
        """Return whether a snapshot exists.

        Args:
            name: Snapshot name.

        Returns:
            ``True`` when the image holds it.

        """
        SnapshotNames.validate(name=name)
        return self.find(name=name) is not None

    def find(
        self,
        *,
        name: str | None = None,
        identifier: str | None = None,
    ) -> Snapshot | None:
        """Find a snapshot by exact name or identifier.

        Args:
            name: Snapshot name.
            identifier: Snapshot identifier.

        Returns:
            The snapshot, or ``None``.

        Raises:
            QemuSnapshotError: If not exactly one selector is given.

        """
        if (name is None) == (identifier is None):
            msg: str = "Provide exactly one of name or identifier."
            raise QemuSnapshotError(msg)
        if name is not None:
            SnapshotNames.validate(name=name)
        return next(
            (
                snapshot
                for snapshot in self.list()
                if (
                    snapshot.name == name
                    if name is not None
                    else snapshot.identifier == identifier
                )
            ),
            None,
        )

    def get(
        self,
        *,
        name: str | None = None,
        identifier: str | None = None,
    ) -> Snapshot:
        """Return a snapshot or fail.

        Args:
            name: Snapshot name.
            identifier: Snapshot identifier.

        Returns:
            The snapshot.

        Raises:
            SnapshotNotFoundError: If it does not exist.

        """
        snapshot: Snapshot | None = self.find(name=name, identifier=identifier)
        if snapshot is None:
            selector: str = (
                name if name is not None else str(object=identifier)
            )
            msg: str = f"Snapshot does not exist: {selector}"
            raise SnapshotNotFoundError(msg)
        return snapshot

    def latest(self) -> Snapshot | None:
        """Return the last snapshot ``qemu-img`` lists.

        Returns:
            The snapshot, or ``None`` when there are none.

        """
        snapshots: tuple[Snapshot, ...] = self.list()
        return snapshots[-1] if snapshots else None

    def oldest(self) -> Snapshot | None:
        """Return the first snapshot ``qemu-img`` lists.

        Returns:
            The snapshot, or ``None`` when there are none.

        """
        snapshots: tuple[Snapshot, ...] = self.list()
        return snapshots[0] if snapshots else None

    def plan_create(self, *, name: str) -> CommandPlan:
        """Plan a creation.

        Args:
            name: Snapshot name.

        Returns:
            The command that would run.

        """
        return self._plan(action=SnapshotAction.create, name=name)

    def plan_revert(self, *, name: str) -> CommandPlan:
        """Plan a restoration.

        Args:
            name: Snapshot name.

        Returns:
            The command that would run.

        """
        return self._plan(action=SnapshotAction.revert, name=name)

    def plan_delete(self, *, name: str) -> CommandPlan:
        """Plan a deletion.

        Args:
            name: Snapshot name.

        Returns:
            The command that would run.

        """
        return self._plan(action=SnapshotAction.delete, name=name)

    def create(self, *, name: str, replace: bool = False) -> OperationResult:
        """Create a snapshot of the current disk state.

        Args:
            name: Snapshot name.
            replace: Replace an existing snapshot of that name.

        Returns:
            The operation result.

        Raises:
            SnapshotExistsError: If it exists and ``replace`` is false.

        """
        SnapshotNames.validate(name=name)
        if self.exists(name=name):
            if not replace:
                msg: str = f"Snapshot already exists: {name}"
                raise SnapshotExistsError(msg)
            _ = self.delete(name=name)
        return self._mutate(action=SnapshotAction.create, name=name)

    def ensure(self, *, name: str) -> OperationResult:
        """Create a snapshot unless it already exists.

        Args:
            name: Snapshot name.

        Returns:
            The result; ``changed`` is false when nothing was done.

        """
        SnapshotNames.validate(name=name)
        if self.exists(name=name):
            return self._unchanged(action=SnapshotAction.create, name=name)
        return self.create(name=name)

    def revert(
        self,
        *,
        name: str,
        checkpoint: str | None = None,
    ) -> OperationResult:
        """Restore the disk to a snapshot.

        Args:
            name: Snapshot to restore.
            checkpoint: Snapshot to save of the current state first.

        Returns:
            The operation result.

        """
        _ = self.get(name=name)
        if checkpoint is not None:
            _ = self.create(name=checkpoint)
        return self._mutate(action=SnapshotAction.revert, name=name)

    def restore(
        self,
        *,
        name: str,
        checkpoint: str | None = None,
    ) -> OperationResult:
        """Restore a snapshot; an alias for :meth:`revert`.

        Args:
            name: Snapshot to restore.
            checkpoint: Snapshot to save of the current state first.

        Returns:
            The operation result.

        """
        return self.revert(name=name, checkpoint=checkpoint)

    def reset_to(self, *, name: str) -> OperationResult:
        """Return the disk to the baseline, creating it on first use.

        The first call captures the *current* state as the baseline; every
        later call restores it, discarding whatever changed since.

        Args:
            name: Baseline snapshot name.

        Returns:
            The operation result.

        """
        SnapshotNames.validate(name=name)
        if self.exists(name=name):
            return self.revert(name=name)
        return self.create(name=name)

    def delete(
        self,
        *,
        name: str,
        missing_ok: bool = False,
    ) -> OperationResult:
        """Delete a snapshot.

        Args:
            name: Snapshot name.
            missing_ok: Treat a missing snapshot as success.

        Returns:
            The operation result.

        Raises:
            SnapshotNotFoundError: If it is missing and not ``missing_ok``.

        """
        SnapshotNames.validate(name=name)
        if not self.exists(name=name):
            if not missing_ok:
                msg: str = f"Snapshot does not exist: {name}"
                raise SnapshotNotFoundError(msg)
            return self._unchanged(action=SnapshotAction.delete, name=name)
        return self._mutate(action=SnapshotAction.delete, name=name)

    def delete_many(
        self,
        *,
        names: tuple[str, ...],
        missing_ok: bool = False,
    ) -> tuple[OperationResult, ...]:
        """Delete several snapshots.

        Args:
            names: Snapshot names.
            missing_ok: Treat missing snapshots as success.

        Returns:
            One result per name.

        """
        return tuple(
            self.delete(name=name, missing_ok=missing_ok) for name in names
        )

    def delete_all(self) -> tuple[OperationResult, ...]:
        """Delete every snapshot.

        Returns:
            One result per deleted snapshot.

        """
        return self.delete_many(names=self.names())

    def prune(self, *, keep: int) -> tuple[OperationResult, ...]:
        """Delete the oldest snapshots, keeping the newest ones.

        Args:
            keep: How many of the newest snapshots to retain.

        Returns:
            One result per deleted snapshot.

        Raises:
            QemuSnapshotError: If ``keep`` is negative.

        """
        if keep < 0:
            msg: str = "keep must be zero or greater."
            raise QemuSnapshotError(msg)
        snapshots: tuple[Snapshot, ...] = self.list()
        surplus: int = max(0, len(snapshots) - keep)
        return self.delete_many(
            names=tuple(snapshot.name for snapshot in snapshots[:surplus]),
        )

    def verify(self, *, name: str | None = None) -> bool:
        """Check the image is readable and optionally holds a snapshot.

        Args:
            name: Snapshot that must exist, if any.

        Returns:
            ``True`` when the image is readable and, if asked, holds it.

        """
        self._validate_image()
        return True if name is None else self.exists(name=name)

    def clear_history(self) -> None:
        """Forget the recorded mutations."""
        self._history.clear()

    def to_json(self, *, indent: int = 2) -> str:
        """Serialize the image and its snapshots.

        Args:
            indent: JSON indentation.

        Returns:
            A JSON document.

        """
        return dumps(
            obj={
                "image": self.image_info().to_dict(),
                "snapshots": [item.to_dict() for item in self.list()],
            },
            indent=indent,
            sort_keys=True,
        )

    @contextmanager
    def checkpoint(
        self,
        *,
        name: str,
        revert_on_error: bool = True,
        delete_on_success: bool = False,
        replace: bool = False,
    ) -> Generator[Snapshot]:
        """Snapshot the disk around a block of work.

        Args:
            name: Snapshot name.
            revert_on_error: Restore the snapshot if the block raises.
            delete_on_success: Delete the snapshot if the block succeeds.
            replace: Replace an existing snapshot of that name.

        Yields:
            The created snapshot (a placeholder in dry-run mode).

        """
        _ = self.create(name=name, replace=replace)
        snapshot: Snapshot = (
            Snapshot(
                identifier="dry-run",
                name=name,
                vm_size="",
                date="",
                vm_clock="",
            )
            if self._dry_run
            else self.get(name=name)
        )
        try:
            yield snapshot
        except BaseException:
            if revert_on_error:
                _ = self.revert(name=name)
            raise
        else:
            if delete_on_success:
                _ = self.delete(name=name)

    @staticmethod
    def _size(*, value: JsonValue) -> int:
        """Read a byte-count field of ``qemu-img info``.

        Args:
            value: Decoded field.

        Returns:
            The integer, or ``0`` when the field is not one.

        """
        return value if isinstance(value, int) else 0

    @staticmethod
    def _parse_snapshots(*, output: str) -> SnapshotRows:
        """Parse ``qemu-img snapshot -l`` output.

        Args:
            output: The command's standard output.

        Returns:
            The listed snapshots.

        """
        snapshots: list[Snapshot] = []
        for line in output.splitlines():
            fields: list[str] = line.split(
                maxsplit=SnapshotConstants.snapshot_fields - 1,
            )
            if len(fields) == SnapshotConstants.snapshot_fields and (
                fields[0].isdigit()
            ):
                snapshots.append(
                    Snapshot(
                        identifier=fields[0],
                        name=fields[1],
                        vm_size=fields[2],
                        date=f"{fields[3]} {fields[4]}",
                        vm_clock=fields[5],
                    ),
                )
        return snapshots

    @staticmethod
    def _resolve_qemu_img(*, qemu_img: Path | None) -> Path:
        """Locate the ``qemu-img`` executable.

        Args:
            qemu_img: Explicit executable, if any.

        Returns:
            The resolved executable.

        Raises:
            QemuSnapshotError: If it cannot be found.

        """
        if qemu_img is not None and qemu_img.parent != Path():
            resolved: Path = qemu_img.expanduser().resolve()
            if not resolved.is_file():
                msg: str = f"qemu-img does not exist: {resolved}"
                raise QemuSnapshotError(msg)
            return resolved
        executable: str | None = which(
            cmd=str(object=qemu_img or SnapshotConstants.qemu_img),
        )
        if executable is None:
            msg = (
                "qemu-img was not found in PATH. Install QEMU or provide "
                "qemu_img."
            )
            raise QemuSnapshotError(msg)
        return Path(executable).resolve()

    def _unchanged(
        self,
        *,
        action: SnapshotAction,
        name: str,
    ) -> OperationResult:
        """Describe an operation that needed no change.

        Args:
            action: What was asked.
            name: Snapshot name.

        Returns:
            A result with ``changed`` false.

        """
        return OperationResult(
            action=action,
            snapshot=name,
            image=self._image,
            command=self._plan(action=action, name=name).command,
            changed=False,
            dry_run=self._dry_run,
        )

    def _mutate(self, *, action: SnapshotAction, name: str) -> OperationResult:
        """Run (or, in dry-run mode, only plan) one mutation.

        Args:
            action: What to do.
            name: Snapshot name.

        Returns:
            The recorded result.

        """
        plan: CommandPlan = self._plan(action=action, name=name)
        if not self._dry_run:
            _ = self._run(arguments=plan.command[1:])
        result: OperationResult = OperationResult(
            action=action,
            snapshot=name,
            image=self._image,
            command=plan.command,
            changed=True,
            dry_run=self._dry_run,
        )
        self._history.append(result)
        return result

    def _plan(self, *, action: SnapshotAction, name: str) -> CommandPlan:
        """Compose the ``qemu-img`` command for an action.

        Args:
            action: What to do.
            name: Snapshot name.

        Returns:
            The planned command.

        """
        SnapshotNames.validate(name=name)
        option: str = {
            SnapshotAction.create: "-c",
            SnapshotAction.delete: "-d",
            SnapshotAction.revert: "-a",
        }[action]
        return CommandPlan(
            action=action,
            snapshot=name,
            command=(
                str(object=self._qemu_img),
                "snapshot",
                option,
                name,
                str(object=self._image),
            ),
        )

    def _validate_image(self) -> None:
        """Check the image exists and is qcow2.

        Raises:
            QemuSnapshotError: If the file is missing.
            UnsupportedImageError: If the format is not qcow2.

        """
        if not self._image.is_file():
            msg: str = f"Disk image does not exist: {self._image}"
            raise QemuSnapshotError(msg)
        if self.image_info().format != "qcow2":
            msg = f"Internal snapshots require qcow2: {self._image}"
            raise UnsupportedImageError(msg)

    def _run(self, *, arguments: tuple[str, ...]) -> CompletedProcess[str]:
        """Run ``qemu-img`` with arguments.

        Args:
            arguments: Arguments after the executable.

        Returns:
            The completed process.

        Raises:
            QemuSnapshotError: If it fails or cannot start.

        """
        try:
            return run(
                args=(str(object=self._qemu_img), *arguments),
                check=True,
                capture_output=True,
                text=True,
            )
        except CalledProcessError as error:
            detail: str = (error.stderr or "").strip() or (
                error.stdout or ""
            ).strip()
            msg: str = (
                detail or f"qemu-img exited with status {error.returncode}."
            )
            raise QemuSnapshotError(msg) from error
        except OSError as error:
            raise QemuSnapshotError(str(object=error)) from error


type CommandOutput = tuple[object, ...]


@final
class DiskCommands:
    """Implement the ``disk`` CLI commands; each returns lines to print."""

    @staticmethod
    def listing(
        *,
        manager: QemuSnapshotManager,
        arguments: Namespace,
    ) -> CommandOutput:
        """List the snapshots.

        Args:
            manager: Disk manager.
            arguments: Parsed arguments.

        Returns:
            One entry per snapshot.

        """
        snapshots: tuple[Snapshot, ...] = manager.list(
            name_contains=arguments.contains,
            reverse=arguments.reverse,
        )
        return tuple(
            dumps(obj=item.to_dict()) if arguments.json else item
            for item in snapshots
        )

    @staticmethod
    def info(
        *,
        manager: QemuSnapshotManager,
        arguments: Namespace,
    ) -> CommandOutput:
        """Describe the image.

        Args:
            manager: Disk manager.
            arguments: Parsed arguments.

        Returns:
            The image description.

        """
        return (manager.to_json() if arguments.json else manager.image_info(),)

    @staticmethod
    def create(
        *,
        manager: QemuSnapshotManager,
        arguments: Namespace,
    ) -> CommandOutput:
        """Create a snapshot.

        Args:
            manager: Disk manager.
            arguments: Parsed arguments.

        Returns:
            The operation result.

        """
        return (
            manager.create(name=arguments.name, replace=arguments.replace),
        )

    @staticmethod
    def revert(
        *,
        manager: QemuSnapshotManager,
        arguments: Namespace,
    ) -> CommandOutput:
        """Restore a snapshot.

        Args:
            manager: Disk manager.
            arguments: Parsed arguments.

        Returns:
            The operation result.

        """
        return (
            manager.revert(
                name=arguments.name,
                checkpoint=arguments.checkpoint,
            ),
        )

    @staticmethod
    def reset(
        *,
        manager: QemuSnapshotManager,
        arguments: Namespace,
    ) -> CommandOutput:
        """Return the disk to a baseline.

        Args:
            manager: Disk manager.
            arguments: Parsed arguments.

        Returns:
            The operation result.

        """
        return (manager.reset_to(name=arguments.name),)

    @staticmethod
    def delete(
        *,
        manager: QemuSnapshotManager,
        arguments: Namespace,
    ) -> CommandOutput:
        """Delete a snapshot.

        Args:
            manager: Disk manager.
            arguments: Parsed arguments.

        Returns:
            The operation result.

        """
        return (
            manager.delete(
                name=arguments.name,
                missing_ok=arguments.missing_ok,
            ),
        )

    @staticmethod
    def prune(
        *,
        manager: QemuSnapshotManager,
        arguments: Namespace,
    ) -> CommandOutput:
        """Delete all but the newest snapshots.

        Args:
            manager: Disk manager.
            arguments: Parsed arguments.

        Returns:
            One result per deleted snapshot.

        """
        return tuple(manager.prune(keep=arguments.keep))

    @staticmethod
    def delete_all(
        *,
        manager: QemuSnapshotManager,
        arguments: Namespace,
    ) -> CommandOutput:
        """Delete every snapshot.

        Args:
            manager: Disk manager.
            arguments: Parsed arguments.

        Returns:
            One result per deleted snapshot.

        """
        del arguments
        return tuple(manager.delete_all())

    @staticmethod
    def verify(
        *,
        manager: QemuSnapshotManager,
        arguments: Namespace,
    ) -> CommandOutput:
        """Verify the image and optionally a snapshot.

        Args:
            manager: Disk manager.
            arguments: Parsed arguments.

        Returns:
            ``OK`` or ``MISSING``.

        """
        return ("OK" if manager.verify(name=arguments.name) else "MISSING",)

    @staticmethod
    def version(
        *,
        manager: QemuSnapshotManager,
        arguments: Namespace,
    ) -> CommandOutput:
        """Report the ``qemu-img`` version.

        Args:
            manager: Disk manager.
            arguments: Parsed arguments.

        Returns:
            The version line.

        """
        del arguments
        return (manager.qemu_img_version(),)


@final
class QmpCommands:
    """Implement the ``qmp`` CLI commands; each returns lines to print."""

    @staticmethod
    def status(
        *,
        manager: QemuVmSnapshotManager,
        arguments: Namespace,
    ) -> CommandOutput:
        """Report the run state.

        Args:
            manager: VM manager.
            arguments: Parsed arguments.

        Returns:
            The status.

        """
        del arguments
        return (manager.status(),)

    @staticmethod
    def blocks(
        *,
        manager: QemuVmSnapshotManager,
        arguments: Namespace,
    ) -> CommandOutput:
        """List the block devices.

        Args:
            manager: VM manager.
            arguments: Parsed arguments.

        Returns:
            One entry per device.

        """
        del arguments
        return tuple(manager.block_devices())

    @staticmethod
    def pause(
        *,
        manager: QemuVmSnapshotManager,
        arguments: Namespace,
    ) -> CommandOutput:
        """Pause the VM.

        Args:
            manager: VM manager.
            arguments: Parsed arguments.

        Returns:
            The status afterwards.

        """
        del arguments
        return (manager.pause(),)

    @staticmethod
    def resume(
        *,
        manager: QemuVmSnapshotManager,
        arguments: Namespace,
    ) -> CommandOutput:
        """Resume the VM.

        Args:
            manager: VM manager.
            arguments: Parsed arguments.

        Returns:
            The status afterwards.

        """
        del arguments
        return (manager.resume(),)

    @staticmethod
    def listing(
        *,
        manager: QemuVmSnapshotManager,
        arguments: Namespace,
    ) -> CommandOutput:
        """List the VM snapshots.

        Args:
            manager: VM manager.
            arguments: Parsed arguments.

        Returns:
            The monitor's listing.

        """
        del arguments
        return (manager.snapshots(),)

    @staticmethod
    def create(
        *,
        manager: QemuVmSnapshotManager,
        arguments: Namespace,
    ) -> CommandOutput:
        """Save the VM state.

        Args:
            manager: VM manager.
            arguments: Parsed arguments.

        Returns:
            The operation result.

        """
        return (
            manager.create(
                name=arguments.name,
                pause=not arguments.no_pause,
                resume=not arguments.no_resume,
            ),
        )

    @staticmethod
    def revert(
        *,
        manager: QemuVmSnapshotManager,
        arguments: Namespace,
    ) -> CommandOutput:
        """Restore the VM state.

        Args:
            manager: VM manager.
            arguments: Parsed arguments.

        Returns:
            The operation result.

        """
        return (
            manager.revert(
                name=arguments.name,
                pause=not arguments.no_pause,
                resume=not arguments.no_resume,
            ),
        )

    @staticmethod
    def delete(
        *,
        manager: QemuVmSnapshotManager,
        arguments: Namespace,
    ) -> CommandOutput:
        """Delete a VM snapshot.

        Args:
            manager: VM manager.
            arguments: Parsed arguments.

        Returns:
            The operation result.

        """
        return (manager.delete(name=arguments.name),)

    @staticmethod
    def commands(
        *,
        manager: QemuVmSnapshotManager,
        arguments: Namespace,
    ) -> CommandOutput:
        """List the advertised QMP commands.

        Args:
            manager: VM manager.
            arguments: Parsed arguments.

        Returns:
            One entry per command.

        """
        del arguments
        return manager.client.commands()

    @staticmethod
    def hmp(
        *,
        manager: QemuVmSnapshotManager,
        arguments: Namespace,
    ) -> CommandOutput:
        """Run a human-monitor line.

        Args:
            manager: VM manager.
            arguments: Parsed arguments.

        Returns:
            The monitor's answer.

        """
        return (
            manager.client.human_monitor_command(
                command_line=arguments.monitor_command,
            ),
        )


@final
class QemuSnapshotCli:
    """Command-line interface for disk and QMP snapshots.

    Attributes:
        disk_handlers: ``disk`` subcommand name to its implementation.
        qmp_handlers: ``qmp`` subcommand name to its implementation.

    """

    disk_handlers: Final[dict[str, Callable[..., CommandOutput]]] = {
        "list": DiskCommands.listing,
        "info": DiskCommands.info,
        "create": DiskCommands.create,
        "revert": DiskCommands.revert,
        "restore": DiskCommands.revert,
        "reset": DiskCommands.reset,
        "delete": DiskCommands.delete,
        "prune": DiskCommands.prune,
        "delete-all": DiskCommands.delete_all,
        "verify": DiskCommands.verify,
        "version": DiskCommands.version,
    }
    qmp_handlers: Final[dict[str, Callable[..., CommandOutput]]] = {
        "status": QmpCommands.status,
        "blocks": QmpCommands.blocks,
        "pause": QmpCommands.pause,
        "resume": QmpCommands.resume,
        "list": QmpCommands.listing,
        "create": QmpCommands.create,
        "revert": QmpCommands.revert,
        "restore": QmpCommands.revert,
        "delete": QmpCommands.delete,
        "commands": QmpCommands.commands,
        "hmp": QmpCommands.hmp,
    }

    @classmethod
    def run(cls, *, argv: tuple[str, ...] | None = None) -> int:
        """Run the CLI.

        Args:
            argv: Arguments; ``sys.argv`` when omitted.

        Returns:
            The process exit status.

        """
        parser: ArgumentParser = cls._parser()
        arguments: Namespace = parser.parse_args(
            args=None if argv is None else list(argv),
        )
        try:
            if arguments.backend == "disk":
                cls._disk(arguments=arguments)
            else:
                cls._qmp(arguments=arguments)
        except QemuSnapshotError as error:
            parser.error(message=str(object=error))
        return 0

    @classmethod
    def _disk(cls, *, arguments: Namespace) -> None:
        """Run one offline-disk command.

        Args:
            arguments: Parsed arguments.

        Raises:
            QemuSnapshotError: If the command is unknown or fails.

        """
        manager: QemuSnapshotManager = QemuSnapshotManager(
            image=arguments.image,
            qemu_img=arguments.qemu_img,
            dry_run=arguments.dry_run,
        )
        handler: Callable[..., CommandOutput] | None = cls.disk_handlers.get(
            arguments.command,
        )
        if handler is None:
            msg: str = f"Unsupported disk command: {arguments.command}"
            raise QemuSnapshotError(msg)
        cls._emit(values=handler(manager=manager, arguments=arguments))

    @classmethod
    def _qmp(cls, *, arguments: Namespace) -> None:
        """Run one running-VM command.

        Args:
            arguments: Parsed arguments.

        Raises:
            QemuSnapshotError: If the command is unknown or fails.

        """
        handler: Callable[..., CommandOutput] | None = cls.qmp_handlers.get(
            arguments.command,
        )
        if handler is None:
            msg: str = f"Unsupported QMP command: {arguments.command}"
            raise QemuSnapshotError(msg)
        endpoint: QmpEndpoint = QmpEndpoint(
            unix_socket=arguments.qmp_socket,
            host=arguments.qmp_host,
            port=arguments.qmp_port,
        )
        with QmpClient(endpoint=endpoint, timeout=arguments.timeout) as client:
            manager: QemuVmSnapshotManager = QemuVmSnapshotManager(
                client=client,
            )
            cls._emit(values=handler(manager=manager, arguments=arguments))

    @staticmethod
    def _emit(*, values: CommandOutput) -> None:
        """Write each value as a line on standard output.

        Args:
            values: Anything printable.

        """
        for value in values:
            _ = stdout.write(f"{value}\n")

    @staticmethod
    def _parser() -> ArgumentParser:
        """Build the argument parser.

        Returns:
            The parser.

        """
        parser: ArgumentParser = ArgumentParser(
            description=(
                "Manage offline qcow2 disk snapshots and running QEMU VM "
                "snapshots through QMP."
            ),
        )
        add_backend: Callable[..., ArgumentParser] = parser.add_subparsers(
            dest="backend",
            required=True,
        ).add_parser
        disk: ArgumentParser = add_backend(name="disk")
        disk.add_argument("--image", required=True, type=Path)
        disk.add_argument("--qemu-img", type=Path, default=None)
        disk.add_argument("--dry-run", action="store_true")
        add_disk: Callable[..., ArgumentParser] = disk.add_subparsers(
            dest="command",
            required=True,
        ).add_parser
        listing: ArgumentParser = add_disk(name="list")
        listing.add_argument("--contains", default=None)
        listing.add_argument("--reverse", action="store_true")
        listing.add_argument("--json", action="store_true")
        info: ArgumentParser = add_disk(name="info")
        info.add_argument("--json", action="store_true")
        create: ArgumentParser = add_disk(name="create")
        create.add_argument("name")
        create.add_argument("--replace", action="store_true")
        revert: ArgumentParser = add_disk(
            name="revert",
            aliases=["restore"],
        )
        revert.add_argument("name")
        revert.add_argument("--checkpoint", default=None)
        reset: ArgumentParser = add_disk(name="reset")
        reset.add_argument("name")
        delete: ArgumentParser = add_disk(name="delete")
        delete.add_argument("name")
        delete.add_argument("--missing-ok", action="store_true")
        prune: ArgumentParser = add_disk(name="prune")
        prune.add_argument("--keep", required=True, type=int)
        _ = add_disk(name="delete-all")
        verify: ArgumentParser = add_disk(name="verify")
        verify.add_argument("--name", default=None)
        _ = add_disk(name="version")
        qmp: ArgumentParser = add_backend(name="qmp")
        qmp.add_argument("--qmp-socket", type=Path, default=None)
        qmp.add_argument("--qmp-host", default=None)
        qmp.add_argument("--qmp-port", type=int, default=None)
        qmp.add_argument(
            "--timeout",
            type=float,
            default=SnapshotConstants.default_qmp_timeout,
        )
        add_qmp: Callable[..., ArgumentParser] = qmp.add_subparsers(
            dest="command",
            required=True,
        ).add_parser
        for simple in ("status", "blocks", "pause", "resume", "list"):
            _ = add_qmp(name=simple)
        _ = add_qmp(name="commands")
        for named in ("create", "revert", "restore"):
            operation: ArgumentParser = add_qmp(name=named)
            operation.add_argument("name")
            operation.add_argument("--no-pause", action="store_true")
            operation.add_argument("--no-resume", action="store_true")
        qmp_delete: ArgumentParser = add_qmp(name="delete")
        qmp_delete.add_argument("name")
        hmp: ArgumentParser = add_qmp(name="hmp")
        hmp.add_argument("monitor_command")
        return parser
