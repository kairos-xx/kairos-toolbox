"""Artifact post-processing commands."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from pathlib import Path

from tools.stationer.partition.checksums import write_sums


def checksum_directory(*, directory: Path, name: str = "SHA256SUMS") -> Path:
    """Write a deterministic SHA-256 manifest for release artifacts.

    Every file below ``directory`` is listed by its slash-separated
    relative path, so a nested build tree checks out in one manifest.

    Args:
        directory: Directory containing the built artifacts.
        name: Manifest file name written inside ``directory``.

    Returns:
        Path of the written checksum manifest.

    """
    output: Path = directory / name
    files: tuple[str, ...] = tuple(
        sorted(
            path.relative_to(directory).as_posix()
            for path in directory.rglob(pattern="*")
            if path.is_file() and path != output
        ),
    )
    return write_sums(
        directory=directory,
        names=files,
        output_name=name,
    )


def main(*, arguments: tuple[str, ...] | None = None) -> int:
    """Run artifact utilities.

    Args:
        arguments: Optional command-line arguments.

    Returns:
        Process exit code.

    """
    parser: ArgumentParser = ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--name", default="SHA256SUMS")
    args: Namespace = parser.parse_args(args=arguments)
    checksum_directory(directory=args.directory, name=args.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
