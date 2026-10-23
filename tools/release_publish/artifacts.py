"""Artifact post-processing commands."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from hashlib import sha256
from pathlib import Path


def checksum_directory(*, directory: Path, name: str = "SHA256SUMS") -> Path:
    """Write a deterministic SHA-256 manifest for release artifacts.

    Args:
        directory: Directory containing the built artifacts.
        name: Manifest file name written inside ``directory``.

    Returns:
        Path of the written checksum manifest.

    """
    output: Path = directory / name
    files: tuple[Path, ...] = tuple(
        sorted(
            path
            for path in directory.iterdir()
            if path.is_file() and path.name != output.name
        ),
    )
    output.write_text(
        data="".join(
            f"{sha256(data=path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in files
        ),
        encoding="utf-8",
    )
    return output


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
