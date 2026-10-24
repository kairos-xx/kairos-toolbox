# Copyright (c) 2026 Project contributors.
"""Tests for the checksum manifest helpers."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

from stationer.partition.checksums import (
    expected_digest,
    file_digest,
    parse_sums,
    verify_file,
    write_sums,
)

from tests.tools.stationer.helpers import check, expect

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


def test_write_and_parse_round_trip(tmp_path: Path) -> None:
    """Written manifests parse back to the same digests.

    Args:
        tmp_path: Per-test temporary directory.

    """
    _ = (tmp_path / "a.bin").write_bytes(data=b"alpha")
    _ = (tmp_path / "b.bin").write_bytes(data=b"beta")
    manifest: Path = write_sums(directory=tmp_path)
    entries: Mapping[str, str] = parse_sums(
        text=manifest.read_text(encoding="ascii"),
    )
    expect(
        actual=entries["a.bin"],
        expected=sha256(data=b"alpha").hexdigest(),
    )
    check(
        condition=manifest.name == "SHA256SUMS.txt",
        reason="Manifest name drifted.",
    )


def test_parse_sums_accepts_star_form() -> None:
    """Both ``sha256  name`` and ``sha256 *name`` forms parse."""
    text: str = "ab12  plain.bin\ncd34 *star.bin\n"
    entries: Mapping[str, str] = parse_sums(text=text)
    expect(actual=entries["plain.bin"], expected="ab12")
    expect(actual=entries["star.bin"], expected="cd34")


def test_expected_digest_and_verify_file(tmp_path: Path) -> None:
    """Verification passes on match and fails on mismatch/absence.

    Args:
        tmp_path: Per-test temporary directory.

    """
    payload: Path = tmp_path / "blob.bin"
    _ = payload.write_bytes(data=b"blob")
    good: str = f"{sha256(data=b'blob').hexdigest()}  blob.bin\n"
    check(
        condition=expected_digest(text=good, name="blob.bin") is not None,
        reason="Digest lookup failed.",
    )
    check(
        condition=verify_file(path=payload, text=good),
        reason="Matching file failed verification.",
    )
    bad: str = f"{'0' * 64}  blob.bin\n"
    check(
        condition=not verify_file(path=payload, text=bad),
        reason="Tampered file passed verification.",
    )
    check(
        condition=expected_digest(text=bad, name="ghost.bin") is None,
        reason="Missing entry returned a digest.",
    )


def test_file_digest_chunks_large_files(tmp_path: Path) -> None:
    """Large files hash identically to in-memory bytes.

    Args:
        tmp_path: Per-test temporary directory.

    """
    big: Path = tmp_path / "big.bin"
    payload: bytes = b"z" * (3 * 1024 * 1024 + 7)
    _ = big.write_bytes(data=payload)
    expect(
        actual=file_digest(path=big),
        expected=sha256(data=payload).hexdigest(),
    )
