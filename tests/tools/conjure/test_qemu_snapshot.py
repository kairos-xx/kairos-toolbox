# Copyright (c) 2026 Project contributors.
"""Tests for qcow2 disk and QMP snapshot management.

Disk tests run the real ``qemu-img`` and ``qemu-io``: a revert is proven by
reading the pattern back from the image, not by inspecting a command line.
The monitor protocol is exercised against a small in-process QMP server.
"""

from __future__ import annotations

from io import StringIO
from json import dumps, loads
from pathlib import Path
from socket import AF_INET, SOCK_STREAM, socket
from subprocess import CompletedProcess, run
from tempfile import TemporaryDirectory
from threading import Thread
from typing import TYPE_CHECKING, Final, cast, final
from unittest.mock import PropertyMock, patch

import pytest

from tools.conjure import qemu_snapshot
from tools.conjure.config import GuestConfig, QemuConfig, SshConfig
from tools.conjure.process import QemuProcess
from tools.conjure.qemu_snapshot import (
    BlockDevice,
    ImageInfo,
    OperationResult,
    QemuSnapshotCli,
    QemuSnapshotError,
    QemuSnapshotManager,
    QemuVmSnapshotManager,
    QmpClient,
    QmpCommandError,
    QmpConnectionError,
    QmpEndpoint,
    Snapshot,
    SnapshotExistsError,
    SnapshotNotFoundError,
    UnsupportedImageError,
    VmRunState,
    VmSnapshotResult,
    VmStatus,
)
from tools.conjure.transport import SshTransport
from tools.conjure.vm import QemuVm

from .helpers import check, expect

if TYPE_CHECKING:
    from types import TracebackType


@final
class Constants:
    """Group test constants.

    Attributes:
        size: Virtual size of the test disks.
        original: Pattern byte of the original disk contents.
        dirty: Pattern byte a "guest" writes while the VM runs.
        stale: Pattern byte written while the VM is off.
        length: Bytes each pattern covers.

    """

    size: Final[str] = "1M"
    original: Final[int] = 0xAA
    dirty: Final[int] = 0xBB
    stale: Final[int] = 0xCC
    length: Final[str] = "64k"


def _qemu_io(*, image: Path, command: str) -> int:
    """Run one ``qemu-io`` command against a qcow2 image.

    Args:
        image: Disk image.
        command: The ``qemu-io`` command string.

    Returns:
        The process exit status.

    """
    finished: CompletedProcess[str] = run(
        args=("qemu-io", "-f", "qcow2", "-c", command, str(object=image)),
        check=False,
        capture_output=True,
        text=True,
    )
    return finished.returncode


def _write(*, image: Path, pattern: int) -> None:
    """Fill the start of the disk with one byte value.

    Args:
        image: Disk image.
        pattern: Byte value to write.

    """
    status: int = _qemu_io(
        image=image,
        command=f"write -P {pattern:#x} 0 {Constants.length}",
    )
    expect(actual=status, expected=0)


def _holds(*, image: Path, pattern: int) -> bool:
    """Return whether the start of the disk holds one byte value.

    Args:
        image: Disk image.
        pattern: Byte value expected.

    Returns:
        ``True`` when every byte matches.

    """
    return (
        _qemu_io(
            image=image,
            command=f"read -P {pattern:#x} 0 {Constants.length}",
        )
        == 0
    )


def _disk(*, directory: Path, name: str = "disk.qcow2") -> Path:
    """Create a qcow2 disk holding the original pattern.

    Args:
        directory: Folder that receives the image.
        name: Image file name.

    Returns:
        The image path.

    """
    image: Path = directory / name
    created: CompletedProcess[str] = run(
        args=("qemu-img", "create", "-f", "qcow2", str(object=image), "1M"),
        check=False,
        capture_output=True,
        text=True,
    )
    expect(actual=created.returncode, expected=0)
    _write(image=image, pattern=Constants.original)
    return image


def test_a_revert_restores_the_disk_contents(tmp_path: Path) -> None:
    """Data written after a snapshot is gone after reverting to it.

    Args:
        tmp_path: Pytest temporary directory.

    """
    image: Path = _disk(directory=tmp_path)
    manager: QemuSnapshotManager = QemuSnapshotManager(image=image)
    created: OperationResult = manager.create(name="baseline")
    expect(actual=created.changed, expected=True)
    _write(image=image, pattern=Constants.dirty)
    check(
        condition=_holds(image=image, pattern=Constants.dirty),
        reason="the write must be visible before the revert",
    )
    _ = manager.revert(name="baseline")
    check(
        condition=_holds(image=image, pattern=Constants.original),
        reason="the original contents must come back",
    )
    check(
        condition=not _holds(image=image, pattern=Constants.dirty),
        reason="the later write must be gone",
    )


def test_listing_and_lookup_describe_the_snapshots(tmp_path: Path) -> None:
    """Every accessor agrees about what the image holds.

    Args:
        tmp_path: Pytest temporary directory.

    """
    manager: QemuSnapshotManager = QemuSnapshotManager(
        image=_disk(directory=tmp_path),
    )
    expect(actual=(manager.empty, manager.count), expected=(True, 0))
    expect(actual=(manager.latest(), manager.oldest()), expected=(None, None))
    _ = manager.create(name="first")
    _ = manager.create(name="second-snap")
    expect(actual=manager.names(), expected=("first", "second-snap"))
    expect(actual=manager.identifiers(), expected=("1", "2"))
    expect(actual=manager.count, expected=2)
    expect(actual=manager.latest(), expected=manager.get(name="second-snap"))
    expect(actual=manager.oldest(), expected=manager.get(name="first"))
    expect(
        actual=[item.name for item in manager.list(reverse=True)],
        expected=["second-snap", "first"],
    )
    expect(
        actual=[item.name for item in manager.list(name_contains="second")],
        expected=["second-snap"],
    )
    found: Snapshot | None = manager.find(identifier="2")
    expect(actual=found, expected=manager.get(identifier="2"))
    expect(actual=manager.find(name="absent"), expected=None)
    expect(actual=manager.exists(name="first"), expected=True)
    expect(actual=manager.exists(name="absent"), expected=False)
    expect(actual=manager.verify(), expected=True)
    expect(actual=manager.verify(name="first"), expected=True)
    expect(actual=manager.verify(name="absent"), expected=False)
    check(
        condition=set(manager.get(name="first").to_dict())
        == {"identifier", "name", "vm_size", "date", "vm_clock"},
        reason="to_dict must expose every field",
    )


def test_selectors_and_names_are_validated(tmp_path: Path) -> None:
    """Both-or-neither selectors and unsafe names are rejected.

    Args:
        tmp_path: Pytest temporary directory.

    """
    manager: QemuSnapshotManager = QemuSnapshotManager(
        image=_disk(directory=tmp_path),
    )
    with pytest.raises(expected_exception=QemuSnapshotError, match="exactly"):
        _ = manager.find()
    with pytest.raises(expected_exception=QemuSnapshotError, match="exactly"):
        _ = manager.find(name="a", identifier="1")
    for bad in ("has space", "semi;colon", "", "../up"):
        with pytest.raises(
            expected_exception=QemuSnapshotError,
            match="Snapshot names",
        ):
            _ = manager.create(name=bad)
    with pytest.raises(expected_exception=SnapshotNotFoundError):
        _ = manager.get(name="absent")
    with pytest.raises(expected_exception=SnapshotNotFoundError):
        _ = manager.get(identifier="99")


def test_create_refuses_to_overwrite_unless_asked(tmp_path: Path) -> None:
    """A duplicate name fails; ``replace`` takes a fresh snapshot instead.

    Args:
        tmp_path: Pytest temporary directory.

    """
    image: Path = _disk(directory=tmp_path)
    manager: QemuSnapshotManager = QemuSnapshotManager(image=image)
    _ = manager.create(name="point")
    with pytest.raises(expected_exception=SnapshotExistsError):
        _ = manager.create(name="point")
    _write(image=image, pattern=Constants.dirty)
    _ = manager.create(name="point", replace=True)
    _write(image=image, pattern=Constants.stale)
    _ = manager.revert(name="point")
    check(
        condition=_holds(image=image, pattern=Constants.dirty),
        reason="the replaced snapshot holds the newer contents",
    )


def test_ensure_and_reset_to_define_a_baseline(tmp_path: Path) -> None:
    """The first reset captures the baseline; later ones restore it.

    Args:
        tmp_path: Pytest temporary directory.

    """
    image: Path = _disk(directory=tmp_path)
    manager: QemuSnapshotManager = QemuSnapshotManager(image=image)
    first: OperationResult = manager.ensure(name="baseline")
    second: OperationResult = manager.ensure(name="baseline")
    expect(actual=(first.changed, second.changed), expected=(True, False))
    manager.clear_history()
    _write(image=image, pattern=Constants.dirty)
    restored: OperationResult = manager.reset_to(name="baseline")
    expect(actual=restored.action.value, expected="revert")
    check(
        condition=_holds(image=image, pattern=Constants.original),
        reason="reset_to must restore the baseline",
    )
    fresh: QemuSnapshotManager = QemuSnapshotManager(
        image=_disk(directory=tmp_path, name="other.qcow2"),
    )
    created: OperationResult = fresh.reset_to(name="baseline")
    expect(actual=created.action.value, expected="create")


def test_revert_can_checkpoint_the_state_it_discards(tmp_path: Path) -> None:
    """``checkpoint`` keeps the current state as a snapshot before reverting.

    Args:
        tmp_path: Pytest temporary directory.

    """
    image: Path = _disk(directory=tmp_path)
    manager: QemuSnapshotManager = QemuSnapshotManager(image=image)
    _ = manager.create(name="baseline")
    _write(image=image, pattern=Constants.dirty)
    _ = manager.restore(name="baseline", checkpoint="before-revert")
    expect(actual=manager.names(), expected=("baseline", "before-revert"))
    _ = manager.revert(name="before-revert")
    check(
        condition=_holds(image=image, pattern=Constants.dirty),
        reason="the checkpoint preserved the discarded state",
    )


def test_delete_prune_and_delete_all(tmp_path: Path) -> None:
    """Deletion honours ``missing_ok`` and keeps the newest on prune.

    Args:
        tmp_path: Pytest temporary directory.

    """
    manager: QemuSnapshotManager = QemuSnapshotManager(
        image=_disk(directory=tmp_path),
    )
    for name in ("a", "b", "c", "d"):
        _ = manager.create(name=name)
    with pytest.raises(expected_exception=SnapshotNotFoundError):
        _ = manager.delete(name="absent")
    skipped: OperationResult = manager.delete(name="absent", missing_ok=True)
    expect(actual=skipped.changed, expected=False)
    with pytest.raises(expected_exception=QemuSnapshotError, match="keep"):
        _ = manager.prune(keep=-1)
    pruned: tuple[OperationResult, ...] = manager.prune(keep=2)
    expect(actual=[item.snapshot for item in pruned], expected=["a", "b"])
    expect(actual=manager.names(), expected=("c", "d"))
    deleted: tuple[OperationResult, ...] = manager.delete_all()
    expect(actual=len(deleted), expected=2)
    expect(actual=manager.empty, expected=True)
    expect(
        actual=len(manager.history),
        expected=4 + len(pruned) + len(deleted),
    )
    manager.clear_history()
    expect(actual=manager.history, expected=())


def test_dry_run_plans_without_touching_the_image(tmp_path: Path) -> None:
    """A dry run reports the command and changes nothing.

    Args:
        tmp_path: Pytest temporary directory.

    """
    manager: QemuSnapshotManager = QemuSnapshotManager(
        image=_disk(directory=tmp_path),
        dry_run=True,
    )
    expect(actual=manager.dry_run, expected=True)
    result: OperationResult = manager.create(name="planned")
    expect(actual=(result.changed, result.dry_run), expected=(True, True))
    expect(actual=manager.empty, expected=True)
    for plan in (
        manager.plan_create(name="planned"),
        manager.plan_revert(name="planned"),
        manager.plan_delete(name="planned"),
    ):
        check(condition=plan.shell().startswith("/"), reason=plan.shell())
        check(condition="snapshot" in plan.command, reason=plan.shell())
    check(
        condition=set(result.to_dict())
        == {"action", "snapshot", "image", "command", "changed", "dry_run"},
        reason="to_dict must expose every field",
    )
    with manager.checkpoint(name="planned") as snapshot:
        expect(actual=snapshot.identifier, expected="dry-run")


def _fail_inside_checkpoint(
    *,
    manager: QemuSnapshotManager,
    image: Path,
    message: str,
) -> None:
    """Dirty the disk inside a checkpoint and then fail.

    Args:
        manager: Disk manager.
        image: Disk image the block writes to.
        message: Text of the failure.

    Raises:
        RuntimeError: Always, after the write.

    """
    with manager.checkpoint(name="guard"):
        _write(image=image, pattern=Constants.dirty)
        raise RuntimeError(message)


def test_checkpoint_reverts_on_error_and_cleans_up_on_success(
    tmp_path: Path,
) -> None:
    """The disk checkpoint undoes a failed block and can tidy a good one.

    Args:
        tmp_path: Pytest temporary directory.

    """
    image: Path = _disk(directory=tmp_path)
    manager: QemuSnapshotManager = QemuSnapshotManager(image=image)
    message: str = "the block failed"
    with pytest.raises(expected_exception=RuntimeError, match=message):
        _fail_inside_checkpoint(manager=manager, image=image, message=message)
    check(
        condition=_holds(image=image, pattern=Constants.original),
        reason="a failing block must be rolled back",
    )
    with manager.checkpoint(name="tidy", delete_on_success=True, replace=True):
        _write(image=image, pattern=Constants.dirty)
    expect(actual=manager.exists(name="tidy"), expected=False)
    check(
        condition=_holds(image=image, pattern=Constants.dirty),
        reason="a good block keeps its changes",
    )


def test_image_metadata_and_json(tmp_path: Path) -> None:
    """Image info and the JSON export describe the disk and snapshots.

    Args:
        tmp_path: Pytest temporary directory.

    """
    manager: QemuSnapshotManager = QemuSnapshotManager(
        image=_disk(directory=tmp_path),
    )
    _ = manager.create(name="meta")
    info: ImageInfo = manager.image_info()
    expect(actual=info.format, expected="qcow2")
    expect(actual=info.virtual_size, expected=1024 * 1024)
    expect(actual=(info.backing_file, info.encrypted), expected=(None, False))
    check(condition=info.actual_size > 0, reason="actual size is reported")
    check(
        condition="virtual_size" in info.to_dict(),
        reason="to_dict must expose the sizes",
    )
    document: dict[str, list[dict[str, str]]] = loads(s=manager.to_json())
    expect(actual=document["snapshots"][0]["name"], expected="meta")
    check(
        condition=manager.qemu_img_version().startswith("qemu-img"),
        reason=manager.qemu_img_version(),
    )
    expect(actual=manager.image, expected=(tmp_path / "disk.qcow2").resolve())
    check(condition=manager.qemu_img.is_file(), reason="resolved executable")


def test_unusable_images_and_executables_are_rejected(
    tmp_path: Path,
) -> None:
    """Missing, non-qcow2 and unrunnable inputs fail clearly.

    Args:
        tmp_path: Pytest temporary directory.

    """
    with pytest.raises(expected_exception=QemuSnapshotError, match="exist"):
        _ = QemuSnapshotManager(image=tmp_path / "absent.qcow2")
    raw: Path = tmp_path / "disk.raw"
    created: CompletedProcess[str] = run(
        args=("qemu-img", "create", "-f", "raw", str(object=raw), "1M"),
        check=False,
        capture_output=True,
        text=True,
    )
    expect(actual=created.returncode, expected=0)
    with pytest.raises(expected_exception=UnsupportedImageError):
        _ = QemuSnapshotManager(image=raw)
    good: Path = _disk(directory=tmp_path)
    with pytest.raises(expected_exception=QemuSnapshotError, match="exist"):
        _ = QemuSnapshotManager(
            image=good,
            qemu_img=tmp_path / "x" / "qemu-img",
        )
    with pytest.raises(
        expected_exception=QemuSnapshotError,
        match="not found",
    ):
        _ = QemuSnapshotManager(image=good, qemu_img=Path("no-such-binary"))
    broken: Path = tmp_path / "broken.qcow2"
    broken.write_bytes(data=b"not a disk image at all")
    with pytest.raises(expected_exception=QemuSnapshotError):
        _ = QemuSnapshotManager(image=broken)


def _vm(*, image: Path, snapshot: bool = True) -> QemuVm:
    """Create a VM whose disk is returned to a baseline snapshot.

    Args:
        image: qcow2 disk the VM boots from.
        snapshot: Whether the baseline snapshot is configured.

    Returns:
        A VM that never launches a real QEMU process in these tests.

    """
    return QemuVm(
        qemu=QemuConfig(
            executable=Path("qemu"),
            arguments=(),
            snapshot_image=image if snapshot else None,
            baseline_snapshot="original" if snapshot else None,
        ),
        ssh=SshConfig(
            username="builder",
            identity_file=Path("key"),
            port=2222,
        ),
        guest=GuestConfig(shutdown_command=None),
    )


def _run_vm(*, vm: QemuVm, guest_write: Path | None, pattern: int) -> None:
    """Start and stop a VM, optionally dirtying its disk while it runs.

    Args:
        vm: The VM.
        guest_write: Disk the "guest" writes to while running, if any.
        pattern: Byte value the guest writes.

    """
    with (
        patch.object(target=QemuProcess, attribute="start"),
        patch.object(target=SshTransport, attribute="wait_until_ready"),
        patch.object(
            target=QemuProcess,
            attribute="is_running",
            new_callable=PropertyMock,
            return_value=True,
        ),
        patch.object(target=QemuProcess, attribute="terminate"),
        patch.object(target=QemuProcess, attribute="wait", return_value=True),
        patch.object(target=QemuProcess, attribute="reap"),
    ):
        vm.start()
        if guest_write is not None:
            _write(image=guest_write, pattern=pattern)
        vm.close()


def test_a_vm_boots_from_and_returns_to_its_baseline(tmp_path: Path) -> None:
    """Each run starts from the original disk and leaves nothing behind.

    Args:
        tmp_path: Pytest temporary directory.

    """
    image: Path = _disk(directory=tmp_path)
    vm: QemuVm = _vm(image=image)
    _run_vm(vm=vm, guest_write=image, pattern=Constants.dirty)
    check(
        condition=_holds(image=image, pattern=Constants.original),
        reason="closing the VM must put the original disk back",
    )
    expect(
        actual=QemuSnapshotManager(image=image).exists(name="original"),
        expected=True,
    )
    _write(image=image, pattern=Constants.stale)
    _run_vm(vm=vm, guest_write=None, pattern=0)
    check(
        condition=_holds(image=image, pattern=Constants.original),
        reason="a dirty disk from a crashed run is reset before booting",
    )


def test_a_vm_without_a_baseline_leaves_its_disk_alone(
    tmp_path: Path,
) -> None:
    """Without a baseline the disk is neither snapshotted nor restored.

    Args:
        tmp_path: Pytest temporary directory.

    """
    image: Path = _disk(directory=tmp_path)
    _run_vm(
        vm=_vm(image=image, snapshot=False),
        guest_write=image,
        pattern=Constants.dirty,
    )
    check(
        condition=_holds(image=image, pattern=Constants.dirty),
        reason="no baseline means the guest's changes persist",
    )
    expect(actual=QemuSnapshotManager(image=image).empty, expected=True)


def test_baseline_options_must_come_together() -> None:
    """A snapshot name without a disk (or the reverse) is a config error."""
    with pytest.raises(expected_exception=ValueError, match="together"):
        _ = QemuConfig(
            executable=Path("qemu"),
            arguments=(),
            baseline_snapshot="original",
        )
    with pytest.raises(expected_exception=ValueError, match="together"):
        _ = QemuConfig(
            executable=Path("qemu"),
            arguments=(),
            snapshot_image=Path("disk.qcow2"),
        )


@final
class FakeQmp:
    """Serve one QMP session on a local TCP port.

    Attributes:
        port: Port the server listens on.
        received: Every command the client sent, in order.
        running: Whether the fake guest is running.
        monitor: Text returned for each ``human-monitor-command`` line.

    """

    def __init__(self, *, monitor: dict[str, str] | None = None) -> None:
        """Start listening.

        Args:
            monitor: Answers keyed by monitor command line.

        """
        self.received: Final[list[str]] = []
        self.running: bool = True
        self.monitor: Final[dict[str, str]] = monitor or {}
        self._listener: Final[socket] = socket(
            family=AF_INET,
            type=SOCK_STREAM,
        )
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self.port: Final[int] = self._listener.getsockname()[1]
        self._thread: Final[Thread] = Thread(target=self._serve, daemon=True)

    def endpoint(self) -> QmpEndpoint:
        """Describe how to reach the server.

        Returns:
            A TCP endpoint.

        """
        return QmpEndpoint(host="127.0.0.1", port=self.port)

    def __enter__(self) -> FakeQmp:
        """Start serving.

        Returns:
            This server.

        """
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop serving.

        Args:
            exc_type: Ignored exception type.
            exc_value: Ignored exception.
            traceback: Ignored traceback.

        """
        del exc_type, exc_value, traceback
        self._listener.close()
        self._thread.join(timeout=5)

    def _serve(self) -> None:
        """Accept one client and answer its commands."""
        try:
            connection, _ = self._listener.accept()
        except OSError:
            return
        with connection, connection.makefile(mode="rw", newline="\n") as lines:
            greeting: dict[str, object] = {
                "QMP": {
                    "version": {
                        "qemu": {"major": 11, "minor": 1, "micro": 1},
                        "package": "fake",
                    },
                    "capabilities": ["oob"],
                },
            }
            _ = lines.write(dumps(obj=greeting) + "\r\n")
            lines.flush()
            for line in lines:
                request: dict[str, object] = loads(s=line)
                command: str = str(object=request["execute"])
                self.received.append(command)
                # An asynchronous event must not be mistaken for a reply.
                _ = lines.write(dumps(obj={"event": "NOISE"}) + "\r\n")
                _ = lines.write(
                    dumps(obj=self._reply(request=request)) + "\r\n",
                )
                lines.flush()

    def _reply(self, *, request: dict[str, object]) -> dict[str, object]:
        """Compose the answer to one command.

        Args:
            request: The decoded command.

        Returns:
            The decoded reply.

        Raises:
            TypeError: If the monitor arguments are not an object.

        """
        command: str = str(object=request["execute"])
        identifier: object = request.get("id")
        result: object = {}
        if command == "stop":
            self.running = False
        elif command == "cont":
            self.running = True
        elif command == "query-status":
            result = {
                "running": self.running,
                "status": "running" if self.running else "paused",
            }
        elif command == "query-block":
            result = [
                {
                    "device": "drive0",
                    "qdev": "/machine/drive0",
                    "removable": False,
                    "locked": False,
                    "tray_open": False,
                    "inserted": {"node-name": "n0", "file": "disk.qcow2"},
                },
                {"device": "cd"},
            ]
        elif command == "query-commands":
            result = [{"name": "stop"}, {"name": "cont"}, {"oops": 1}]
        elif command == "human-monitor-command":
            arguments: object = request["arguments"]
            if not isinstance(arguments, dict):
                msg: str = "monitor arguments must be an object"
                raise TypeError(msg)
            line: str = str(
                object=cast(typ="dict[str, object]", val=arguments)[
                    "command-line"
                ],
            )
            self.received.append(f"hmp:{line}")
            result = self.monitor.get(line, "")
        elif command == "explode":
            return {
                "error": {"class": "GenericError", "desc": "no"},
                "id": identifier,
            }
        return {"return": result, "id": identifier}


def test_qmp_endpoints_are_validated() -> None:
    """Exactly one complete transport must be configured."""
    for bad in (
        QmpEndpoint(),
        QmpEndpoint(unix_socket=Path("qmp.sock"), host="h", port=1),
        QmpEndpoint(host="h"),
        QmpEndpoint(port=1),
    ):
        with pytest.raises(expected_exception=QmpConnectionError):
            _ = QmpClient(endpoint=bad)


def test_qmp_client_negotiates_and_runs_commands() -> None:
    """The client greets, skips events, and returns command results."""
    with FakeQmp() as server, QmpClient(endpoint=server.endpoint()) as client:
        expect(actual=client.connected, expected=True)
        expect(actual=client.greeting.major, expected=11)
        expect(actual=client.greeting.package, expected="fake")
        expect(actual=client.greeting.capabilities, expected=("oob",))
        expect(actual=client.connect(), expected=client)
        status: VmStatus = client.query_status()
        expect(actual=status.state, expected=VmRunState.running)
        expect(actual=client.stop().state, expected=VmRunState.paused)
        expect(actual=client.cont().state, expected=VmRunState.running)
        client.system_reset()
        client.system_powerdown()
        expect(actual=client.commands(), expected=("stop", "cont"))
        expect(actual=client.supports(command="stop"), expected=True)
        expect(actual=client.supports(command="absent"), expected=False)
        devices: tuple[BlockDevice, ...] = client.query_block()
        expect(actual=len(devices), expected=2)
        expect(actual=devices[0].inserted_file, expected="disk.qcow2")
        expect(actual=devices[1].node_name, expected=None)
        expect(actual=devices[0].to_dict()["device"], expected="drive0")
        with pytest.raises(expected_exception=QmpCommandError):
            _ = client.execute(command="explode")
        client.quit()
    check(condition="query-status" in server.received, reason="was sent")
    expect(actual=client.connected, expected=False)
    with pytest.raises(expected_exception=QmpConnectionError):
        _ = client.greeting
    with pytest.raises(expected_exception=QmpConnectionError):
        _ = client.execute(command="query-status")


def test_qmp_over_a_unix_socket() -> None:
    """The same protocol works on a Unix-domain socket."""
    with TemporaryDirectory(prefix="q") as directory:
        path: Path = Path(directory) / "qmp.sock"
        listener: socket = socket(family=1, type=SOCK_STREAM)
        listener.bind(str(object=path))
        listener.listen(1)

        def serve() -> None:
            """Greet one client and acknowledge its capability request."""
            connection, _ = listener.accept()
            with connection, connection.makefile(mode="rw") as lines:
                _ = lines.write(
                    dumps(
                        obj={
                            "QMP": {
                                "version": {
                                    "qemu": {
                                        "major": 1,
                                        "minor": 2,
                                        "micro": 3,
                                    },
                                },
                            },
                        },
                    )
                    + "\n",
                )
                lines.flush()
                for line in lines:
                    identifier: object = loads(s=line).get("id")
                    _ = lines.write(
                        dumps(obj={"return": {}, "id": identifier}) + "\n",
                    )
                    lines.flush()

        thread: Thread = Thread(target=serve, daemon=True)
        thread.start()
        with QmpClient(endpoint=QmpEndpoint(unix_socket=path)) as client:
            expect(actual=client.greeting.micro, expected=3)
            expect(actual=client.greeting.capabilities, expected=())
        listener.close()
        thread.join(timeout=5)


def test_qmp_connection_failures_are_reported() -> None:
    """Nothing listening, or a peer that is not QMP, is a clear error."""
    with socket(family=AF_INET, type=SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        free: int = probe.getsockname()[1]
    client: QmpClient = QmpClient(
        endpoint=QmpEndpoint(host="127.0.0.1", port=free),
        timeout=0.5,
    )
    with pytest.raises(expected_exception=QmpConnectionError):
        _ = client.connect()
    expect(actual=client.connected, expected=False)
    listener: socket = socket(family=AF_INET, type=SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve() -> None:
        """Answer with something that is not a QMP greeting, then hang up."""
        connection, _ = listener.accept()
        with connection:
            _ = connection.sendall(b'{"hello": "world"}\n')

    thread: Thread = Thread(target=serve, daemon=True)
    thread.start()
    other: QmpClient = QmpClient(
        endpoint=QmpEndpoint(host="127.0.0.1", port=listener.getsockname()[1]),
        timeout=2.0,
    )
    with pytest.raises(
        expected_exception=QmpConnectionError,
        match="greeting",
    ):
        _ = other.connect()
    listener.close()
    thread.join(timeout=5)


def test_vm_snapshots_pause_around_the_monitor_command() -> None:
    """``savevm`` and ``loadvm`` run paused and resume the guest after."""
    with FakeQmp() as server, QmpClient(endpoint=server.endpoint()) as client:
        manager: QemuVmSnapshotManager = QemuVmSnapshotManager(client=client)
        expect(actual=manager.client, expected=client)
        expect(actual=manager.status().state, expected=VmRunState.running)
        created: VmSnapshotResult = manager.create(name="warm")
        expect(actual=created.resumed, expected=True)
        expect(actual=server.running, expected=True)
        restored: VmSnapshotResult = manager.restore(name="warm")
        expect(actual=restored.action.value, expected="revert")
        deleted: VmSnapshotResult = manager.delete(name="warm")
        expect(actual=deleted.resumed, expected=False)
        unpaused: VmSnapshotResult = manager.revert(
            name="warm",
            pause=False,
        )
        expect(actual=unpaused.resumed, expected=False)
        kept: VmSnapshotResult = manager.create(name="keep", resume=False)
        expect(actual=kept.resumed, expected=False)
        expect(actual=server.running, expected=False)
        _ = manager.resume()
        expect(actual=manager.snapshots(), expected="")
        expect(actual=len(manager.block_devices()), expected=2)
        expect(actual=manager.pause().state, expected=VmRunState.paused)
    check(condition="hmp:savevm warm" in server.received, reason="savevm")
    check(condition="hmp:loadvm warm" in server.received, reason="loadvm")
    check(condition="hmp:delvm warm" in server.received, reason="delvm")


def test_monitor_failures_resume_the_guest_and_raise() -> None:
    """A rejected ``savevm`` still resumes the VM and surfaces the error."""
    with (
        FakeQmp(monitor={"savevm bad": "Error: no writable disk"}) as server,
        QmpClient(endpoint=server.endpoint()) as client,
    ):
        manager: QemuVmSnapshotManager = QemuVmSnapshotManager(client=client)
        with pytest.raises(
            expected_exception=QmpCommandError,
            match="no writable disk",
        ):
            _ = manager.create(name="bad")
        expect(actual=server.running, expected=True)
        with pytest.raises(expected_exception=QemuSnapshotError):
            _ = manager.create(name="not valid")
        with pytest.raises(expected_exception=QemuSnapshotError):
            _ = manager.delete(name="not valid")


def test_pause_and_checkpoint_context_managers() -> None:
    """``paused`` restores the run state; ``checkpoint`` undoes failures.

    Raises:
        RuntimeError: Raised on purpose inside a checkpoint to prove the
            checkpoint reverts and re-raises it.

    """
    with FakeQmp() as server, QmpClient(endpoint=server.endpoint()) as client:
        manager: QemuVmSnapshotManager = QemuVmSnapshotManager(client=client)
        with manager.paused() as inside:
            expect(actual=inside.state, expected=VmRunState.paused)
            expect(actual=server.running, expected=False)
        expect(actual=server.running, expected=True)
        with manager.paused(resume=False):
            pass
        expect(actual=server.running, expected=False)
        with manager.paused():
            expect(actual=server.running, expected=False)
        _ = manager.resume()
        with manager.checkpoint(name="ok", delete_on_success=True):
            pass
        message: str = "guest work failed"
        with (
            pytest.raises(expected_exception=RuntimeError, match=message),
            manager.checkpoint(name="guard"),
        ):
            raise RuntimeError(message)
    check(condition="hmp:delvm ok" in server.received, reason="cleaned up")
    check(condition="hmp:loadvm guard" in server.received, reason="reverted")


def test_vm_status_normalizes_run_states() -> None:
    """Every QEMU status maps to one of four states."""
    expect(
        actual=VmStatus(running=True, status="running").state,
        expected=VmRunState.running,
    )
    for status in ("paused", "prelaunch", "suspended"):
        expect(
            actual=VmStatus(running=False, status=status).state,
            expected=VmRunState.paused,
        )
    for status in ("shutdown", "guest-panicked"):
        expect(
            actual=VmStatus(running=False, status=status).state,
            expected=VmRunState.shutdown,
        )
    expect(
        actual=VmStatus(running=False, status="inmigrate").state,
        expected=VmRunState.unknown,
    )


def _cli(*, argv: tuple[str, ...]) -> tuple[int, str]:
    """Run the snapshot CLI and capture what it prints.

    Args:
        argv: Command-line arguments.

    Returns:
        The exit status and everything written to standard output.

    """
    buffer: StringIO = StringIO()
    with patch.object(target=qemu_snapshot, attribute="stdout", new=buffer):
        status: int = QemuSnapshotCli.run(argv=argv)
    return status, buffer.getvalue()


def test_the_disk_cli_manages_snapshots_end_to_end(tmp_path: Path) -> None:
    """Every ``disk`` subcommand works against a real image.

    Args:
        tmp_path: Pytest temporary directory.

    """
    image: Path = _disk(directory=tmp_path)
    base: tuple[str, ...] = ("disk", "--image", str(object=image))
    status, _ = _cli(argv=(*base, "create", "one"))
    expect(actual=status, expected=0)
    _ = _cli(argv=(*base, "create", "two"))
    _, listing = _cli(argv=(*base, "list"))
    check(condition="one" in listing and "two" in listing, reason=listing)
    _, as_json = _cli(argv=(*base, "list", "--json", "--reverse"))
    expect(actual=loads(s=as_json.splitlines()[0])["name"], expected="two")
    _, filtered = _cli(argv=(*base, "list", "--contains", "tw"))
    check(
        condition="two" in filtered and "one" not in filtered,
        reason=filtered,
    )
    _, info = _cli(argv=(*base, "info", "--json"))
    expect(actual=loads(s=info)["image"]["format"], expected="qcow2")
    _, plain = _cli(argv=(*base, "info"))
    check(condition="qcow2" in plain, reason=plain)
    _write(image=image, pattern=Constants.dirty)
    _ = _cli(argv=(*base, "revert", "one", "--checkpoint", "saved"))
    check(
        condition=_holds(image=image, pattern=Constants.original),
        reason="the CLI revert must restore the disk",
    )
    _ = _cli(argv=(*base, "restore", "saved"))
    _, reset = _cli(argv=(*base, "reset", "baseline"))
    check(condition="create" in reset, reason=reset)
    _, verified = _cli(argv=(*base, "verify", "--name", "baseline"))
    expect(actual=verified.strip(), expected="OK")
    _, missing = _cli(argv=(*base, "verify", "--name", "absent"))
    expect(actual=missing.strip(), expected="MISSING")
    _, version = _cli(argv=(*base, "version"))
    check(condition=version.startswith("qemu-img"), reason=version)
    _, replaced = _cli(argv=(*base, "create", "two", "--replace"))
    check(condition="create" in replaced, reason=replaced)
    _, deleted = _cli(argv=(*base, "delete", "one"))
    check(condition="delete" in deleted, reason=deleted)
    _ = _cli(argv=(*base, "delete", "one", "--missing-ok"))
    _, pruned = _cli(argv=(*base, "prune", "--keep", "1"))
    check(condition=bool(pruned), reason="prune reports what it removed")
    _, cleared = _cli(argv=(*base, "delete-all"))
    check(condition="delete" in cleared, reason=cleared)
    _, dry = _cli(
        argv=(
            "disk",
            "--image",
            str(object=image),
            "--dry-run",
            "create",
            "x",
        ),
    )
    check(condition="dry_run=True" in dry, reason=dry)


def test_the_cli_reports_errors_through_the_parser(tmp_path: Path) -> None:
    """A snapshot error becomes a usage error and a non-zero exit.

    Args:
        tmp_path: Pytest temporary directory.

    """
    image: Path = _disk(directory=tmp_path)
    with pytest.raises(expected_exception=SystemExit) as raised:
        _ = _cli(
            argv=("disk", "--image", str(object=image), "delete", "absent"),
        )
    expect(actual=raised.value.code, expected=2)


def test_the_qmp_cli_drives_a_running_vm() -> None:
    """Every ``qmp`` subcommand reaches the monitor."""
    with FakeQmp() as server:
        base: tuple[str, ...] = (
            "qmp",
            "--qmp-host",
            "127.0.0.1",
            "--qmp-port",
            str(object=server.port),
        )
        for command in (
            "status",
            "blocks",
            "pause",
            "resume",
            "list",
            "commands",
        ):
            with FakeQmp() as fresh:
                status, output = _cli(
                    argv=(
                        "qmp",
                        "--qmp-host",
                        "127.0.0.1",
                        "--qmp-port",
                        str(object=fresh.port),
                        command,
                    ),
                )
                expect(actual=status, expected=0)
                check(
                    condition=output != "\n" or command == "list",
                    reason=command,
                )
        server.running = True
        for argv in (
            ("create", "snap"),
            ("revert", "snap", "--no-pause"),
            ("restore", "snap", "--no-resume"),
            ("delete", "snap"),
            ("hmp", "info snapshots"),
        ):
            with FakeQmp() as fresh:
                status, _ = _cli(
                    argv=(
                        "qmp",
                        "--qmp-host",
                        "127.0.0.1",
                        "--qmp-port",
                        str(object=fresh.port),
                        *argv,
                    ),
                )
                expect(actual=status, expected=0)
        del base


def test_the_qmp_cli_reports_a_dead_monitor() -> None:
    """An unreachable monitor is a usage error, not a traceback."""
    with socket(family=AF_INET, type=SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        free: int = probe.getsockname()[1]
    with pytest.raises(expected_exception=SystemExit) as raised:
        _ = _cli(
            argv=(
                "qmp",
                "--qmp-host",
                "127.0.0.1",
                "--qmp-port",
                str(object=free),
                "--timeout",
                "0.5",
                "status",
            ),
        )
    expect(actual=raised.value.code, expected=2)
