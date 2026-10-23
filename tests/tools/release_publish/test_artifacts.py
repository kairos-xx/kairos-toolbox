"""Artifact checksum tests."""

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.release_publish.artifacts import checksum_directory, main

from .helpers import expect


def test_default_manifest_name_is_unchanged() -> None:
    """Existing callers keep getting ``SHA256SUMS``."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        (root / "a.bin").write_bytes(data=b"a")
        out: Path = checksum_directory(directory=root)
        expect(actual=out.name, expected="SHA256SUMS")
        expect(
            actual=out.read_text(encoding="utf-8"),
            expected=f"{sha256(data=b'a').hexdigest()}  a.bin\n",
        )


def test_custom_name_is_excluded_from_its_own_digest() -> None:
    """``--name SHA256SUMS.txt`` writes the file Lake Terminal expects."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        (root / "a.bin").write_bytes(data=b"a")
        expect(
            actual=main(
                arguments=(str(object=root), "--name", "SHA256SUMS.txt"),
            ),
            expected=0,
        )
        expect(
            actual=main(
                arguments=(str(object=root), "--name", "SHA256SUMS.txt"),
            ),
            expected=0,
        )
        text: str = (root / "SHA256SUMS.txt").read_text(encoding="utf-8")
        expect(
            actual=text,
            expected=f"{sha256(data=b'a').hexdigest()}  a.bin\n",
        )
