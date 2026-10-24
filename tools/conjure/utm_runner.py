"""Run commands in a UTM guest through ``utmctl`` and the guest agent."""

from __future__ import annotations

from pathlib import Path
from secrets import token_hex
from socket import AF_INET, SOCK_DGRAM, socket
from subprocess import CompletedProcess, TimeoutExpired, run
from time import monotonic, sleep
from typing import TYPE_CHECKING, Final, Self

from tools.conjure.errors import (
    QemuRunnerError,
    RemoteCommandError,
    SshTimeoutError,
    TransferError,
)
from tools.conjure.models import CommandResult

if TYPE_CHECKING:
    from types import TracebackType


class UtmRunner:
    """Drive one UTM virtual machine without an SSH server in the guest.

    UTM's guest agent can start a program (``utmctl exec``) and copy files
    (``utmctl file push``/``pull``). ``exec`` only *starts* the program: it
    returns at once, whatever the program later does, and captures nothing.
    :meth:`run` therefore wraps each command in a small PowerShell script
    that records the command's output and exit status in files, and waits
    for the status file, so a command finishes before :meth:`run` returns
    and its status and output are real. Guests must be Windows.

    Attributes:
        DEFAULT_UTMCTL: Where the App Store and DMG builds install ``utmctl``.
        IPV4_DOTS: Number of dots in a dotted-quad IPv4 address.
        WORK_DIRECTORY: Guest folder for a command's script, log and status.
        SHELL: Guest program that runs the wrapper script.
        STATUS_POLL_SECONDS: Wait between looks for a command's status file.

    """

    DEFAULT_UTMCTL: Final[Path] = Path(
        "/Applications/UTM.app/Contents/MacOS/utmctl",
    )
    IPV4_DOTS: Final[int] = 3
    WORK_DIRECTORY: Final[str] = "C:\\Windows\\Temp"
    SHELL: Final[tuple[str, ...]] = (
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
    )
    STATUS_POLL_SECONDS: Final[float] = 1.0
    _name: str
    _utmctl: Path
    _start_timeout: float
    _poll_interval: float
    _stop_on_close: bool
    _started_here: bool
    _is_running: bool

    def __init__(
        self,
        *,
        name: str,
        utmctl: Path | None = None,
        start_timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 1.0,
        stop_on_close: bool = False,
    ) -> None:
        """Initialize the runner.

        Args:
            name: VM name or UUID as UTM lists it.
            utmctl: ``utmctl`` executable; the standard install by default.
            start_timeout_seconds: Longest wait for a started guest to
                report an IPv4 address.
            poll_interval_seconds: Pause between readiness polls.
            stop_on_close: Power the guest off on :meth:`close`, but only
                when this runner started it.

        """
        self._name = name
        self._utmctl = utmctl if utmctl is not None else self.DEFAULT_UTMCTL
        self._start_timeout = start_timeout_seconds
        self._poll_interval = poll_interval_seconds
        self._stop_on_close = stop_on_close
        self._started_here = False
        self._is_running = False

    @property
    def is_running(self) -> bool:
        """Return whether the runner is attached to a ready guest.

        Returns:
            ``True`` after :meth:`start` until :meth:`close`.

        """
        return self._is_running

    def ip_address(self) -> str | None:
        """Ask the guest agent for the guest's IPv4 address.

        Returns:
            The first dotted-quad address, or ``None`` while unavailable.

        """
        completed: CompletedProcess[str] = self._control(verb="ip-address")
        if completed.returncode != 0:
            return None
        return next(
            (
                line.strip()
                for line in completed.stdout.splitlines()
                if line.strip().count(".") == self.IPV4_DOTS
                and ":" not in line
            ),
            None,
        )

    def host_address(self) -> str:
        """Find the host's address on the network the guest shares.

        Returns:
            The local IPv4 address the host uses to reach the guest.

        Raises:
            QemuRunnerError: If the guest has no address yet.

        """
        guest: str | None = self.ip_address()
        if guest is None:
            msg: str = f"UTM guest {self._name!r} has no IPv4 address."
            raise QemuRunnerError(msg)
        with socket(family=AF_INET, type=SOCK_DGRAM) as probe:
            probe.connect((guest, 9))
            return str(object=probe.getsockname()[0])

    def start(self) -> None:
        """Start the guest if needed and wait until its agent answers.

        Raises:
            QemuRunnerError: If UTM cannot start the guest.
            SshTimeoutError: If the guest reports no address in time.

        """
        status: CompletedProcess[str] = self._control(verb="status")
        if status.stdout.strip() != "started":
            started: CompletedProcess[str] = self._control(verb="start")
            if started.returncode != 0:
                raise QemuRunnerError(
                    started.stderr.strip() or "utmctl start failed.",
                )
            self._started_here = True
        deadline: float = monotonic() + self._start_timeout
        while monotonic() < deadline:
            if self.ip_address() is not None:
                self._is_running = True
                return
            sleep(self._poll_interval)
        msg: str = (
            f"UTM guest {self._name!r} reported no address within "
            f"{self._start_timeout:.1f} seconds."
        )
        raise SshTimeoutError(msg)

    def run(
        self,
        *,
        command: tuple[str, ...],
        check: bool = False,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        """Run a program in the guest and wait for it to finish.

        Args:
            command: Program and arguments.
            check: Whether to raise on a non-zero exit status.
            timeout_seconds: Longest to wait for the program to finish.

        Returns:
            The program's real exit status and its combined output (standard
            error is merged into ``stdout``).

        Raises:
            ValueError: If the command is empty.
            RuntimeError: If the runner is not started.
            RemoteCommandError: If ``check`` is set and it fails.
            TimeoutExpired: If the program outlives ``timeout_seconds``.

        """
        if not self._is_running:
            msg: str = "UTM runner has not been started."
            raise RuntimeError(msg)
        if not command:
            msg = "UTM command must not be empty."
            raise ValueError(msg)
        base: str = f"{self.WORK_DIRECTORY}\\conjure-{token_hex(nbytes=6)}"
        self._push(
            destination=f"{base}.ps1",
            data=self._wrapper(command=command, base=base).encode(
                encoding="utf-8",
            ),
        )
        started: CompletedProcess[str] = self._control(
            "exec",
            "--cmd",
            *self.SHELL,
            f"{base}.ps1",
        )
        if started.returncode != 0:
            result: CommandResult = CommandResult(
                command=command,
                return_code=started.returncode,
                stdout=started.stdout,
                stderr=started.stderr,
            )
        else:
            status: int = self._await_status(
                command=command,
                base=base,
                timeout_seconds=timeout_seconds,
            )
            result = CommandResult(
                command=command,
                return_code=status,
                stdout=self._read_log(base=base),
                stderr="",
            )
            self._discard(base=base)
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
        """Copy one host file into the guest.

        Args:
            source: Existing host file.
            destination: Destination path in the guest.
            recursive: Unsupported; directories are rejected.

        Raises:
            FileNotFoundError: If ``source`` does not exist.
            ValueError: If ``source`` is a directory.
            TransferError: If ``utmctl`` fails.

        """
        del recursive
        if not source.exists():
            raise FileNotFoundError(source)
        if source.is_dir():
            msg: str = "UTM transfers one file at a time; zip directories."
            raise ValueError(msg)
        self._push(destination=destination, data=source.read_bytes())

    def download(
        self,
        *,
        source: str,
        destination: Path,
        recursive: bool = False,
    ) -> None:
        """Copy one guest file to the host.

        Args:
            source: Guest file path.
            destination: Host destination file.
            recursive: Unsupported.

        Raises:
            ValueError: If ``recursive`` is requested.
            TransferError: If ``utmctl`` fails.

        """
        if recursive:
            msg: str = "UTM transfers one file at a time; zip directories."
            raise ValueError(msg)
        data: bytes = self._pull(source=source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_bytes(data=data)

    def close(self) -> None:
        """Detach; power off only a guest this runner started, if asked."""
        if self._is_running and self._stop_on_close and self._started_here:
            _ = self._control("stop", "--request")
        self._is_running = False
        self._started_here = False

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
        """Close this runner.

        Args:
            exc_type: Propagated exception type, when an error occurred.
            exc_value: Propagated exception instance, when an error occurred.
            traceback: Propagated exception traceback, when an error occurred.

        """
        del exc_type, exc_value, traceback
        self.close()

    @classmethod
    def _wrapper(cls, *, command: tuple[str, ...], base: str) -> str:
        """Compose the guest script that runs a command and records its end.

        Args:
            command: Program and arguments.
            base: Guest path prefix of the script, log and status files.

        Returns:
            PowerShell source whose exit status lands in ``<base>.status``.

        """
        arguments: str = " ".join(cls._quote(text=part) for part in command)
        return (
            "$ErrorActionPreference = 'Continue'\n"
            f"$log = {cls._quote(text=base + '.log')}\n"
            f"$status = {cls._quote(text=base + '.status')}\n"
            "try {\n"
            f'    & {arguments} 2>&1 | ForEach-Object {{ "$_" }} | '
            "Out-File -LiteralPath $log -Encoding utf8\n"
            "    $code = $LASTEXITCODE\n"
            "    if ($null -eq $code) { $code = 0 }\n"
            "} catch {\n"
            "    $_ | Out-File -LiteralPath $log -Append -Encoding utf8\n"
            "    $code = 127\n"
            "}\n"
            "Set-Content -LiteralPath $status -Value $code -Encoding ascii\n"
        )

    @staticmethod
    def _quote(*, text: str) -> str:
        """Quote text as a PowerShell single-quoted literal.

        Args:
            text: Text to quote.

        Returns:
            A literal that evaluates to ``text``.

        """
        return "'" + text.replace("'", "''") + "'"

    def _await_status(
        self,
        *,
        command: tuple[str, ...],
        base: str,
        timeout_seconds: float | None,
    ) -> int:
        """Wait until the guest script has written its exit status.

        Args:
            command: The command, for the timeout report.
            base: Guest path prefix of the status file.
            timeout_seconds: Longest to wait; ``None`` waits indefinitely.

        Returns:
            The command's exit status.

        Raises:
            TimeoutExpired: If the status does not appear in time.

        """
        deadline: float | None = (
            None if timeout_seconds is None else monotonic() + timeout_seconds
        )
        while True:
            try:
                text: str = (
                    self._pull(source=f"{base}.status")
                    .decode(errors="replace")
                    .strip()
                )
            except TransferError:
                text = ""
            if text.lstrip("-").isdigit():
                return int(text)
            if deadline is not None and monotonic() >= deadline:
                raise TimeoutExpired(cmd=command, timeout=timeout_seconds or 0)
            sleep(self.STATUS_POLL_SECONDS)

    def _read_log(self, *, base: str) -> str:
        """Read a finished command's output, if it produced any.

        Args:
            base: Guest path prefix of the log file.

        Returns:
            The output text, empty when the command printed nothing.

        """
        try:
            return self._pull(source=f"{base}.log").decode(
                encoding="utf-8-sig",
                errors="replace",
            )
        except TransferError:
            return ""

    def _discard(self, *, base: str) -> None:
        """Remove a finished command's files from the guest, best effort.

        Args:
            base: Guest path prefix of the script, log and status files.

        """
        names: str = ",".join(
            self._quote(text=base + suffix)
            for suffix in (".ps1", ".log", ".status")
        )
        _ = self._control(
            "exec",
            "--cmd",
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"Remove-Item -LiteralPath {names} -Force "
            "-ErrorAction SilentlyContinue",
        )

    def _push(self, *, destination: str, data: bytes) -> None:
        """Write bytes to a guest file with ``utmctl file push``.

        Args:
            destination: Guest file path.
            data: File content.

        Raises:
            TransferError: If ``utmctl`` fails; it reports some failures on
                standard error while still exiting with status zero.

        """
        completed: CompletedProcess[bytes] = run(
            args=(
                str(object=self._utmctl),
                "file",
                "push",
                self._name,
                destination,
            ),
            input=data,
            capture_output=True,
            check=False,
        )
        error: str = completed.stderr.decode(errors="replace").strip()
        if completed.returncode != 0 or error:
            raise TransferError(
                error
                or f"utmctl file push exited with {completed.returncode}.",
            )

    def _pull(self, *, source: str) -> bytes:
        """Read one guest file with ``utmctl file pull``.

        Args:
            source: Guest file path.

        Returns:
            The file's bytes.

        Raises:
            TransferError: If ``utmctl`` fails; a file that does not exist
                is reported on standard error with exit status zero.

        """
        completed: CompletedProcess[bytes] = run(
            args=(
                str(object=self._utmctl),
                "file",
                "pull",
                self._name,
                source,
            ),
            capture_output=True,
            check=False,
        )
        error: str = completed.stderr.decode(errors="replace").strip()
        if completed.returncode != 0 or error:
            raise TransferError(
                error
                or f"utmctl file pull exited with {completed.returncode}.",
            )
        return completed.stdout

    def _control(
        self,
        verb: str,
        *arguments: str,
        timeout: float | None = None,
    ) -> CompletedProcess[str]:
        """Run one ``utmctl`` subcommand for this guest.

        Args:
            verb: ``status``, ``start``, ``stop``, ``ip-address`` or ``exec``.
            *arguments: Further ``utmctl`` arguments after the guest name.
            timeout: Optional limit in seconds.

        Returns:
            The completed process, without raising on failure.

        """
        return run(
            args=(str(object=self._utmctl), verb, self._name, *arguments),
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
