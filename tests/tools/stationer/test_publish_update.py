"""Tests for publishing to an existing release and for explicit files."""

from pathlib import Path
from subprocess import run
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

import pytest

from tools.conjure import CommandResult
from tools.stationer import cli
from tools.stationer.config import RepositoryConfig, load_config
from tools.stationer.engine import ReleaseEngine
from tools.stationer.providers import GitHubProvider

from .fake_hosts import Opening
from .helpers import check, expect

if TYPE_CHECKING:
    from tools.stationer.models import Action, ReleasePlan


class GhRunner:
    """Answer git tag queries and ``gh release view`` from fixed state."""

    def __init__(self, *, tags: tuple[str, ...], exists: bool) -> None:
        """Create the runner.

        Args:
            tags: Tags known to the fake repository.
            exists: Whether ``gh release view`` succeeds.

        """
        self.tags = tags
        self.exists = exists
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
        if command[:3] == ("gh", "release", "view"):
            if not self.exists:
                msg: str = "release not found"
                raise RuntimeError(msg)
            return CommandResult(
                command=command,
                return_code=0,
                stdout="",
                stderr="",
            )
        out: str = ""
        if command[:3] == ("git", "tag", "--list"):
            pattern: str = command[3]
            prefix: str = pattern.rstrip("*")
            out = "\n".join(t for t in self.tags if t.startswith(prefix))
        return CommandResult(
            command=command,
            return_code=0,
            stdout=out,
            stderr="",
        )


def _engine(*, root: Path, runner: GhRunner) -> ReleaseEngine:
    """Create an engine over a one-project repository.

    Args:
        root: Repository root.
        runner: Command runner.

    Returns:
        The engine.

    """
    (root / "p").mkdir()
    (root / "p" / "product.py").write_text(
        data='class P:\n    version: Final[str] = "1.0.0"\n',
        encoding="utf-8",
    )
    (root / "release.toml").write_text(
        data=(
            '[repository]\nowner="o"\nname="r"\n'
            '[projects.demo]\npath="p"\nversion_files=["p/product.py"]\n'
        ),
        encoding="utf-8",
    )
    return ReleaseEngine(
        config=load_config(path=root / "release.toml"),
        open_host=Opening(answers=runner),
    )


def test_provider_uploads_to_an_existing_release(tmp_path: Path) -> None:
    """Upload replaces same-named assets on the existing release.

    Args:
        tmp_path: Pytest temporary directory.

    """
    actions: tuple[Action, ...] = GitHubProvider().upload(
        repository=RepositoryConfig(provider="github", owner="o", name="r"),
        tag="demo/v1.0.0",
        artifacts=(tmp_path / "a", tmp_path / "b"),
        root=tmp_path,
    )
    expect(
        actual=actions[0].command,
        expected=(
            "gh",
            "release",
            "upload",
            "demo/v1.0.0",
            "--repo",
            "o/r",
            "--clobber",
            str(object=tmp_path / "a"),
            str(object=tmp_path / "b"),
        ),
    )


@pytest.mark.parametrize("exists", [True, False])
def test_release_exists_asks_github(*, exists: bool) -> None:
    """The check is a ``gh release view`` for the project's tag.

    Args:
        exists: Whether the fake release exists.

    """
    with TemporaryDirectory() as directory:
        runner: GhRunner = GhRunner(tags=("demo/v1.0.0",), exists=exists)
        engine: ReleaseEngine = _engine(root=Path(directory), runner=runner)
        expect(actual=engine.release_exists(project="demo"), expected=exists)
    expect(
        actual=runner.commands[-1],
        expected=("gh", "release", "view", "demo/v1.0.0", "--repo", "o/r"),
    )


def test_publish_plan_accepts_files_and_directories() -> None:
    """Explicit files and directories combine, sorted and de-duplicated."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        runner: GhRunner = GhRunner(tags=("demo/v1.0.0",), exists=False)
        engine: ReleaseEngine = _engine(root=root, runner=runner)
        (root / "dist").mkdir()
        (root / "dist" / "b.bin").write_bytes(data=b"b")
        (root / "dist" / "a.bin").write_bytes(data=b"a")
        (root / "extra.md").write_text(data="x", encoding="utf-8")
        plan: ReleasePlan = engine.publish_plan(
            project="demo",
            artifacts=(
                root / "extra.md",
                root / "dist",
                root / "dist" / "a.bin",
            ),
        )
    command: tuple[str, ...] = plan.actions[0].command
    expect(actual=command[:3], expected=("gh", "release", "create"))
    names: list[str] = [Path(c).name for c in command if "/" in c]
    expect(actual=names[-3:], expected=["a.bin", "b.bin", "extra.md"])


def test_publish_plan_updates_an_existing_release() -> None:
    """``update=True`` uploads instead of creating."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        engine: ReleaseEngine = _engine(
            root=root,
            runner=GhRunner(tags=("demo/v1.0.0",), exists=True),
        )
        (root / "a.bin").write_bytes(data=b"a")
        plan: ReleasePlan = engine.publish_plan(
            project="demo",
            artifacts=(root / "a.bin",),
            update=True,
        )
    expect(
        actual=plan.actions[0].command[:4],
        expected=("gh", "release", "upload", "demo/v1.0.0"),
    )


def test_publish_plan_rejects_missing_paths() -> None:
    """A path that does not exist is an error, not silently skipped."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        engine: ReleaseEngine = _engine(
            root=root,
            runner=GhRunner(tags=("demo/v1.0.0",), exists=False),
        )
        with pytest.raises(
            expected_exception=ValueError,
            match="does not exist",
        ):
            _ = engine.publish_plan(
                project="demo",
                artifacts=(root / "nope.bin",),
            )


def test_cli_publish_update_with_several_paths(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``publish --update --artifacts A B`` plans one upload.

    Args:
        capsys: Pytest output capture fixture.

    """
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        for command in (
            ("git", "init", "-q", str(object=root)),
            ("git", "-C", str(object=root), "config", "user.name", "t"),
            ("git", "-C", str(object=root), "config", "user.email", "t@e.t"),
        ):
            _ = run(args=command, check=True)
        (root / "p").mkdir()
        (root / "p" / "product.py").write_text(
            data='class P:\n    version: Final[str] = "1.0.0"\n',
            encoding="utf-8",
        )
        (root / "release.toml").write_text(
            data=(
                '[repository]\nowner="o"\nname="r"\n'
                '[projects.demo]\npath="p"\n'
                'version_files=["p/product.py"]\n'
            ),
            encoding="utf-8",
        )
        for command in (
            ("git", "-C", str(object=root), "add", "."),
            ("git", "-C", str(object=root), "commit", "-q", "-m", "x"),
            ("git", "-C", str(object=root), "tag", "demo/v1.0.0"),
        ):
            _ = run(args=command, check=True)
        (root / "a.bin").write_bytes(data=b"a")
        (root / "b.bin").write_bytes(data=b"b")
        code: int = cli.main(
            arguments=(
                "--config",
                str(object=root / "release.toml"),
                "publish",
                "--project",
                "demo",
                "--artifacts",
                str(object=root / "a.bin"),
                str(object=root / "b.bin"),
                "--update",
                "--dry-run",
            ),
        )
    output: str = capsys.readouterr().out
    expect(actual=code, expected=0)
    check(
        condition="gh release upload demo/v1.0.0" in output,
        reason=output,
    )
    check(condition="--clobber" in output, reason=output)
    check(condition="a.bin" in output and "b.bin" in output, reason=output)
