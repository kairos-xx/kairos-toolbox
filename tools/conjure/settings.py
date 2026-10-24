"""Layered settings: defaults < user file < ``pyproject.toml`` < CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tomllib import load as _toml_load
from typing import TYPE_CHECKING, Final, cast, final

if TYPE_CHECKING:
    from collections.abc import Mapping
type Scalar = str | int | float | bool | None
type SettingValue = (
    Scalar
    | Path
    | list[SettingValue]
    | tuple[SettingValue, ...]
    | dict[str, SettingValue]
)
type SettingTable = dict[str, SettingValue]


@final
class Constants:
    """Group module-level constants.

    Attributes:
        default_registry: Grouped module constant.
        default_user_settings: Grouped module constant.
        tables: Grouped module constant.

    """

    default_registry: Final[Path] = Path.home() / ".conjure" / "vms.json"
    default_user_settings: Final[Path] = (
        Path.home() / ".conjure" / "settings.toml"
    )
    tables: Final[frozenset[str]] = frozenset(
        {"general", "local", "docker", "qemu", "ssh", "guest"},
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Settings:
    """Resolved conjure settings shared by the CLI.

    Attributes:
        json_output: Emit machine-readable JSON output.
        registry_path: Persistent VM registry file.
        local_working_directory: Working directory for local execution.
        docker_executable: Docker CLI executable.
        docker_image: Default image for ``docker run``.
        docker_working_directory: Working directory inside containers.
        docker_create_arguments: Extra ``docker create`` arguments.
        docker_command: Keepalive command passed to ``docker create``.
        docker_remove_on_close: Remove owned containers on close.
        docker_stop_timeout_seconds: Grace period for ``docker stop``.
        docker_owns_container: Own attached containers by default.
        qemu_executable: QEMU system executable.
        qemu_arguments: Host/architecture-specific QEMU arguments.
        qemu_network_device: Guest-compatible virtual NIC model.
        qemu_network_id: Identifier for generated QEMU user networking.
        qemu_automatic_host_forward: Add loopback SSH forwarding.
        qemu_startup_timeout_seconds: Maximum SSH readiness wait.
        qemu_shutdown_timeout_seconds: Maximum graceful shutdown wait.
        qemu_terminate_timeout_seconds: Maximum wait after termination.
        qemu_snapshot_image: qcow2 disk returned to its baseline snapshot.
        qemu_baseline_snapshot: Snapshot the disk boots from and is put back
            to; taken from the disk's current state on the first run.
        qemu_img_executable: ``qemu-img`` executable.
        qemu_process_executable: Process-listing executable.
        qemu_image_roots: Default roots for image discovery.
        ssh_username: Guest account used for execution.
        ssh_identity_file: Private authentication key.
        ssh_host: SSH endpoint host.
        ssh_port: Optional explicit SSH port.
        ssh_executable: Host OpenSSH client.
        scp_executable: Host SCP client.
        ssh_known_hosts_file: Optional isolated known-hosts database.
        ssh_connect_timeout_seconds: Per-attempt SSH timeout.
        ssh_poll_interval_seconds: Readiness polling delay.
        guest_shutdown_command: Graceful guest shutdown command.
        guest_ssh_port: SSH service port inside the guest.

    """

    json_output: bool = False
    registry_path: Path = Constants.default_registry
    local_working_directory: Path | None = None
    docker_executable: Path = Path("docker")
    docker_image: str | None = None
    docker_working_directory: str | None = None
    docker_create_arguments: tuple[str, ...] = ()
    docker_command: tuple[str, ...] = ()
    docker_remove_on_close: bool = True
    docker_stop_timeout_seconds: int = 10
    docker_owns_container: bool = True
    qemu_executable: Path | None = None
    qemu_arguments: tuple[str, ...] = ()
    qemu_network_device: str = "virtio-net-pci"
    qemu_network_id: str = "conjure_net"
    qemu_automatic_host_forward: bool = True
    qemu_startup_timeout_seconds: float = 120.0
    qemu_shutdown_timeout_seconds: float = 30.0
    qemu_terminate_timeout_seconds: float = 5.0
    qemu_snapshot_image: Path | None = None
    qemu_baseline_snapshot: str | None = None
    qemu_img_executable: Path = Path("qemu-img")
    qemu_process_executable: Path = Path("ps")
    qemu_image_roots: tuple[Path, ...] = ()
    ssh_username: str | None = None
    ssh_identity_file: Path | None = None
    ssh_host: str = "127.0.0.1"
    ssh_port: int | None = None
    ssh_executable: Path = Path("ssh")
    scp_executable: Path = Path("scp")
    ssh_known_hosts_file: Path | None = None
    ssh_connect_timeout_seconds: int = 5
    ssh_poll_interval_seconds: float = 0.5
    guest_shutdown_command: tuple[str, ...] | None = None
    guest_ssh_port: int = 22


@final
class SettingsLoader:
    """Load layered conjure settings."""

    _fields: Final[frozenset[str]] = frozenset(
        {
            "json_output",
            "registry_path",
            "local_working_directory",
            "docker_executable",
            "docker_image",
            "docker_working_directory",
            "docker_create_arguments",
            "docker_command",
            "docker_remove_on_close",
            "docker_stop_timeout_seconds",
            "docker_owns_container",
            "qemu_executable",
            "qemu_arguments",
            "qemu_network_device",
            "qemu_network_id",
            "qemu_automatic_host_forward",
            "qemu_startup_timeout_seconds",
            "qemu_shutdown_timeout_seconds",
            "qemu_terminate_timeout_seconds",
            "qemu_snapshot_image",
            "qemu_baseline_snapshot",
            "qemu_img_executable",
            "qemu_process_executable",
            "qemu_image_roots",
            "ssh_username",
            "ssh_identity_file",
            "ssh_host",
            "ssh_port",
            "ssh_executable",
            "scp_executable",
            "ssh_known_hosts_file",
            "ssh_connect_timeout_seconds",
            "ssh_poll_interval_seconds",
            "guest_shutdown_command",
            "guest_ssh_port",
        },
    )

    @classmethod
    def load(
        cls,
        *,
        pyproject: Path | None = None,
        user_settings: Path | None = None,
        overrides: Mapping[str, SettingValue] | None = None,
    ) -> Settings:
        """Resolve layered settings: user file, project file, overrides.

        Args:
            pyproject: Explicit project file; discovered from the current
                directory upward when omitted.
            user_settings: Explicit user-level file using the same
                ``[tool.conjure]`` tables; defaults to
                ``~/.conjure/settings.toml``. A path that does not exist
                disables the layer.
            overrides: Field-name overrides; ``None`` values are ignored.

        Returns:
            Fully resolved immutable settings.

        Raises:
            ValueError: If an unknown conjure key is present.

        """
        raw: SettingTable = {}
        user: Path = (
            Constants.default_user_settings
            if user_settings is None
            else user_settings
        )
        if user.is_file():
            raw.update(cls._file_values(source=user))
        discovered: Path | None = pyproject or cls.discover_pyproject(
            start=Path.cwd(),
        )
        if discovered is not None and discovered.is_file():
            raw.update(cls._file_values(source=discovered))
        for name, value in (overrides or {}).items():
            if value is None:
                continue
            if name not in cls._fields:
                msg: str = f"Unknown conjure setting: {name}"
                raise ValueError(msg)
            raw[name] = value
        return cls._settings(raw=raw)

    @staticmethod
    def discover_pyproject(*, start: Path) -> Path | None:
        """Return the nearest ``pyproject.toml`` at or above ``start``.

        Args:
            start: Directory or file to begin searching from.

        Returns:
            The discovered file, or ``None`` when none exists.

        """
        directory: Path = start if start.is_dir() else start.parent
        current: Path = directory.resolve()
        while True:
            candidate: Path = current / "pyproject.toml"
            if candidate.is_file():
                return candidate
            if current.parent == current:
                return None
            current = current.parent

    @classmethod
    def _file_values(cls, *, source: Path) -> SettingTable:
        """Extract validated field values from one TOML settings file.

        Args:
            source: TOML file carrying a ``tool.conjure`` table.

        Returns:
            Field-name to raw value mapping.

        Raises:
            ValueError: If an unknown conjure key is present.
            TypeError: If ``tool.conjure`` is not a table.

        """
        with source.open(mode="rb") as stream:
            root: SettingTable = cast(
                typ="SettingTable",
                val=_toml_load(stream),
            )
        tool: SettingValue = root.get("tool")
        if not isinstance(tool, dict):
            return {}
        conjure: SettingValue = tool.get("conjure")
        if conjure is None:
            return {}
        if not isinstance(conjure, dict):
            msg: str = "tool.conjure must be a table."
            raise TypeError(msg)
        values: SettingTable = {}
        for section, body in conjure.items():
            if section not in Constants.tables or not isinstance(body, dict):
                msg = f"Unknown conjure setting: {section}"
                raise ValueError(msg)
            prefix: str = "" if section == "general" else f"{section}_"
            for key, value in body.items():
                name: str = (
                    f"{prefix}{key}"
                    if f"{prefix}{key}" in cls._fields
                    else key
                )
                if name not in cls._fields:
                    msg = f"Unknown conjure setting: {section}.{key}"
                    raise ValueError(msg)
                values[name] = value
        return values

    @classmethod
    def _settings(cls, *, raw: SettingTable) -> Settings:
        """Build settings from raw values using typed getters.

        Args:
            raw: Merged settings table.

        Returns:
            Typed runtime settings.

        """
        return Settings(
            json_output=cls._boolean(
                raw=raw,
                key="json_output",
                default=False,
            ),
            registry_path=cls._path(
                raw=raw,
                key="registry_path",
                default=Constants.default_registry,
            ),
            local_working_directory=cls._optional_path(
                raw=raw,
                key="local_working_directory",
            ),
            docker_executable=cls._path(
                raw=raw,
                key="docker_executable",
                default=Path("docker"),
            ),
            docker_image=cls._optional_str(
                raw=raw,
                key="docker_image",
            ),
            docker_working_directory=cls._optional_str(
                raw=raw,
                key="docker_working_directory",
            ),
            docker_create_arguments=cls._strings(
                raw=raw,
                key="docker_create_arguments",
            ),
            docker_command=cls._strings(
                raw=raw,
                key="docker_command",
            ),
            docker_remove_on_close=cls._boolean(
                raw=raw,
                key="docker_remove_on_close",
                default=True,
            ),
            docker_stop_timeout_seconds=cls._integer(
                raw=raw,
                key="docker_stop_timeout_seconds",
                default=10,
            ),
            docker_owns_container=cls._boolean(
                raw=raw,
                key="docker_owns_container",
                default=True,
            ),
            qemu_executable=cls._optional_path(
                raw=raw,
                key="qemu_executable",
            ),
            qemu_arguments=cls._strings(
                raw=raw,
                key="qemu_arguments",
            ),
            qemu_network_device=cls._string(
                raw=raw,
                key="qemu_network_device",
                default="virtio-net-pci",
            ),
            qemu_network_id=cls._string(
                raw=raw,
                key="qemu_network_id",
                default="conjure_net",
            ),
            qemu_automatic_host_forward=cls._boolean(
                raw=raw,
                key="qemu_automatic_host_forward",
                default=True,
            ),
            qemu_startup_timeout_seconds=cls._number(
                raw=raw,
                key="qemu_startup_timeout_seconds",
                default=120.0,
            ),
            qemu_shutdown_timeout_seconds=cls._number(
                raw=raw,
                key="qemu_shutdown_timeout_seconds",
                default=30.0,
            ),
            qemu_terminate_timeout_seconds=cls._number(
                raw=raw,
                key="qemu_terminate_timeout_seconds",
                default=5.0,
            ),
            qemu_snapshot_image=cls._optional_path(
                raw=raw,
                key="qemu_snapshot_image",
            ),
            qemu_baseline_snapshot=cls._optional_str(
                raw=raw,
                key="qemu_baseline_snapshot",
            ),
            qemu_img_executable=cls._path(
                raw=raw,
                key="qemu_img_executable",
                default=Path("qemu-img"),
            ),
            qemu_process_executable=cls._path(
                raw=raw,
                key="qemu_process_executable",
                default=Path("ps"),
            ),
            qemu_image_roots=cls._paths(
                raw=raw,
                key="qemu_image_roots",
            ),
            ssh_username=cls._optional_str(
                raw=raw,
                key="ssh_username",
            ),
            ssh_identity_file=cls._optional_path(
                raw=raw,
                key="ssh_identity_file",
            ),
            ssh_host=cls._string(
                raw=raw,
                key="ssh_host",
                default="127.0.0.1",
            ),
            ssh_port=cls._optional_int(raw=raw, key="ssh_port"),
            ssh_executable=cls._path(
                raw=raw,
                key="ssh_executable",
                default=Path("ssh"),
            ),
            scp_executable=cls._path(
                raw=raw,
                key="scp_executable",
                default=Path("scp"),
            ),
            ssh_known_hosts_file=cls._optional_path(
                raw=raw,
                key="ssh_known_hosts_file",
            ),
            ssh_connect_timeout_seconds=cls._integer(
                raw=raw,
                key="ssh_connect_timeout_seconds",
                default=5,
            ),
            ssh_poll_interval_seconds=cls._number(
                raw=raw,
                key="ssh_poll_interval_seconds",
                default=0.5,
            ),
            guest_shutdown_command=cls._optional_strings(
                raw=raw,
                key="guest_shutdown_command",
            ),
            guest_ssh_port=cls._integer(
                raw=raw,
                key="guest_ssh_port",
                default=22,
            ),
        )

    @classmethod
    def _strings(cls, *, raw: SettingTable, key: str) -> tuple[str, ...]:
        """Return a string-sequence field or an empty tuple.

        Args:
            raw: Field values.
            key: Field name.

        Returns:
            Resolved string tuple.

        Raises:
            TypeError: If the value is not a string sequence.

        """
        if key not in raw:
            return ()
        value: tuple[str, ...] | None = cls._sequence(
            raw=raw,
            key=key,
        )
        if value is None:
            msg: str = f"{key} must be a string list."
            raise TypeError(msg)
        return value

    @classmethod
    def _optional_strings(
        cls,
        *,
        raw: SettingTable,
        key: str,
    ) -> tuple[str, ...] | None:
        """Return an optional string-sequence field.

        Args:
            raw: Field values.
            key: Field name.

        Returns:
            Resolved string tuple or ``None``.

        Raises:
            TypeError: If the value is not a string sequence or null.

        """
        if raw.get(key) is None:
            return None
        value: tuple[str, ...] | None = cls._sequence(
            raw=raw,
            key=key,
        )
        if value is None:
            msg: str = f"{key} must be a string list or null."
            raise TypeError(msg)
        return value

    @staticmethod
    def _boolean(*, raw: SettingTable, key: str, default: bool) -> bool:
        """Return a boolean field or its default.

        Args:
            raw: Field values.
            key: Field name.
            default: Fallback when the key is absent.

        Returns:
            Resolved boolean.

        Raises:
            TypeError: If the value is not a boolean.

        """
        value: SettingValue = raw.get(key, default)
        if not isinstance(value, bool):
            msg: str = f"{key} must be a boolean."
            raise TypeError(msg)
        return value

    @staticmethod
    def _integer(*, raw: SettingTable, key: str, default: int) -> int:
        """Return an integer field or its default.

        Args:
            raw: Field values.
            key: Field name.
            default: Fallback when the key is absent.

        Returns:
            Resolved integer.

        Raises:
            TypeError: If the value is not an integer.

        """
        value: SettingValue = raw.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool):
            msg: str = f"{key} must be an integer."
            raise TypeError(msg)
        return value

    @staticmethod
    def _optional_int(*, raw: SettingTable, key: str) -> int | None:
        """Return an optional integer field.

        Args:
            raw: Field values.
            key: Field name.

        Returns:
            Resolved integer or ``None``.

        Raises:
            TypeError: If the value is not an integer.

        """
        value: SettingValue = raw.get(key)
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            msg: str = f"{key} must be an integer or null."
            raise TypeError(msg)
        return value

    @staticmethod
    def _number(*, raw: SettingTable, key: str, default: float) -> float:
        """Return a numeric field or its default.

        Args:
            raw: Field values.
            key: Field name.
            default: Fallback when the key is absent.

        Returns:
            Resolved number.

        Raises:
            TypeError: If the value is not numeric.

        """
        value: SettingValue = raw.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            msg: str = f"{key} must be numeric."
            raise TypeError(msg)
        return float(value)

    @staticmethod
    def _string(*, raw: SettingTable, key: str, default: str) -> str:
        """Return a string field or its default.

        Args:
            raw: Field values.
            key: Field name.
            default: Fallback when the key is absent.

        Returns:
            Resolved string.

        Raises:
            TypeError: If the value is not a string.

        """
        value: SettingValue = raw.get(key, default)
        if not isinstance(value, str):
            msg: str = f"{key} must be a string."
            raise TypeError(msg)
        return value

    @staticmethod
    def _optional_str(*, raw: SettingTable, key: str) -> str | None:
        """Return an optional string field.

        Args:
            raw: Field values.
            key: Field name.

        Returns:
            Resolved string or ``None``.

        Raises:
            TypeError: If the value is not a string.

        """
        value: SettingValue = raw.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            msg: str = f"{key} must be a string or null."
            raise TypeError(msg)
        return value

    @staticmethod
    def _path(*, raw: SettingTable, key: str, default: Path) -> Path:
        """Return a path field or its default.

        Args:
            raw: Field values.
            key: Field name.
            default: Fallback when the key is absent.

        Returns:
            Resolved path.

        Raises:
            TypeError: If the value is not a string or path.

        """
        value: SettingValue = raw.get(key, default)
        if isinstance(value, Path):
            return value
        if not isinstance(value, str):
            msg: str = f"{key} must be a string or path."
            raise TypeError(msg)
        return Path(value)

    @staticmethod
    def _optional_path(*, raw: SettingTable, key: str) -> Path | None:
        """Return an optional path field.

        Args:
            raw: Field values.
            key: Field name.

        Returns:
            Resolved path or ``None``.

        Raises:
            TypeError: If the value is not a string or path.

        """
        value: SettingValue = raw.get(key)
        if value is None:
            return None
        if isinstance(value, Path):
            return value
        if not isinstance(value, str):
            msg: str = f"{key} must be a string, path, or null."
            raise TypeError(msg)
        return Path(value)

    @staticmethod
    def _sequence(
        *,
        raw: SettingTable,
        key: str,
    ) -> tuple[str, ...] | None:
        """Return a string-sequence field or ``None`` when malformed.

        Args:
            raw: Field values.
            key: Field name.

        Returns:
            Resolved string tuple, or ``None`` for a wrong type.

        """
        value: SettingValue = raw.get(key)
        if not isinstance(value, (list, tuple)):
            return None
        items: list[SettingValue] = list(value)
        if not all(isinstance(item, str) for item in items):
            return None
        return tuple(cast(typ="list[str]", val=items))

    @staticmethod
    def _paths(*, raw: SettingTable, key: str) -> tuple[Path, ...]:
        """Return a path-sequence field or an empty tuple.

        Args:
            raw: Field values.
            key: Field name.

        Returns:
            Resolved path tuple.

        Raises:
            TypeError: If the value is not a string/path sequence.

        """
        value: SettingValue = raw.get(key, ())
        if not isinstance(value, (list, tuple)):
            msg: str = f"{key} must be a string list."
            raise TypeError(msg)
        paths: list[Path] = []
        for item in value:
            if isinstance(item, Path):
                paths.append(item)
            elif isinstance(item, str):
                paths.append(Path(item))
            else:
                msg = f"{key} entries must be strings or paths."
                raise TypeError(msg)
        return tuple(paths)
