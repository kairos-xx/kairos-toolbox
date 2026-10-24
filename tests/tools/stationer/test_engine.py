"""Engine guard tests."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from tools.conjure import CommandResult
from tools.stationer.config import load_config
from tools.stationer.engine import ReleaseEngine
from tools.stationer.models import Action, ActionKind, Bump, ReleasePlan

from .fake_hosts import Opening
from .helpers import check, expect


class FakeRunner:
    """Answer git queries from a fixed tag list."""

    def __init__(
        self,
        *,
        tags: tuple[str, ...],
        changes: str = "",
    ) -> None:
        """Create a fake runner with a fixed tag list.

        Args:
            tags: Tag names known to the fake repository.
            changes: Lines reported by ``git diff --name-only``.

        """
        self.tags = tags
        self.changes = changes
        self.commands: list[tuple[str, ...]] = []
        self.directories: list[Path] = []

    def run(
        self,
        *,
        command: tuple[str, ...],
        cwd: Path,
    ) -> CommandResult:
        """Record the command and answer tag listings.

        Args:
            command: Command arguments.
            cwd: Working directory for the command.

        Returns:
            The canned command output.

        """
        self.commands.append(command)
        self.directories.append(cwd)
        out: str = ""
        if command[:3] == ("git", "diff", "--name-only"):
            out = self.changes
        elif command[:3] == ("git", "tag", "--list"):
            pattern: str = command[3]
            prefix: str = pattern.rstrip("*")
            out = "\n".join(
                t
                for t in sorted(self.tags, reverse=True)
                if t.startswith(prefix) or t == pattern
            )
        return CommandResult(
            command=command,
            return_code=0,
            stdout=out,
            stderr="",
        )


def _engine(root: Path, runner: FakeRunner) -> ReleaseEngine:
    """Build an engine against a fake repository.

    Args:
        root: Repository root to populate.
        runner: Fake command runner.

    Returns:
        The configured release engine.

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


def test_existing_tag_blocks_the_plan() -> None:
    """A release never plans over an existing tag."""
    with TemporaryDirectory() as directory:
        engine: ReleaseEngine = _engine(
            root=Path(directory),
            runner=FakeRunner(tags=("demo/v1.0.1",)),
        )
        with pytest.raises(
            expected_exception=ValueError,
            match="already exists",
        ):
            engine.plan(projects=("demo",), bump=Bump.PATCH)


def test_explicit_version_must_exceed_the_latest_tag() -> None:
    """Going backwards past a released tag is rejected."""
    with TemporaryDirectory() as directory:
        engine: ReleaseEngine = _engine(
            root=Path(directory),
            runner=FakeRunner(tags=("demo/v2.0.0",)),
        )
        with pytest.raises(expected_exception=ValueError, match="not newer"):
            engine.plan(projects=("demo",), version="1.5.0")


def test_plan_succeeds_when_tags_allow_it() -> None:
    """Unrelated tags do not interfere."""
    with TemporaryDirectory() as directory:
        engine: ReleaseEngine = _engine(
            root=Path(directory),
            runner=FakeRunner(tags=("other/v9.0.0",)),
        )
        plan: ReleasePlan = engine.plan(projects=("demo",), bump=Bump.MINOR)
        expect(actual=plan.projects[0].new_version, expected="1.1.0")


def test_plan_rejects_bump_and_version_together() -> None:
    """An explicit version and a bump keyword are mutually exclusive."""
    with TemporaryDirectory() as directory:
        engine: ReleaseEngine = _engine(
            root=Path(directory),
            runner=FakeRunner(tags=()),
        )
        with pytest.raises(
            expected_exception=ValueError,
            match="mutually exclusive",
        ):
            engine.plan(
                projects=("demo",),
                bump=Bump.PATCH,
                version="9.9.9",
            )


def test_changed_projects_reports_everything_without_tags() -> None:
    """A project with no release tag counts as changed."""
    with TemporaryDirectory() as directory:
        engine: ReleaseEngine = _engine(
            root=Path(directory),
            runner=FakeRunner(tags=()),
        )
        expect(actual=engine.changed_projects(), expected=("demo",))


def test_changed_projects_compares_against_the_latest_tag() -> None:
    """Paths under the project directory mark it changed."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        dirty: ReleaseEngine = _engine(
            root=root,
            runner=FakeRunner(
                tags=("demo/v1.0.0", "demo/v0.9.0"),
                changes="p/product.py\n",
            ),
        )
        expect(actual=dirty.changed_projects(), expected=("demo",))
        clean_root: Path = root / "clean"
        clean_root.mkdir()
        clean: ReleaseEngine = _engine(
            root=clean_root,
            runner=FakeRunner(
                tags=("demo/v1.0.0",),
                changes="docs/readme.md\n",
            ),
        )
        expect(actual=clean.changed_projects(), expected=())


def test_execute_writes_the_version_and_a_journal() -> None:
    """A real run updates the version file and records each action."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        engine: ReleaseEngine = _engine(
            root=root,
            runner=FakeRunner(tags=()),
        )
        plan: ReleasePlan = engine.plan(
            projects=("demo",),
            bump=Bump.PATCH,
        )
        engine.execute(plan=plan)
        check(
            condition='"1.0.1"'
            in (root / "p" / "product.py").read_text(
                encoding="utf-8",
            ),
            reason="version file must carry the new version",
        )
        journal: Path = root / ".stationer-journal.jsonl"
        check(condition=journal.is_file(), reason="journal must exist")
        check(
            condition=len(journal.read_text(encoding="utf-8").splitlines())
            == len(plan.actions),
            reason="every action must be journaled",
        )


def test_execute_dry_run_changes_nothing() -> None:
    """A dry run performs no writes and runs no commands."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        runner: FakeRunner = FakeRunner(tags=())
        engine: ReleaseEngine = _engine(root=root, runner=runner)
        plan: ReleasePlan = engine.plan(
            projects=("demo",),
            bump=Bump.PATCH,
        )
        planning_commands: int = len(runner.commands)
        engine.execute(plan=plan, dry_run=True)
        check(
            condition='"1.0.0"'
            in (root / "p" / "product.py").read_text(
                encoding="utf-8",
            ),
            reason="dry run must not touch the version file",
        )
        expect(actual=len(runner.commands), expected=planning_commands)
        check(
            condition=not (root / ".stationer-journal.jsonl").exists(),
            reason="dry run must not create a journal",
        )


def test_execute_resume_skips_completed_actions() -> None:
    """A resumed run repeats only the actions missing from the journal."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        runner: FakeRunner = FakeRunner(tags=())
        engine: ReleaseEngine = _engine(root=root, runner=runner)
        plan: ReleasePlan = engine.plan(
            projects=("demo",),
            bump=Bump.PATCH,
        )
        engine.execute(plan=plan)
        ran: int = len(runner.commands)
        engine.execute(plan=plan, resume=True)
        expect(actual=len(runner.commands), expected=ran)


def test_execute_rejects_a_malformed_write_action() -> None:
    """A WRITE action without a path aborts with ``RuntimeError``."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        engine: ReleaseEngine = _engine(
            root=root,
            runner=FakeRunner(tags=()),
        )
        plan: ReleasePlan = ReleasePlan(
            projects=(),
            actions=(
                Action(
                    kind=ActionKind.WRITE,
                    description="broken write",
                ),
            ),
        )
        with pytest.raises(
            expected_exception=RuntimeError,
            match="Invalid write action",
        ):
            engine.execute(plan=plan)
