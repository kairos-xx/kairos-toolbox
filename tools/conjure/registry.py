"""Persistent named QEMU VM definitions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from json import dump, load
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Final, cast, final

from tools.conjure.config import GuestConfig, QemuConfig, SshConfig
from tools.conjure.vm import QemuVm

type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)
type JsonObject = dict[str, JsonValue]


@dataclass(frozen=True, slots=True, kw_only=True)
class VmDefinition:
    """Describe one reusable named QEMU VM.

    Attributes:
        name: Unique registry name.
        description: Optional human-readable description.
        qemu: QEMU launch configuration.
        ssh: SSH endpoint configuration.
        guest: Guest OS configuration.

    """

    name: str
    qemu: QemuConfig
    ssh: SshConfig
    guest: GuestConfig = field(default_factory=GuestConfig)
    description: str = ""

    def __post_init__(self) -> None:
        """Validate the persistent identity."""
        if not self.name.strip():
            msg: str = "VM definition name must not be empty."
            raise ValueError(msg)


@final
class VmRegistry:
    """Persist and materialize named QEMU VM definitions."""

    _version: Final[int] = 1
    _path: Path

    def __init__(self, *, path: Path) -> None:
        """Initialize a registry at an explicit portable path.

        Args:
            path: JSON registry file.

        """
        self._path = path

    @property
    def path(self) -> Path:
        """Return the registry path.

        Returns:
            Registry file location.

        """
        return self._path

    def list(self) -> tuple[VmDefinition, ...]:
        """Return all definitions ordered by name.

        Returns:
            Definitions ordered by name.

        """
        definitions: dict[str, VmDefinition] = self._read()
        return tuple(definitions[name] for name in sorted(definitions))

    def details(self, *, name: str) -> VmDefinition:
        """Return one named definition.

        Args:
            name: Registered VM name.

        Returns:
            Matching immutable definition.

        Raises:
            KeyError: If no definition has that name.

        """
        return self._read()[name]

    def register(
        self,
        *,
        definition: VmDefinition,
        replace: bool = False,
    ) -> None:
        """Register a VM definition.

        Args:
            definition: Definition to persist.
            replace: Permit replacing an existing name.

        Raises:
            ValueError: If the name already exists and replacement is false.

        """
        definitions: dict[str, VmDefinition] = self._read()
        if definition.name in definitions and not replace:
            msg: str = f"VM definition already exists: {definition.name}"
            raise ValueError(
                msg,
            )
        definitions[definition.name] = definition
        self._write(definitions=definitions)

    def remove(self, *, name: str) -> VmDefinition:
        """Remove and return a named definition.

        Args:
            name: Definition name to remove.

        Returns:
            The removed definition.

        """
        definitions: dict[str, VmDefinition] = self._read()
        definition: VmDefinition = definitions.pop(name)
        self._write(definitions=definitions)
        return definition

    def use(self, *, name: str) -> QemuVm:
        """Materialize a normal QEMU runner from a registered definition.

        Args:
            name: Definition name to materialize.

        Returns:
            QEMU runner built from the definition.

        """
        definition: VmDefinition = self.details(name=name)
        return QemuVm(
            qemu=definition.qemu,
            ssh=definition.ssh,
            guest=definition.guest,
        )

    @classmethod
    def _decode(cls, *, value: JsonValue) -> VmDefinition:
        """Decode and validate one persisted definition.

        Args:
            value: Decoded JSON value to validate.

        Returns:
            Validated VM definition.

        Raises:
            TypeError: when the document has unexpected types.

        """
        if not isinstance(value, dict):
            msg: str = "VM definition must be an object."
            raise TypeError(msg)
        raw: JsonObject = value
        name: str = cls._string(raw=raw, key="name")
        description_value: JsonValue = raw.get("description", "")
        if not isinstance(description_value, str):
            msg = "VM description must be a string."
            raise TypeError(msg)
        qemu: JsonObject = cls._mapping(raw=raw, key="qemu")
        ssh: JsonObject = cls._mapping(raw=raw, key="ssh")
        guest: JsonObject = cls._mapping(raw=raw, key="guest")
        return VmDefinition(
            name=name,
            description=description_value,
            qemu=QemuConfig(
                executable=Path(
                    cls._string(raw=qemu, key="executable"),
                ),
                arguments=cls._strings(raw=qemu, key="arguments"),
                network_device=cls._string(
                    raw=qemu,
                    key="network_device",
                ),
                network_id=cls._string(raw=qemu, key="network_id"),
                automatic_host_forward=cls._boolean(
                    raw=qemu,
                    key="automatic_host_forward",
                ),
                startup_timeout_seconds=cls._number(
                    raw=qemu,
                    key="startup_timeout_seconds",
                ),
                shutdown_timeout_seconds=cls._number(
                    raw=qemu,
                    key="shutdown_timeout_seconds",
                ),
                terminate_timeout_seconds=cls._number(
                    raw=qemu,
                    key="terminate_timeout_seconds",
                ),
                snapshot_image=cls._optional_path(
                    raw={"snapshot_image": qemu.get("snapshot_image")},
                    key="snapshot_image",
                ),
                baseline_snapshot=cls._optional_text(
                    raw=qemu,
                    key="baseline_snapshot",
                ),
                qemu_img=cls._optional_path(
                    raw={"qemu_img": qemu.get("qemu_img")},
                    key="qemu_img",
                ),
            ),
            ssh=SshConfig(
                username=cls._string(raw=ssh, key="username"),
                identity_file=Path(
                    cls._string(raw=ssh, key="identity_file"),
                ),
                host=cls._string(raw=ssh, key="host"),
                port=cls._optional_int(raw=ssh, key="port"),
                ssh_executable=Path(
                    cls._string(raw=ssh, key="ssh_executable"),
                ),
                scp_executable=Path(
                    cls._string(raw=ssh, key="scp_executable"),
                ),
                known_hosts_file=cls._optional_path(
                    raw=ssh,
                    key="known_hosts_file",
                ),
                connect_timeout_seconds=cls._integer(
                    raw=ssh,
                    key="connect_timeout_seconds",
                ),
                poll_interval_seconds=cls._number(
                    raw=ssh,
                    key="poll_interval_seconds",
                ),
            ),
            guest=GuestConfig(
                shutdown_command=cls._optional_strings(
                    raw=guest,
                    key="shutdown_command",
                ),
                ssh_port=cls._integer(raw=guest, key="ssh_port"),
            ),
        )

    @staticmethod
    def _encode(*, definition: VmDefinition) -> JsonObject:
        """Encode one definition using JSON-compatible values.

        Args:
            definition: Definition to serialize.

        Returns:
            JSON-compatible mapping.

        """
        return {
            "name": definition.name,
            "description": definition.description,
            "qemu": {
                **asdict(obj=definition.qemu),
                "executable": str(object=definition.qemu.executable),
                "arguments": list(definition.qemu.arguments),
                "snapshot_image": (
                    None
                    if definition.qemu.snapshot_image is None
                    else str(object=definition.qemu.snapshot_image)
                ),
                "qemu_img": (
                    None
                    if definition.qemu.qemu_img is None
                    else str(object=definition.qemu.qemu_img)
                ),
            },
            "ssh": {
                **asdict(obj=definition.ssh),
                "identity_file": str(object=definition.ssh.identity_file),
                "ssh_executable": str(object=definition.ssh.ssh_executable),
                "scp_executable": str(object=definition.ssh.scp_executable),
                "known_hosts_file": (
                    None
                    if definition.ssh.known_hosts_file is None
                    else str(object=definition.ssh.known_hosts_file)
                ),
            },
            "guest": {
                "shutdown_command": (
                    None
                    if definition.guest.shutdown_command is None
                    else list(definition.guest.shutdown_command)
                ),
                "ssh_port": definition.guest.ssh_port,
            },
        }

    @staticmethod
    def _mapping(*, raw: JsonObject, key: str) -> JsonObject:
        """Return a required mapping field.

        Args:
            raw: Decoded JSON object.
            key: Field name to extract.

        Returns:
            The nested object field.

        Raises:
            TypeError: when the field has an unexpected type.

        """
        value: JsonValue = raw[key]
        if not isinstance(value, dict):
            msg: str = f"{key} must be an object."
            raise TypeError(msg)
        return value

    @staticmethod
    def _string(*, raw: JsonObject, key: str) -> str:
        """Return a required string field.

        Args:
            raw: Decoded JSON object.
            key: Field name to extract.

        Returns:
            The validated field value.

        Raises:
            TypeError: when the field has an unexpected type.

        """
        value: JsonValue = raw[key]
        if not isinstance(value, str):
            msg: str = f"{key} must be a string."
            raise TypeError(msg)
        return value

    @staticmethod
    def _boolean(*, raw: JsonObject, key: str) -> bool:
        """Return a required boolean field.

        Args:
            raw: Decoded JSON object.
            key: Field name to extract.

        Returns:
            The validated field value.

        Raises:
            TypeError: when the field has an unexpected type.

        """
        value: JsonValue = raw[key]
        if not isinstance(value, bool):
            msg: str = f"{key} must be a boolean."
            raise TypeError(msg)
        return value

    @staticmethod
    def _integer(*, raw: JsonObject, key: str) -> int:
        """Return a required integer field.

        Args:
            raw: Decoded JSON object.
            key: Field name to extract.

        Returns:
            The validated field value.

        Raises:
            TypeError: when the field has an unexpected type.

        """
        value: JsonValue = raw[key]
        if not isinstance(value, int) or isinstance(value, bool):
            msg: str = f"{key} must be an integer."
            raise TypeError(msg)
        return value

    @staticmethod
    def _number(*, raw: JsonObject, key: str) -> float:
        """Return a required numeric field.

        Args:
            raw: Decoded JSON object.
            key: Field name to extract.

        Returns:
            The validated field value.

        Raises:
            TypeError: when the field has an unexpected type.

        """
        value: JsonValue = raw[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            msg: str = f"{key} must be numeric."
            raise TypeError(msg)
        return float(value)

    @staticmethod
    def _optional_int(*, raw: JsonObject, key: str) -> int | None:
        """Return an optional integer field.

        Args:
            raw: Decoded JSON object.
            key: Field name to extract.

        Returns:
            The integer field, or ``None``.

        Raises:
            TypeError: when the field has an unexpected type.

        """
        value: JsonValue = raw[key]
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            msg: str = f"{key} must be an integer or null."
            raise TypeError(msg)
        return value

    @staticmethod
    def _strings(*, raw: JsonObject, key: str) -> tuple[str, ...]:
        """Return a required string sequence.

        Args:
            raw: Decoded JSON object.
            key: Field name to extract.

        Returns:
            Tuple of strings.

        Raises:
            TypeError: when the field has an unexpected type.

        """
        value: JsonValue = raw[key]
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            msg: str = f"{key} must be a string list."
            raise TypeError(msg)
        return tuple(cast(typ="list[str]", val=value))

    @staticmethod
    def _optional_strings(
        *,
        raw: JsonObject,
        key: str,
    ) -> tuple[str, ...] | None:
        """Return an optional string sequence.

        Args:
            raw: Decoded JSON object.
            key: Field name to extract.

        Returns:
            Tuple of strings, or ``None``.

        Raises:
            TypeError: when the field has an unexpected type.

        """
        value: JsonValue = raw[key]
        if value is None:
            return None
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            msg: str = f"{key} must be a string list or null."
            raise TypeError(msg)
        return tuple(cast(typ="list[str]", val=value))

    @staticmethod
    def _optional_text(*, raw: JsonObject, key: str) -> str | None:
        """Return an optional text field; a missing key reads as ``None``.

        Args:
            raw: Decoded JSON object.
            key: Field name to extract.

        Returns:
            The text, or ``None``.

        Raises:
            TypeError: when the field has an unexpected type.

        """
        value: JsonValue = raw.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            msg: str = f"{key} must be a string or null."
            raise TypeError(msg)
        return value

    @staticmethod
    def _optional_path(*, raw: JsonObject, key: str) -> Path | None:
        """Return an optional path field.

        Args:
            raw: Decoded JSON object.
            key: Field name to extract.

        Returns:
            The path field, or ``None``.

        Raises:
            TypeError: when the field has an unexpected type.

        """
        value: JsonValue = raw[key]
        if value is None:
            return None
        if not isinstance(value, str):
            msg: str = f"{key} must be a string or null."
            raise TypeError(msg)
        return Path(value)

    def _read(self) -> dict[str, VmDefinition]:
        """Read and decode the registry.

        Returns:
            Definitions keyed by name.

        Raises:
            TypeError: when persisted values have unexpected types.
            ValueError: when the document structure is invalid.

        """
        if not self._path.exists():
            return {}
        with self._path.open(mode="r", encoding="utf-8") as stream:
            decoded: JsonValue = cast(typ="JsonValue", val=load(fp=stream))
        if not isinstance(decoded, dict):
            msg: str = "VM registry root must be an object."
            raise TypeError(msg)
        root: JsonObject = decoded
        if root.get("version") != self._version:
            msg = "Unsupported VM registry version."
            raise ValueError(msg)
        raw_definitions: JsonValue = root.get("definitions")
        if not isinstance(raw_definitions, list):
            msg = "VM registry definitions must be a list."
            raise TypeError(msg)
        definitions: dict[str, VmDefinition] = {}
        for raw_definition in raw_definitions:
            definition: VmDefinition = self._decode(value=raw_definition)
            if definition.name in definitions:
                msg = f"Duplicate VM definition: {definition.name}"
                raise ValueError(
                    msg,
                )
            definitions[definition.name] = definition
        return definitions

    def _write(self, *, definitions: dict[str, VmDefinition]) -> None:
        """Atomically persist all definitions.

        Args:
            definitions: Definitions to persist atomically.

        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload: JsonObject = {
            "version": self._version,
            "definitions": [
                self._encode(definition=definitions[name])
                for name in sorted(definitions)
            ],
        }
        temporary_path: Path
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self._path.parent,
            prefix=f".{self._path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            dump(obj=payload, fp=stream, indent=2, sort_keys=True)
            stream.write("\n")
        temporary_path.replace(target=self._path)
