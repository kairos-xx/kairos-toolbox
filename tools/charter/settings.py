# Copyright (c) 2026 Project contributors.
"""Layered charter settings: defaults < ``pyproject.toml`` < CLI overrides."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tomllib import load as _toml_load
from typing import TYPE_CHECKING, Final, cast, final

from tools.charter.models import CharterConstants

if TYPE_CHECKING:
    from collections.abc import Mapping
type Scalar = str | int | float | bool | None
type SettingValue = Scalar | list[SettingValue] | dict[str, SettingValue]
type SettingTable = dict[str, SettingValue]


@dataclass(frozen=True, slots=True, kw_only=True)
class Settings:
    """Resolved charter settings shared by the Python and CLI APIs.

    Attributes:
        backend: Default backend: ``direct``, ``ngrok``, ``pinggy``,
            ``tunnl`` or ``runlocal``.
        json_output: Emit machine-readable JSON output.
        ngrok_authtoken: ngrok account token; ``NGROK_AUTHTOKEN`` or the
            existing ngrok configuration is used when unset.
        ngrok_executable: Explicit ngrok executable name or path.
        ssh_executable: OpenSSH client used by the SSH backends.
        expect_executable: ``expect`` interpreter used by pinggy.
        npx_executable: ``npx`` launcher used by runlocal.
        url_timeout_seconds: Wait for a log-discovered charter URL.
        ngrok_timeout_seconds: Wait for ngrok to establish a charter.
        stop_timeout_seconds: Wait after asking a charter to stop.
        shortener: Anonymous URL shortening endpoint.
        keep_alive: Run tunnels in their own session so they outlive the
            Python process and ``close``; end them with ``stop``.
        ttl_seconds: Stop each charter this many seconds after it starts;
            ``None`` lets it live until stopped.
        registry_path: JSON file listing keep-alive tunnels; defaults to
            ``~/.charter/tunnels.json``.

    """

    backend: str = "direct"
    json_output: bool = False
    ngrok_authtoken: str | None = None
    ngrok_executable: str | None = None
    ssh_executable: str = "ssh"
    expect_executable: str = "expect"
    npx_executable: str = "npx"
    url_timeout_seconds: float = CharterConstants.url_timeout
    ngrok_timeout_seconds: float = CharterConstants.ngrok_timeout
    stop_timeout_seconds: float = CharterConstants.stop_timeout
    shortener: str = CharterConstants.shortener
    keep_alive: bool = False
    ttl_seconds: float | None = None
    registry_path: str | None = None


@final
class SettingsLoader:
    """Load layered charter settings."""

    _fields: Final[frozenset[str]] = frozenset(
        {
            "backend",
            "json_output",
            "ngrok_authtoken",
            "ngrok_executable",
            "ssh_executable",
            "expect_executable",
            "npx_executable",
            "url_timeout_seconds",
            "ngrok_timeout_seconds",
            "stop_timeout_seconds",
            "shortener",
            "keep_alive",
            "ttl_seconds",
            "registry_path",
        },
    )

    @classmethod
    def load(
        cls,
        *,
        pyproject: Path | None = None,
        overrides: Mapping[str, SettingValue] | None = None,
    ) -> Settings:
        """Resolve settings from ``pyproject.toml`` and explicit overrides.

        Args:
            pyproject: Explicit project file; discovered from the current
                directory upward when omitted.
            overrides: Field-name overrides; ``None`` values are ignored.

        Returns:
            Fully resolved immutable settings.

        Raises:
            ValueError: If an unknown charter key is present.

        """
        raw: SettingTable = {}
        discovered: Path | None = pyproject or cls.discover_pyproject(
            start=Path.cwd(),
        )
        if discovered is not None and discovered.is_file():
            raw.update(cls._project_values(pyproject=discovered))
        for name, value in (overrides or {}).items():
            if value is None:
                continue
            if name not in cls._fields:
                msg: str = f"Unknown charter setting: {name}"
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
    def _project_values(cls, *, pyproject: Path) -> SettingTable:
        """Extract validated field values from one project file.

        Args:
            pyproject: Project file to read.

        Returns:
            Field-name to raw value mapping.

        Raises:
            ValueError: If an unknown charter key is present.
            TypeError: If ``tool.charter`` is not a table.

        """
        with pyproject.open(mode="rb") as stream:
            root: SettingTable = cast(
                typ="SettingTable",
                val=_toml_load(stream),
            )
        tool: SettingValue = root.get("tool")
        if not isinstance(tool, dict):
            return {}
        charter: SettingValue = tool.get("charter")
        if charter is None:
            return {}
        if not isinstance(charter, dict):
            msg: str = "tool.charter must be a table."
            raise TypeError(msg)
        values: SettingTable = {}
        for key, value in charter.items():
            if key not in cls._fields:
                msg = f"Unknown charter setting: {key}"
                raise ValueError(msg)
            values[key] = value
        return values

    @classmethod
    def _settings(cls, *, raw: SettingTable) -> Settings:
        """Build settings from raw values using typed getters.

        Args:
            raw: Merged settings table.

        Returns:
            Typed runtime settings.

        Raises:
            ValueError: If ``backend`` names an unknown backend.

        """
        backend: str = cls._string(
            raw=raw,
            key="backend",
            default="direct",
        )
        if backend not in CharterConstants.backends:
            msg: str = "backend must be one of " + ", ".join(
                sorted(CharterConstants.backends),
            )
            raise ValueError(msg)
        return Settings(
            backend=backend,
            json_output=cls._boolean(
                raw=raw,
                key="json_output",
                default=False,
            ),
            ngrok_authtoken=cls._optional_str(
                raw=raw,
                key="ngrok_authtoken",
            ),
            ngrok_executable=cls._optional_str(
                raw=raw,
                key="ngrok_executable",
            ),
            ssh_executable=cls._string(
                raw=raw,
                key="ssh_executable",
                default="ssh",
            ),
            expect_executable=cls._string(
                raw=raw,
                key="expect_executable",
                default="expect",
            ),
            npx_executable=cls._string(
                raw=raw,
                key="npx_executable",
                default="npx",
            ),
            url_timeout_seconds=cls._number(
                raw=raw,
                key="url_timeout_seconds",
                default=CharterConstants.url_timeout,
            ),
            ngrok_timeout_seconds=cls._number(
                raw=raw,
                key="ngrok_timeout_seconds",
                default=CharterConstants.ngrok_timeout,
            ),
            stop_timeout_seconds=cls._number(
                raw=raw,
                key="stop_timeout_seconds",
                default=CharterConstants.stop_timeout,
            ),
            shortener=cls._string(
                raw=raw,
                key="shortener",
                default=CharterConstants.shortener,
            ),
            keep_alive=cls._boolean(
                raw=raw,
                key="keep_alive",
                default=False,
            ),
            ttl_seconds=cls._optional_number(raw=raw, key="ttl_seconds"),
            registry_path=cls._optional_str(raw=raw, key="registry_path"),
        )

    @staticmethod
    def _optional_number(*, raw: SettingTable, key: str) -> float | None:
        """Return an optional positive numeric field.

        Args:
            raw: Raw field mapping.
            key: Field name.

        Returns:
            The number, or ``None`` when absent.

        Raises:
            TypeError: If the value is not a number.
            ValueError: If the number is not positive.

        """
        value: SettingValue = raw.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int | float):
            msg: str = f"{key} must be numeric or null."
            raise TypeError(msg)
        if value <= 0:
            msg = f"{key} must be positive."
            raise ValueError(msg)
        return float(value)

    @staticmethod
    def _boolean(*, raw: SettingTable, key: str, default: bool) -> bool:
        """Return a boolean field or its default.

        Args:
            raw: Field values.
            key: Field name.
            default: Fallback when the key is absent.

        Returns:
            Resolved flag.

        Raises:
            TypeError: If the value is not boolean.

        """
        value: SettingValue = raw.get(key, default)
        if not isinstance(value, bool):
            msg: str = f"{key} must be a boolean."
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
