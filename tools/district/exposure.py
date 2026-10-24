# Copyright (c) 2026 Project contributors.
"""Optional public exposure of the web dashboard through a managed charter.

The charter mechanics live in the standalone ``tools.charter`` package,
which is not bundled into installed builds. This module therefore keeps
``tools.charter`` out of the import graph and reaches it lazily through
:func:`charter_api`; exposure attempts on packaged installs fail with a
clear :class:`ExposureError` instead of an import error.

Exposure choices are layered: built-in defaults, then the
``[tool.district.charter]`` table of the nearest ``pyproject.toml``,
then explicit overrides (the CLI applies its flags this way). Backend
mechanics such as executable paths and timeouts remain configurable
through ``[tool.charter]``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from importlib import import_module
from pathlib import Path
from tomllib import load as _toml_load
from typing import TYPE_CHECKING, Final, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import ModuleType

    from _typeshed import DataclassInstance
type Scalar = str | int | float | bool | None
type SettingValue = Scalar | list[SettingValue] | dict[str, SettingValue]
type SettingTable = dict[str, SettingValue]


class ExposureError(RuntimeError):
    """Public exposure of the web dashboard failed."""


class ExposureConstants:
    """Group public-exposure constants.

    Attributes:
        backends: Accepted ``--expose`` backend names; mirrors
            ``tools.charter.CharterConstants.backends`` without importing
            the package.
        keys: Accepted ``[tool.district.charter]`` keys.

    """

    backends: Final[frozenset[str]] = frozenset(
        {"direct", "ngrok", "pinggy", "tunnl", "runlocal"},
    )
    keys: Final[frozenset[str]] = frozenset(
        {"backend", "ngrok_authtoken", "alias", "shorten"},
    )


class CharterHandle(Protocol):
    """The subset of ``tools.charter.Charter`` the dashboard needs.

    Attributes:
        public_url: Backend-assigned public HTTPS URL.

    """

    public_url: str

    def close(self) -> None:
        """Stop the charter backend process."""
        ...


class CharterSettings(Protocol):
    """The ``tools.charter.Settings`` attributes this module reads.

    Attributes:
        ngrok_authtoken: Configured ngrok account token, if any.

    """

    ngrok_authtoken: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class PublicEndpoint:
    """One running public endpoint for the dashboard.

    Attributes:
        url: Public URL to reach the dashboard, possibly shortened.
        charter_url: Backend-assigned URL before shortening.
        charter: Running charter; close it when the dashboard stops.

    """

    url: str
    charter_url: str
    charter: CharterHandle


@dataclass(frozen=True, slots=True, kw_only=True)
class Exposure:
    """Resolved public-exposure choices for ``district --web``.

    Attributes:
        backend: Charter backend: ``direct``, ``ngrok``, ``pinggy``,
            ``tunnl`` or ``runlocal``. ``direct`` disables exposure.
        ngrok_authtoken: ngrok account token; overrides
            ``[tool.charter]`` and the environment when set.
        alias: Requested short-link alias.
        shorten: Shorten the public URL through the configured shortener.

    """

    backend: str = "direct"
    ngrok_authtoken: str | None = None
    alias: str | None = None
    shorten: bool = False


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


def load_exposure(
    *,
    pyproject: Path | None = None,
    overrides: Mapping[str, SettingValue] | None = None,
) -> Exposure:
    """Resolve exposure choices from ``pyproject.toml`` and overrides.

    Reads the ``[tool.district.charter]`` table of the nearest
    ``pyproject.toml`` (searched upward from the current directory), then
    applies non-``None`` overrides on top.

    Args:
        pyproject: Explicit project file; discovered when omitted.
        overrides: Field-name overrides; ``None`` values are ignored.

    Returns:
        Resolved immutable exposure choices.

    Raises:
        ValueError: If an unknown key is present or ``backend`` names an
            unknown backend.
        TypeError: If ``[tool.district.charter]`` is not a table or a
            value has the wrong type.

    """
    raw: SettingTable = {}
    discovered: Path | None = pyproject or discover_pyproject(
        start=Path.cwd(),
    )
    if discovered is not None and discovered.is_file():
        raw.update(_project_values(pyproject=discovered))
    for name, value in (overrides or {}).items():
        if value is None:
            continue
        if name not in ExposureConstants.keys:
            msg: str = f"Unknown exposure setting: {name}"
            raise ValueError(msg)
        raw[name] = value
    return _exposure(raw=raw)


def _project_values(*, pyproject: Path) -> SettingTable:
    """Extract validated keys from ``[tool.district.charter]``.

    Args:
        pyproject: Project file to read.

    Returns:
        Field-name to raw value mapping.

    Raises:
        ValueError: If an unknown key is present.
        TypeError: If the table is not a table.

    """
    with pyproject.open(mode="rb") as stream:
        root: SettingTable = cast(typ="SettingTable", val=_toml_load(stream))
    tool: SettingValue = root.get("tool")
    if not isinstance(tool, dict):
        return {}
    section: SettingValue = cast(
        typ="SettingTable",
        val=tool,
    ).get("district")
    if not isinstance(section, dict):
        return {}
    charter: SettingValue = cast(
        typ="SettingTable",
        val=section,
    ).get("charter")
    if charter is None:
        return {}
    if not isinstance(charter, dict):
        msg: str = "tool.district.charter must be a table."
        raise TypeError(msg)
    values: SettingTable = {}
    for key, value in charter.items():
        if key not in ExposureConstants.keys:
            msg = f"Unknown exposure setting: {key}"
            raise ValueError(msg)
        values[key] = value
    return values


def _exposure(*, raw: SettingTable) -> Exposure:
    """Build the resolved exposure choices from raw values.

    Args:
        raw: Merged settings table.

    Returns:
        Typed exposure choices.

    Raises:
        ValueError: If ``backend`` names an unknown backend.
        TypeError: If a value has the wrong type.

    """
    backend: str = _string(raw=raw, key="backend", default="direct")
    if backend not in ExposureConstants.backends:
        msg: str = "backend must be one of " + ", ".join(
            sorted(ExposureConstants.backends),
        )
        raise ValueError(msg)
    return Exposure(
        backend=backend,
        ngrok_authtoken=_optional_str(raw=raw, key="ngrok_authtoken"),
        alias=_optional_str(raw=raw, key="alias"),
        shorten=_boolean(raw=raw, key="shorten", default=False),
    )


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


def charter_api() -> ModuleType:
    """Import ``tools.charter`` lazily for charter startup.

    Returns:
        The ``tools.charter`` module.

    Raises:
        ExposureError: If ``tools.charter`` is unavailable, for example on
            an installed ``district`` build.

    """
    try:
        return import_module(name="tools.charter")
    except ImportError as error:
        msg: str = (
            "Public dashboard exposure requires the tools.charter package; "
            "run `district --web` from a source checkout that provides it."
        )
        raise ExposureError(msg) from error


def _charter_settings(
    *,
    api: ModuleType,
    exposure: Exposure,
) -> DataclassInstance:
    """Load ``tools.charter`` settings merged with the exposure choices.

    Backend mechanics (executables, timeouts, shortener) come from
    ``[tool.charter]``; the backend and ngrok token come from ``exposure``.

    Args:
        api: The lazily imported ``tools.charter`` module.
        exposure: Resolved exposure choices.

    Returns:
        Merged ``tools.charter.Settings`` value.

    """
    loaded: CharterSettings = cast(
        typ="CharterSettings",
        val=api.SettingsLoader.load(),
    )
    return replace(
        cast(typ="DataclassInstance", val=loaded),
        backend=exposure.backend,
        ngrok_authtoken=exposure.ngrok_authtoken or loaded.ngrok_authtoken,
    )


def open_exposure(
    *,
    port: int,
    exposure: Exposure | None = None,
) -> PublicEndpoint | None:
    """Open a public endpoint for one local dashboard port.

    Args:
        port: Local port the dashboard listens on.
        exposure: Resolved choices; loaded from ``pyproject.toml`` when
            omitted.

    Returns:
        The running public endpoint, or ``None`` for ``direct``.

    Raises:
        ExposureError: If the backend fails to start or shortening fails.
        ValueError: If settings resolve to an invalid configuration.

    """
    resolved: Exposure = exposure or load_exposure()
    if resolved.backend == "direct":
        return None
    api: ModuleType = charter_api()
    settings: DataclassInstance = _charter_settings(
        api=api,
        exposure=resolved,
    )
    charter_errors: tuple[type[BaseException], ...] = (
        cast(typ="type[BaseException]", val=api.CharterError),
    )
    try:
        charter: CharterHandle = cast(
            typ="CharterHandle",
            val=api.open_charter(port=port, settings=settings),
        )
        url: str = charter.public_url
        if resolved.shorten:
            url = cast(
                typ="str",
                val=api.shorten_url(
                    url=url,
                    alias=resolved.alias,
                    settings=settings,
                ),
            )
    except charter_errors as error:
        raise ExposureError(str(object=error)) from error
    return PublicEndpoint(
        url=url,
        charter_url=charter.public_url,
        charter=charter,
    )
