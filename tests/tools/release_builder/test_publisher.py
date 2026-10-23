# Copyright (c) 2026 Project contributors.
"""Tests for publishing the built artifacts to a GitHub release."""

from __future__ import annotations

from json import dumps
from pathlib import Path
from typing import TYPE_CHECKING, Final, final

import pytest

from tools.release_builder import (
    BuildConfiguration,
    BuildError,
    Cli,
    ReleaseBuilder,
)
from tools.release_builder.publisher import ReleasePublisher
from tools.release_publish.runner import CommandResult

from .helpers import check, expect

if TYPE_CHECKING:
    from tools.release_builder.release_builder import PlatformName
    from tools.release_publish.models import ReleasePlan


@final
class GhRunner:
    """Answer git tag queries and ``gh release view`` from fixed state."""

    def __init__(self, *, tags: tuple[str, ...], exists: bool) -> None:
        """Create the runner.

        Args:
            tags: Tags known to the fake repository.
            exists: Whether ``gh release view`` succeeds.

        """
        self.tags: Final[tuple[str, ...]] = tags
        self.exists: Final[bool] = exists
        self.commands: list[tuple[str, ...]] = []

    def run(self, *, command: tuple[str, ...], cwd: Path) -> CommandResult:
        """Record one command and answer it.

        Args:
            command: Argument vector.
            cwd: Working directory (unused).

        Returns:
            The canned result.

        Raises:
            RuntimeError: When ``gh release view`` should fail.

        """
        _ = cwd
        self.commands.append(command)
        if command[:3] == ("gh", "release", "view") and not self.exists:
            msg: str = "release not found"
            raise RuntimeError(msg)
        out: str = ""
        if command[:3] == ("git", "tag", "--list"):
            prefix: str = command[3].rstrip("*")
            out = "\n".join(t for t in self.tags if t.startswith(prefix))
        return CommandResult(
            command=command,
            returncode=0,
            stdout=out,
            stderr="",
        )

    def executed(self, *, verb: str) -> list[tuple[str, ...]]:
        """Return the ``gh release <verb>`` commands that ran.

        Args:
            verb: ``create`` or ``upload``.

        Returns:
            Matching commands.

        """
        return [c for c in self.commands if c[:3] == ("gh", "release", verb)]


def _repository(*, root: Path, version: str = "1.2.3") -> Path:
    """Create a release configuration for the project ``demo``.

    Args:
        root: Repository root.
        version: Version written to the version file.

    Returns:
        The configuration path.

    """
    (root / "p").mkdir(parents=True)
    (root / "p" / "product.py").write_text(
        data=f'class P:\n    version: Final[str] = "{version}"\n',
        encoding="utf-8",
    )
    path: Path = root / "release.toml"
    path.write_text(
        data=(
            '[repository]\nowner="o"\nname="r"\n'
            '[projects.demo]\npath="p"\nversion_files=["p/product.py"]\n'
        ),
        encoding="utf-8",
    )
    return path


def _output(*, root: Path) -> Path:
    """Create a build directory as the builder leaves it.

    Args:
        root: Parent of the ``build`` directory.

    Returns:
        The build directory.

    """
    output: Path = root / "build"
    (output / "linux").mkdir(parents=True)
    for name in ("demo-1.2.3-linux-arm64", "demo_1.2.3_arm64.deb"):
        (output / "linux" / name).write_text(data=name, encoding="utf-8")
    (output / "BUILD.md").write_text(data="# Build\n", encoding="utf-8")
    (output / "demo-1.2.3-linux-arm64.pyz").write_bytes(data=b"zip")
    (output / "artifacts.json").write_text(
        data=dumps(
            obj=[
                {
                    "platform": "linux",
                    "architecture": "arm64",
                    "executable": "linux/demo-1.2.3-linux-arm64",
                    "installer": "linux/demo_1.2.3_arm64.deb",
                },
            ],
        ),
        encoding="utf-8",
    )
    return output


def _publish(
    *,
    tmp_path: Path,
    runner: GhRunner,
    version: str = "1.2.3",
    dry_run: bool = False,
) -> ReleasePlan:
    """Publish the fixture build through ``runner``.

    Args:
        tmp_path: Pytest temporary directory.
        runner: Fake command runner.
        version: Version the builder reports.
        dry_run: Plan without running the final command.

    Returns:
        The executed plan.

    """
    config: Path = _repository(root=tmp_path / "repo")
    return ReleasePublisher(config_path=config, runner=runner).publish(
        output=_output(root=tmp_path),
        project="demo",
        version=version,
        dry_run=dry_run,
    )


def test_creates_the_release_with_binaries_and_report(
    tmp_path: Path,
) -> None:
    """A missing release is created with executables, installers, BUILD.md.

    Args:
        tmp_path: Pytest temporary directory.

    """
    runner: GhRunner = GhRunner(tags=("demo/v1.2.3",), exists=False)
    _ = _publish(tmp_path=tmp_path, runner=runner)
    created: list[tuple[str, ...]] = runner.executed(verb="create")
    expect(actual=len(created), expected=1)
    names: set[str] = {Path(a).name for a in created[0] if "/" in a}
    check(
        condition={
            "demo-1.2.3-linux-arm64",
            "demo_1.2.3_arm64.deb",
            "BUILD.md",
        }
        <= names,
        reason=str(object=names),
    )
    check(
        condition=not any(n.endswith((".pyz", ".json")) for n in names),
        reason=f".pyz is CI's job and artifacts.json is internal: {names}",
    )


def test_uploads_when_the_release_already_exists(tmp_path: Path) -> None:
    """CI usually creates the release first; the binaries are added to it.

    Args:
        tmp_path: Pytest temporary directory.

    """
    runner: GhRunner = GhRunner(tags=("demo/v1.2.3",), exists=True)
    _ = _publish(tmp_path=tmp_path, runner=runner)
    expect(actual=len(runner.executed(verb="create")), expected=0)
    uploads: list[tuple[str, ...]] = runner.executed(verb="upload")
    expect(actual=len(uploads), expected=1)
    check(condition="--clobber" in uploads[0], reason=str(object=uploads[0]))


def test_dry_run_plans_without_publishing(tmp_path: Path) -> None:
    """A dry run returns the plan and runs no release command.

    Args:
        tmp_path: Pytest temporary directory.

    """
    runner: GhRunner = GhRunner(tags=("demo/v1.2.3",), exists=False)
    plan: ReleasePlan = _publish(
        tmp_path=tmp_path,
        runner=runner,
        dry_run=True,
    )
    expect(actual=len(plan.actions), expected=1)
    expect(actual=runner.executed(verb="create"), expected=[])


def test_rejects_a_version_the_release_config_does_not_know(
    tmp_path: Path,
) -> None:
    """Artifacts built for one version never go on another version's tag.

    Args:
        tmp_path: Pytest temporary directory.

    """
    runner: GhRunner = GhRunner(tags=("demo/v1.2.3",), exists=False)
    with pytest.raises(expected_exception=BuildError, match=r"9\.9\.9"):
        _ = _publish(tmp_path=tmp_path, runner=runner, version="9.9.9")
    expect(actual=runner.executed(verb="create"), expected=[])


def test_requires_the_tag_to_be_pushed(tmp_path: Path) -> None:
    """An unpushed tag is reported as a build error with the reason.

    Args:
        tmp_path: Pytest temporary directory.

    """
    runner: GhRunner = GhRunner(tags=(), exists=False)
    with pytest.raises(expected_exception=BuildError, match="tag"):
        _ = _publish(tmp_path=tmp_path, runner=runner)


def test_requires_something_to_publish(tmp_path: Path) -> None:
    """An empty output directory is an error, not an empty release.

    Args:
        tmp_path: Pytest temporary directory.

    """
    config: Path = _repository(root=tmp_path / "repo")
    empty: Path = tmp_path / "build"
    empty.mkdir()
    with pytest.raises(expected_exception=BuildError, match="build"):
        _ = ReleasePublisher(
            config_path=config,
            runner=GhRunner(tags=("demo/v1.2.3",), exists=False),
        ).publish(
            output=empty,
            project="demo",
            version="1.2.3",
            dry_run=False,
        )


def test_cli_publishes_after_building(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--publish`` builds first, then publishes that version's output.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    root: Path = tmp_path / "proj"
    root.mkdir()
    (root / "app.py").write_text(data="print()\n", encoding="utf-8")
    manifest: Path = tmp_path / "m.toml"
    manifest.write_text(
        data=(
            '[project]\nname = "demo"\nversion = "1.2.3"\n'
            'entrypoint = "app.py"\n'
        ),
        encoding="utf-8",
    )
    order: list[str] = []
    published: list[tuple[Path, str, str, bool]] = []

    def fake_build(
        self: ReleaseBuilder,
        *,
        configuration: BuildConfiguration,
        output: Path,
        platforms: tuple[PlatformName, ...],
        invocation: str = "",
    ) -> tuple[()]:
        """Record that the build ran and which output it used.

        Args:
            self: The ``ReleaseBuilder`` instance (unused).
            configuration: Build configuration (unused).
            output: Directory receiving artifacts.
            platforms: Selected platforms (unused).
            invocation: Command prefix (unused).

        Returns:
            No build results.

        """
        _ = (self, configuration, platforms, invocation)
        order.append(f"build:{output.name}")
        return ()

    def fake_publish(
        self: ReleasePublisher,
        *,
        output: Path,
        project: str,
        version: str,
        dry_run: bool,
    ) -> None:
        """Record the publish call's arguments.

        Args:
            self: The ``ReleasePublisher`` instance (unused).
            output: Artifact directory.
            project: Published project name.
            version: Released version.
            dry_run: Whether the publish is a dry run.

        """
        _ = self
        order.append("publish")
        published.append((output, project, version, dry_run))

    monkeypatch.setattr(target=ReleaseBuilder, name="build", value=fake_build)
    monkeypatch.setattr(
        target=ReleasePublisher,
        name="publish",
        value=fake_publish,
    )
    status: int = Cli.main(
        arguments=(
            "--project",
            str(object=root),
            "--manifest",
            str(object=manifest),
            "--output",
            str(object=tmp_path / "build"),
            "--platform",
            "linux",
            "--publish",
            "demo",
            "--release-config",
            str(object=tmp_path / "release.toml"),
        ),
    )
    expect(actual=status, expected=0)
    expect(actual=order, expected=["build:build", "publish"])
    expect(
        actual=published,
        expected=[((tmp_path / "build").resolve(), "demo", "1.2.3", False)],
    )


def test_cli_rejects_publishing_the_pyz_format(tmp_path: Path) -> None:
    """The ``.pyz`` is built and checksummed by CI, never uploaded here.

    Args:
        tmp_path: Pytest temporary directory.

    """
    with pytest.raises(expected_exception=SystemExit) as raised:
        _ = Cli.main(
            arguments=(
                "--project",
                str(object=tmp_path),
                "--manifest",
                str(object=tmp_path / "m.toml"),
                "--format",
                "pyz",
                "--publish",
                "demo",
            ),
        )
    expect(actual=raised.value.code, expected=2)
