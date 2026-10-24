"""Public API for host- and guest-agnostic execution backends."""

from tools.conjure.attached import AttachedVm
from tools.conjure.config import (
    DockerConfig,
    GuestConfig,
    LocalConfig,
    QemuConfig,
    SshConfig,
)
from tools.conjure.docker import DockerRunner
from tools.conjure.docker_inventory import DockerInventory
from tools.conjure.errors import (
    QemuRunnerError,
    QemuStartError,
    RemoteCommandError,
    SshTimeoutError,
    TransferError,
)
from tools.conjure.inventory import (
    DockerContainerInfo,
    DockerImageInfo,
    QemuImageInfo,
    QemuProcessInfo,
)
from tools.conjure.local import LocalRunner
from tools.conjure.models import CommandResult
from tools.conjure.qemu_inventory import QemuInventory
from tools.conjure.registry import VmDefinition, VmRegistry
from tools.conjure.settings import Settings, SettingsLoader
from tools.conjure.utm import UtmQemuExtractor, UtmVm
from tools.conjure.utm_runner import UtmRunner
from tools.conjure.vm import QemuVm

__all__: tuple[str, ...] = (
    "AttachedVm",
    "CommandResult",
    "DockerConfig",
    "DockerContainerInfo",
    "DockerImageInfo",
    "DockerInventory",
    "DockerRunner",
    "GuestConfig",
    "LocalConfig",
    "LocalRunner",
    "QemuConfig",
    "QemuImageInfo",
    "QemuInventory",
    "QemuProcessInfo",
    "QemuRunnerError",
    "QemuStartError",
    "QemuVm",
    "RemoteCommandError",
    "Settings",
    "SettingsLoader",
    "SshConfig",
    "SshTimeoutError",
    "TransferError",
    "UtmQemuExtractor",
    "UtmRunner",
    "UtmVm",
    "VmDefinition",
    "VmRegistry",
)
