"""Planner tests."""

from pathlib import Path
from sys import executable
from tempfile import TemporaryDirectory

from tools.release_publish.config import Config, load_config
from tools.release_publish.models import Action, Bump, ReleasePlan
from tools.release_publish.planner import Planner

from .helpers import check, expect


def test_dependency_order_and_version_update() -> None:
    """Dependencies precede dependants and version writes are planned."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        (root / "a").mkdir()
        (root / "b").mkdir()
        (root / "a/pyproject.toml").write_text(
            data='[project]\nversion = "1.0.0"\n',
            encoding="utf-8",
        )
        (root / "release.toml").write_text(
            data='[repository]\nowner="o"\nname="r"\n[projects.b]\npath="b"\nversion="1.0.0"\ndepends_on=["a"]\n[projects.a]\npath="a"\nversion="1.0.0"\nversion_files=["a/pyproject.toml"]\n',
            encoding="utf-8",
        )
        config: Config = load_config(path=root / "release.toml")
        plan: ReleasePlan = Planner(config=config).plan(
            names=("b", "a"),
            bump=Bump.MINOR,
            explicit_version=None,
            publish=False,
        )
        expect(
            actual=tuple(item.project.name for item in plan.projects),
            expected=("a", "b"),
        )
        expect(actual=plan.projects[0].new_version, expected="1.1.0")


def _repo(root: Path, *, hooks: str = "") -> Path:
    """Create a one-project repository whose version lives in Python.

    Args:
        root: Repository root.
        hooks: Extra ``[release]`` TOML.

    Returns:
        The configuration path.

    """
    (root / "p").mkdir()
    (root / "p" / "product.py").write_text(
        data='class P:\n    version: Final[str] = "1.0.0"\n',
        encoding="utf-8",
    )
    path: Path = root / "release.toml"
    path.write_text(
        data=(
            '[repository]\nowner="o"\nname="r"\n'
            f"[release]\ncreate_github_release=false\n{hooks}\n"
            '[projects.demo]\npath="p"\nversion_files=["p/product.py"]\n'
        ),
        encoding="utf-8",
    )
    return path


def _commands(*, plan: ReleasePlan) -> list[tuple[str, ...]]:
    """Collect the non-empty commands from a plan.

    Args:
        plan: Release plan to inspect.

    Returns:
        Command argument tuples.

    """
    return [a.command for a in plan.actions if a.command]


def test_python_version_line_is_rewritten() -> None:
    """The annotated ``Product.version`` assignment is a valid target."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        config: Config = load_config(path=_repo(root=root))
        plan: ReleasePlan = Planner(config=config).plan(
            names=("demo",),
            bump=Bump.PATCH,
            explicit_version=None,
            publish=False,
        )
        write: Action = next(a for a in plan.actions if a.content is not None)
        check(
            condition='version: Final[str] = "1.0.1"' in (write.content or ""),
            reason="the version assignment must be rewritten",
        )
        expect(actual=plan.projects[0].tag, expected="demo/v1.0.1")


def test_only_version_files_are_staged_and_committed() -> None:
    """Untracked or unrelated project files never enter the release commit."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        plan: ReleasePlan = Planner(
            config=load_config(path=_repo(root=root)),
        ).plan(
            names=("demo",),
            bump=Bump.PATCH,
            explicit_version=None,
            publish=False,
        )
        commands: list[tuple[str, ...]] = _commands(plan=plan)
        check(
            condition=("git", "add", "--", "p/product.py") in commands,
            reason="the version file must be staged",
        )
        commit: tuple[str, ...] = next(
            c for c in commands if c[:2] == ("git", "commit")
        )
        expect(actual=commit[-2:], expected=("--", "p/product.py"))
        check(
            condition=("git", "add", "--", "p") not in commands,
            reason="the whole project directory must not be staged",
        )


def test_preflight_covers_staged_and_unstaged_changes() -> None:
    """The clean-tree guard compares the working tree and index to HEAD."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        plan: ReleasePlan = Planner(
            config=load_config(path=_repo(root=root)),
        ).plan(
            names=("demo",),
            bump=Bump.PATCH,
            explicit_version=None,
            publish=False,
        )
        expect(
            actual=_commands(plan=plan)[0],
            expected=(
                "git",
                "diff",
                "--quiet",
                "--exit-code",
                "HEAD",
            ),
        )


def test_hooks_expand_python_and_can_be_skipped() -> None:
    """``{python}`` becomes the running interpreter; hooks can be skipped."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        config: Config = load_config(
            path=_repo(
                root=root,
                hooks='pre_hooks=[["{python}","-m","pytest"]]',
            ),
        )
        planner: Planner = Planner(config=config)
        plan: ReleasePlan = planner.plan(
            names=("demo",),
            bump=Bump.PATCH,
            explicit_version=None,
            publish=False,
        )
        check(
            condition=(executable, "-m", "pytest") in _commands(plan=plan),
            reason="the hook must expand {python}",
        )
        skipped: ReleasePlan = planner.plan(
            names=("demo",),
            bump=Bump.PATCH,
            explicit_version=None,
            publish=False,
            skip_hooks=True,
        )
        check(
            condition=(executable, "-m", "pytest")
            not in _commands(plan=skipped),
            reason="skip_hooks must exclude hooks",
        )
