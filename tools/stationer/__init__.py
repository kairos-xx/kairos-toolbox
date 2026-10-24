"""Cross-platform release builder and publishing control plane."""

from __future__ import annotations

from importlib import import_module
from types import FunctionType
from typing import TYPE_CHECKING, Final, cast, final

if TYPE_CHECKING:
    from types import ModuleType

    from tools.stationer.build_report import BuildReport, BuildSource
    from tools.stationer.builder import (
        BuildConfiguration,
        BuildError,
        BuildResult,
        Cli,
        LinuxConfig,
        LinuxDockerBuilder,
        MacOSBuilder,
        MacOSConfig,
        ManifestLoader,
        PosixBuild,
        PosixGuestConfig,
        PosixQemuBuilder,
        ProjectConfig,
        ReleaseBuilder,
        WindowsConfig,
        WindowsQemuBuilder,
    )
    from tools.stationer.config import Config, load_config
    from tools.stationer.engine import ReleaseEngine
    from tools.stationer.models import Bump, Project, ReleasePlan
    from tools.stationer.publisher import ReleasePublisher
    from tools.stationer.pypi_builder import PypiBuilder
    from tools.stationer.pyz_builder import PyzBuilder, PyzConfig
    from tools.stationer.remote_builder import RemoteBuilder, RemoteSettings
type _PublicAttribute = (
    type[
        BuildConfiguration
        | BuildError
        | BuildReport
        | BuildResult
        | BuildSource
        | Bump
        | Cli
        | Config
        | LinuxConfig
        | LinuxDockerBuilder
        | MacOSBuilder
        | MacOSConfig
        | ManifestLoader
        | PosixBuild
        | PosixGuestConfig
        | PosixQemuBuilder
        | Project
        | ProjectConfig
        | PypiBuilder
        | PyzBuilder
        | PyzConfig
        | ReleaseBuilder
        | ReleaseEngine
        | ReleasePlan
        | ReleasePublisher
        | RemoteBuilder
        | RemoteSettings
        | WindowsConfig
        | WindowsQemuBuilder
    ]
    | FunctionType
)
__all__: tuple[str, ...] = (
    "BuildConfiguration",
    "BuildError",
    "BuildReport",
    "BuildResult",
    "BuildSource",
    "Bump",
    "Cli",
    "Config",
    "LinuxConfig",
    "LinuxDockerBuilder",
    "MacOSBuilder",
    "MacOSConfig",
    "ManifestLoader",
    "PosixBuild",
    "PosixGuestConfig",
    "PosixQemuBuilder",
    "Project",
    "ProjectConfig",
    "PypiBuilder",
    "PyzBuilder",
    "PyzConfig",
    "ReleaseBuilder",
    "ReleaseEngine",
    "ReleasePlan",
    "ReleasePublisher",
    "RemoteBuilder",
    "RemoteSettings",
    "WindowsConfig",
    "WindowsQemuBuilder",
    "load_config",
)


@final
class Constants:
    """Group module-level constants.

    Attributes:
        sources: Grouped module constant.

    """

    sources: Final[dict[str, str]] = {
        "BuildConfiguration": "tools.stationer.builder",
        "BuildError": "tools.stationer.builder",
        "BuildReport": "tools.stationer.build_report",
        "BuildResult": "tools.stationer.builder",
        "BuildSource": "tools.stationer.build_report",
        "Bump": "tools.stationer.models",
        "Cli": "tools.stationer.builder",
        "Config": "tools.stationer.config",
        "LinuxConfig": "tools.stationer.builder",
        "LinuxDockerBuilder": "tools.stationer.builder",
        "MacOSBuilder": "tools.stationer.builder",
        "MacOSConfig": "tools.stationer.builder",
        "ManifestLoader": "tools.stationer.builder",
        "PosixBuild": "tools.stationer.builder",
        "PosixGuestConfig": "tools.stationer.builder",
        "PosixQemuBuilder": "tools.stationer.builder",
        "Project": "tools.stationer.models",
        "ProjectConfig": "tools.stationer.builder",
        "PypiBuilder": "tools.stationer.pypi_builder",
        "PyzBuilder": "tools.stationer.pyz_builder",
        "PyzConfig": "tools.stationer.pyz_builder",
        "ReleaseBuilder": "tools.stationer.builder",
        "ReleaseEngine": "tools.stationer.engine",
        "ReleasePlan": "tools.stationer.models",
        "ReleasePublisher": "tools.stationer.publisher",
        "RemoteBuilder": "tools.stationer.remote_builder",
        "RemoteSettings": "tools.stationer.remote_builder",
        "WindowsConfig": "tools.stationer.builder",
        "WindowsQemuBuilder": "tools.stationer.builder",
        "load_config": "tools.stationer.config",
    }


def __getattr__(name: str) -> _PublicAttribute:
    """Resolve one public name from its owner module on first access.

    Args:
        name: Public API name listed in ``__all__``.

    Returns:
        The attribute from its owner submodule.

    Raises:
        AttributeError: When the name is not part of the public API.

    """
    source: str | None = Constants.sources.get(name)
    if source is None:
        msg: str = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    module: ModuleType = import_module(name=source)
    return cast(typ="_PublicAttribute", val=getattr(module, name))
