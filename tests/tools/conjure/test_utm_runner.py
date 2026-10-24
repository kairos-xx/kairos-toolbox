"""Tests for the UTM guest-agent runner."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from typing import TYPE_CHECKING, Final, final
from unittest.mock import patch

import pytest

from tools.conjure.errors import (
    QemuRunnerError,
    RemoteCommandError,
    SshTimeoutError,
    TransferError,
)
from tools.conjure.utm_runner import UtmRunner

from .helpers import check, expect

if TYPE_CHECKING:
    from collections.abc import Iterable

    from tools.conjure.models import CommandResult


@final
class Dispatch:
    """Answer ``utmctl`` invocations by verb.

    ``status``, ``start``, ``stop``, ``ip-address`` and ``exec`` run in
    text mode; ``file push`` and ``file pull`` run in bytes mode and are
    keyed by their direction.

    Attributes:
        calls: Every argv ``run`` was asked to execute.
        inputs: Standard input of each call that had any (pushed files).
        answers: ``(returncode, stdout, stderr)`` queued per verb; the
            first entry is consumed when more than one is queued.

    """

    def __init__(
        self,
        *,
        answers: dict[str, list[tuple[int, str, str]]] | None = None,
    ) -> None:
        """Initialize the dispatcher.

        Args:
            answers: Canned results keyed by ``utmctl`` verb (or ``push``
                and ``pull`` for file transfers); an absent verb succeeds
                with empty output.

        """
        self.calls: list[tuple[str, ...]] = []
        self.inputs: list[bytes] = []
        self.answers: Final[dict[str, list[tuple[int, str, str]]]] = (
            answers or {}
        )

    def __call__(
        self,
        *,
        args: Iterable[str],
        text: bool = False,
        **ignored: object,
    ) -> CompletedProcess[str] | CompletedProcess[bytes]:
        """Pretend to run ``utmctl``.

        Args:
            args: Command vector; ``args[1]`` is the verb.
            text: Whether the result carries text or bytes output.
            **ignored: ``input``, ``capture_output``, ``check`` and
                ``timeout``, which the canned answers make irrelevant.

        Returns:
            The canned result for the verb, text- or bytes-typed.

        """
        pushed: object = ignored.get("input")
        if isinstance(pushed, bytes):
            self.inputs.append(pushed)
        argv: tuple[str, ...] = tuple(args)
        self.calls.append(argv)
        verb: str = argv[1] if argv[1] != "file" else argv[2]
        queued: list[tuple[int, str, str]] = self.answers.setdefault(
            verb,
            [(0, "", "")],
        )
        code: int
        out: str
        err: str
        code, out, err = queued.pop(0) if len(queued) > 1 else queued[0]
        if text:
            return CompletedProcess(
                args=argv,
                returncode=code,
                stdout=out,
                stderr=err,
            )
        return CompletedProcess(
            args=argv,
            returncode=code,
            stdout=out.encode(),
            stderr=err.encode(),
        )


@final
class Probe:
    """Fake UDP socket reporting a fixed host address.

    Attributes:
        address: Dotted quad ``getsockname`` returns.
        connected: Addresses ``connect`` was asked to reach.

    """

    def __init__(self, *, address: str) -> None:
        """Initialize the probe.

        Args:
            address: Host address to report.

        """
        self.address: Final[str] = address
        self.connected: list[tuple[str, int]] = []

    def connect(self, address: tuple[str, int]) -> None:
        """Record the connection target without doing I/O.

        Args:
            address: Guest address and port.

        """
        self.connected.append(address)

    def getsockname(self) -> tuple[str, int]:
        """Return the canned host address.

        Returns:
            The configured address and a port.

        """
        return (self.address, 9)

    def __enter__(self) -> Probe:
        """Return this socket for ``with`` blocks.

        Returns:
            This probe.

        """
        return self

    def __exit__(self, *ignored: object) -> None:
        """Do nothing on ``with`` exit.

        Args:
            *ignored: Exception details.

        """
        _ = ignored


def _runner(*, stop_on_close: bool = False) -> UtmRunner:
    """Create a runner for VM ``win`` with tiny timeouts.

    Args:
        stop_on_close: Whether ``close`` powers off a guest it started.

    Returns:
        The runner.

    """
    return UtmRunner(
        name="win",
        utmctl=Path("/opt/utmctl"),
        start_timeout_seconds=0.05,
        poll_interval_seconds=0.001,
        stop_on_close=stop_on_close,
    )


def _ready(dispatch: Dispatch) -> None:
    """Make ``dispatch`` report a started guest with an IPv4 address.

    Args:
        dispatch: Dispatcher to seed.

    """
    dispatch.answers.setdefault("status", [(0, "started", "")])
    dispatch.answers.setdefault("ip-address", [(0, "10.0.2.15\n", "")])


def test_init_and_is_running() -> None:
    """A fresh runner is attached to no guest."""
    runner: UtmRunner = _runner()
    check(condition=not runner.is_running, reason="runner must start detached")


def test_ip_address_parses_the_first_dotted_quad() -> None:
    """Skip link-local and IPv6 lines; ``None`` when the agent fails."""
    dispatch: Dispatch = Dispatch(
        answers={"ip-address": [(0, "fe80::1\n10.0.2.15\nfd00::2\n", "")]},
    )
    with patch(target="tools.conjure.utm_runner.run", new=dispatch):
        expect(actual=_runner().ip_address(), expected="10.0.2.15")
    failing: Dispatch = Dispatch(answers={"ip-address": [(1, "", "down")]})
    with patch(target="tools.conjure.utm_runner.run", new=failing):
        expect(actual=_runner().ip_address(), expected=None)
    odd: Dispatch = Dispatch(answers={"ip-address": [(0, "none\n", "")]})
    with patch(target="tools.conjure.utm_runner.run", new=odd):
        expect(actual=_runner().ip_address(), expected=None)


def test_host_address_reports_the_reachable_host_ip() -> None:
    """A UDP connect to the guest reveals the host's shared address."""
    dispatch: Dispatch = Dispatch()
    _ready(dispatch=dispatch)
    probe: Probe = Probe(address="192.168.64.1")

    def fake_socket(**_ignored: object) -> Probe:
        """Return the canned probe.

        Args:
            **_ignored: Socket family and type arguments.

        Returns:
            The probe.

        """
        return probe

    with (
        patch(target="tools.conjure.utm_runner.run", new=dispatch),
        patch(target="tools.conjure.utm_runner.socket", new=fake_socket),
    ):
        expect(actual=_runner().host_address(), expected="192.168.64.1")
    expect(actual=probe.connected, expected=[("10.0.2.15", 9)])


def test_host_address_fails_without_a_guest_address() -> None:
    """A guest with no IPv4 address makes the host address unknowable."""
    dispatch: Dispatch = Dispatch(answers={"ip-address": [(1, "", "")]})
    with (
        patch(target="tools.conjure.utm_runner.run", new=dispatch),
        pytest.raises(expected_exception=QemuRunnerError, match="no IPv4"),
    ):
        _ = _runner().host_address()


def test_start_waits_for_the_agent_and_marks_the_runner_running() -> None:
    """An already-started guest only needs its agent to answer."""
    dispatch: Dispatch = Dispatch()
    _ready(dispatch=dispatch)
    runner: UtmRunner = _runner()
    with patch(target="tools.conjure.utm_runner.run", new=dispatch):
        runner.start()
    check(condition=runner.is_running, reason="agent answered")
    verbs: list[str] = [call[1] for call in dispatch.calls]
    check(condition="start" not in verbs, reason="guest already up")


def test_start_boots_a_stopped_guest() -> None:
    """A stopped guest is booted through ``utmctl start`` first."""
    dispatch: Dispatch = Dispatch(
        answers={"status": [(0, "stopped", "")], "start": [(0, "", "")]},
    )
    _ready(dispatch=dispatch)
    runner: UtmRunner = _runner()
    with patch(target="tools.conjure.utm_runner.run", new=dispatch):
        runner.start()
    verbs: list[str] = [call[1] for call in dispatch.calls]
    expect(actual=verbs[:2], expected=["status", "start"])


def test_start_raises_when_utmctl_cannot_boot() -> None:
    """A failed ``utmctl start`` surfaces its stderr."""
    dispatch: Dispatch = Dispatch(
        answers={
            "status": [(0, "stopped", "")],
            "start": [(1, "", "no image")],
        },
    )
    runner: UtmRunner = _runner()
    with (
        patch(target="tools.conjure.utm_runner.run", new=dispatch),
        pytest.raises(expected_exception=QemuRunnerError, match="no image"),
    ):
        runner.start()


def test_start_times_out_without_an_address() -> None:
    """A guest that never reports an address fails the start."""
    dispatch: Dispatch = Dispatch(
        answers={"status": [(0, "started", "")], "ip-address": [(1, "", "")]},
    )
    runner: UtmRunner = _runner()
    with (
        patch(target="tools.conjure.utm_runner.run", new=dispatch),
        patch(target="tools.conjure.utm_runner.sleep"),
        pytest.raises(expected_exception=SshTimeoutError, match="no address"),
    ):
        runner.start()


def test_run_execs_commands_in_the_guest() -> None:
    """``exec`` reports the exit status and honours ``check``."""
    dispatch: Dispatch = Dispatch()
    _ready(dispatch=dispatch)
    dispatch.answers["exec"] = [(7, "", "boom")]
    runner: UtmRunner = _runner()
    with patch(target="tools.conjure.utm_runner.run", new=dispatch):
        runner.start()
        result: CommandResult = runner.run(command=("echo", "hi"))
        expect(actual=result.return_code, expected=7)
        with pytest.raises(
            expected_exception=RemoteCommandError,
            match="exited with 7",
        ):
            _ = runner.run(command=("echo", "hi"), check=True)
    check(
        condition=dispatch.calls[-1][:2] == ("/opt/utmctl", "exec"),
        reason="commands go through exec",
    )


def test_run_rejects_empty_commands_and_detached_runners() -> None:
    """An empty command is a ``ValueError``; no start is ``RuntimeError``."""
    runner: UtmRunner = _runner()
    with pytest.raises(expected_exception=RuntimeError, match="not been"):
        _ = runner.run(command=("echo",))
    dispatch: Dispatch = Dispatch()
    _ready(dispatch=dispatch)
    with patch(target="tools.conjure.utm_runner.run", new=dispatch):
        runner.start()
        with pytest.raises(expected_exception=ValueError, match="empty"):
            _ = runner.run(command=())


def test_upload_streams_one_file_to_the_guest(tmp_path: Path) -> None:
    """``file push`` receives the file's bytes on stdin.

    Args:
        tmp_path: Pytest temporary directory.

    """
    source: Path = tmp_path / "in.bin"
    _ = source.write_bytes(data=b"data")
    dispatch: Dispatch = Dispatch()
    runner: UtmRunner = _runner()
    with patch(target="tools.conjure.utm_runner.run", new=dispatch):
        runner.upload(source=source, destination="/guest/in.bin")
    expect(actual=dispatch.calls[0][1:3], expected=("file", "push"))


def test_upload_rejects_missing_files_directories_and_failures(
    tmp_path: Path,
) -> None:
    """Bad sources and failed pushes raise the documented errors.

    Args:
        tmp_path: Pytest temporary directory.

    """
    runner: UtmRunner = _runner()
    with pytest.raises(expected_exception=FileNotFoundError):
        runner.upload(source=tmp_path / "absent", destination="/guest/x")
    with pytest.raises(expected_exception=ValueError, match="one file"):
        runner.upload(source=tmp_path, destination="/guest/x")
    failing: Dispatch = Dispatch(answers={"push": [(1, "", "refused")]})
    with (
        patch(target="tools.conjure.utm_runner.run", new=failing),
        pytest.raises(expected_exception=TransferError, match="refused"),
    ):
        runner.upload(source=Path(__file__), destination="/guest/x")


def test_download_writes_the_guest_file(tmp_path: Path) -> None:
    """``file pull`` writes its stdout into the destination file.

    Args:
        tmp_path: Pytest temporary directory.

    """
    dispatch: Dispatch = Dispatch(answers={"pull": [(0, "guest bytes", "")]})
    destination: Path = tmp_path / "deep" / "out.bin"
    runner: UtmRunner = _runner()
    with patch(target="tools.conjure.utm_runner.run", new=dispatch):
        runner.download(source="/guest/out.bin", destination=destination)
    expect(actual=destination.read_bytes(), expected=b"guest bytes")
    with pytest.raises(expected_exception=ValueError, match="one file"):
        runner.download(
            source="/guest/d",
            destination=destination,
            recursive=True,
        )


@final
class Constants:
    """Group module-level constants.

    Attributes:
        guest_error: Grouped module constant.

    """

    guest_error: Final[str] = "Error from event: failed to open file"


def test_run_waits_for_the_guest_status_and_returns_its_output() -> None:
    """``exec`` returns at once, so ``run`` polls for the status file."""
    dispatch: Dispatch = Dispatch(
        answers={
            "pull": [
                (0, "", Constants.guest_error),
                (0, "3\r\n", ""),
                (0, "guest output", ""),
            ],
        },
    )
    _ready(dispatch=dispatch)
    pauses: list[float] = []
    runner: UtmRunner = _runner()
    with (
        patch(target="tools.conjure.utm_runner.run", new=dispatch),
        patch(target="tools.conjure.utm_runner.sleep", new=pauses.append),
    ):
        runner.start()
        pauses.clear()
        result: CommandResult = runner.run(command=("echo", "it's"))
    expect(actual=result.return_code, expected=3)
    expect(actual=result.stdout, expected="guest output")
    expect(actual=pauses, expected=[UtmRunner.STATUS_POLL_SECONDS])
    script: str = dispatch.inputs[0].decode()
    check(condition="& 'echo' 'it''s'" in script, reason=script)
    check(condition=".status" in script and ".log" in script, reason=script)
    execs: list[tuple[str, ...]] = [
        call for call in dispatch.calls if call[1] == "exec"
    ]
    expect(
        actual=execs[0][-len(UtmRunner.SHELL) - 2 : -1],
        expected=("--cmd", *UtmRunner.SHELL),
    )
    check(condition=execs[0][-1].endswith(".ps1"), reason=str(object=execs[0]))
    check(
        condition="Remove-Item" in execs[-1][-1],
        reason="the guest files are cleaned up",
    )


def test_run_raises_when_the_guest_never_reports_a_status() -> None:
    """A command that outlives its timeout is a ``TimeoutExpired``."""
    dispatch: Dispatch = Dispatch(
        answers={"pull": [(0, "", Constants.guest_error)]},
    )
    _ready(dispatch=dispatch)
    runner: UtmRunner = _runner()
    with patch(target="tools.conjure.utm_runner.run", new=dispatch):
        runner.start()
        with pytest.raises(expected_exception=TimeoutExpired):
            _ = runner.run(command=("sleep", "99"), timeout_seconds=0)


def test_run_tolerates_a_command_that_printed_nothing() -> None:
    """No log file is not an error; the output is just empty."""
    dispatch: Dispatch = Dispatch(
        answers={"pull": [(0, "0", ""), (0, "", Constants.guest_error)]},
    )
    _ready(dispatch=dispatch)
    runner: UtmRunner = _runner()
    with patch(target="tools.conjure.utm_runner.run", new=dispatch):
        runner.start()
        result: CommandResult = runner.run(command=("quiet",), check=True)
    expect(actual=(result.return_code, result.stdout), expected=(0, ""))


def test_a_failure_reported_on_stderr_is_a_transfer_error(
    tmp_path: Path,
) -> None:
    """``utmctl`` exits 0 for a missing file and says so on stderr.

    Args:
        tmp_path: Pytest temporary directory.

    """
    dispatch: Dispatch = Dispatch(
        answers={
            "pull": [(0, "", Constants.guest_error)],
            "push": [(0, "", Constants.guest_error)],
        },
    )
    runner: UtmRunner = _runner()
    source: Path = tmp_path / "in.txt"
    source.write_text(data="x", encoding="utf-8")
    with patch(target="tools.conjure.utm_runner.run", new=dispatch):
        with pytest.raises(
            expected_exception=TransferError,
            match="failed to open file",
        ):
            runner.download(
                source="C:/missing.bin",
                destination=tmp_path / "out.bin",
            )
        with pytest.raises(
            expected_exception=TransferError,
            match="failed to open file",
        ):
            runner.upload(source=source, destination="C:/x.txt")
    check(
        condition=not (tmp_path / "out.bin").exists(),
        reason="a failed read must not leave an empty file behind",
    )


def test_download_failure_is_a_transfer_error(tmp_path: Path) -> None:
    """A failed ``file pull`` raises ``TransferError``.

    Args:
        tmp_path: Pytest temporary directory.

    """
    dispatch: Dispatch = Dispatch(answers={"pull": [(1, "", "gone")]})
    runner: UtmRunner = _runner()
    with (
        patch(target="tools.conjure.utm_runner.run", new=dispatch),
        pytest.raises(expected_exception=TransferError, match="gone"),
    ):
        runner.download(source="/guest/out.bin", destination=tmp_path / "o")


def test_close_stops_only_a_guest_the_runner_started() -> None:
    """``stop --request`` runs only for a started, owned guest."""
    dispatch: Dispatch = Dispatch(
        answers={"status": [(0, "stopped", "")], "start": [(0, "", "")]},
    )
    _ready(dispatch=dispatch)
    owned: UtmRunner = _runner(stop_on_close=True)
    with patch(target="tools.conjure.utm_runner.run", new=dispatch):
        owned.start()
        owned.close()
    verbs: list[str] = [call[1] for call in dispatch.calls]
    check(condition="stop" in verbs, reason=str(object=verbs))
    check(condition=not owned.is_running, reason="closed")
    attached: Dispatch = Dispatch()
    _ready(dispatch=attached)
    other: UtmRunner = _runner(stop_on_close=True)
    with patch(target="tools.conjure.utm_runner.run", new=attached):
        other.start()
        other.close()
    verbs = [call[1] for call in attached.calls]
    check(condition="stop" not in verbs, reason=str(object=verbs))


def test_context_manager_starts_and_closes() -> None:
    """``with`` starts the runner and closes it afterwards."""
    dispatch: Dispatch = Dispatch()
    _ready(dispatch=dispatch)
    with (
        patch(target="tools.conjure.utm_runner.run", new=dispatch),
        _runner() as runner,
    ):
        check(condition=runner.is_running, reason="entered")
    check(condition=not runner.is_running, reason="exited")


def test_commands_carry_the_guest_name() -> None:
    """Every ``utmctl`` call targets the configured VM name."""
    dispatch: Dispatch = Dispatch()
    _ready(dispatch=dispatch)
    runner: UtmRunner = _runner()
    with patch(target="tools.conjure.utm_runner.run", new=dispatch):
        _ = runner.ip_address()
    expect(actual=dispatch.calls[0][2], expected="win")
