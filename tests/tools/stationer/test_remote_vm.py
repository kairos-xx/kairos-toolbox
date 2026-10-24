# Copyright (c) 2026 Project contributors.
"""Tests for running the remote worker inside a VM session."""

from __future__ import annotations

from pathlib import Path
from re import Match, search
from subprocess import CompletedProcess
from typing import TYPE_CHECKING, Final, final
from unittest.mock import patch

import pytest

from tools.conjure import (
    CommandResult,
    QemuRunnerError,
    Settings,
    UtmRunner,
)
from tools.stationer import remote_builder
from tools.stationer.builder import BuildError, BuildResult
from tools.stationer.remote_builder import (
    RemoteBuilder,
    RemoteSettings,
)

from .helpers import check, expect, present
from .test_remote_builder import (
    FixedSteps,
    manifest,
    project,
    run_worker_when_announced,
)

if TYPE_CHECKING:
    from threading import Thread

    from tools.stationer.sessions import JobSession


def _posix_command(*, command: str) -> str:
    """Rewrite the Windows bootstrap as the equivalent POSIX one.

    The job server answers every worker script under the same secret, so
    swapping the script name runs the real worker on this host.

    Args:
        command: The bootstrap command the VM received.

    Returns:
        The POSIX bootstrap command.

    """
    found: Match[str] | None = search(
        pattern=r"http://\S+install\.ps1",
        string=command,
    )
    url: str = (
        present(value=found)
        .group(0)
        .replace(
            "install.ps1",
            "install-linux.sh",
        )
    )
    return f"curl -fsSL {url} | bash"


@final
class FakeVm:
    """Stand in for a VM session; ``run`` can launch the real worker."""

    def __init__(
        self,
        *,
        run_worker: bool = False,
        fail_start: bool = False,
        failure: str | None = None,
        launch_status: int = 0,
    ) -> None:
        """Create the fake.

        Args:
            run_worker: Whether ``run`` starts the announced worker.
            fail_start: Whether ``start`` raises like a VM that never boots.
            failure: What ``run`` raises once it is called.
            launch_status: Exit status of the worker launch command, like a
                guest whose ``curl`` could not reach this host.

        """
        self.events: Final[list[str]] = []
        self.commands: Final[list[tuple[str, ...]]] = []
        self.outcome: Final[dict[str, int]] = {}
        self.workers: Final[list[Thread]] = []
        self.run_worker: Final[bool] = run_worker
        self.fail_start: Final[bool] = fail_start
        self.reported: Final[str | None] = failure
        self.launch_status: Final[int] = launch_status

    def start(self) -> None:
        """Boot the fake VM.

        Raises:
            QemuRunnerError: When configured to fail.

        """
        self.events.append("start")
        if self.fail_start:
            msg: str = "the guest never answered SSH"
            raise QemuRunnerError(msg)

    def run(
        self,
        *,
        command: tuple[str, ...],
        check: bool = False,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        """Record the command and optionally run its worker.

        Args:
            command: Command vector.
            check: Ignored.
            timeout_seconds: Ignored.

        Returns:
            A successful result.

        Raises:
            QemuRunnerError: When a failure was scripted.

        """
        _ = (check, timeout_seconds)
        self.events.append("run")
        self.commands.append(command)
        if self.run_worker and "curl.exe" in command[-1]:
            self.workers.append(
                run_worker_when_announced(
                    messages=[_posix_command(command=command[-1])],
                    outcome=self.outcome,
                ),
            )
        if self.reported is not None:
            raise QemuRunnerError(self.reported)
        return CommandResult(
            command=command,
            return_code=self.launch_status if "curl.exe" in command[-1] else 0,
            stdout="",
            stderr="",
        )

    def upload(
        self,
        *,
        source: Path,
        destination: str,
        recursive: bool | None = None,
    ) -> None:
        """Record an upload.

        Args:
            source: Ignored host path.
            destination: Ignored destination inside the session.
            recursive: Ignored.

        """
        _ = (source, destination, recursive)
        self.events.append("upload")

    def download(
        self,
        *,
        source: str,
        destination: Path,
        recursive: bool = False,
    ) -> None:
        """Record a download.

        Args:
            source: Ignored path inside the session.
            destination: Ignored host destination.
            recursive: Ignored.

        """
        _ = (source, destination, recursive)
        self.events.append("download")

    def close(self) -> None:
        """Record the shutdown."""
        self.events.append("close")


@final
class Factory:
    """Hand a prepared fake VM session to the builder."""

    def __init__(self, *, session: FakeVm) -> None:
        """Create the factory.

        Args:
            session: The session every call returns.

        """
        self.session: Final[FakeVm] = session
        self.calls: Final[list[tuple[str, bool, Settings]]] = []

    def __call__(
        self,
        *,
        vm: str,
        attach: bool,
        settings: Settings,
    ) -> JobSession:
        """Record the request and return the prepared session.

        Args:
            vm: Registry name, or empty for the configured VM.
            attach: Whether to attach to a running guest.
            settings: Resolved ``conjure`` settings.

        Returns:
            The prepared fake.

        """
        self.calls.append((vm, attach, settings))
        return self.session


def _settings(*, vm: str | None, vm_attach: bool = False) -> RemoteSettings:
    """Create remote settings for a quick local job.

    Args:
        vm: Registered VM name; ``""`` uses the configured VM and ``None``
            prints the command instead.
        vm_attach: Attach to a running guest instead of booting one.

    Returns:
        The settings.

    """
    return RemoteSettings(
        port=0,
        worker_host="127.0.0.1",
        timeout_seconds=30,
        vm=vm,
        vm_attach=vm_attach,
    )


def _build(
    *,
    tmp_path: Path,
    factory: Factory,
    settings: RemoteSettings,
    messages: list[str],
    job_settings: Settings | None = None,
) -> BuildResult:
    """Run a remote build with the fake VM opener.

    Args:
        tmp_path: Pytest temporary directory.
        factory: Supplies the fake VM session.
        settings: Remote settings under test.
        messages: Receives the builder's messages.
        job_settings: ``conjure`` settings handed to the opener.

    Returns:
        The build result.

    """
    return RemoteBuilder(
        say=messages.append,
        open_vm=factory,
        job_settings=Settings() if job_settings is None else job_settings,
    ).build(
        project=project(root=tmp_path / "proj"),
        settings=settings,
        target="windows",
        output=tmp_path / "build",
    )


def test_the_worker_runs_inside_the_vm_and_the_vm_is_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bootstrap goes into the VM instead of being printed.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(
        target=remote_builder,
        name="remote_steps",
        value=FixedSteps(
            body="import pathlib, sys;(pathlib.Path(sys.argv[1])/'demo')"
            ".write_bytes(b'from-the-vm')",
        ),
    )
    vm: FakeVm = FakeVm(run_worker=True)
    factory: Factory = Factory(session=vm)
    messages: list[str] = []
    result: BuildResult = _build(
        tmp_path=tmp_path,
        factory=factory,
        settings=_settings(vm="builder-vm"),
        messages=messages,
    )
    for worker in vm.workers:
        worker.join(timeout=20)
    expect(actual=vm.events, expected=["start", "run", "run", "close"])
    expect(actual=vm.outcome.get("status"), expected=0)
    expect(actual=result.executable.read_bytes(), expected=b"from-the-vm")
    expect(
        actual=vm.commands[0][:4],
        expected=("cmd.exe", "/d", "/s", "/c"),
    )
    check(
        condition=vm.commands[0][-1].startswith(
            "(curl.exe -fsSL -o C:\\Windows\\Temp\\stationer-",
        )
        and "-ExecutionPolicy Bypass -File" in vm.commands[0][-1]
        and ":8765" not in vm.commands[0][-1],
        reason=vm.commands[0][-1],
    )
    log: str = vm.commands[0][-1].split(sep="> ")[1].split(sep=" ")[0]
    script: str = log.removesuffix(".log") + ".ps1"
    expect(
        actual=vm.commands[1],
        expected=("cmd.exe", "/d", "/c", "del", "/f", "/q", script, log),
    )
    check(
        condition=not any("Run this" in message for message in messages),
        reason="nothing is left for a human to paste",
    )
    check(
        condition=any(message.startswith("[vm]") for message in messages),
        reason="VM progress is prefixed",
    )
    expect(actual=factory.calls[0][:2], expected=("builder-vm", False))


def test_the_vm_selection_and_job_settings_reach_the_opener(
    tmp_path: Path,
) -> None:
    """The resolved ``conjure`` settings select and configure the VM.

    Args:
        tmp_path: Pytest temporary directory.

    """
    key: Path = tmp_path / "id_ed25519"
    key.write_text(data="key\n", encoding="utf-8")
    job: Settings = Settings(
        ssh_username="builder",
        ssh_identity_file=key,
        ssh_port=2299,
    )
    factory: Factory = Factory(session=FakeVm(fail_start=True))
    with pytest.raises(expected_exception=BuildError):
        _ = _build(
            tmp_path=tmp_path,
            factory=factory,
            settings=_settings(vm=None, vm_attach=True),
            messages=[],
            job_settings=job,
        )
    vm, attach, settings = factory.calls[0]
    expect(actual=(vm, attach), expected=("", True))
    expect(
        actual=(settings.ssh_username, settings.ssh_identity_file),
        expected=("builder", key),
    )
    expect(actual=settings.ssh_port, expected=2299)


def test_a_vm_that_fails_to_boot_is_a_build_error_and_is_closed(
    tmp_path: Path,
) -> None:
    """Boot failures surface with the VM's reason, and nothing leaks.

    Args:
        tmp_path: Pytest temporary directory.

    """
    vm: FakeVm = FakeVm(fail_start=True)
    with pytest.raises(
        expected_exception=BuildError,
        match="never answered SSH",
    ):
        _ = _build(
            tmp_path=tmp_path,
            factory=Factory(session=vm),
            settings=_settings(vm="builder-vm"),
            messages=[],
        )
    expect(actual=vm.events, expected=["start", "close"])


def test_a_launch_that_dies_ends_the_wait_early(tmp_path: Path) -> None:
    """A failing bootstrap command does not wait for the full timeout.

    Args:
        tmp_path: Pytest temporary directory.

    """
    vm: FakeVm = FakeVm(failure="curl: (7) connection refused")
    with pytest.raises(
        expected_exception=BuildError,
        match=r"launch failed: curl: \(7\)",
    ):
        _ = _build(
            tmp_path=tmp_path,
            factory=Factory(session=vm),
            settings=_settings(vm="builder-vm"),
            messages=[],
        )
    expect(actual=vm.events[-1], expected="close")


def test_a_failed_launch_reads_its_log_before_the_guest_is_cleaned(
    tmp_path: Path,
) -> None:
    """The log is fetched for the error first, then deleted from the guest.

    A guest that cannot even run the delete command is not a second error.

    Args:
        tmp_path: Pytest temporary directory.

    """
    vm: FakeVm = FakeVm(failure="curl: (7) connection refused")
    with pytest.raises(expected_exception=BuildError):
        _ = _build(
            tmp_path=tmp_path,
            factory=Factory(session=vm),
            settings=_settings(vm="builder-vm"),
            messages=[],
        )
    expect(
        actual=vm.events,
        expected=["start", "run", "download", "run", "close"],
    )
    expect(actual=vm.commands[-1][3:6], expected=("del", "/f", "/q"))


def _discard(message: str) -> None:
    """Ignore a progress message.

    Args:
        message: Ignored text.

    """
    _ = message


def test_a_guest_that_cannot_fetch_the_worker_fails_the_launch_at_once(
    tmp_path: Path,
) -> None:
    """A failed ``curl`` ends the wait instead of waiting out the timeout.

    Args:
        tmp_path: Pytest temporary directory.

    """
    vm: FakeVm = FakeVm(launch_status=1)
    with pytest.raises(
        expected_exception=BuildError,
        match=r"launch failed.*exited with status 1",
    ):
        _ = _build(
            tmp_path=tmp_path,
            factory=Factory(session=vm),
            settings=_settings(vm="builder-vm"),
            messages=[],
        )
    command: str = vm.commands[0][-1]
    script: str = command.split(sep="del /q ")[1].split(maxsplit=1, sep=")")[0]
    check(
        condition=command.endswith(f"|| (del /q {script} & exit /b 1)"),
        reason=command,
    )
    check(
        condition=f"&& (del /q {script})" in command,
        reason="the success path still removes the script",
    )


@final
class UtmctlRecorder:
    """Stand in for ``utmctl`` on a guest that never runs the worker.

    Attributes:
        calls: Every ``utmctl`` argument vector, in order.
        scripts: Content of each file pushed into the guest.

    """

    def __init__(self) -> None:
        """Start with nothing recorded."""
        self.calls: Final[list[tuple[str, ...]]] = []
        self.scripts: Final[list[str]] = []

    def __call__(
        self,
        *,
        args: tuple[str, ...],
        text: bool = False,
        **ignored: object,
    ) -> CompletedProcess[str] | CompletedProcess[bytes]:
        """Answer one ``utmctl`` call.

        Args:
            args: Complete argument vector.
            text: Whether the caller wants text rather than bytes.
            **ignored: Subprocess options the fake does not need.

        Returns:
            A successful result; the guest "has" the loopback address, which
            keeps the host-address lookup local to this machine.

        """
        pushed: object = ignored.get("input")
        if isinstance(pushed, bytes):
            self.scripts.append(pushed.decode())
        self.calls.append(tuple(args))
        answers: dict[str, str] = {
            "status": "started\n",
            "ip-address": "127.0.0.1\n",
            # Every command in this guest "finishes" with exit status 0.
            "file": "0\r\n",
        }
        output: str = answers.get(args[1], "")
        if text:
            return CompletedProcess(
                args=args,
                returncode=0,
                stdout=output,
                stderr="",
            )
        return CompletedProcess(
            args=args,
            returncode=0,
            stdout=output.encode(),
            stderr=b"",
        )


def test_a_utm_guest_is_given_the_host_address_on_its_own_network(
    tmp_path: Path,
) -> None:
    """The worker URL is this host as the guest sees it, not 10.0.2.2.

    Args:
        tmp_path: Pytest temporary directory.

    """
    recorder: UtmctlRecorder = UtmctlRecorder()

    def open_utm(
        *,
        vm: str,
        attach: bool,
        settings: Settings,
    ) -> JobSession:
        """Create the guest-agent session of the UTM guest.

        Args:
            vm: Ignored VM selection.
            attach: Ignored attach flag.
            settings: Ignored ``conjure`` settings.

        Returns:
            A session driven by the recording ``utmctl``.

        """
        _ = (vm, attach, settings)
        return UtmRunner(
            name="Windows 2",
            utmctl=Path("/opt/utmctl"),
            start_timeout_seconds=2.0,
            poll_interval_seconds=0.01,
        )

    with (
        patch(target="tools.conjure.utm_runner.run", new=recorder),
        pytest.raises(expected_exception=BuildError, match="timed out"),
    ):
        _ = RemoteBuilder(
            say=_discard,
            open_vm=open_utm,
            job_settings=Settings(),
        ).build(
            project=project(root=tmp_path / "proj"),
            settings=RemoteSettings(
                port=0,
                worker_host="10.0.2.2",
                timeout_seconds=1,
                vm="utm:Windows 2",
            ),
            target="windows",
            output=tmp_path / "build",
        )
    expect(actual=len(recorder.scripts) >= 1, expected=True)
    worker_script: str = recorder.scripts[0]
    check(
        condition="http://127.0.0.1:" in worker_script,
        reason=worker_script,
    )
    check(condition="10.0.2.2" not in worker_script, reason=worker_script)


def test_an_unresolvable_vm_is_a_build_error(tmp_path: Path) -> None:
    """A VM the registry rejects is reported, not a traceback.

    Args:
        tmp_path: Pytest temporary directory.

    """

    def missing(*, vm: str, attach: bool, settings: Settings) -> JobSession:
        """Reject every VM like an unknown registry name.

        Args:
            vm: Registry name being resolved.
            attach: Ignored flag.
            settings: Ignored settings.

        Returns:
            The session; the call raises before returning.

        Raises:
            ValueError: Always.

        """
        _ = (attach, settings)
        msg: str = f"No VM named {vm!r} in the registry."
        raise ValueError(msg)

    messages: list[str] = []
    with pytest.raises(expected_exception=BuildError, match="No VM named"):
        _ = RemoteBuilder(
            say=messages.append,
            open_vm=missing,
            job_settings=Settings(),
        ).build(
            project=project(root=tmp_path / "proj"),
            settings=_settings(vm="ghost-vm"),
            target="windows",
            output=tmp_path / "build",
        )


def test_manifest_vm_keys_become_settings(tmp_path: Path) -> None:
    """``[remote]`` selects the VM by name, or asks to attach instead.

    Args:
        tmp_path: Pytest temporary directory.

    """
    settings: RemoteSettings = RemoteSettings.load(
        manifest=manifest(
            tmp_path=tmp_path,
            body='[remote]\nvm = "builder-vm"\nvm_attach = true\n',
        ),
    )
    expect(
        actual=(settings.vm, settings.vm_attach),
        expected=("builder-vm", True),
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("[remote]\nvm = 3\n", "vm must be a string"),
        ('[remote]\nvm_attach = "x"\n', "vm_attach must be of type bool"),
        ("[remote]\nvm_attach = 1\n", "vm_attach must be of type bool"),
        ('[remote]\nvm_user = "builder"\n', "vm_user"),
        ("[remote]\nvm_ssh_port = 2299\n", "vm_ssh_port"),
    ],
)
def test_invalid_vm_settings_are_rejected(
    tmp_path: Path,
    body: str,
    message: str,
) -> None:
    """Mistyped VM keys and removed legacy keys fail loudly.

    Args:
        tmp_path: Pytest temporary directory.
        body: TOML under test.
        message: Text the error must contain.

    """
    with pytest.raises(expected_exception=BuildError, match=message):
        _ = RemoteSettings.load(
            manifest=manifest(tmp_path=tmp_path, body=body),
        )
