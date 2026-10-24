# Copyright (c) 2026 Project contributors.
"""Tests for guest-to-host build progress reporting."""

from __future__ import annotations

from http.client import HTTPConnection
from io import StringIO
from json import dumps
from pathlib import Path
from re import DOTALL, Match, search
from shutil import which
from subprocess import PIPE, STDOUT, CompletedProcess, run
from threading import Event, Thread
from typing import TYPE_CHECKING, Final, final
from urllib.parse import SplitResult, urlsplit
from zipfile import ZipFile

import pytest

from tools.stationer.builder import (
    DockerSteps,
    LinuxDockerBuilder,
)
from tools.stationer.conjure import (
    Credentials,
    Payload,
    RemoteSpec,
    RemoteStep,
)
from tools.stationer.progress import (
    BuildProgress,
    PipOutput,
    ProgressDisplay,
    ProgressTracker,
    StepMarker,
)
from tools.stationer.remote_server import JobHandler, JobServer, JobState
from tools.stationer.remote_workers import posix_worker

from .fake_sessions import FakeSession
from .helpers import check, expect, present
from .test_builder import linux_config, project

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

    from tools.stationer.sessions import StreamingSession


@final
class Constants:
    """Group module-level constants.

    Attributes:
        pip_download: Grouped module constant.
        template: Grouped module constant.

    """

    pip_download: Final[str] = (
        "Downloading https://files.example/packages/numpy-2.0.0-cp313-none"
        "-macosx_11_0_arm64.whl (16.0 MB)"
    )
    template: Final[Path] = (
        Path(__file__).resolve().parents[3]
        / "tools"
        / "stationer"
        / "templates"
        / "worker_windows.ps1.tmpl"
    )


def _discard(message: str) -> None:
    """Ignore a progress message.

    Args:
        message: Ignored text.

    """
    _ = message


def _job_state(*, tmp_path: Path) -> JobState:
    """Create the state of one job with a tiny payload.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        The job state.

    """
    archive: Path = tmp_path / "payload.zip"
    archive.write_bytes(data=b"payload-bytes")
    return JobState(
        spec=RemoteSpec(steps=(RemoteStep(message="m", argv=("{python}",)),)),
        credentials=Credentials.create(),
        payload=Payload.of(path=archive),
        output=tmp_path / "out",
        public_url="http://127.0.0.1:0",
        target="linux",
        done=Event(),
        say=_discard,
    )


@final
class _Running:
    """Serve one job on an ephemeral port for the length of a ``with``."""

    def __init__(self, *, state: JobState) -> None:
        """Prepare the server without starting it.

        Args:
            state: Job state to serve.

        """
        self._server: Final[JobServer] = JobServer(
            ("127.0.0.1", 0),
            JobHandler,
        )
        self._server.state = state
        self._thread: Final[Thread] = Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )

    def __enter__(self) -> str:
        """Start serving.

        Returns:
            The server's base URL.

        """
        self._thread.start()
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop serving.

        Args:
            exc_type: Ignored exception type.
            exc_value: Ignored exception.
            traceback: Ignored traceback.

        """
        _ = (exc_type, exc_value, traceback)
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _send(*, url: str, token: str | None = None, data: bytes = b"") -> int:
    """POST a body to the job server.

    Args:
        url: Full URL.
        token: Bearer token, if any.
        data: Request body.

    Returns:
        The HTTP status code.

    """
    parts: SplitResult = urlsplit(url=url)
    connection: HTTPConnection = HTTPConnection(
        host=parts.hostname or "",
        port=parts.port,
        timeout=10,
    )
    try:
        connection.request(
            method="POST",
            url=parts.path,
            body=data,
            headers={"Authorization": f"Bearer {token}"} if token else {},
        )
        return connection.getresponse().status
    finally:
        connection.close()


def _tracker(
    *,
    weights: tuple[int, ...] = (1, 3),
) -> tuple[ProgressTracker, list[BuildProgress]]:
    """Create a tracker that records what it publishes.

    Args:
        weights: Step weights.

    Returns:
        The tracker and its updates.

    """
    updates: list[BuildProgress] = []
    return (ProgressTracker(weights=weights, sink=updates.append), updates)


def test_pip_raw_bar_and_download_lines_are_decoded() -> None:
    """Pip 26 prints ``Progress N of M``, not a bare byte count."""
    expect(
        actual=PipOutput.bar(line="Progress 1024 of 4096"),
        expected=(1024, 4096),
    )
    expect(actual=PipOutput.bar(line="Progress 1024"), expected=None)
    expect(actual=PipOutput.bar(line="Collecting numpy"), expected=None)
    expect(
        actual=PipOutput.artifact(line=Constants.pip_download),
        expected="numpy",
    )
    expect(
        actual=PipOutput.artifact(line="Downloading pip-26.0.tar.gz"),
        expected="pip",
    )
    expect(actual=PipOutput.artifact(line="Collecting numpy"), expected=None)


def test_step_markers_round_trip_through_a_real_shell() -> None:
    """The printf line a container prints decodes back to its step."""
    printed: str = run(
        args=("sh", "-c", StepMarker.shell(index=4, message="upgrading pip")),
        check=True,
        stdout=PIPE,
        text=True,
    ).stdout
    expect(
        actual=StepMarker.parse(line=printed.rstrip()),
        expected=(4, "upgrading pip"),
    )
    expect(actual=StepMarker.parse(line="step 4 nope"), expected=None)
    expect(
        actual=StepMarker.event(body=dumps(obj={"step": 2, "message": "m"})),
        expected=(2, "m"),
    )
    expect(actual=StepMarker.event(body="plain text"), expected=None)
    expect(actual=StepMarker.event(body="{not json"), expected=None)
    expect(actual=StepMarker.event(body='{"step": "x"}'), expected=None)


def test_percent_advances_by_weighted_finished_steps() -> None:
    """A heavy step takes a proportionally larger slice of the bar."""
    tracker, updates = _tracker(weights=(1, 3))
    tracker.step(index=1, message="light")
    expect(actual=updates[-1].percent, expected=0.0)
    tracker.step(index=2, message="heavy")
    expect(actual=updates[-1].percent, expected=25.0)
    expect(actual=(updates[-1].step, updates[-1].total_steps), expected=(2, 2))
    tracker.finish()
    expect(actual=updates[-1].percent, expected=100.0)
    check(condition=updates[-1].complete, reason="finish completes")


def test_out_of_range_steps_are_clamped() -> None:
    """A confused guest cannot push the bar past its plan."""
    tracker, updates = _tracker(weights=(1, 1))
    tracker.step(index=99, message="late")
    tracker.step(index=-4, message="early")
    expect(actual=[update.step for update in updates], expected=[2, 1])


def test_pip_output_gives_byte_accurate_download_detail() -> None:
    """Raw pip lines yield package, bytes and a measurable percentage."""
    tracker, updates = _tracker()
    tracker.step(index=2, message="install")
    tracker.output(
        text=f"{Constants.pip_download}\nProgress 4000000 of 16000000\n",
    )
    latest: BuildProgress = updates[-1]
    expect(actual=latest.package, expected="numpy")
    expect(
        actual=(latest.downloaded_bytes, latest.total_bytes),
        expected=(4000000, 16000000),
    )
    expect(actual=latest.download_percent, expected=25.0)
    expect(actual=latest.percent, expected=25.0)
    tracker.output(text="Collecting scipy\r")
    expect(actual=updates[-1].detail, expected="Collecting scipy")


def test_step_markers_inside_output_switch_steps() -> None:
    """A container prints markers in-band; the tracker follows them."""
    tracker, updates = _tracker(weights=(1, 1, 2))
    tracker.output(
        text=(
            f"{
                StepMarker.shell(index=1, message='a')
            }\n@@district-step@@ 1 first\n@@district-step@@ "
            f"3 third\nbuilding\n"
        ),
    )
    expect(actual=updates[-1].step, expected=3)
    expect(actual=updates[-1].message, expected="third")
    expect(actual=updates[-1].detail, expected="building")


def test_summary_formats_bar_step_and_download() -> None:
    """The one-line summary shows percent, step and the active download."""
    progress: BuildProgress = BuildProgress(
        percent=50.0,
        step=2,
        total_steps=4,
        message="install",
        package="numpy",
        downloaded_bytes=8000000,
        total_bytes=16000000,
    )
    text: str = ProgressDisplay.summary(progress=progress)
    check(condition=" 50%" in text, reason=text)
    check(condition="step 2/4 install" in text, reason=text)
    check(condition="numpy 8.0 MB/16.0 MB" in text, reason=text)
    expect(actual=ProgressDisplay.size(count=512), expected="512 B")
    expect(actual=ProgressDisplay.size(count=2500000000), expected="2.5 GB")


@final
class _Terminal(StringIO):
    """A string stream that claims to be a terminal."""

    def __init__(self) -> None:
        """Create an empty stream that reports itself as interactive."""
        super().__init__()
        self._interactive: Final[bool] = True

    def isatty(self) -> bool:
        """Report whether the stream pretends to be interactive.

        Returns:
            ``True``, the stream mimics a terminal.

        """
        return self._interactive


def test_display_without_a_terminal_prints_each_step_once() -> None:
    """Logs and pipes get one line per step plus a finish, never a bar."""
    stream: StringIO = StringIO()
    display: ProgressDisplay = ProgressDisplay(stream=stream)
    tracker: ProgressTracker = ProgressTracker(
        weights=(1, 1),
        sink=display.update,
    )
    tracker.step(index=1, message="one")
    tracker.output(text="Progress 1 of 2\nProgress 2 of 2\n")
    tracker.step(index=2, message="two")
    tracker.finish()
    lines: list[str] = stream.getvalue().splitlines()
    expect(actual=len(lines), expected=3)
    expect(actual=lines[0], expected="[  0%] step 1/2 one")
    expect(actual=lines[1], expected="[ 50%] step 2/2 two")
    check(condition=lines[2].endswith("build complete"), reason=lines[2])
    check(condition="\r" not in stream.getvalue(), reason="no redraw")


def test_display_on_a_terminal_redraws_in_place() -> None:
    """The live line is erased before permanent lines are printed."""
    clock: list[float] = [0.0]
    stream: _Terminal = _Terminal()
    display: ProgressDisplay = ProgressDisplay(
        stream=stream,
        clock=lambda: clock[0],
    )
    display.update(
        progress=BuildProgress(
            percent=0.0,
            step=1,
            total_steps=2,
            message="one",
        ),
    )
    display.line(message="[worker] note")
    clock[0] = 5.0
    display.update(
        progress=BuildProgress(
            percent=50.0,
            step=2,
            total_steps=2,
            message="two",
        ),
    )
    display.update(
        progress=BuildProgress(
            percent=100.0,
            step=2,
            total_steps=2,
            message="two",
            complete=True,
        ),
    )
    text: str = stream.getvalue()
    check(condition="\r\x1b[2K[worker] note\n" in text, reason=repr(text))
    check(condition="step 2/2 two" in text, reason=repr(text))
    check(condition=text.rstrip().endswith("build complete"), reason=text)


def test_display_throttles_redraws_within_a_step() -> None:
    """Hundreds of byte updates a second do not flood the terminal."""
    stream: _Terminal = _Terminal()
    display: ProgressDisplay = ProgressDisplay(
        stream=stream,
        clock=lambda: 1.0,
    )
    for done in range(200):
        display.update(
            progress=BuildProgress(
                percent=0.0,
                step=1,
                total_steps=1,
                message="dl",
                package="numpy",
                downloaded_bytes=done,
                total_bytes=200,
            ),
        )
    expect(actual=stream.getvalue().count("step 1/1"), expected=2)


def test_server_routes_step_events_and_output_to_the_tracker(
    tmp_path: Path,
) -> None:
    """JSON steps and forwarded output are authenticated and interpreted.

    Args:
        tmp_path: Pytest temporary directory.

    """
    tracker, updates = _tracker(weights=(1, 1))
    state: JobState = _job_state(tmp_path=tmp_path)
    state.track = tracker
    with _Running(state=state) as base:
        token: str = state.credentials.token
        expect(
            actual=_send(url=f"{base}/v1/output", data=b"Progress 1 of 2"),
            expected=401,
        )
        expect(
            actual=_send(
                url=f"{base}/v1/progress",
                token=token,
                data=dumps(obj={"step": 2, "message": "build"}).encode(),
            ),
            expected=200,
        )
        expect(
            actual=_send(
                url=f"{base}/v1/output",
                token=token,
                data=f"{Constants.pip_download}\nProgress 8 of 16\n".encode(),
            ),
            expected=200,
        )
    expect(actual=updates[-1].step, expected=2)
    expect(actual=updates[-1].message, expected="build")
    expect(actual=updates[-1].downloaded_bytes, expected=8)


def test_server_without_a_tracker_still_prints_step_messages(
    tmp_path: Path,
) -> None:
    """Without a display, a JSON step degrades to its plain message.

    Args:
        tmp_path: Pytest temporary directory.

    """
    state: JobState = _job_state(tmp_path=tmp_path)
    messages: list[str] = []
    state.say = messages.append
    with _Running(state=state) as base:
        token: str = state.credentials.token
        for body in (dumps(obj={"step": 1, "message": "freeze"}), "plain"):
            _ = _send(
                url=f"{base}/v1/progress",
                token=token,
                data=body.encode(),
            )
        _ = _send(url=f"{base}/v1/output", token=token, data=b"ignored")
    expect(actual=messages, expected=["freeze", "plain"])


def test_a_real_posix_worker_streams_its_step_output(tmp_path: Path) -> None:
    """A rendered worker reports steps and pip bytes to a live server.

    Args:
        tmp_path: Pytest temporary directory.

    """
    code: str = "\n".join(
        (
            "import pathlib, sys, time",
            f"print({Constants.pip_download!r}, flush=True)",
            "print('Progress 8 of 16', flush=True)",
            "time.sleep(0.8)",
            "print('Progress 16 of 16', flush=True)",
            "print('compiling', flush=True)",
            "pathlib.Path(sys.argv[1], 'app').write_bytes(b'x')",
            "",
        ),
    )
    spec: RemoteSpec = RemoteSpec(
        steps=(
            RemoteStep(
                message="prepare",
                argv=("{python}", "-c", "print('hello')"),
            ),
            RemoteStep(
                message="install",
                argv=("{python}", "-c", code, "{dist}"),
                weight=3,
            ),
        ),
    )
    archive: Path = tmp_path / "src.zip"
    with ZipFile(file=archive, mode="w") as bundle:
        bundle.writestr(zinfo_or_arcname="readme.txt", data="x")
    state: JobState = _job_state(tmp_path=tmp_path)
    state.spec = spec
    state.payload = Payload.of(path=archive)
    tracker, updates = _tracker(weights=(1, 3))
    state.track = tracker
    with _Running(state=state) as base:
        script: Path = tmp_path / "worker.sh"
        script.write_text(
            data=posix_worker(
                base_url=base,
                credentials=state.credentials,
                payload=state.payload,
                spec=spec,
                os_name="linux",
            ),
            encoding="utf-8",
        )
        bash: str = present(value=which(cmd="bash"))
        finished: CompletedProcess[str] = run(
            args=(bash, str(object=script)),
            check=False,
            stdout=PIPE,
            stderr=STDOUT,
            text=True,
            timeout=120,
        )
    check(condition=finished.returncode == 0, reason=finished.stdout[-600:])
    check(condition=state.done.is_set(), reason="the worker finished")
    expect(actual=updates[0].message, expected="prepare")
    expect(actual=sorted({update.step for update in updates}), expected=[1, 2])
    downloads: list[BuildProgress] = [
        update for update in updates if update.package == "numpy"
    ]
    check(condition=bool(downloads), reason=str(object=updates[-3:]))
    expect(actual=downloads[0].percent, expected=25.0)
    expect(actual=downloads[-1].total_bytes, expected=16)
    check(
        condition=any(update.detail == "compiling" for update in updates),
        reason="plain output reaches the host too",
    )


def _functions() -> str:
    """Extract the streaming helpers from the Windows worker template.

    Returns:
        PowerShell source from ``Send-Progress`` to ``Send-Failure``.

    """
    text: str = Constants.template.read_text(encoding="utf-8")
    found: Match[str] | None = search(
        pattern="function Send-Progress.*?(?=function Send-Failure)",
        string=text,
        flags=DOTALL,
    )
    return present(value=found).group(0)


@pytest.mark.parametrize("exit_code", [0, 3])
def test_the_powershell_worker_streams_output_and_judges_by_exit_code(
    tmp_path: Path,
    exit_code: int,
) -> None:
    """Stderr is not an error under ``Stop``; only the exit code is.

    Args:
        tmp_path: Pytest temporary directory.
        exit_code: Exit status of the faked native command.

    """
    tracker, updates = _tracker(weights=(1, 1))
    state: JobState = _job_state(tmp_path=tmp_path)
    state.track = tracker
    with _Running(state=state) as base:
        python: str = which(cmd="python3") or which(cmd="python") or ""
        faked: str = (
            "import sys; print('Progress 5 of 10'); "
            f"print('warn', file=sys.stderr); sys.exit({exit_code})"
        )
        script: Path = tmp_path / "stream.ps1"
        script.write_text(
            encoding="utf-8",
            data="\n".join(
                (
                    "$ErrorActionPreference = 'Stop'",
                    f"$Base = '{base}'",
                    "$Headers = @{ Authorization = 'Bearer "
                    f"{state.credentials.token}' }}",
                    _functions(),
                    "Send-Step -Index 2 -Message 'install'",
                    f"Invoke-Streamed -Argv @('{python}', '-c', \"{faked}\")",
                    "",
                ),
            ),
        )
        pwsh: str = present(value=which(cmd="pwsh"))
        finished: CompletedProcess[str] = run(
            args=(pwsh, "-NoProfile", "-File", str(object=script)),
            check=False,
            stdout=PIPE,
            stderr=STDOUT,
            text=True,
            timeout=120,
        )
    expect(actual=finished.returncode == 0, expected=exit_code == 0)
    expect(actual=updates[0].step, expected=2)
    expect(actual=updates[-1].downloaded_bytes, expected=5)
    check(
        condition=any(update.detail == "warn" for update in updates),
        reason="stderr is forwarded as output",
    )


def _opener(*, session: FakeSession) -> Callable[..., StreamingSession]:
    """Create a container opener that always returns ``session``.

    Args:
        session: The fake session to hand out.

    Returns:
        The opener.

    """

    def open_session(*, config: object) -> StreamingSession:
        """Return the shared fake.

        Args:
            config: Ignored Linux configuration.

        Returns:
            The fake session.

        """
        _ = config
        return session

    return open_session


def _container_output(arguments: tuple[str, ...]) -> str | None:
    """Fabricate the container script's streamed output.

    Args:
        arguments: Command vector being faked.

    Returns:
        Marker and pip lines for the build script; nothing for others.

    """
    if arguments[:3] != ("sh", "-eu", "-c"):
        return None
    return "".join(
        f"{line}\n"
        for line in (
            "@@district-step@@ 1 x",
            "@@district-step@@ 5 installing",
            Constants.pip_download,
            "Progress 8 of 16",
            "@@district-step@@ 6 freezing",
        )
    )


def test_container_script_announces_every_phase_and_asks_for_raw_bars(
    tmp_path: Path,
) -> None:
    """The script prints a marker per planned phase, in order.

    Args:
        tmp_path: Pytest temporary directory.

    """
    script: str = LinuxDockerBuilder.worker_script(
        project=project(root=tmp_path / "p"),
        config=linux_config(),
    )
    positions: list[int] = [
        script.index(f"@@district-step@@ {index} {message}")
        for index, (message, _) in enumerate(
            iterable=DockerSteps.plan,
            start=1,
        )
    ]
    expect(actual=positions, expected=sorted(positions))
    check(condition="--progress-bar raw" in script, reason=script)
    check(condition="--upgrade pip" in script, reason=script)


def test_container_output_becomes_progress_updates(tmp_path: Path) -> None:
    """Streamed ``docker exec`` lines reach the progress sink.

    Args:
        tmp_path: Pytest temporary directory.

    """
    session: FakeSession = FakeSession(handler=_container_output)
    updates: list[BuildProgress] = []
    _ = LinuxDockerBuilder(
        open_session=_opener(session=session),
        progress=updates.append,
    ).build(
        project=project(root=tmp_path / "src"),
        config=linux_config(),
        output=tmp_path / "out",
    )
    running: list[BuildProgress] = [
        update for update in updates if not update.complete
    ]
    expect(actual=running[-1].message, expected="freezing")
    expect(
        actual=sorted({update.step for update in updates}),
        expected=[1, 5, 6],
    )
    downloads: list[BuildProgress] = [
        update for update in updates if update.package == "numpy"
    ]
    expect(actual=downloads[-1].downloaded_bytes, expected=8)
    expect(actual=updates[-1].complete, expected=True)
