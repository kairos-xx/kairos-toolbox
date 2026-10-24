"""Typed inventory records for Docker and QEMU resources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True, kw_only=True)
class DockerImageInfo:
    """Describe one local Docker image.

    Attributes:
        image_id: Content identifier reported by Docker.
        repository: Image repository name.
        tag: Image tag.
        created_at: Creation timestamp as reported by Docker.
        size: Human-readable image size.

    """

    image_id: str
    repository: str
    tag: str
    created_at: str
    size: str

    @property
    def reference(self) -> str:
        """Return the ``repository:tag`` pull reference.

        Returns:
            Display reference for the image.

        """
        return f"{self.repository}:{self.tag}"


@dataclass(frozen=True, slots=True, kw_only=True)
class DockerContainerInfo:
    """Describe one Docker container.

    Attributes:
        container_id: Short container identifier.
        image: Image reference the container was created from.
        name: Container name.
        state: Docker-reported lifecycle state.
        status: Human-readable status text.

    """

    container_id: str
    image: str
    name: str
    state: str
    status: str

    @property
    def is_running(self) -> bool:
        """Return whether the container is currently running.

        Returns:
            ``True`` when the container state is ``running``.

        """
        return self.state == "running"


@dataclass(frozen=True, slots=True, kw_only=True)
class QemuImageInfo:
    """Describe one QEMU disk image.

    Attributes:
        path: Disk image location.
        format: Image format reported by ``qemu-img``.
        virtual_size_bytes: Guest-visible capacity.
        actual_size_bytes: Host-side allocated size.

    """

    path: Path
    format: str | None
    virtual_size_bytes: int | None
    actual_size_bytes: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class QemuProcessInfo:
    """Describe one running QEMU system process.

    Attributes:
        process_id: Operating-system process identifier.
        executable: QEMU executable path.
        arguments: Command-line arguments after the executable.

    """

    process_id: int
    executable: Path
    arguments: tuple[str, ...]
