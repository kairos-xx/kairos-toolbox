# Copyright (c) 2026 Project contributors.
"""Tests for ``stationer --remote``."""

from __future__ import annotations

from io import StringIO
from json import loads
from time import monotonic
from typing import TYPE_CHECKING

import pytest

from tools.stationer import Cli, ReleasePublisher
from tools.stationer import builder as builder_module
from tools.stationer.builder import (
    ArtifactUtilities,
    BuildError,
    BuildResult,
    ProjectConfig,
)
from tools.stationer.remote_builder import RemoteBuilder, RemoteSettings

from .helpers import check, expect, present
from .test_configured import layout

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tools.conjure import Settings


def _manifest(*, tmp_path: Path, remote: str = "") -> tuple[Path, Path]:
    """Create a project and a manifest.

    Args:
        tmp_path: Pytest temporary directory.
        remote: Extra TOML for the ``[remote]`` table.

    Returns:
        The project root and the manifest path.

    """
    root: Path = tmp_path / "proj"
    root.mkdir()
    (root / "app.py").write_text(data="print()\n", encoding="utf-8")
    manifest: Path = tmp_path / "m.toml"
    manifest.write_text(
        data=(
            '[project]\nname = "demo"\nversion = "1.2.3"\n'
            'entrypoint = "app.py"\n'
            f"[remote]\n{remote}"
        ),
        encoding="utf-8",
    )
    return root, manifest


class Recorder:
    """Capture what the CLI hands to the remote builder."""

    def __init__(self, *, output: Path, fail: bool = False) -> None:
        """Create the recorder.

        Args:
            output: Output root the fake build writes into.
            fail: Whether the fake build raises ``BuildError``.

        """
        self.output: Path = output
        self.fail: bool = fail
        self.calls: list[tuple[RemoteSettings, str, Path, ProjectConfig]] = []
        self.messages: list[str] = []
        self.job_settings: Settings | None = None

    def install(self, *, monkeypatch: pytest.MonkeyPatch) -> None:
        """Replace ``RemoteBuilder.build`` and ``__init__`` with fakes.

        Args:
            monkeypatch: Pytest monkeypatch fixture.

        """
        recorder: Recorder = self

        def init(
            self: RemoteBuilder,
            *,
            say: Callable[[str], None],
            job_settings: Settings | None = None,
            progress: object = None,
        ) -> None:
            """Record the job settings instead of building the real sink.

            Args:
                self: Ignored builder.
                say: Ignored message sink.
                job_settings: Resolved ``conjure`` settings.
                progress: Ignored progress sink.

            """
            _ = (self, say, progress)
            recorder.job_settings = job_settings

        def build(
            self: RemoteBuilder,
            *,
            project: ProjectConfig,
            settings: RemoteSettings,
            target: str,
            output: Path,
            **kwargs: object,
        ) -> BuildResult:
            """Record the call and fabricate a one-file build.

            Args:
                self: Ignored builder.
                project: Project being built.
                settings: Resolved remote settings.
                target: Operating system targeted.
                output: Output root.
                kwargs: Ignored extras such as installer metadata.

            Returns:
                A verified result for the fabricated executable.

            Raises:
                BuildError: When the recorder is configured to fail.

            """
            _ = (self, kwargs)
            recorder.calls.append((settings, target, output, project))
            if recorder.fail:
                msg: str = "worker exploded"
                raise BuildError(msg)
            executable: Path = output / target / "demo"
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(data=b"binary")
            return ArtifactUtilities.result(
                platform_name="linux",
                architecture="arm64",
                executable_path=executable,
                installer_path=None,
                started_at=monotonic(),
            )

        monkeypatch.setattr(target=RemoteBuilder, name="__init__", value=init)
        monkeypatch.setattr(target=RemoteBuilder, name="build", value=build)


def _run(
    *,
    tmp_path: Path,
    extra: tuple[str, ...],
    remote: str = "",
    target: str = "linux",
) -> int:
    """Run the CLI in remote mode against a fresh project.

    Args:
        tmp_path: Pytest temporary directory.
        extra: Additional CLI arguments.
        remote: Extra TOML for the ``[remote]`` table.
        target: Operating system the remote worker builds for.

    Returns:
        The CLI exit status.

    """
    root, manifest = _manifest(tmp_path=tmp_path, remote=remote)
    return Cli.main(
        arguments=(
            "--project",
            str(object=root),
            "--manifest",
            str(object=manifest),
            "--output",
            str(object=tmp_path / "build"),
            "--remote",
            target,
            *extra,
        ),
    )


def test_remote_builds_and_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A remote build lands in the shared manifest and ``BUILD.md``.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    recorder: Recorder = Recorder(output=tmp_path / "build")
    recorder.install(monkeypatch=monkeypatch)
    expect(actual=_run(tmp_path=tmp_path, extra=()), expected=0)
    settings, target, output, project = recorder.calls[0]
    expect(actual=target, expected="linux")
    expect(actual=output, expected=(tmp_path / "build").resolve())
    expect(actual=project.name, expected="demo")
    entries: list[dict[str, str]] = loads(
        s=(tmp_path / "build" / "artifacts.json").read_text(encoding="utf-8"),
    )
    expect(actual=len(entries), expected=1)
    check(condition="installer" not in entries[0], reason=str(object=entries))
    text: str = (tmp_path / "build" / "BUILD.md").read_text(encoding="utf-8")
    check(condition="| linux | arm64 |" in text, reason=text)
    expect(actual=settings.expose, expected="direct")


def test_manifest_settings_and_cli_overrides_combine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Command-line options win over the ``[remote]`` table.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    recorder: Recorder = Recorder(output=tmp_path / "build")
    recorder.install(monkeypatch=monkeypatch)
    status: int = _run(
        tmp_path=tmp_path,
        remote='port = 9000\nexpose = "tunnl"\ntimeout_seconds = 99\n',
        extra=("--port", "9100", "--expose", "pinggy", "--short"),
    )
    expect(actual=status, expected=0)
    settings: RemoteSettings = recorder.calls[0][0]
    expect(
        actual=(settings.port, settings.expose, settings.timeout_seconds),
        expected=(9100, "pinggy", 99),
    )
    expect(actual=settings.short, expected="")


def test_an_invalid_override_is_a_clean_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Out-of-range overrides are reported, not raised.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    Recorder(output=tmp_path).install(monkeypatch=monkeypatch)
    errors: StringIO = StringIO()
    monkeypatch.setattr(target=builder_module, name="sys_stderr", value=errors)
    expect(
        actual=_run(tmp_path=tmp_path, extra=("--port", "70000")),
        expected=1,
    )
    check(condition="port" in errors.getvalue(), reason="port named")


def test_build_errors_return_one_and_write_no_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed remote build prints the reason and leaves no report.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    Recorder(output=tmp_path / "build", fail=True).install(
        monkeypatch=monkeypatch,
    )
    errors: StringIO = StringIO()
    monkeypatch.setattr(target=builder_module, name="sys_stderr", value=errors)
    expect(actual=_run(tmp_path=tmp_path, extra=()), expected=1)
    check(
        condition="worker exploded" in errors.getvalue(),
        reason="reason printed",
    )
    check(
        condition=not (tmp_path / "build" / "BUILD.md").exists(),
        reason="no report for a failed build",
    )


def test_remote_can_publish_afterwards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--publish`` works after a remote build like after a native one.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    Recorder(output=tmp_path / "build").install(monkeypatch=monkeypatch)
    published: list[tuple[str, str]] = []

    def fake_publish(
        self: ReleasePublisher,
        *,
        output: Path,
        project: str,
        version: str,
        dry_run: bool,
    ) -> None:
        """Record what would be published.

        Args:
            self: Ignored publisher.
            output: Ignored output directory.
            project: Release project name.
            version: Version being published.
            dry_run: Ignored dry-run flag.

        """
        _ = (self, output, dry_run)
        published.append((project, version))

    monkeypatch.setattr(
        target=ReleasePublisher,
        name="publish",
        value=fake_publish,
    )
    status: int = _run(
        tmp_path=tmp_path,
        extra=("--publish", "demo", "--release-config", "release.toml"),
    )
    expect(actual=status, expected=0)
    expect(actual=published, expected=[("demo", "1.2.3")])


@pytest.mark.parametrize(
    "conflict",
    [("--format", "pyz"), ("--platform", "linux")],
)
def test_remote_excludes_other_build_modes(
    tmp_path: Path,
    conflict: tuple[str, ...],
) -> None:
    """A remote build is its own mode.

    Args:
        tmp_path: Pytest temporary directory.
        conflict: Options that cannot be combined with ``--remote``.

    """
    with pytest.raises(expected_exception=SystemExit) as raised:
        _ = _run(tmp_path=tmp_path, extra=conflict)
    expect(actual=raised.value.code, expected=2)


def test_vm_options_become_remote_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--vm`` and its companions configure the VM the worker runs in.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    recorder: Recorder = Recorder(output=tmp_path / "build")
    recorder.install(monkeypatch=monkeypatch)
    key: Path = tmp_path / "key"
    expect(
        actual=_run(
            tmp_path=tmp_path,
            target="windows",
            extra=(
                "--vm",
                "builder-vm",
                "--attach",
                "--vm-user",
                "builder",
                "--vm-identity",
                str(object=key),
                "--vm-port",
                "2299",
            ),
        ),
        expected=0,
    )
    settings: RemoteSettings = recorder.calls[0][0]
    expect(
        actual=(settings.vm, settings.vm_attach),
        expected=("builder-vm", True),
    )
    job: Settings = present(value=recorder.job_settings)
    expect(
        actual=(job.ssh_username, job.ssh_identity_file, job.ssh_port),
        expected=("builder", key.resolve(), 2299),
    )


def test_vm_options_need_remote_mode(tmp_path: Path) -> None:
    """A VM without ``--remote`` is a usage error.

    Args:
        tmp_path: Pytest temporary directory.

    """
    root, manifest = _manifest(tmp_path=tmp_path)
    with pytest.raises(expected_exception=SystemExit) as raised:
        _ = Cli.main(
            arguments=(
                "--project",
                str(object=root),
                "--manifest",
                str(object=manifest),
                "--vm",
                "windows.qcow2",
            ),
        )
    expect(actual=raised.value.code, expected=2)


def _configured_root(*, root: Path, extra: str) -> None:
    """Create a repository whose pyproject lists remote builds.

    Args:
        root: Directory acting as the repository root.
        extra: Extra ``[tool.stationer.builder]`` lines.

    """
    _ = layout(root=root)
    _ = (root / "pyproject.toml").write_text(
        data=(
            "[tool.stationer.builder]\n"
            'project = "src"\n'
            'manifest = "stationer.toml"\n'
            'output = "out"\n'
            "formats = []\n"
            f"{extra}"
        ),
        encoding="utf-8",
    )


def test_pyproject_lists_remote_builds_and_their_vm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare run builds each remote target inside the configured VM.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    _configured_root(
        root=tmp_path,
        extra='remote = ["windows"]\nvm = "builder-vm"\nattach = true\n',
    )
    recorder: Recorder = Recorder(output=tmp_path / "out")
    recorder.install(monkeypatch=monkeypatch)
    monkeypatch.chdir(path=tmp_path)
    expect(actual=Cli.main(arguments=()), expected=0)
    expect(
        actual=[call[1] for call in recorder.calls],
        expected=["windows", "windows"],
    )
    expect(
        actual=[call[0].architecture for call in recorder.calls],
        expected=["arm64", "amd64"],
    )
    settings: RemoteSettings = recorder.calls[0][0]
    expect(
        actual=(settings.vm, settings.vm_attach),
        expected=("builder-vm", True),
    )


def test_a_dry_run_skips_remote_builds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dry run never boots a VM.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    _configured_root(
        root=tmp_path,
        extra='remote = ["windows"]\ndry_run = true\n',
    )
    recorder: Recorder = Recorder(output=tmp_path / "out")
    recorder.install(monkeypatch=monkeypatch)
    monkeypatch.chdir(path=tmp_path)
    expect(actual=Cli.main(arguments=()), expected=0)
    expect(actual=recorder.calls, expected=[])


def test_a_failing_remote_build_stops_the_configured_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first failing remote build ends the run with its status.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    _configured_root(
        root=tmp_path,
        extra='remote = ["windows", "linux"]\n',
    )
    recorder: Recorder = Recorder(output=tmp_path / "out", fail=True)
    recorder.install(monkeypatch=monkeypatch)
    monkeypatch.setattr(
        target=builder_module,
        name="sys_stderr",
        value=StringIO(),
    )
    monkeypatch.chdir(path=tmp_path)
    expect(actual=Cli.main(arguments=()), expected=1)
    expect(actual=len(recorder.calls), expected=1)


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ("vm = 3\n", "vm must be a string"),
        ('remote = ["plan9"]\n', "remote must be a list"),
    ],
)
def test_invalid_pyproject_remote_keys_return_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra: str,
    message: str,
) -> None:
    """Mistyped ``remote`` and ``vm`` keys are configuration errors.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
        extra: Extra ``[tool.stationer.builder]`` lines.
        message: Text the error must contain.

    """
    _configured_root(root=tmp_path, extra=extra)
    errors: StringIO = StringIO()
    monkeypatch.setattr(target=builder_module, name="sys_stderr", value=errors)
    monkeypatch.chdir(path=tmp_path)
    expect(actual=Cli.main(arguments=()), expected=2)
    check(condition=message in errors.getvalue(), reason=errors.getvalue())


def test_arch_selects_the_executable_architecture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--arch amd64`` builds x64 on an ARM64 guest; default is native.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    recorder: Recorder = Recorder(output=tmp_path / "build")
    recorder.install(monkeypatch=monkeypatch)
    expect(
        actual=_run(tmp_path=tmp_path, extra=("--arch", "amd64")),
        expected=0,
    )
    expect(actual=recorder.calls[0][0].architecture, expected="amd64")
    other: Path = tmp_path / "second"
    other.mkdir()
    Recorder(output=other).install(monkeypatch=monkeypatch)
    again: Recorder = Recorder(output=other)
    again.install(monkeypatch=monkeypatch)
    expect(actual=_run(tmp_path=other, extra=()), expected=0)
    expect(actual=again.calls[0][0].architecture, expected="native")
