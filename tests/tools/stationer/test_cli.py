"""Command-line tests."""

from json import loads
from pathlib import Path
from subprocess import run
from tempfile import TemporaryDirectory

import pytest

from tools.stationer import cli

from .helpers import check, expect


def _repo(*, root: Path) -> Path:
    """Create a minimal repository with a release configuration.

    Args:
        root: Repository root to populate.

    Returns:
        The configuration path.

    """
    command: tuple[str, ...] = ("git", "init", "-q", str(object=root))
    _ = run(args=command, check=True)
    (root / "p").mkdir()
    (root / "p" / "product.py").write_text(
        data='class P:\n    version: Final[str] = "1.0.0"\n',
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


def test_status_lists_projects(capsys: pytest.CaptureFixture[str]) -> None:
    """``status`` prints each project, its version and tag template.

    Args:
        capsys: Pytest output capture fixture.

    """
    with TemporaryDirectory() as directory:
        config: Path = _repo(root=Path(directory))
        expect(
            actual=cli.main(
                arguments=("--config", str(object=config), "status"),
            ),
            expected=0,
        )
    check(
        condition="demo 1.0.0" in capsys.readouterr().out,
        reason="status must list the project and its version",
    )


def test_show_prints_json_for_automation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``show`` gives CI the project path, version and current tag.

    Args:
        capsys: Pytest output capture fixture.

    """
    with TemporaryDirectory() as directory:
        config: Path = _repo(root=Path(directory))
        code: int = cli.main(
            arguments=(
                "--config",
                str(object=config),
                "show",
                "--project",
                "demo",
            ),
        )
    expect(actual=code, expected=0)
    data: dict[str, object] = loads(s=capsys.readouterr().out)
    expect(
        actual=data,
        expected={
            "project": "demo",
            "path": "p",
            "version": "1.0.0",
            "tag": "demo/v1.0.0",
        },
    )


def test_plan_and_dry_run_print_every_action(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Plans are visible, and a dry run changes nothing.

    Args:
        capsys: Pytest output capture fixture.

    """
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        config: Path = _repo(root=root)
        base: tuple[str, ...] = ("--config", str(object=config))
        expect(
            actual=cli.main(
                arguments=(*base, "plan", "--project", "demo"),
            ),
            expected=0,
        )
        planned: str = capsys.readouterr().out
        dry_run: tuple[str, ...] = (
            *base,
            "release",
            "--project",
            "demo",
            "--dry-run",
            "--no-publish",
        )
        expect(actual=cli.main(arguments=dry_run), expected=0)
        dry: str = capsys.readouterr().out
        check(
            condition='"1.0.0"'
            in (root / "p" / "product.py").read_text(encoding="utf-8"),
            reason="a dry run must not rewrite the version file",
        )
    for output in (planned, dry):
        check(
            condition="demo 1.0.0 -> 1.0.1" in output,
            reason="the version change must be printed",
        )
        check(
            condition="git tag -a demo/v1.0.1" in output,
            reason="the tag command must be printed",
        )


def test_errors_become_exit_codes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Validation problems print a message and return a status.

    Args:
        capsys: Pytest output capture fixture.

    """
    with TemporaryDirectory() as directory:
        config: Path = _repo(root=Path(directory))
        code: int = cli.main(
            arguments=(
                "--config",
                str(object=config),
                "plan",
                "--project",
                "nope",
            ),
        )
    expect(actual=code, expected=2)
    check(
        condition="Unknown project" in capsys.readouterr().err,
        reason="the error must name the problem",
    )


def _released_repo(*, root: Path, version: str = "1.0.0") -> Path:
    """Create a repository with two release tags and built artifacts.

    Args:
        root: Repository root to populate.
        version: Version written to the version file.

    Returns:
        The configuration path.

    """
    config: Path = _repo(root=root)
    (root / "p" / "product.py").write_text(
        data=f'class P:\n    version: Final[str] = "{version}"\n',
        encoding="utf-8",
    )
    env_args: tuple[str, ...] = (
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.test",
    )
    for command in (
        ("git", *env_args, "add", "."),
        ("git", *env_args, "commit", "-q", "-m", "seed"),
        ("git", *env_args, "tag", "demo/v0.9.0"),
        ("git", *env_args, "tag", f"demo/v{version}"),
    ):
        _ = run(args=command, check=True, cwd=root)
    (root / "dist").mkdir()
    (root / "dist" / "b.pyz").write_bytes(data=b"b")
    (root / "dist" / "a.pyz").write_bytes(data=b"a")
    return config


def test_publish_plans_a_release_for_the_existing_tag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``publish`` attaches the built files to the project's current tag.

    Args:
        capsys: Pytest output capture fixture.

    """
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        config: Path = _released_repo(root=root)
        code: int = cli.main(
            arguments=(
                "--config",
                str(object=config),
                "publish",
                "--project",
                "demo",
                "--artifacts",
                str(object=root / "dist"),
                "--dry-run",
            ),
        )
        output: str = capsys.readouterr().out
    expect(actual=code, expected=0)
    check(
        condition="gh release create demo/v1.0.0" in output,
        reason=output,
    )
    check(condition="--generate-notes" in output, reason=output)
    check(
        condition="--notes-start-tag demo/v0.9.0" in output,
        reason=output,
    )
    check(condition="--prerelease" not in output, reason=output)
    check(
        condition=output.index("a.pyz") < output.index("b.pyz"),
        reason="artifacts are listed in a stable order",
    )


def test_publish_marks_prerelease_versions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A ``-rc`` version becomes a GitHub prerelease.

    Args:
        capsys: Pytest output capture fixture.

    """
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        config: Path = _released_repo(root=root, version="1.0.0-rc.1")
        code: int = cli.main(
            arguments=(
                "--config",
                str(object=config),
                "publish",
                "--project",
                "demo",
                "--artifacts",
                str(object=root / "dist"),
                "--dry-run",
            ),
        )
        output: str = capsys.readouterr().out
    expect(actual=code, expected=0)
    check(condition="--prerelease" in output, reason=output)


def test_publish_requires_a_pushed_tag_and_artifacts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing tags or files are reported instead of half-publishing.

    Args:
        capsys: Pytest output capture fixture.

    """
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        config: Path = _repo(root=root)
        (root / "dist").mkdir()
        base: tuple[str, ...] = (
            "--config",
            str(object=config),
            "publish",
            "--project",
            "demo",
        )
        no_tag: int = cli.main(
            arguments=(*base, "--artifacts", str(object=root / "dist")),
        )
        no_tag_error: str = capsys.readouterr().err
        released: Path = _released_repo(root=root / "second")
        (root / "second" / "empty").mkdir()
        no_files: int = cli.main(
            arguments=(
                "--config",
                str(object=released),
                "publish",
                "--project",
                "demo",
                "--artifacts",
                str(object=root / "second" / "empty"),
            ),
        )
        no_files_error: str = capsys.readouterr().err
    expect(actual=no_tag, expected=2)
    check(condition="does not exist" in no_tag_error, reason=no_tag_error)
    expect(actual=no_files, expected=2)
    check(condition="no files" in no_files_error, reason=no_files_error)


def test_bare_invocation_uses_tool_stationer_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``[tool.stationer.publish]`` supplies the bare-invocation arguments.

    Args:
        tmp_path: Pytest temporary directory acting as repository root.
        monkeypatch: Pytest monkeypatch fixture.
        capsys: Pytest output capture fixture.

    """
    _ = _repo(root=tmp_path)
    _ = (tmp_path / "pyproject.toml").write_text(
        data=(
            '[tool.stationer.publish]\ncommand = "status"\n'
            'config = "release.toml"\n'
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(path=tmp_path)
    expect(actual=cli.main(arguments=()), expected=0)
    check(
        condition="demo 1.0.0" in capsys.readouterr().out,
        reason="the configured command must run",
    )


def test_bare_invocation_without_a_table_returns_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No arguments and no ``[tool.stationer.publish]`` table is an error.

    Args:
        tmp_path: Pytest temporary directory without a pyproject.
        monkeypatch: Pytest monkeypatch fixture.
        capsys: Pytest output capture fixture.

    """
    monkeypatch.chdir(path=tmp_path)
    expect(actual=cli.main(arguments=()), expected=2)
    check(
        condition="tool.stationer.publish" in capsys.readouterr().err,
        reason="the error must name the missing table",
    )


def test_bare_invocation_rejects_bad_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An invalid ``[tool.stationer.publish]`` value is a usage error.

    Args:
        tmp_path: Pytest temporary directory acting as repository root.
        monkeypatch: Pytest monkeypatch fixture.

    """
    _ = (tmp_path / "pyproject.toml").write_text(
        data='[tool.stationer.publish]\ncommand = "explode"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(path=tmp_path)
    expect(actual=cli.main(arguments=()), expected=2)


def _configured_repo(*, root: Path, table: str) -> None:
    """Create a repo fixture plus a ``[tool.stationer.publish]`` table.

    Args:
        root: Repository root to populate.
        table: Extra ``[tool.stationer.publish]`` table rows.

    """
    _ = _repo(root=root)
    _ = (root / "pyproject.toml").write_text(
        data=('[tool.stationer.publish]\nconfig = "release.toml"\n' + table),
        encoding="utf-8",
    )


def test_configured_release_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A configured ``release`` dry run plans without side effects.

    Args:
        tmp_path: Pytest temporary directory acting as repository root.
        monkeypatch: Pytest monkeypatch fixture.
        capsys: Pytest output capture fixture.

    """
    _configured_repo(
        root=tmp_path,
        table=(
            'command = "release"\nprojects = ["demo"]\nbump = "minor"\n'
            "dry_run = true\nresume = true\nno_publish = true\n"
            "skip_hooks = true\n"
        ),
    )
    monkeypatch.chdir(path=tmp_path)
    expect(actual=cli.main(arguments=()), expected=0)
    check(
        condition="demo 1.0.0 -> 1.1.0" in capsys.readouterr().out,
        reason="the configured bump must reach the plan",
    )


def test_configured_version_and_single_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``version`` plus the singular ``project`` key reach the plan.

    Args:
        tmp_path: Pytest temporary directory acting as repository root.
        monkeypatch: Pytest monkeypatch fixture.

    """
    _configured_repo(
        root=tmp_path,
        table=('command = "plan"\nproject = "demo"\nversion = "9.9.9"\n'),
    )
    monkeypatch.chdir(path=tmp_path)
    expect(actual=cli.main(arguments=()), expected=0)


def test_configured_show_and_repo_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``show``/``repo-sync`` are configurable bare commands.

    Args:
        tmp_path: Pytest temporary directory acting as repository root.
        monkeypatch: Pytest monkeypatch fixture.

    """
    _configured_repo(
        root=tmp_path,
        table='command = "show"\nproject = "demo"\n',
    )
    monkeypatch.chdir(path=tmp_path)
    expect(actual=cli.main(arguments=()), expected=0)
    _ = (tmp_path / "pyproject.toml").write_text(
        data=(
            '[tool.stationer.publish]\nconfig = "release.toml"\n'
            'command = "repo-sync"\ndry_run = true\n'
        ),
        encoding="utf-8",
    )
    expect(actual=cli.main(arguments=()), expected=0)


def test_configured_publish_reports_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``publish`` artifacts/update flags reach the publish plan.

    Args:
        tmp_path: Pytest temporary directory acting as repository root.
        monkeypatch: Pytest monkeypatch fixture.

    """
    (tmp_path / "dist").mkdir()
    _ = (tmp_path / "dist" / "a.whl").write_text(
        data="wheel",
        encoding="utf-8",
    )
    _configured_repo(
        root=tmp_path,
        table=(
            'command = "publish"\nproject = "demo"\n'
            'artifacts = ["dist"]\nupdate = true\ndry_run = true\n'
        ),
    )
    monkeypatch.chdir(path=tmp_path)
    code: int = cli.main(arguments=())
    check(
        condition=code in {0, 2},
        reason="publish fails only when the tag or git state disagrees",
    )


def test_configured_release_without_projects_returns_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``release`` with no project selection is a usage error.

    Args:
        tmp_path: Pytest temporary directory acting as repository root.
        monkeypatch: Pytest monkeypatch fixture.

    """
    _configured_repo(
        root=tmp_path,
        table='command = "release"\ndry_run = true\n',
    )
    monkeypatch.chdir(path=tmp_path)
    expect(actual=cli.main(arguments=()), expected=2)


def test_configured_table_type_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrong value types in the table surface as exit status 2.

    Args:
        tmp_path: Pytest temporary directory acting as repository root.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.chdir(path=tmp_path)
    for table in (
        "[tool.stationer.publish]\nconfig = 1\n",
        '[tool.stationer.publish]\nprojects = "demo"\n',
        '[tool.stationer.publish]\ndry_run = "yes"\n',
        '[tool.stationer.publish]\nversion = 3\ncommand = "plan"\n'
        'projects = ["demo"]\n',
    ):
        _ = (tmp_path / "pyproject.toml").write_text(
            data=table,
            encoding="utf-8",
        )
        expect(actual=cli.main(arguments=()), expected=2)
