# Copyright (c) 2026 Project contributors.
"""Tests for ``district --make-release``, a front end over stationer."""

from __future__ import annotations

from io import StringIO
from json import loads
from shutil import copy2
from subprocess import CompletedProcess, run
from sys import executable
from typing import TYPE_CHECKING, Final, final

import pytest
from district import cli as cli_module
from district import release
from district.cli import Cli
from district.core import (
    District,
    SessionRepository,
)
from district.update import UpdateConstants

from tests.conftest import sibling_tree
from tools.stationer import cli as publish_cli
from tools.stationer.config import Config as PublishConfig
from tools.stationer.config import load_config

from .helpers import check, expect, present

if TYPE_CHECKING:
    from pathlib import Path

    from tools.stationer.models import Project as PublishProject


@final
class Constants:
    """Group module-level constants.

    Attributes:
        workspace: Grouped module constant.
        product: Grouped module constant.
        product_text: Grouped module constant.

    """

    workspace: Final[Path] = sibling_tree().project.parent.parent
    product: Final[str] = "tools/district/product.py"
    product_text: Final[str] = (
        'class Product:\n    name: Final[str] = "District Terminal"\n    '
        'version: Final[str] = "{version}"\n    command: Final[str] = '
        '"district"\n'
    )


def git(*arguments: str, cwd: Path | None = None) -> str:
    """Run git and return its stdout, failing the test on a non-zero exit.

    Args:
        *arguments: Git arguments.
        cwd: Working directory.
        arguments: Command-line arguments.

    Returns:
        Captured standard output.

    """
    command: tuple[str, ...] = ("git", *arguments)
    return run(
        args=command,
        check=True,
        capture_output=True,
        text=True,
        cwd=cwd,
    ).stdout


def fake_which(*, cmd: str) -> str:
    """Pretend every tool is installed.

    Args:
        cmd: Tool name.

    Returns:
        A fake absolute path.

    """
    return f"/bin/{cmd}"


def make_tree(*, root: Path, version: str = "1.0.0") -> Path:
    """Create the minimal checkout layout the front end expects.

    Args:
        root: Checkout root.
        version: Version written to ``product.py``.

    Returns:
        The product file path.

    """
    (root / "tools" / "district" / "district").mkdir(parents=True)
    _ = (root / "tools" / "district" / "pyproject.toml").write_text(
        data="[project]\n",
    )
    _ = copy2(
        src=Constants.workspace / "release.toml",
        dst=root / "release.toml",
    )
    product: Path = root / Constants.product
    _ = product.write_text(data=Constants.product_text.format(version=version))
    return product


class FakeExec:
    """Record the commands the front end would run.

    Attributes:
        calls: Argument vectors in order.
        codes: Exit status per command substring.

    """

    def __init__(self, *, codes: dict[str, int] | None = None) -> None:
        """Create the fake.

        Args:
            codes: Exit status keyed by a substring of the command.

        """
        self.calls: list[tuple[str, ...]] = []
        self.codes: dict[str, int] = codes or {}

    def __call__(
        self,
        *,
        args: tuple[str, ...],
        root: Path,
        capture: bool = False,
    ) -> CompletedProcess[str]:
        """Record one command and answer with its scripted status.

        Args:
            args: Argument vector.
            root: Working directory (unused).
            capture: Whether output was requested (unused).

        Returns:
            Scripted result.

        """
        _ = (root, capture)
        self.calls.append(args)
        joined: str = " ".join(args)
        code: int = next(
            (c for needle, c in self.codes.items() if needle in joined),
            0,
        )
        return CompletedProcess(args=args, returncode=code, stdout="")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Create a fake checkout and return its root.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Checkout root.

    """
    _ = make_tree(root=tmp_path)
    _ = git("init", "-q", str(object=tmp_path))
    return tmp_path


def anchor_in(*, root: Path) -> Path:
    """Return a module path inside the fake checkout.

    Args:
        root: Checkout root.

    Returns:
        A path under ``tools/district``.

    """
    return root / "tools" / "district" / "district" / "release.py"


@pytest.mark.parametrize(
    ("arg", "expected"),
    [
        ("patch", ("--bump", "patch")),
        ("minor", ("--bump", "minor")),
        ("major", ("--bump", "major")),
        ("1.2.4", ("--version", "1.2.4")),
    ],
)
def test_release_command_delegates_to_stationer(
    arg: str,
    expected: tuple[str, ...],
    tree: Path,
) -> None:
    """Version selection becomes stationer arguments.

    Args:
        arg: Version selector.
        expected: Expected arguments.
        tree: Fake checkout root.

    """
    command: tuple[str, ...] = release.release_command(root=tree, arg=arg)
    expect(
        actual=command[:3],
        expected=(executable, "-m", "tools.stationer"),
    )
    expect(
        actual=command[3:8],
        expected=(
            "--config",
            str(object=tree / "release.toml"),
            "release",
            "--project",
            "district",
        ),
    )
    check(
        condition=all(x in command for x in expected),
        reason=str(object=command),
    )
    check(condition="--no-publish" in command, reason="CI publishes")
    check(condition="--dry-run" not in command, reason=str(object=command))
    check(condition="--skip-hooks" not in command, reason=str(object=command))


def test_release_command_flags(tree: Path) -> None:
    """Dry run and ``--no-verify`` map to stationer switches.

    Args:
        tree: Fake checkout root.

    """
    command: tuple[str, ...] = release.release_command(
        root=tree,
        arg="patch",
        dry_run=True,
        verify=False,
    )
    check(condition="--dry-run" in command, reason=str(object=command))
    check(condition="--skip-hooks" in command, reason=str(object=command))


@pytest.mark.parametrize("arg", ["", "garbage", "1.2", "v1.2.4", "patchy"])
def test_release_command_rejects_bad_versions(arg: str, tree: Path) -> None:
    """Only keywords and strict ``X.Y.Z`` are accepted.

    Args:
        arg: Version selector.
        tree: Fake checkout root.

    """
    with pytest.raises(
        expected_exception=ValueError,
        match="patch, minor, major",
    ):
        _ = release.release_command(root=tree, arg=arg)


def test_repo_root_requires_source_checkout(tmp_path: Path) -> None:
    """A git checkout containing the project and release.toml resolves.

    Args:
        tmp_path: Pytest temporary directory.

    """
    repo: Path = tmp_path / "repo"
    repo.mkdir()
    _ = git("init", "-q", str(object=repo))
    _ = make_tree(root=repo)
    found: Path = present(value=release.repo_root(anchor=anchor_in(root=repo)))
    expect(actual=found.resolve(), expected=repo.resolve())
    elsewhere: Path = tmp_path / "elsewhere"
    elsewhere.mkdir()
    expect(
        actual=release.repo_root(anchor=elsewhere / "release.py"),
        expected=None,
    )
    no_config: Path = tmp_path / "no_config"
    no_config.mkdir()
    _ = git("init", "-q", str(object=no_config))
    _ = make_tree(root=no_config)
    (no_config / "release.toml").unlink()
    expect(
        actual=release.repo_root(anchor=anchor_in(root=no_config)),
        expected=None,
    )


def test_run_cli_gate_outside_checkout(tmp_path: Path) -> None:
    """Archives and copies cannot cut releases.

    Args:
        tmp_path: Pytest temporary directory.

    """
    out: StringIO = StringIO()
    status: int = release.run_cli(
        arguments=(),
        anchor=tmp_path / "nowhere" / "release.py",
        stream=out,
    )
    expect(actual=status, expected=2)
    check(
        condition="only available from a source checkout" in out.getvalue(),
        reason=out.getvalue(),
    )


@pytest.mark.parametrize(
    "arguments",
    [("--bogus",), ("patch", "minor"), ("not-a-version",)],
)
def test_run_cli_usage_errors(arguments: tuple[str, ...], tree: Path) -> None:
    """Unknown flags, extra positionals and bad versions are usage errors.

    Args:
        arguments: Command-line arguments.
        tree: Fake checkout root.

    """
    out: StringIO = StringIO()
    status: int = release.run_cli(
        arguments=arguments,
        anchor=anchor_in(root=tree),
        stream=out,
    )
    expect(actual=status, expected=2)
    check(condition="usage" in out.getvalue(), reason=out.getvalue())


def test_run_cli_runs_stationer_in_the_checkout(
    tree: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The front end runs exactly one stationer command.

    Args:
        tree: Fake checkout root.
        monkeypatch: Pytest monkeypatch fixture.

    """
    fake: FakeExec = FakeExec()
    monkeypatch.setattr(target=release, name="execute", value=fake)
    status: int = release.run_cli(
        arguments=("minor", "--dry-run", "--no-verify"),
        anchor=anchor_in(root=tree),
        stream=StringIO(),
    )
    expect(actual=status, expected=0)
    expect(actual=len(fake.calls), expected=1)
    call: tuple[str, ...] = fake.calls[0]
    check(
        condition="--bump" in call and "minor" in call,
        reason=str(object=call),
    )
    check(condition="--dry-run" in call, reason=str(object=call))
    check(condition="--skip-hooks" in call, reason=str(object=call))


def test_run_cli_propagates_failure_and_skips_watch(
    tree: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed release returns its status and never watches CI.

    Args:
        tree: Fake checkout root.
        monkeypatch: Pytest monkeypatch fixture.

    """
    fake: FakeExec = FakeExec(codes={"stationer": 1})
    monkeypatch.setattr(target=release, name="execute", value=fake)
    monkeypatch.setattr(target=release, name="which", value=fake_which)
    status: int = release.run_cli(
        arguments=("--watch",),
        anchor=anchor_in(root=tree),
        stream=StringIO(),
    )
    expect(actual=status, expected=1)
    expect(actual=len(fake.calls), expected=1)


def test_run_cli_watch_follows_ci_when_gh_exists(
    tree: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--watch`` follows the workflow run after a successful push.

    Args:
        tree: Fake checkout root.
        monkeypatch: Pytest monkeypatch fixture.

    """
    fake: FakeExec = FakeExec()
    monkeypatch.setattr(target=release, name="execute", value=fake)
    monkeypatch.setattr(target=release, name="which", value=fake_which)
    status: int = release.run_cli(
        arguments=("--watch",),
        anchor=anchor_in(root=tree),
        stream=StringIO(),
    )
    expect(actual=status, expected=0)
    expect(actual=len(fake.calls), expected=2)
    check(condition="gh run watch" in " ".join(fake.calls[1]), reason="gh")

    def _no_gh(**_: object) -> None:
        """Handle no gh.

        Args:
            _: _.

        """
        return

    monkeypatch.setattr(target=release, name="which", value=_no_gh)
    fake.calls.clear()
    __: int = release.run_cli(
        arguments=("--watch",),
        anchor=anchor_in(root=tree),
        stream=StringIO(),
    )
    expect(actual=len(fake.calls), expected=1)


def test_cli_make_release_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI forwards everything after the flag to the front end.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    seen: list[tuple[str, ...]] = []

    def fake(*, arguments: tuple[str, ...], **_: object) -> int:
        """Return fake.

        Args:
            arguments: Arguments.
            _: _.

        Returns:
            The resulting value.

        """
        seen.append(arguments)
        return 7

    monkeypatch.setattr(target=release, name="run_cli", value=fake)
    monkeypatch.setattr(target=cli_module, name="stderr", value=StringIO())
    cli: Cli = Cli(
        service=District(repository=SessionRepository(home=tmp_path)),
    )
    expect(
        actual=cli.run(arguments=("--make-release", "minor", "--dry-run")),
        expected=7,
    )
    expect(actual=seen, expected=[("minor", "--dry-run")])


def test_release_toml_matches_the_update_client() -> None:
    """The tag stationer creates is the tag the updater looks for."""
    config: PublishConfig = load_config(
        path=Constants.workspace / "release.toml",
    )
    project: PublishProject = config.project(name="district")
    template: str = config.release.tag_template
    expect(
        actual=template.format(project="district", version=""),
        expected=UpdateConstants.default_tag_prefix,
    )
    expect(actual=str(object=project.path), expected="tools/district")
    expect(
        actual=[str(object=path) for path in project.version_files],
        expected=[Constants.product],
    )
    expect(
        actual=f"{config.repository.owner}/{config.repository.name}",
        expected=UpdateConstants.default_repository,
    )
    check(
        condition=config.release.create_github_release is False,
        reason="CI attaches the per-platform archives, not the local tool",
    )


def test_release_toml_end_to_end_with_real_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bump, commit, tag and push with the repository's real release.toml.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
        capsys: Pytest output capture fixture.

    """
    for name, value in {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
    }.items():
        monkeypatch.setenv(name=name, value=value)
    remote: Path = tmp_path / "remote.git"
    work: Path = tmp_path / "work"
    work.mkdir()
    _ = git("init", "-q", "--bare", str(object=remote))
    _ = git("init", "-q", "-b", "main", str(object=work))
    _ = make_tree(root=work)
    _ = git("add", ".", cwd=work)
    _ = git("commit", "-q", "-m", "seed", cwd=work)
    _ = git("remote", "add", "origin", str(object=remote), cwd=work)
    _ = (work / "notes.txt").write_text(data="untracked draft")
    config: str = str(object=work / "release.toml")
    status: int = publish_cli.main(
        arguments=(
            "--config",
            config,
            "release",
            "--project",
            "district",
            "--bump",
            "patch",
            "--no-publish",
            "--skip-hooks",
        ),
    )
    expect(actual=status, expected=0)
    tags: list[str] = git("tag", "-l", "district/v*", cwd=remote).split()
    expect(actual=tags, expected=["district/v1.0.1"])
    shown: str = git("show", "--stat", "--format=%s", "HEAD", cwd=work)
    check(condition="product.py" in shown, reason=shown)
    check(
        condition="notes.txt" not in shown,
        reason="untracked files must not enter the release commit: " + shown,
    )
    check(
        condition='version: Final[str] = "1.0.1"'
        in (work / Constants.product).read_text(),
        reason="product.py bumped",
    )
    _ = capsys.readouterr()
    shown_status: int = publish_cli.main(
        arguments=("--config", config, "show", "--project", "district"),
    )
    expect(actual=shown_status, expected=0)
    expect(
        actual=loads(s=capsys.readouterr().out)["version"],
        expected="1.0.1",
    )
    repeat: int = publish_cli.main(
        arguments=(
            "--config",
            config,
            "release",
            "--project",
            "district",
            "--version",
            "1.0.1",
            "--no-publish",
            "--skip-hooks",
            "--dry-run",
        ),
    )
    expect(actual=repeat, expected=2)
