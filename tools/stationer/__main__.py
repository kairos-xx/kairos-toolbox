# Copyright (c) 2026 Project contributors.
"""Build and publish releases: ``python -m tools.stationer``.

Works both as ``python -m tools.stationer`` and as ``python ./tools/stationer``
— the latter puts the package directory on ``sys.path`` instead of the
repository root, so it is added back here.

Building is the default mode: ``python -m tools.stationer --project …``
runs the cross-platform release builder (an explicit ``build`` word is
accepted but optional), and a bare run executes the pipeline described by
``[tool.stationer.builder]``. A publish command — ``init``, ``status``,
``show``, ``plan``, ``release``, ``publish`` or ``repo-sync`` — selects the
release control plane instead; ``python -m tools.stationer.cli`` with no
arguments synthesizes one from ``[tool.stationer.publish]``.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from sys import argv as sys_argv
from sys import path as sys_path
from typing import TYPE_CHECKING, Final, final

if TYPE_CHECKING:
    from types import ModuleType


@final
class Constants:
    """Group module-level constants.

    Attributes:
        publish_commands: Grouped module constant.
        value_flags: Grouped module constant.

    """

    publish_commands: Final[frozenset[str]] = frozenset(
        {"init", "status", "show", "plan", "release", "publish", "repo-sync"},
    )
    value_flags: Final[frozenset[str]] = frozenset(
        {
            "--arch",
            "--artifacts",
            "--bind",
            "--bump",
            "--config",
            "--expose",
            "--format",
            "--manifest",
            "--output",
            "--platform",
            "--port",
            "--project",
            "--publish",
            "--python",
            "--release-config",
            "--remote",
            "--version",
            "--vm",
            "--vm-identity",
            "--vm-port",
            "--vm-user",
            "--worker-host",
        },
    )


def _publishes(*, arguments: tuple[str, ...]) -> bool:
    """Return whether the arguments select the release control plane.

    The publish CLI's subcommand is its first bare positional; every other
    bare token is the value of the flag that precedes it.

    Args:
        arguments: Command-line arguments after the module name.

    Returns:
        Whether the first bare token is a publish subcommand.

    """
    skip: bool = False
    for token in arguments:
        if skip:
            skip = False
            continue
        if token in Constants.value_flags:
            skip = True
            continue
        if token.startswith("-"):
            continue
        return token in Constants.publish_commands
    return False


def main() -> None:
    """Run the command-line interface.

    Raises:
        SystemExit: Always, with the CLI's exit status.

    """
    root: Path = Path(__file__).resolve().parents[2]
    if str(object=root) not in sys_path:
        sys_path.insert(0, str(object=root))
    arguments: tuple[str, ...] = tuple(sys_argv[1:])
    module: ModuleType
    if arguments[:1] == ("build",):
        module = import_module(name="tools.stationer.builder")
        raise SystemExit(module.Cli.main(arguments=arguments[1:]))
    if _publishes(arguments=arguments):
        module = import_module(name="tools.stationer.cli")
        raise SystemExit(module.main(arguments=arguments))
    module = import_module(name="tools.stationer.builder")
    raise SystemExit(module.Cli.main(arguments=arguments))


if __name__ == "__main__":
    main()
