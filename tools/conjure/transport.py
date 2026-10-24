"""OpenSSH transport for Windows guests."""

from __future__ import annotations

from subprocess import CompletedProcess, list2cmdline, run
from time import monotonic, sleep
from typing import TYPE_CHECKING

from tools.conjure.errors import (
    RemoteCommandError,
    SshTimeoutError,
    TransferError,
)
from tools.conjure.models import CommandResult

if TYPE_CHECKING:
    from pathlib import Path

    from tools.conjure.config import SshConfig


class SshTransport:
    """Execute commands and transfer files through OpenSSH."""

    _config: SshConfig

    def __init__(self, *, config: SshConfig) -> None:
        """Initialize the transport.

        Args:
            config: Immutable SSH configuration.

        """
        self._config = config

    def wait_until_ready(self, *, timeout_seconds: float) -> None:
        """Wait until a non-interactive SSH command succeeds.

        Args:
            timeout_seconds: Overall readiness deadline.

        Raises:
            SshTimeoutError: If SSH never becomes ready.

        """
        deadline: float = monotonic() + timeout_seconds
        while monotonic() < deadline:
            completed: CompletedProcess[str] = run(
                args=(
                    *self._ssh_prefix(),
                    "cmd.exe",
                    "/d",
                    "/s",
                    "/c",
                    "exit 0",
                ),
                capture_output=True,
                check=False,
                text=True,
                timeout=self._config.connect_timeout_seconds + 2,
            )
            if completed.returncode == 0:
                return
            sleep(self._config.poll_interval_seconds)
        msg: str = (
            f"Guest SSH did not become ready within "
            f"{timeout_seconds:.1f} seconds."
        )
        raise SshTimeoutError(
            msg,
        )

    def execute(
        self,
        *,
        command: tuple[str, ...],
        check: bool = False,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        """Execute an argument vector in the guest.

        Args:
            command: Remote executable and its arguments.
            check: Raise for a non-zero guest exit code.
            timeout_seconds: Optional command timeout.

        Returns:
            Captured command result.

        Raises:
            ValueError: If command is empty.
            RemoteCommandError: If check is enabled and execution
                fails.

        """
        if not command:
            msg: str = "Remote command must not be empty."
            raise ValueError(msg)
        completed: CompletedProcess[str] = run(
            args=(*self._ssh_prefix(), "--", list2cmdline(seq=command)),
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
        result: CommandResult = CommandResult(
            command=command,
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        if check and result.return_code != 0:
            raise RemoteCommandError(result=result)
        return result

    def upload(
        self,
        *,
        source: Path,
        destination: str,
        recursive: bool = False,
    ) -> None:
        """Upload a host path to the guest.

        Args:
            source: Existing host path.
            destination: Guest SCP destination.
            recursive: Transfer a directory recursively.

        Raises:
            FileNotFoundError: If source does not exist.
            TransferError: If SCP fails.

        """
        if not source.exists():
            raise FileNotFoundError(source)
        self._transfer(
            arguments=(
                *(("-r",) if recursive else ()),
                str(object=source),
                f"{self._target()}:{destination}",
            ),
        )

    def download(
        self,
        *,
        source: str,
        destination: Path,
        recursive: bool = False,
    ) -> None:
        """Download a guest path to the host.

        Args:
            source: Guest SCP source.
            destination: Host destination.
            recursive: Transfer a directory recursively.

        Raises:
            TransferError: If SCP fails.

        """
        self._transfer(
            arguments=(
                *(("-r",) if recursive else ()),
                f"{self._target()}:{source}",
                str(object=destination),
            ),
        )

    def _resolved_port(self) -> int:
        """Return the mandatory resolved SSH port.

        Returns:
            Configured SSH port.

        Raises:
            ValueError: when the port has not been resolved.

        """
        if self._config.port is None:
            msg: str = "SSH port has not been resolved."
            raise ValueError(msg)
        return self._config.port

    def _ssh_prefix(self) -> tuple[str, ...]:
        """Return deterministic non-interactive SSH arguments.

        Returns:
            Non-interactive SSH argument prefix.

        """
        return (
            str(object=self._config.ssh_executable),
            "-p",
            str(object=self._resolved_port()),
            "-i",
            str(object=self._config.identity_file),
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={self._config.connect_timeout_seconds}",
            "-o",
            "StrictHostKeyChecking=accept-new",
            *self._known_hosts_arguments(),
            self._target(),
        )

    def _scp_prefix(self) -> tuple[str, ...]:
        """Return deterministic non-interactive SCP arguments.

        Returns:
            Non-interactive SCP argument prefix.

        """
        return (
            str(object=self._config.scp_executable),
            "-P",
            str(object=self._resolved_port()),
            "-i",
            str(object=self._config.identity_file),
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={self._config.connect_timeout_seconds}",
            "-o",
            "StrictHostKeyChecking=accept-new",
            *self._known_hosts_arguments(),
        )

    def _known_hosts_arguments(self) -> tuple[str, ...]:
        """Return optional dedicated known-hosts arguments.

        Returns:
            Known-hosts override arguments, or none.

        """
        return (
            ()
            if self._config.known_hosts_file is None
            else (
                "-o",
                f"UserKnownHostsFile={self._config.known_hosts_file}",
            )
        )

    def _target(self) -> str:
        """Return the SSH target.

        Returns:
            ``user@host`` SSH target.

        """
        return f"{self._config.username}@{self._config.host}"

    def _transfer(self, *, arguments: tuple[str, ...]) -> None:
        """Run SCP and normalize transfer failure.

        Args:
            arguments: SCP arguments appended after the prefix.

        Raises:
            TransferError: when SCP exits unsuccessfully.

        """
        completed: CompletedProcess[str] = run(
            args=(*self._scp_prefix(), *arguments),
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            raise TransferError(
                completed.stderr.strip()
                or f"SCP exited with {completed.returncode}.",
            )
