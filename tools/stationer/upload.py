"""Safe package-registry upload helper."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from pathlib import Path
from subprocess import run


def main(*, arguments: tuple[str, ...] | None = None) -> int:
    """Upload every built distribution without shell glob expansion.

    Args:
        arguments: Optional command-line arguments.

    Returns:
        Process exit code.

    Raises:
        FileNotFoundError: If no distributions exist in ``directory``.

    """
    parser: ArgumentParser = ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--repository-url")
    args: Namespace = parser.parse_args(args=arguments)
    artifacts: tuple[str, ...] = tuple(
        sorted(
            str(object=path)
            for path in args.directory.rglob(pattern="*")
            if path.suffix in {".whl", ".gz", ".zip"}
        ),
    )
    if not artifacts:
        msg: str = f"No distributions in {args.directory}"
        raise FileNotFoundError(msg)
    command: list[str] = ["python", "-m", "twine", "upload"]
    if args.repository_url is not None:
        command.extend(("--repository-url", args.repository_url))
    command.extend(artifacts)
    return run(args=command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
