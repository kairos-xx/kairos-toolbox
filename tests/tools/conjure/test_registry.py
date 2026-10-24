"""Tests for persistent named VM definitions."""

from __future__ import annotations

from json import dumps, loads
from pathlib import Path
from typing import cast

import pytest

from tools.conjure import (
    GuestConfig,
    QemuConfig,
    QemuVm,
    SshConfig,
    VmDefinition,
    VmRegistry,
)

from .helpers import check, expect


def _definition(*, name: str = "linux-arm64") -> VmDefinition:
    """Return one portable registry fixture.

    Args:
        name: Registered definition name.

    Returns:
        Deterministic VM definition.

    """
    return VmDefinition(
        name=name,
        description="Reusable Linux builder",
        qemu=QemuConfig(
            executable=Path("qemu-system-aarch64"),
            arguments=("-machine", "virt", "-m", "8G"),
        ),
        ssh=SshConfig(
            username="builder",
            identity_file=Path("keys/builder"),
        ),
        guest=GuestConfig(
            shutdown_command=("sudo", "shutdown", "-h", "now"),
        ),
    )


def test_register_list_details_and_use(tmp_path: Path) -> None:
    """Round-trip a definition and materialize a VM.

    Args:
        tmp_path: Pytest temporary directory.

    """
    registry: VmRegistry = VmRegistry(path=tmp_path / "vms.json")
    definition: VmDefinition = _definition()
    registry.register(definition=definition)
    expect(actual=registry.list(), expected=(definition,))
    expect(
        actual=registry.details(name="linux-arm64"),
        expected=definition,
    )
    vm: QemuVm = registry.use(name="linux-arm64")
    check(
        condition=not vm.is_running,
        reason="materialized VM should start idle",
    )


def test_baseline_snapshot_settings_round_trip(tmp_path: Path) -> None:
    """The baseline disk options survive being saved and loaded.

    Args:
        tmp_path: Pytest temporary directory.

    """
    registry: VmRegistry = VmRegistry(path=tmp_path / "vms.json")
    definition: VmDefinition = VmDefinition(
        name="baseline-vm",
        description="Resets to its original disk",
        qemu=QemuConfig(
            executable=Path("qemu-system-aarch64"),
            arguments=(),
            snapshot_image=Path("disks/win.qcow2"),
            baseline_snapshot="original",
            qemu_img=Path("/opt/qemu/bin/qemu-img"),
        ),
        ssh=SshConfig(
            username="builder",
            identity_file=Path("keys/builder"),
        ),
    )
    registry.register(definition=definition)
    loaded: VmDefinition = registry.details(name="baseline-vm")
    expect(actual=loaded, expected=definition)
    expect(actual=loaded.qemu.baseline_snapshot, expected="original")
    expect(actual=loaded.qemu.snapshot_image, expected=Path("disks/win.qcow2"))


def test_registries_written_before_baselines_still_load(
    tmp_path: Path,
) -> None:
    """A definition without the baseline keys reads as having no baseline.

    Args:
        tmp_path: Pytest temporary directory.

    """
    path: Path = tmp_path / "vms.json"
    registry: VmRegistry = VmRegistry(path=path)
    registry.register(definition=_definition())
    document: dict[str, object] = loads(s=path.read_text(encoding="utf-8"))
    definitions: list[dict[str, dict[str, object]]] = cast(
        typ="list[dict[str, dict[str, object]]]",
        val=document["definitions"],
    )
    for key in ("snapshot_image", "baseline_snapshot", "qemu_img"):
        del definitions[0]["qemu"][key]
    path.write_text(data=dumps(obj=document), encoding="utf-8")
    loaded: VmDefinition = registry.details(name="linux-arm64")
    expect(actual=loaded.qemu.baseline_snapshot, expected=None)
    expect(actual=loaded.qemu.snapshot_image, expected=None)
    expect(actual=loaded.qemu.qemu_img, expected=None)


def test_duplicate_requires_replace(tmp_path: Path) -> None:
    """Protect existing definitions unless replacement is explicit.

    Args:
        tmp_path: Pytest temporary directory.

    """
    registry: VmRegistry = VmRegistry(path=tmp_path / "vms.json")
    registry.register(definition=_definition())
    with pytest.raises(
        expected_exception=ValueError,
        match="already exists",
    ):
        registry.register(definition=_definition())
    replacement: VmDefinition = _definition(name="linux-arm64")
    registry.register(definition=replacement, replace=True)
    expect(
        actual=registry.details(name="linux-arm64"),
        expected=replacement,
    )


def test_remove_definition(tmp_path: Path) -> None:
    """Remove a persistent definition.

    Args:
        tmp_path: Pytest temporary directory.

    """
    registry: VmRegistry = VmRegistry(path=tmp_path / "vms.json")
    definition: VmDefinition = _definition()
    registry.register(definition=definition)
    expect(
        actual=registry.remove(name=definition.name),
        expected=definition,
    )
    expect(actual=registry.list(), expected=())


def test_registry_orders_names(tmp_path: Path) -> None:
    """Return deterministic name ordering.

    Args:
        tmp_path: Pytest temporary directory.

    """
    registry: VmRegistry = VmRegistry(path=tmp_path / "vms.json")
    registry.register(definition=_definition(name="zeta"))
    registry.register(definition=_definition(name="alpha"))
    expect(
        actual=tuple(item.name for item in registry.list()),
        expected=("alpha", "zeta"),
    )


def _encode(*, definition: VmDefinition) -> dict[str, object]:
    """Encode a definition exactly as the registry writes it.

    Args:
        definition: Definition to encode.

    Returns:
        JSON-compatible mapping.

    """
    return {
        "name": definition.name,
        "description": definition.description,
        "qemu": {
            "executable": str(object=definition.qemu.executable),
            "arguments": list(definition.qemu.arguments),
            "network_device": definition.qemu.network_device,
            "network_id": definition.qemu.network_id,
            "automatic_host_forward": (definition.qemu.automatic_host_forward),
            "startup_timeout_seconds": (
                definition.qemu.startup_timeout_seconds
            ),
            "shutdown_timeout_seconds": (
                definition.qemu.shutdown_timeout_seconds
            ),
            "terminate_timeout_seconds": (
                definition.qemu.terminate_timeout_seconds
            ),
        },
        "ssh": {
            "username": definition.ssh.username,
            "identity_file": str(object=definition.ssh.identity_file),
            "host": definition.ssh.host,
            "port": definition.ssh.port,
            "ssh_executable": str(object=definition.ssh.ssh_executable),
            "scp_executable": str(object=definition.ssh.scp_executable),
            "known_hosts_file": (
                None
                if definition.ssh.known_hosts_file is None
                else str(object=definition.ssh.known_hosts_file)
            ),
            "connect_timeout_seconds": (
                definition.ssh.connect_timeout_seconds
            ),
            "poll_interval_seconds": (definition.ssh.poll_interval_seconds),
        },
        "guest": {
            "shutdown_command": definition.guest.shutdown_command,
            "ssh_port": definition.guest.ssh_port,
        },
    }


def _write(
    *,
    path: Path,
    payload: dict[str, object] | list[object],
) -> None:
    """Write a raw registry document.

    Args:
        path: Registry file to write.
        payload: JSON document to persist.

    """
    path.write_text(
        data=dumps(obj=payload),
        encoding="utf-8",
    )


def _malformed(
    *,
    mutate: dict[str, object],
    definition: VmDefinition | None = None,
) -> dict[str, object]:
    """Return a registry document with one malformed definition.

    Args:
        mutate: Nested key-path replacement (``section.field``).
        definition: Definition to encode before mutation.

    Returns:
        Registry document containing the mutated definition.

    Raises:
        TypeError: when the fixture mutation path is not a mapping.

    """
    record: dict[str, object] = _encode(
        definition=definition or _definition(),
    )
    for dotted, value in mutate.items():
        parts: list[str] = dotted.split(sep=".")
        cursor: dict[str, object] = record
        for part in parts[:-1]:
            nested: object = cursor[part]
            if not isinstance(nested, dict):
                msg: str = "fixture requires a nested object"
                raise TypeError(msg)
            cursor = cast(typ="dict[str, object]", val=nested)
        cursor[parts[-1]] = value
    return {"version": 1, "definitions": [record]}


def test_empty_definition_name_is_rejected() -> None:
    """Persisted names must not be blank."""
    with pytest.raises(
        expected_exception=ValueError,
        match="must not be empty",
    ):
        VmDefinition(
            name="  ",
            qemu=_definition().qemu,
            ssh=_definition().ssh,
        )


def test_registry_path_property(tmp_path: Path) -> None:
    """Expose the configured registry path.

    Args:
        tmp_path: Pytest temporary directory.

    """
    target: Path = tmp_path / "vms.json"
    registry: VmRegistry = VmRegistry(path=target)
    expect(actual=registry.path, expected=target)


def test_registry_rejects_non_object_root(tmp_path: Path) -> None:
    """A non-object registry document is rejected.

    Args:
        tmp_path: Pytest temporary directory.

    """
    target: Path = tmp_path / "vms.json"
    _write(path=target, payload=[])
    with pytest.raises(
        expected_exception=TypeError,
        match="root must be an object",
    ):
        VmRegistry(path=target).list()


def test_registry_rejects_unsupported_version(tmp_path: Path) -> None:
    """A mismatched schema version is rejected.

    Args:
        tmp_path: Pytest temporary directory.

    """
    target: Path = tmp_path / "vms.json"
    _write(path=target, payload={"version": 99, "definitions": []})
    with pytest.raises(
        expected_exception=ValueError,
        match="Unsupported VM registry version",
    ):
        VmRegistry(path=target).list()


def test_registry_rejects_non_list_definitions(tmp_path: Path) -> None:
    """Definitions must be a list.

    Args:
        tmp_path: Pytest temporary directory.

    """
    target: Path = tmp_path / "vms.json"
    _write(path=target, payload={"version": 1, "definitions": {}})
    with pytest.raises(
        expected_exception=TypeError,
        match="definitions must be a list",
    ):
        VmRegistry(path=target).list()


def test_registry_rejects_duplicate_names(tmp_path: Path) -> None:
    """Duplicate names inside the document are rejected.

    Args:
        tmp_path: Pytest temporary directory.

    """
    record: dict[str, object] = _encode(definition=_definition())
    target: Path = tmp_path / "vms.json"
    _write(
        path=target,
        payload={"version": 1, "definitions": [record, dict(record)]},
    )
    with pytest.raises(
        expected_exception=ValueError,
        match="Duplicate VM definition",
    ):
        VmRegistry(path=target).list()


def test_registry_rejects_non_object_definition(tmp_path: Path) -> None:
    """A non-object definition entry is rejected.

    Args:
        tmp_path: Pytest temporary directory.

    """
    target: Path = tmp_path / "vms.json"
    _write(path=target, payload={"version": 1, "definitions": ["x"]})
    with pytest.raises(
        expected_exception=TypeError,
        match="must be an object",
    ):
        VmRegistry(path=target).list()


def test_registry_rejects_non_string_description(tmp_path: Path) -> None:
    """Descriptions must be strings.

    Args:
        tmp_path: Pytest temporary directory.

    """
    target: Path = tmp_path / "vms.json"
    _write(
        path=target,
        payload=_malformed(mutate={"description": 1}),
    )
    with pytest.raises(
        expected_exception=TypeError,
        match="description must be a string",
    ):
        VmRegistry(path=target).list()


def test_registry_rejects_non_object_sections(tmp_path: Path) -> None:
    """qemu, ssh, and guest sections must be objects.

    Args:
        tmp_path: Pytest temporary directory.

    """
    target: Path = tmp_path / "vms.json"
    _write(path=target, payload=_malformed(mutate={"qemu": "x"}))
    with pytest.raises(
        expected_exception=TypeError,
        match="qemu must be an object",
    ):
        VmRegistry(path=target).list()


def test_registry_rejects_bad_field_types(tmp_path: Path) -> None:
    """Every persisted field type is validated.

    Args:
        tmp_path: Pytest temporary directory.

    """
    cases: tuple[tuple[str, object, str], ...] = (
        ("qemu.executable", 1, "executable must be a string"),
        ("qemu.arguments", "x", "arguments must be a string list"),
        ("qemu.automatic_host_forward", "x", "must be a boolean"),
        ("qemu.startup_timeout_seconds", "x", "must be numeric"),
        ("ssh.port", "x", "must be an integer or null"),
        ("ssh.connect_timeout_seconds", "x", "must be an integer"),
        ("ssh.known_hosts_file", 1, "must be a string or null"),
        ("guest.shutdown_command", "x", "must be a string list"),
    )
    for index, (dotted, value, match) in enumerate(iterable=cases):
        target: Path = tmp_path / f"vms{index}.json"
        _write(path=target, payload=_malformed(mutate={dotted: value}))
        with pytest.raises(
            expected_exception=TypeError,
            match=match,
        ):
            VmRegistry(path=target).list()
