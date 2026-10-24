"""Command-line interface."""

from __future__ import annotations

from argparse import REMAINDER, ArgumentParser, Namespace
from argparse import Action as ArgparseAction
from importlib import import_module
from json import dumps
from pathlib import Path
from sys import argv as sys_argv
from tomllib import load as load_toml
from typing import TYPE_CHECKING, Final, TextIO, cast, final

from tools.stationer.config import load_config
from tools.stationer.engine import ReleaseEngine
from tools.stationer.models import Bump, Project, ReleasePlan
from tools.stationer.models import Constants as _GroupFinalConstants
from tools.stationer.planner import Planner

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from tools.stationer.config import Config
    from tools.stationer.models import Action


@final
class Constants:
    """Group module-level constants.

    Attributes:
        template: Grouped module constant.

    """

    template: Final[str] = (
        '[repository]\nprovider = "github"\nowner = "OWNER"\nname = '
        '"REPOSITORY"\ndefault_branch = "main"\nvisibility = '
        '"private"\n\n[release]\ntag_template = "{project}'
        '/v{version}"\npublish_pypi = true\ncreate_github_release = '
        'true\n\n[projects.root]\npath = "."\nversion = "0.1.0"\n'
        'version_files = ["pyproject.toml"]\npython_distribution = true\n'
    )


def _stdout() -> TextIO:
    """Return the live ``sys.stdout`` stream.

    Returns:
        The current standard output object, resolved per call so output
        capture applies.

    """
    return import_module(name="sys").stdout


def _stderr() -> TextIO:
    """Return the live ``sys.stderr`` stream.

    Returns:
        The current standard error object, resolved per call so output
        capture applies.

    """
    return import_module(name="sys").stderr


def _parser() -> ArgumentParser:
    """Build the argument parser.

    Returns:
        The configured argument parser.

    """
    parser: ArgumentParser = ArgumentParser(
        prog="stationer",
        description=(
            "Repository, monorepo, release, and package publishing "
            "control plane."
        ),
    )
    _ = parser.add_argument(
        "--config",
        type=Path,
        default=Path(_GroupFinalConstants.config_name),
    )
    add_subparser: Callable[..., ArgumentParser] = parser.add_subparsers(
        dest="command",
        required=True,
    ).add_parser
    _ = add_subparser("init", help="Create a starter release.toml")
    _ = add_subparser("status", help="Show configured projects")
    show: ArgumentParser = add_subparser(
        "show",
        help="Print one project's release metadata as JSON",
    )
    _ = show.add_argument("--project", required=True)
    plan: ArgumentParser = add_subparser(
        "plan",
        help="Compile and print a release plan",
    )
    release: ArgumentParser = add_subparser(
        "release",
        help="Compile and execute a release plan",
    )
    for command in (plan, release):
        _ = command.add_argument(
            "--project",
            action="append",
            dest="projects",
        )
        select: Callable[..., ArgparseAction] = (
            command.add_mutually_exclusive_group().add_argument
        )
        _ = select(
            "--all",
            action="store_true",
            dest="all_projects",
        )
        _ = select("--changed", action="store_true")
        group: Callable[..., ArgparseAction] = (
            command.add_mutually_exclusive_group().add_argument
        )
        _ = group(
            "--bump",
            choices=tuple(Bump),
            default=Bump.PATCH,
            type=Bump,
        )
        _ = group("--version")
        _ = command.add_argument("--no-publish", action="store_true")
        _ = command.add_argument("--skip-hooks", action="store_true")
    _ = release.add_argument("--dry-run", action="store_true")
    _ = release.add_argument("--resume", action="store_true")
    publish: ArgumentParser = add_subparser(
        "publish",
        help="Create the hosted release for a project's pushed tag",
    )
    _ = publish.add_argument("--project", required=True)
    _ = publish.add_argument(
        "--artifacts",
        type=Path,
        nargs="+",
        required=True,
        help="Files, or directories whose files, to attach",
    )
    _ = publish.add_argument(
        "--update",
        action="store_true",
        help="Upload to an existing release instead of creating one",
    )
    _ = publish.add_argument("--dry-run", action="store_true")
    sync: ArgumentParser = add_subparser(
        "repo-sync",
        help="Create/update the configured GitHub repository",
    )
    _ = sync.add_argument("--dry-run", action="store_true")
    partition: ArgumentParser = add_subparser(
        "partition",
        help="Content-addressed partition store operations",
    )
    _ = partition.add_argument("partition_args", nargs=REMAINDER)
    return parser


def _print_plan(*, plan: ReleasePlan) -> None:
    """Print planned version changes and every action.

    Args:
        plan: Release plan to display.

    """
    for item in plan.projects:
        _ = _stdout().write(
            f"{item.project.name} {item.old_version} -> {item.new_version}\n",
        )
    for action in plan.actions:
        line: str = (
            " ".join(action.command) if action.command else action.description
        )
        _ = _stdout().write(f"{line}\n")


def _selected_names(*, args: Namespace, config: Config) -> tuple[str, ...]:
    """Resolve the project selection from parsed arguments.

    Args:
        args: Parsed command-line arguments.
        config: Loaded release configuration.

    Returns:
        The selected project names.

    Raises:
        ValueError: If no project selection is provided.

    """
    engine: ReleaseEngine = ReleaseEngine(config=config)
    names: tuple[str, ...] = (
        engine.changed_projects()
        if args.changed
        else tuple(project.name for project in config.projects)
        if args.all_projects
        else tuple(args.projects or ())
    )
    if not names:
        msg: str = "Select --project NAME or --all"
        raise ValueError(msg)
    return names


def _run(*, args: Namespace, config_path: Path) -> int:
    """Run the selected subcommand.

    Args:
        args: Parsed command-line arguments.
        config_path: Path of the release configuration.

    Returns:
        Process exit code.

    Raises:
        ValueError: If the selection or plan is invalid.

    """
    config: Config = load_config(path=config_path)
    if args.command == "status":
        for project in config.projects:
            _ = _stdout().write(f"{project.name} {project.version}\n")
        return 0
    if args.command == "show":
        shown: Project = config.project(name=args.project)
        _ = _stdout().write(
            dumps(
                obj={
                    "project": shown.name,
                    "path": str(object=shown.path),
                    "version": shown.version,
                    "tag": config.release.tag_template.format(
                        project=shown.name,
                        version=shown.version,
                    ),
                },
            )
            + "\n",
        )
        return 0
    engine: ReleaseEngine = ReleaseEngine(config=config)
    if args.command == "repo-sync":
        actions: tuple[Action, ...] = Planner(
            config=config,
        ).github.sync_repository(
            config=config.repository,
            root=config.root,
        )
        repo_plan: ReleasePlan = ReleasePlan(projects=(), actions=actions)
        engine.execute(plan=repo_plan, dry_run=args.dry_run)
        return 0
    if args.command == "publish":
        publish_plan: ReleasePlan = engine.publish_plan(
            project=args.project,
            artifacts=tuple(args.artifacts),
            update=args.update,
        )
        _print_plan(plan=publish_plan)
        engine.execute(plan=publish_plan, dry_run=args.dry_run)
        return 0
    names: tuple[str, ...] = _selected_names(args=args, config=config)
    release_plan: ReleasePlan = engine.plan(
        projects=names,
        bump=None if args.version else args.bump,
        version=args.version,
        publish=not args.no_publish,
        skip_hooks=args.skip_hooks,
    )
    _print_plan(plan=release_plan)
    if args.command == "plan":
        return 0
    engine.execute(
        plan=release_plan,
        dry_run=args.dry_run,
        resume=args.resume,
    )
    return 0


type JsonValue = (
    str | int | float | bool | list[JsonValue] | dict[str, JsonValue]
)


def _defaults_table(
    *,
    start: Path,
) -> tuple[dict[str, JsonValue], Path] | None:
    """Read ``[tool.stationer.publish]`` from the nearest ``pyproject.toml``.

    Args:
        start: Directory the ``pyproject.toml`` search starts at.

    Returns:
        The table and the directory containing it, or ``None``.

    """
    for directory in (start, *start.parents):
        pyproject: Path = directory / "pyproject.toml"
        if not pyproject.is_file():
            continue
        with pyproject.open(mode="rb") as stream:
            data: dict[str, object] = cast(
                typ="dict[str, object]",
                val=load_toml(stream),
            )
        tool: object = data.get("tool", {})
        if not isinstance(tool, dict):
            continue
        stationer: object = cast(
            typ="dict[str, object]",
            val=tool,
        ).get("stationer")
        if not isinstance(stationer, dict):
            continue
        raw: object = cast(
            typ="dict[str, object]",
            val=stationer,
        ).get("publish")
        if isinstance(raw, dict):
            return cast(typ="dict[str, JsonValue]", val=raw), directory
    return None


def _string_list(
    *,
    table: dict[str, JsonValue],
    key: str,
    default: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Return a validated list-of-strings key from the table.

    Args:
        table: Loaded ``[tool.stationer.publish]`` table.
        key: List key.
        default: Value used when the key is absent.

    Returns:
        The string values.

    Raises:
        TypeError: If the value is not a list of strings.

    """
    value: object = table.get(key, list(default))
    if not isinstance(value, list):
        msg: str = f"[tool.stationer.publish] {key} must be a list of strings"
        raise TypeError(msg)
    entries: list[object] = cast(typ="list[object]", val=value)
    items: list[str] = [item for item in entries if isinstance(item, str)]
    if len(items) != len(entries):
        msg = f"[tool.stationer.publish] {key} must be a list of strings"
        raise TypeError(msg)
    return tuple(items)


def _flag(*, table: dict[str, JsonValue], key: str) -> bool:
    """Return a validated boolean key from the defaults table.

    Args:
        table: Loaded ``[tool.stationer.publish]`` table.
        key: Boolean key.

    Returns:
        The flag value, defaulting to ``False``.

    Raises:
        TypeError: If the value is not a boolean.

    """
    value: object = table.get(key, False)
    if not isinstance(value, bool):
        msg: str = f"[tool.stationer.publish] {key} must be a boolean"
        raise TypeError(msg)
    return value


def _command(
    *,
    table: dict[str, JsonValue],
    root: Path,
) -> tuple[str, list[str]]:
    """Validate ``command``/``config`` and start the argument list.

    Args:
        table: Loaded ``[tool.stationer.publish]`` table.
        root: Directory containing ``pyproject.toml``.

    Returns:
        The command name and the ``["--config", path, command]`` prefix.

    Raises:
        TypeError: If ``config`` is not a string.
        ValueError: If ``command`` names an unknown subcommand.

    """
    config: object = table.get("config", "release.toml")
    if not isinstance(config, str):
        msg: str = "[tool.stationer.publish] config must be a string"
        raise TypeError(msg)
    command: object = table.get("command", "release")
    if not isinstance(command, str) or command not in {
        "init",
        "status",
        "show",
        "plan",
        "release",
        "publish",
        "repo-sync",
    }:
        msg = f"[tool.stationer.publish] unknown command: {command!r}"
        raise ValueError(msg)
    return command, ["--config", str(object=root / config), command]


def _projects(*, table: dict[str, JsonValue]) -> tuple[str, ...]:
    """Return the configured project names.

    Args:
        table: Loaded ``[tool.stationer.publish]`` table.

    Returns:
        ``projects`` plus the optional singular ``project``.

    """
    projects: tuple[str, ...] = _string_list(table=table, key="projects")
    project: object = table.get("project")
    if isinstance(project, str):
        projects = (*projects, project)
    return projects


def _publish_argv(
    *,
    table: dict[str, JsonValue],
    root: Path,
    argv: list[str],
) -> tuple[str, ...]:
    """Finish the argument list for the ``publish`` subcommand.

    Args:
        table: Loaded ``[tool.stationer.publish]`` table.
        root: Directory containing ``pyproject.toml``.
        argv: Argument list started with ``--config`` and ``publish``.

    Returns:
        The completed arguments.

    """
    for name, key in (("update", "--update"), ("dry_run", "--dry-run")):
        if _flag(table=table, key=name):
            argv.append(key)
    artifacts: tuple[str, ...] = _string_list(table=table, key="artifacts")
    if artifacts:
        argv.append("--artifacts")
        argv.extend(str(object=root / item) for item in artifacts)
    return tuple(argv)


def _release_argv(
    *,
    table: dict[str, JsonValue],
    command: str,
    argv: list[str],
) -> tuple[str, ...]:
    """Finish the argument list for ``plan`` and ``release``.

    Args:
        table: Loaded ``[tool.stationer.publish]`` table.
        command: ``plan`` or ``release``.
        argv: Argument list started with ``--config`` and the command.

    Returns:
        The completed arguments.

    Raises:
        TypeError: If ``version`` is not a string.

    """
    keys: tuple[tuple[str, str], ...] = (
        ("all_projects", "--all"),
        ("changed", "--changed"),
        ("no_publish", "--no-publish"),
        ("skip_hooks", "--skip-hooks"),
    )
    if command == "release":
        keys = (*keys, ("dry_run", "--dry-run"), ("resume", "--resume"))
    for key, name in keys:
        if _flag(table=table, key=key):
            argv.append(name)
    for project in _projects(table=table):
        argv.extend(("--project", project))
    version: object = table.get("version")
    bump: object = table.get("bump")
    if version is not None:
        if not isinstance(version, str):
            msg: str = "[tool.stationer.publish] version must be a string"
            raise TypeError(msg)
        argv.extend(("--version", version))
    elif isinstance(bump, str):
        argv.extend(("--bump", bump))
    return tuple(argv)


def _configured_argv(*, start: Path) -> tuple[str, ...] | None:
    """Synthesize command-line arguments from ``[tool.stationer.publish]``.

    The table mirrors the CLI: ``command`` selects the subcommand
    (``release`` by default), ``projects``/``all``/``changed`` select the
    targets, ``bump``/``version`` choose the version change, and the
    remaining booleans map to the same-named flags; ``artifacts`` and
    ``update`` serve the ``publish`` subcommand.

    Args:
        start: Directory the ``pyproject.toml`` search starts at.

    Returns:
        Synthesized arguments, or ``None`` when no table is found.

    Raises:
        TypeError: If a key has the wrong type.
        ValueError: If the command is unknown or a project is missing.

    """
    found: tuple[dict[str, JsonValue], Path] | None = _defaults_table(
        start=start,
    )
    if found is None:
        return None
    table, root = found
    argv: list[str]
    command, argv = _command(table=table, root=root)
    projects: tuple[str, ...] = _projects(table=table)
    if command in {"init", "status"}:
        return tuple(argv)
    if command == "repo-sync":
        if _flag(table=table, key="dry_run"):
            argv.append("--dry-run")
        return tuple(argv)
    if command in {"show", "publish"}:
        if not projects:
            msg: str = f"[tool.stationer.publish] {command} needs `project`"
            raise ValueError(msg)
        argv.extend(("--project", projects[0]))
    if command == "publish":
        return _publish_argv(table=table, root=root, argv=argv)
    if command == "show":
        return tuple(argv)
    return _release_argv(table=table, command=command, argv=argv)


def main(*, arguments: tuple[str, ...] | None = None) -> int:
    """Run the CLI.

    Args:
        arguments: Optional command-line arguments. When invoked with no
            arguments at all, ``[tool.stationer.publish]`` in the nearest
            ``pyproject.toml`` supplies them.

    Returns:
        Process exit code.

    Raises:
        FileExistsError: If ``init`` finds an existing configuration file.

    """
    argv: tuple[str, ...] | None = arguments
    if argv is None:
        argv = tuple(sys_argv[1:])
    if not argv:
        try:
            argv = _configured_argv(start=Path.cwd())
        except (TypeError, ValueError) as error:
            _ = _stderr().write(f"{error}\n")
            return 2
        if argv is None:
            _ = _stderr().write(
                "no arguments given and no [tool.stationer.publish] table "
                "found in pyproject.toml\n",
            )
            return 2
    args: Namespace = _parser().parse_args(args=argv)
    if args.command == "partition":
        partition_cli: ModuleType = import_module(
            name="tools.stationer.partition.cli",
        )
        return cast(
            typ="int",
            val=partition_cli.main(
                arguments=tuple(args.partition_args),
            ),
        )
    config_path: Path = args.config
    if args.command == "init":
        if config_path.exists():
            raise FileExistsError(config_path)
        config_path.write_text(data=Constants.template, encoding="utf-8")
        return 0
    try:
        return _run(args=args, config_path=config_path)
    except (KeyError, ValueError) as error:
        _ = _stderr().write(f"{error}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
