# Copyright (c) 2026 Project contributors.
"""Front end for ``lake --make-release`` (maintainers, source checkouts)."""

from __future__ import annotations

from pathlib import Path
from re import compile as compile_pattern
from shutil import which
from sys import executable, stdout
from typing import TYPE_CHECKING, Final, TextIO

from .process import run

if TYPE_CHECKING:
    from re import Pattern
    from subprocess import CompletedProcess


class ReleaseConstants:
    """Group release front-end constants.

    Attributes:
        project: Project name in ``release.toml``.
        project_file: Marker proving a checkout contains the project.
        config_file: ``release_publish`` configuration at the repository root.
        keywords: Bump keywords understood by ``release_publish``.
        flags: Accepted command-line switches.
        usage: Command usage text.
        semver: Pattern for an explicit ``X.Y.Z`` argument.

    """

    project: Final[str] = "lake-terminal"
    project_file: Final[str] = "tools/lake_terminal/pyproject.toml"
    config_file: Final[str] = "release.toml"
    keywords: Final[tuple[str, ...]] = ("patch", "minor", "major")
    flags: Final[frozenset[str]] = frozenset(
        {"--dry-run", "--no-verify", "--watch"},
    )
    usage: Final[str] = (
        "usage: lake --make-release [patch|minor|major|X.Y.Z] "
        "[--dry-run] [--no-verify] [--watch]\n"
    )
    semver: Final[Pattern[str]] = compile_pattern(pattern=r"\d+\.\d+\.\d+")


def execute(
    *,
    args: tuple[str, ...],
    root: Path,
    capture: bool = False,
) -> CompletedProcess[str]:
    """Run one command inside the checkout without a shell.

    Args:
        args: Argument vector.
        root: Working directory.
        capture: Whether to capture output.

    Returns:
        The completed process.

    """
    return run(args=args, check=False, capture_output=capture, cwd=root)


def repo_root(*, anchor: Path | None = None) -> Path | None:
    """Return the git checkout that can cut releases, if any.

    A releasable checkout contains this project and the repository's
    ``release.toml``.

    Args:
        anchor: Module path to start from; defaults to this file.

    Returns:
        Repository root, or None outside a source checkout.

    """
    start: Path = (anchor if anchor is not None else Path(__file__)).parent
    try:
        result: CompletedProcess[str] = run(
            args=("git", "rev-parse", "--show-toplevel"),
            check=False,
            capture_output=True,
            cwd=start,
        )
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    root: Path = Path(result.stdout.strip())
    required: tuple[str, ...] = (
        ReleaseConstants.project_file,
        ReleaseConstants.config_file,
    )
    if not all((root / name).is_file() for name in required):
        return None
    return root


def release_command(
    *,
    root: Path,
    arg: str = "patch",
    dry_run: bool = False,
    verify: bool = True,
) -> tuple[str, ...]:
    """Build the ``release_publish`` invocation for this project.

    Version bumping, tag guards, the commit, the tag and the push are all
    ``release_publish`` behaviour; pushing the tag lets CI build and publish
    the archives, so nothing is published from this machine.

    Args:
        root: Repository root.
        arg: ``patch``, ``minor``, ``major`` or ``X.Y.Z``.
        dry_run: Print the plan without changing anything.
        verify: Run the hooks configured in ``release.toml`` (the tests).

    Returns:
        The argument vector.

    Raises:
        ValueError: When ``arg`` is neither a keyword nor ``X.Y.Z``.

    """
    if arg in ReleaseConstants.keywords:
        selection: tuple[str, ...] = ("--bump", arg)
    elif ReleaseConstants.semver.fullmatch(string=arg):
        selection = ("--version", arg)
    else:
        msg: str = f"{arg!r} is not patch, minor, major or X.Y.Z."
        raise ValueError(msg)
    return (
        executable,
        "-m",
        "tools.release_publish",
        "--config",
        str(object=root / ReleaseConstants.config_file),
        "release",
        "--project",
        ReleaseConstants.project,
        *selection,
        "--no-publish",
        *(("--dry-run",) if dry_run else ()),
        *(() if verify else ("--skip-hooks",)),
    )


def run_cli(
    *,
    arguments: tuple[str, ...],
    anchor: Path | None = None,
    stream: TextIO | None = None,
) -> int:
    """Parse ``--make-release`` arguments and run the release.

    Args:
        arguments: Arguments after ``--make-release``.
        anchor: Module path used to locate the checkout.
        stream: Message destination; stdout by default.

    Returns:
        Exit status; ``release_publish``'s status when it runs.

    """
    out: TextIO = stream if stream is not None else stdout
    options: tuple[str, ...] = tuple(a for a in arguments if a.startswith("-"))
    positional: tuple[str, ...] = tuple(
        a for a in arguments if not a.startswith("-")
    )
    chosen: str = positional[0] if positional else "patch"
    if (
        len(positional) > 1
        or not set(options) <= ReleaseConstants.flags
        or not (
            chosen in ReleaseConstants.keywords
            or ReleaseConstants.semver.fullmatch(string=chosen)
        )
    ):
        _ = out.write(ReleaseConstants.usage)
        return 2
    root: Path | None = repo_root(anchor=anchor)
    if root is None:
        _ = out.write(
            "--make-release is only available from a source checkout.\n",
        )
        return 2
    status: int = execute(
        args=release_command(
            root=root,
            arg=chosen,
            dry_run="--dry-run" in options,
            verify="--no-verify" not in options,
        ),
        root=root,
    ).returncode
    if status == 0 and "--watch" in options and which(cmd="gh") is not None:
        return execute(
            args=("gh", "run", "watch", "--exit-status"),
            root=root,
        ).returncode
    return status
