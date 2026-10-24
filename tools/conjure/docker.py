"""Disposable and attachable Docker execution backend."""

from __future__ import annotations

from subprocess import (
    PIPE,
    STDOUT,
    CompletedProcess,
    Popen,
    TimeoutExpired,
    run,
)
from threading import Timer
from typing import TYPE_CHECKING, Self

from tools.conjure.errors import (
    QemuRunnerError,
    RemoteCommandError,
    TransferError,
)
from tools.conjure.models import CommandResult

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from types import TracebackType

    from tools.conjure.config import DockerConfig


class DockerRunner:
    """Run commands inside a Docker container."""

    _config: DockerConfig
    _container_id: str | None
    _owns_container: bool

    def __init__(
        self,
        *,
        config: DockerConfig,
        container_id: str | None = None,
        owns_container: bool = True,
    ) -> None:
        """Initialize a Docker runner.

        Args:
            config: Disposable container configuration.
            container_id: Existing container to attach to instead of
                creating one.
            owns_container: Whether :meth:`close` may stop and remove an
                attached container.

        """
        self._config = config
        self._container_id = container_id
        self._owns_container = owns_container

    @property
    def is_running(self) -> bool:
        """Return whether a container is associated and active.

        Returns:
            ``True`` while the container exists and is running.

        """
        return self._container_id is not None

    def start(self) -> None:
        """Create or attach the container and ensure it is running.

        Raises:
            QemuRunnerError: If Docker fails to create or start it.

        """
        if self._container_id is not None:
            self._start_existing()
            return
        created: CompletedProcess[str] = self._execute(
            arguments=(
                "create",
                *(
                    ("-w", self._config.working_directory)
                    if self._config.working_directory is not None
                    else ()
                ),
                *self._config.create_arguments,
                self._config.image,
                *self._config.command,
            ),
        )
        container_id: str = created.stdout.strip()
        if not container_id:
            msg: str = "docker create returned no container identifier."
            raise QemuRunnerError(msg)
        self._container_id = container_id
        self._execute(arguments=("start", container_id))

    def run(
        self,
        *,
        command: tuple[str, ...],
        check: bool = False,
        timeout_seconds: float | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> CommandResult:
        """Execute a command inside the container.

        Args:
            command: Container executable and its arguments.
            check: Raise for a non-zero exit status.
            timeout_seconds: Optional command timeout.
            on_output: Receives each output line as the command prints it;
                standard error is then merged into standard output so the
                two streams keep their order.

        Returns:
            Captured command result.

        Raises:
            ValueError: If command is empty.
            QemuRunnerError: If the runner has not been started.
            RemoteCommandError: If check is enabled and execution fails.

        """
        if not command:
            msg: str = "Container command must not be empty."
            raise ValueError(msg)
        arguments: tuple[str, ...] = (
            str(object=self._config.docker_executable),
            "exec",
            *(
                ("-w", self._config.working_directory)
                if self._config.working_directory is not None
                else ()
            ),
            self._require_container(),
            *command,
        )
        result: CommandResult = (
            self._stream(
                command=command,
                arguments=arguments,
                timeout_seconds=timeout_seconds,
                on_output=on_output,
            )
            if on_output is not None
            else self._capture(
                command=command,
                arguments=arguments,
                timeout_seconds=timeout_seconds,
            )
        )
        if check and result.return_code != 0:
            raise RemoteCommandError(result=result)
        return result

    def upload(
        self,
        *,
        source: Path,
        destination: str,
        recursive: bool | None = None,
    ) -> None:
        """Copy a host path into the container.

        Args:
            source: Existing host path.
            destination: Container destination.
            recursive: Unused; ``docker cp`` handles directories.

        Raises:
            FileNotFoundError: If source does not exist.
            TransferError: If the copy fails.

        """
        del recursive
        if not source.exists():
            raise FileNotFoundError(source)
        self._copy(
            source=str(object=source),
            destination=f"{self._require_container()}:{destination}",
        )

    def download(
        self,
        *,
        source: str,
        destination: Path,
        recursive: bool = False,
    ) -> None:
        """Copy a container path to the host.

        Args:
            source: Container path.
            destination: Host destination.
            recursive: Unused; ``docker cp`` handles directories.

        Raises:
            TransferError: If the copy fails.

        """
        del recursive
        self._copy(
            source=f"{self._require_container()}:{source}",
            destination=str(object=destination),
        )

    def close(self) -> None:
        """Stop and optionally remove an owned container."""
        container_id: str | None = self._container_id
        self._container_id = None
        if container_id is None or not self._owns_container:
            return
        run(
            args=(
                str(object=self._config.docker_executable),
                "stop",
                "-t",
                str(object=self._config.stop_timeout_seconds),
                container_id,
            ),
            capture_output=True,
            check=False,
            text=True,
        )
        if self._config.remove_on_close:
            run(
                args=(
                    str(object=self._config.docker_executable),
                    "rm",
                    container_id,
                ),
                capture_output=True,
                check=False,
                text=True,
            )

    def __enter__(self) -> Self:
        """Start and return this runner.

        Returns:
            This runner.

        """
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the container according to its ownership.

        Args:
            exc_type: Propagated exception type, when an error occurred.
            exc_value: Propagated exception instance, when an error occurred.
            traceback: Propagated exception traceback, when an error occurred.

        """
        del exc_type, exc_value, traceback
        self.close()

    @staticmethod
    def _capture(
        *,
        command: tuple[str, ...],
        arguments: tuple[str, ...],
        timeout_seconds: float | None,
    ) -> CommandResult:
        """Run a command and collect its output once it ends.

        Args:
            command: Container command, recorded in the result.
            arguments: Complete ``docker exec`` argument vector.
            timeout_seconds: Optional command timeout.

        Returns:
            Captured command result.

        """
        completed: CompletedProcess[str] = run(
            args=arguments,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
        return CommandResult(
            command=command,
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    @staticmethod
    def _stream(
        *,
        command: tuple[str, ...],
        arguments: tuple[str, ...],
        timeout_seconds: float | None,
        on_output: Callable[[str], None],
    ) -> CommandResult:
        """Run a command and hand its output on line by line.

        Args:
            command: Container command, recorded in the result.
            arguments: Complete ``docker exec`` argument vector.
            timeout_seconds: Optional command timeout.
            on_output: Receives each output line, terminator included.

        Returns:
            Command result holding the merged output as ``stdout``.

        Raises:
            TimeoutExpired: If the command outlives ``timeout_seconds``.

        """
        process: Popen[str] = Popen(
            args=arguments,
            stdout=PIPE,
            stderr=STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
        )
        expired: list[bool] = []

        def _expire() -> None:
            """Kill the command after its timeout."""
            expired.append(True)
            process.kill()

        watchdog: Timer | None = (
            Timer(interval=timeout_seconds, function=_expire)
            if timeout_seconds is not None
            else None
        )
        if watchdog is not None:
            watchdog.start()
        lines: list[str] = []
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    lines.append(line)
                    on_output(line)
            return_code: int = process.wait()
        finally:
            if watchdog is not None:
                watchdog.cancel()
        if expired:
            raise TimeoutExpired(cmd=arguments, timeout=timeout_seconds or 0.0)
        return CommandResult(
            command=command,
            return_code=return_code,
            stdout="".join(lines),
            stderr="",
        )

    def _start_existing(self) -> None:
        """Ensure an attached container is running.

        Raises:
            QemuRunnerError: If inspection or start fails.

        """
        container_id: str = self._require_container()
        inspected: CompletedProcess[str] = self._execute(
            arguments=(
                "inspect",
                "--format",
                "{{.State.Running}}",
                container_id,
            ),
        )
        if inspected.stdout.strip() != "true":
            self._execute(arguments=("start", container_id))

    def _require_container(self) -> str:
        """Return the associated container identifier.

        Returns:
            Container identifier.

        Raises:
            QemuRunnerError: If no container has been started or attached.

        """
        if self._container_id is None:
            msg: str = "Docker container has not been started."
            raise QemuRunnerError(msg)
        return self._container_id

    def _execute(
        self,
        *,
        arguments: tuple[str, ...],
    ) -> CompletedProcess[str]:
        """Run one Docker command and normalize failure.

        Args:
            arguments: Docker subcommand arguments.

        Returns:
            Completed process result.

        Raises:
            QemuRunnerError: If Docker exits unsuccessfully.

        """
        completed: CompletedProcess[str] = run(
            args=(str(object=self._config.docker_executable), *arguments),
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

    def _copy(self, *, source: str, destination: str) -> None:
        """Run ``docker cp`` and normalize transfer failure.

        Args:
            source: Copy source specification.
            destination: Copy destination specification.

        Raises:
            TransferError: If the copy fails.

        """
        completed: CompletedProcess[str] = run(
            args=(
                str(object=self._config.docker_executable),
                "cp",
                source,
                destination,
            ),
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            raise TransferError(
                completed.stderr.strip()
                or f"docker cp exited with {completed.returncode}.",
            )
