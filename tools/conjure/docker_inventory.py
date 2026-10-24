"""Docker image and container discovery."""

from __future__ import annotations

from json import loads
from pathlib import Path
from subprocess import CompletedProcess, run
from typing import TYPE_CHECKING, Final, cast, final

from tools.conjure.errors import QemuRunnerError
from tools.conjure.inventory import DockerContainerInfo, DockerImageInfo

if TYPE_CHECKING:
    from collections.abc import Sequence
type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)
type JsonObject = dict[str, JsonValue]


@final
class Constants:
    """Group module-level constants.

    Attributes:
        image_fields: Grouped module constant.
        container_fields: Grouped module constant.
        image_format: Grouped module constant.
        container_format: Grouped module constant.

    """

    image_fields: Final[int] = 5
    container_fields: Final[int] = 5
    image_format: Final[str] = (
        "{{.ID}}\t{{.Repository}}\t{{.Tag}}\t{{.CreatedAt}}\t{{.Size}}"
    )
    container_format: Final[str] = (
        "{{.ID}}\t{{.Image}}\t{{.Names}}\t{{.State}}\t{{.Status}}"
    )


class DockerInventory:
    """Discover reusable Docker resources on the current host."""

    _docker_executable: Path

    def __init__(self, *, docker_executable: Path = Path("docker")) -> None:
        """Initialize the Docker discovery executable.

        Args:
            docker_executable: Docker CLI to invoke.

        """
        self._docker_executable = docker_executable

    def images(self) -> tuple[DockerImageInfo, ...]:
        """List local Docker images.

        Returns:
            Parsed image inventory.

        Raises:
            QemuRunnerError: If Docker listing fails.

        """
        completed: CompletedProcess[str] = self._execute(
            arguments=("images", "--format", Constants.image_format),
        )
        return tuple(
            self._image(fields=line.split(sep="\t"))
            for line in completed.stdout.splitlines()
            if line.strip()
        )

    def containers(
        self,
        *,
        running: bool = False,
    ) -> tuple[DockerContainerInfo, ...]:
        """List Docker containers.

        Args:
            running: Restrict to running containers.

        Returns:
            Parsed container inventory.

        Raises:
            QemuRunnerError: If Docker listing fails.

        """
        completed: CompletedProcess[str] = self._execute(
            arguments=(
                "ps",
                *(() if running else ("--all",)),
                "--format",
                Constants.container_format,
            ),
        )
        return tuple(
            self._container(fields=line.split(sep="\t"))
            for line in completed.stdout.splitlines()
            if line.strip()
        )

    def inspect(self, *, resource: str) -> JsonObject:
        """Inspect one container or image.

        Args:
            resource: Container or image identifier.

        Returns:
            First decoded JSON object from ``docker inspect``.

        Raises:
            QemuRunnerError: If inspection fails or output is malformed.

        """
        completed: CompletedProcess[str] = self._execute(
            arguments=("inspect", resource),
        )
        decoded: JsonValue = cast(
            typ="JsonValue",
            val=loads(s=completed.stdout),
        )
        if (
            not isinstance(decoded, list)
            or not decoded
            or (not isinstance(decoded[0], dict))
        ):
            msg: str = "docker inspect returned unexpected output."
            raise QemuRunnerError(msg)
        return decoded[0]

    @staticmethod
    def _image(*, fields: list[str]) -> DockerImageInfo:
        """Parse one ``docker images`` row.

        Args:
            fields: Tab-separated columns.

        Returns:
            Parsed image record.

        Raises:
            QemuRunnerError: If the row is malformed.

        """
        if len(fields) != Constants.image_fields:
            msg: str = "docker images returned unexpected output."
            raise QemuRunnerError(msg)
        return DockerImageInfo(
            image_id=fields[0],
            repository=fields[1],
            tag=fields[2],
            created_at=fields[3],
            size=fields[4],
        )

    @staticmethod
    def _container(*, fields: list[str]) -> DockerContainerInfo:
        """Parse one ``docker ps`` row.

        Args:
            fields: Tab-separated columns.

        Returns:
            Parsed container record.

        Raises:
            QemuRunnerError: If the row is malformed.

        """
        if len(fields) != Constants.container_fields:
            msg: str = "docker ps returned unexpected output."
            raise QemuRunnerError(msg)
        return DockerContainerInfo(
            container_id=fields[0],
            image=fields[1],
            name=fields[2],
            state=fields[3],
            status=fields[4],
        )

    def _execute(self, *, arguments: Sequence[str]) -> CompletedProcess[str]:
        """Run one Docker command and normalize failure.

        Args:
            arguments: Docker subcommand arguments.

        Returns:
            Completed process result.

        Raises:
            QemuRunnerError: If Docker exits unsuccessfully.

        """
        completed: CompletedProcess[str] = run(
            args=(str(object=self._docker_executable), *arguments),
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            raise QemuRunnerError(
                completed.stderr.strip()
                or f"docker exited with {completed.returncode}.",
            )
        return completed
