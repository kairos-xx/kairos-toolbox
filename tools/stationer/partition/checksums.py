# Copyright (c) 2026 Project contributors.
"""SHA-256 checksum manifest read/write helpers."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _hashlib import HASH
    from collections.abc import Mapping, Sequence
    from pathlib import Path


def file_digest(*, path: Path) -> str:
    """Return the SHA-256 hex digest of one file.

    Args:
        path: File to hash in one-megabyte chunks.

    Returns:
        Lowercase hexadecimal digest.

    """
    digest: HASH = sha256()
    with path.open(mode="rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_sums(
    *,
    directory: Path,
    names: Sequence[str] | None = None,
    output_name: str = "SHA256SUMS.txt",
) -> Path:
    """Write a ``<digest>  <name>`` manifest for files in a directory.

    Args:
        directory: Directory whose regular files are hashed.
        names: File names to include; ``None`` covers every regular
            file except the manifest itself.
        output_name: Manifest file name written inside ``directory``.

    Returns:
        Path of the written manifest.

    """
    selected: list[str] = (
        sorted(names)
        if names is not None
        else sorted(
            entry.name
            for entry in directory.iterdir()
            if entry.is_file() and entry.name != output_name
        )
    )
    lines: list[str] = [
        f"{file_digest(path=directory / name)}  {name}" for name in selected
    ]
    manifest: Path = directory / output_name
    _ = manifest.write_text(
        data="".join(line + "\n" for line in lines),
        encoding="ascii",
    )
    return manifest


def parse_sums(*, text: str) -> Mapping[str, str]:
    """Parse ``<digest>  <name>`` or ``<digest> *<name>`` manifest text.

    Args:
        text: Manifest content.

    Returns:
        File name to expected digest.

    """
    entries: dict[str, str] = {}
    for line in text.splitlines():
        digest, _, filename = line.partition(" ")
        if digest and filename:
            entries[filename.lstrip("*").strip()] = digest
    return entries


def expected_digest(*, text: str, name: str) -> str | None:
    """Return the pinned digest for one file, when present.

    Args:
        text: Manifest content.
        name: File name to look up.

    Returns:
        The digest, or ``None`` when the file is unlisted.

    """
    return parse_sums(text=text).get(name)


def verify_file(*, path: Path, text: str, name: str | None = None) -> bool:
    """Check a file against its pinned manifest digest.

    Args:
        path: File to hash.
        text: Manifest content.
        name: Manifest entry; defaults to ``path.name``.

    Returns:
        ``True`` when the file's digest matches its entry.

    """
    wanted: str | None = expected_digest(
        text=text,
        name=name if name is not None else path.name,
    )
    return wanted is not None and file_digest(path=path) == wanted
