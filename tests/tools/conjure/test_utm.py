"""Tests for UTM QEMU VM discovery and standalone command derivation."""

from __future__ import annotations

from plistlib import dumps
from subprocess import CompletedProcess
from typing import TYPE_CHECKING, Final, final
from unittest.mock import patch

import pytest

from tools.conjure.qemu_inventory import QemuInventory
from tools.conjure.utm import (
    DependencyKind,
    UtmBundle,
    UtmDrive,
    UtmQemuExtractor,
    UtmVm,
)

from .helpers import check, expect, present

if TYPE_CHECKING:
    from pathlib import Path


@final
class Constants:
    """Group module-level constants.

    Attributes:
        qemu: Grouped module constant.
        argv: Grouped module constant.

    """

    qemu: Final[str] = (
        "/Applications/UTM.app/Contents/MacOS/qemu-system-aarch64"
    )
    argv: Final[tuple[str, ...]] = (
        qemu,
        "-L",
        "/res/qemu",
        "-S",
        "-spice",
        "unix=on,addr=/tmp/x.spice",
        "-chardev",
        "spiceport,id=org.qemu.monitor.qmp,name=org.qemu.monitor.qmp.0",
        "-mon",
        "chardev=org.qemu.monitor.qmp,mode=control",
        "-name",
        "Windows",
        "-uuid",
        "U-1",
        "-machine",
        "virt",
        "-accel",
        "hvf",
        "-cpu",
        "host",
        "-smp",
        "cpus=4,sockets=1",
        "-m",
        "8192",
        "-drive",
        "if=none,media=disk,id=drive0,file.filename=/vm/disk.qcow2,readonly=on",
        "-device",
        "virtio-blk-pci,drive=drive0",
        "-device",
        "usb-redir,chardev=usbredir0",
        "-chardev",
        "socket,id=chrtpm,path=/tmp/x.swtpm",
        "-tpmdev",
        "emulator,id=tpm0,chardev=chrtpm",
        "-device",
        "tpm-tis-device,tpmdev=tpm0",
        "-audiodev",
        "spice,id=audio0",
        "-device",
        "intel-hda,audiodev=audio0",
        "-serial",
        "chardev:org.qemu.monitor.qmp",
    )


def _vm() -> UtmVm:
    """Parse the sample UTM command.

    Returns:
        The parsed VM.

    """
    return UtmQemuExtractor(home=None).from_argv(argv=Constants.argv)


def test_standalone_argv_drops_utm_integration() -> None:
    """UTM channels go; hardware and disks stay."""
    standalone: tuple[str, ...] = _vm().standalone_argv
    for gone in (
        "-S",
        "-spice",
        "-tpmdev",
        "-audiodev",
        "-mon",
        "-serial",
        "usb-redir,chardev=usbredir0",
        "tpm-tis-device,tpmdev=tpm0",
        "intel-hda,audiodev=audio0",
    ):
        check(condition=gone not in standalone, reason=gone)
    for kept in ("-machine", "hvf", "virtio-blk-pci,drive=drive0"):
        check(condition=kept in standalone, reason=kept)
    expect(actual=standalone[-2:], expected=("-display", "default"))
    check(
        condition=not any("swtpm" in part for part in standalone),
        reason="the TPM socket is removed",
    )


def test_a_display_choice_is_kept() -> None:
    """An explicit ``-nographic`` suppresses the default display."""
    standalone: tuple[str, ...] = UtmQemuExtractor.normalize_for_standalone(
        argv=(Constants.qemu, "-nographic", "-m", "1"),
    )
    expect(
        actual=standalone,
        expected=(Constants.qemu, "-nographic", "-m", "1"),
    )


def test_the_vm_describes_the_captured_launch() -> None:
    """Hardware, disks and requirements are extracted."""
    vm: UtmVm = _vm()
    expect(
        actual=(vm.name, vm.architecture, vm.machine, vm.accelerator, vm.cpu),
        expected=("Windows", "aarch64", "virt", "hvf", "host"),
    )
    expect(actual=(vm.memory_mib, vm.cpus), expected=(8192, 4))
    expect(actual=vm.uuid, expected="U-1")
    drive: UtmDrive = vm.drives[0]
    expect(
        actual=(drive.identifier, drive.media, drive.readonly),
        expected=("drive0", "disk", True),
    )
    expect(
        actual=str(object=present(value=drive.path)),
        expected="/vm/disk.qcow2",
    )
    kinds: set[DependencyKind] = {item.kind for item in vm.dependencies}
    expect(
        actual=kinds,
        expected={
            DependencyKind.directory,
            DependencyKind.file,
            DependencyKind.socket,
            DependencyKind.utm_runtime,
        },
    )
    expect(actual=len(vm.fidelity_warnings), expected=3)
    expect(actual=vm.running_pid, expected=None)
    check(condition="-S" in vm.captured_command, reason="captured keeps -S")
    check(
        condition="-S" not in vm.standalone_argv,
        reason="standalone drops it",
    )
    check(condition=bool(vm.standalone_command), reason="renders a command")


def test_commands_and_pids_round_trip() -> None:
    """A shell-rendered command parses back with its PID."""
    vm: UtmVm = UtmQemuExtractor().from_command(
        command=_vm().captured_command,
        pid=42,
    )
    expect(actual=(vm.name, vm.running_pid), expected=("Windows", 42))


def test_non_qemu_commands_are_rejected() -> None:
    """Only ``qemu-system-*`` argv is accepted."""
    with pytest.raises(
        expected_exception=ValueError,
        match="QEMU system emulator",
    ):
        _ = UtmQemuExtractor().from_argv(argv=("/bin/ls",))
    with pytest.raises(
        expected_exception=ValueError,
        match="QEMU system emulator",
    ):
        _ = UtmQemuExtractor().from_argv(argv=())


def test_option_parsing_edge_cases() -> None:
    """Missing, non-numeric and trailing options are tolerated."""
    vm: UtmVm = UtmQemuExtractor().from_argv(
        argv=(Constants.qemu, "-m", "lots", "-smp", "2", "-name"),
    )
    expect(actual=(vm.memory_mib, vm.cpus), expected=(None, 2))
    expect(actual=vm.name, expected="aarch64")
    bare: UtmVm = UtmQemuExtractor().from_argv(
        argv=(Constants.qemu, "-smp", "x"),
    )
    expect(actual=bare.cpus, expected=None)
    expect(
        actual=UtmQemuExtractor().from_argv(argv=(Constants.qemu,)).cpus,
        expected=None,
    )


def test_discovery_keeps_only_utm_qemu_processes() -> None:
    """Plain QEMU and other processes are ignored; results are sorted."""
    other: tuple[str, ...] = ("/usr/bin/qemu-system-x86_64", "-name", "plain")
    second: tuple[str, ...] = (
        *Constants.argv[:10],
        "-name",
        "Alpha",
        *Constants.argv[12:],
    )
    found: tuple[UtmVm, ...] = UtmQemuExtractor(
        home=None,
        process_rows=(
            (9, ("/bin/ls",)),
            (7, other),
            (5, Constants.argv),
            (3, second),
            (1, ()),
        ),
    ).discover()
    expect(actual=[vm.name for vm in found], expected=["Alpha", "Windows"])
    inspected: UtmVm = UtmQemuExtractor(
        process_rows=((5, Constants.argv),),
    ).inspect_running(pid=5)
    expect(actual=inspected.running_pid, expected=5)
    with pytest.raises(expected_exception=LookupError, match="99"):
        _ = UtmQemuExtractor(
            process_rows=((5, Constants.argv),),
        ).inspect_running(pid=99)


def test_bundles_are_located_and_matched(tmp_path: Path) -> None:
    """Stopped bundles are read from plists and matched by UUID.

    Args:
        tmp_path: Pytest temporary directory.

    """
    documents: Path = tmp_path / "Documents"
    bundle: Path = documents / "Windows.utm"
    bundle.mkdir(parents=True)
    (bundle / "config.plist").write_bytes(
        data=dumps(
            value={
                "Information": {"Name": "Windows", "UUID": "U-1"},
                "System": {"Architecture": "aarch64"},
                "List": [{"Skipped": 1}, "text"],
            },
        ),
    )
    (documents / "Bare.utm").mkdir()
    extractor: UtmQemuExtractor = UtmQemuExtractor(
        home=tmp_path,
        process_rows=((5, Constants.argv),),
    )
    bundles: tuple[UtmBundle, ...] = extractor.locate_bundles()
    expect(
        actual=[item.name for item in bundles],
        expected=["Bare", "Windows"],
    )
    windows: UtmBundle = bundles[1]
    expect(
        actual=(windows.uuid, windows.architecture),
        expected=("U-1", "aarch64"),
    )
    expect(actual=bundles[0].config, expected=None)
    vm: UtmVm = extractor.discover()[0]
    expect(actual=vm.bundle, expected=windows.path)
    expect(actual=vm.config, expected=windows.config)


def test_availability_needs_macos_and_utm(tmp_path: Path) -> None:
    """UTM is only used on macOS hosts where it is installed.

    Args:
        tmp_path: Pytest temporary directory.

    """
    (tmp_path / "Applications" / "UTM.app").mkdir(parents=True)
    extractor: UtmQemuExtractor = UtmQemuExtractor(home=tmp_path)
    empty: UtmQemuExtractor = UtmQemuExtractor(home=tmp_path / "none")
    local_apps: tuple[str, ...] = ("Applications/UTM.app",)
    with (
        patch.object(
            target=UtmQemuExtractor,
            attribute="utm_apps",
            new=local_apps,
        ),
        patch(target="tools.conjure.utm.system", return_value="Linux"),
    ):
        expect(actual=extractor.available(), expected=False)
    with (
        patch.object(
            target=UtmQemuExtractor,
            attribute="utm_apps",
            new=local_apps,
        ),
        patch(target="tools.conjure.utm.system", return_value="Darwin"),
    ):
        expect(actual=extractor.available(), expected=True)
        expect(actual=empty.available(), expected=False)
    containers: UtmQemuExtractor = UtmQemuExtractor(home=tmp_path / "c")
    (tmp_path / "c/Library/Containers/com.utmapp.UTM/Data/Documents").mkdir(
        parents=True,
    )
    with (
        patch.object(
            target=UtmQemuExtractor,
            attribute="utm_apps",
            new=local_apps,
        ),
        patch(target="tools.conjure.utm.system", return_value="Darwin"),
    ):
        expect(actual=containers.available(), expected=True)


def test_inventory_uses_utm_only_when_available(tmp_path: Path) -> None:
    """``QemuInventory.utm_vms`` reports nothing without UTM.

    Args:
        tmp_path: Pytest temporary directory.

    """
    (tmp_path / "Applications" / "UTM.app").mkdir(parents=True)
    extractor: UtmQemuExtractor = UtmQemuExtractor(
        home=tmp_path,
        process_rows=((5, Constants.argv),),
    )
    with patch(target="tools.conjure.utm.system", return_value="Linux"):
        expect(actual=QemuInventory.utm_vms(extractor=extractor), expected=())
    with patch(target="tools.conjure.utm.system", return_value="Darwin"):
        found: tuple[UtmVm, ...] = QemuInventory.utm_vms(extractor=extractor)
        expect(actual=[vm.name for vm in found], expected=["Windows"])
    with patch(target="tools.conjure.utm.system", return_value="Linux"):
        expect(actual=QemuInventory.utm_vms(), expected=())


def test_process_table_is_read_and_unparseable_rows_skipped() -> None:
    """``ps`` output is parsed; broken quoting does not abort discovery."""
    output: str = (
        f"  11 {Constants.qemu} -name Windows -uuid U-1 -chardev "
        f"spiceport,id=org.qemu.monitor.qmp\n  12 {
            Constants.qemu
        } -name 'broken\n  13 /bin/sleep 1\n\n"
    )
    completed: CompletedProcess[str] = CompletedProcess(
        args=(),
        returncode=0,
        stdout=output,
        stderr="",
    )
    with patch(target="tools.conjure.utm.run", return_value=completed):
        found: tuple[UtmVm, ...] = UtmQemuExtractor().discover()
    expect(
        actual=[(vm.name, vm.running_pid) for vm in found],
        expected=[("Windows", 11)],
    )


def test_executable_resolution(tmp_path: Path) -> None:
    """A bare name resolves through ``PATH`` or the UTM app.

    Args:
        tmp_path: Pytest temporary directory.

    """
    real: Path = tmp_path / "bin" / "qemu-system-aarch64"
    real.parent.mkdir()
    real.write_text(data="", encoding="utf-8")
    absolute: UtmVm = UtmQemuExtractor().from_argv(argv=(str(object=real),))
    expect(actual=absolute.executable, expected=real)
    with patch(
        target="tools.conjure.utm.which",
        return_value=str(object=real),
    ):
        named: UtmVm = UtmQemuExtractor(home=tmp_path).from_argv(
            argv=("qemu-system-aarch64",),
        )
    expect(actual=named.executable, expected=real)
    inside: Path = (
        tmp_path / "Applications/UTM.app/Contents/qemu-system-x86_64"
    )
    inside.parent.mkdir(parents=True)
    inside.write_text(data="", encoding="utf-8")
    with patch(target="tools.conjure.utm.which", return_value=None):
        bundled: UtmVm = UtmQemuExtractor(home=tmp_path).from_argv(
            argv=("qemu-system-x86_64",),
        )
        missing: UtmVm = UtmQemuExtractor(home=tmp_path).from_argv(
            argv=("qemu-system-riscv64",),
        )
    expect(actual=bundled.executable, expected=inside)
    expect(
        actual=str(object=missing.executable),
        expected="qemu-system-riscv64",
    )
