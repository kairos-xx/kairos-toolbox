# Copyright (c) 2026 Project contributors.
"""Tests of the portable Zstandard CLI wrapper."""

from __future__ import annotations

from hashlib import sha256
from importlib import import_module
from pathlib import Path
from secrets import token_bytes
from stat import S_IXUSR
from typing import TYPE_CHECKING, Final, cast, final
from zipfile import ZipFile

import pytest
from stationer.partition.zstd_portable import Zstd, ZstdOptions, export_binary

from tests.tools.stationer.helpers import check, expect

if TYPE_CHECKING:
    from types import ModuleType

    from stationer.partition.zstd_portable import OpenMode
zstd_module: ModuleType = import_module(
    name="stationer.partition.zstd_portable",
)


@final
class Constants:
    """Group module-level constants.

    Attributes:
        payload: Grouped module constant.
        read_chunk: Grouped module constant.
        complete: Grouped module constant.
        one_mib: Grouped module constant.

    """

    payload: Final[bytes] = b"stationer-partition\x00" * 4096 + bytes(
        range(256),
    )
    read_chunk: Final[int] = 64
    complete: Final[float] = 100.0
    one_mib: Final[int] = 1024 * 1024


def _call(owner: object, *, helper: str, **arguments: object) -> object:
    """Invoke a private helper by name for boundary coverage.

    Args:
        owner: Module or instance holding the helper.
        helper: Helper attribute name.
        arguments: Keyword arguments to forward.

    Returns:
        Helper result.

    """
    return getattr(owner, helper)(**arguments)


def _attribute(owner: object, *, name: str) -> object:
    """Read a private attribute by name for boundary coverage.

    Args:
        owner: Module or class holding the attribute.
        name: Attribute name.

    Returns:
        Attribute value.

    """
    return getattr(owner, name)


def _roundtrip(*, target: Path, payload: bytes = Constants.payload) -> None:
    """Compress then decompress ``payload`` through real ``zstd``.

    Args:
        target: Destination ``.zst`` path.
        payload: Bytes to compress and verify.

    """
    with Zstd(file=target, options=ZstdOptions(level=3)) as writer:
        _ = writer.write(payload)
    check(condition=target.is_file(), reason="Compressed output missing.")
    restored: bytes = b""
    with Zstd(file=target, mode="r") as reader:
        restored = reader.read()
    expect(actual=restored, expected=payload)


@pytest.mark.parametrize(
    ("os_name", "cpu", "expected"),
    [
        ("darwin", "amd64", ("darwin", "x86_64")),
        ("linux", "aarch64", ("linux", "arm64")),
        ("windows", "i686", ("windows", "x86")),
    ],
)
def test_platform_normalization(
    os_name: str,
    cpu: str,
    expected: tuple[str, str],
) -> None:
    """Normalize common architecture aliases.

    Args:
        os_name: Input operating-system name.
        cpu: Input architecture name.
        expected: Normalized target key.

    """
    expect(
        actual=_call(
            owner=zstd_module,
            helper="_normalize",
            os_name=os_name,
            machine=cpu,
        ),
        expected=expected,
    )


def test_platform_normalization_rejects_unknown_values() -> None:
    """Reject unsupported CPUs and operating systems explicitly."""
    with pytest.raises(
        expected_exception=RuntimeError,
        match="Unsupported CPU",
    ):
        _call(
            owner=zstd_module,
            helper="_normalize",
            os_name="linux",
            machine="riscv64",
        )
    with pytest.raises(
        expected_exception=RuntimeError,
        match="Unsupported OS",
    ):
        _call(
            owner=zstd_module,
            helper="_normalize",
            os_name="plan9",
            machine="amd64",
        )


def test_options_validation(tmp_path: Path) -> None:
    """Reject out-of-range modes and option values.

    Args:
        tmp_path: Per-test temporary directory.

    """
    target: Path = tmp_path / "payload.zst"
    for mode in ("x", "a", "rw"):
        with pytest.raises(
            expected_exception=ValueError,
            match="Unsupported mode",
        ):
            Zstd(file=target, mode=cast(typ="OpenMode", val=mode))
    for options in (
        ZstdOptions(level=0),
        ZstdOptions(level=23),
        ZstdOptions(threads=-1),
        ZstdOptions(expected_size=-1),
    ):
        with pytest.raises(expected_exception=ValueError, match="must be"):
            Zstd(file=target, options=options)
    instance: Zstd = Zstd(file=target, mode="rb")
    check(condition=instance.readable(), reason="rb must be readable.")
    check(condition=not instance.writable(), reason="rb not writable.")
    check(
        condition=instance.progress is None,
        reason="Unopened reader reports unknown progress.",
    )
    other: Zstd = Zstd(file=target, mode="wb")
    check(condition=other.writable(), reason="wb must be writable.")
    check(condition=not other.readable(), reason="wb not readable.")


def test_roundtrip_and_counters(tmp_path: Path) -> None:
    """Compress and decompress through real ``zstd`` end to end.

    Args:
        tmp_path: Per-test temporary directory.

    """
    target: Path = tmp_path / "nested" / "payload.zst"
    seen: list[tuple[float | None, str]] = []

    def _progress(percent: float | None, status: str) -> None:
        """Record one progress emission.

        Args:
            percent: Reported completion percent.
            status: Reported status label.

        """
        seen.append((percent, status))

    writer: Zstd = Zstd(
        file=target,
        options=ZstdOptions(
            level=1,
            threads=1,
            progress=_progress,
            expected_size=len(Constants.payload),
        ),
        cache=tmp_path / "cli-cache",
    )
    check(condition=not writer.closed, reason="Stream starts open.")
    writer.open()
    check(condition=writer.progress == 0.0, reason="No bytes written yet.")
    expect(actual=writer.write(data=Constants.payload[:100]), expected=100)
    writer.flush()
    _ = writer.write(data=bytearray(Constants.payload[100:]))
    check(
        condition=writer.progress is not None and writer.progress > 0.0,
        reason="Expected progress after writes.",
    )
    writer.close()
    check(condition=writer.closed, reason="Stream must be closed.")
    expect(actual=writer.bytes_written, expected=len(Constants.payload))
    statuses: list[str] = [status for _, status in seen]
    check(condition="started" in statuses, reason="started emitted.")
    check(condition="compressing" in statuses, reason="compressing emitted.")
    check(condition="complete" in statuses, reason="complete emitted.")
    check(
        condition=seen[-1][0] == Constants.complete,
        reason="Completion reports 100 percent.",
    )
    head: bytearray = bytearray(10)
    tail: bytearray = bytearray(len(Constants.payload) - 10)
    with Zstd(file=target, mode="rb") as reader:
        expect(actual=reader.readinto(buffer=head), expected=10)
        expect(
            actual=reader.readinto(buffer=tail),
            expected=len(Constants.payload) - 10,
        )
        expect(actual=reader.read(), expected=b"")
    expect(actual=bytes(head + tail), expected=Constants.payload)
    check(
        condition=reader.bytes_read == len(Constants.payload),
        reason="Decompressed byte counter mismatch.",
    )
    check(
        condition=reader.progress == Constants.complete,
        reason="Consumed input reports 100 percent.",
    )
    writer.close()
    check(condition=writer.closed, reason="Second close is a no-op.")


def test_round_trip_on_host(tmp_path: Path) -> None:
    """Round-trip one mebibyte of random bytes through the host binary.

    Args:
        tmp_path: Per-test temporary directory.

    """
    _roundtrip(
        target=tmp_path / "random.zst",
        payload=token_bytes(nbytes=Constants.one_mib),
    )


def test_zero_expected_size(tmp_path: Path) -> None:
    """Report full progress when the expected size is zero.

    Args:
        tmp_path: Per-test temporary directory.

    """
    writer: Zstd = Zstd(
        file=tmp_path / "empty.zst",
        options=ZstdOptions(expected_size=0),
    )
    check(
        condition=writer.progress == Constants.complete,
        reason="Zero-length input reports complete.",
    )
    writer.close()


def test_open_guards(tmp_path: Path) -> None:
    """Reject double-open and open-after-close.

    Args:
        tmp_path: Per-test temporary directory.

    """
    target: Path = tmp_path / "payload.zst"
    writer: Zstd = Zstd(file=target)
    writer.open()
    with pytest.raises(expected_exception=RuntimeError, match="already open"):
        writer.open()
    writer.close()
    with pytest.raises(expected_exception=RuntimeError, match="closed"):
        writer.open()


def test_stream_method_guards(tmp_path: Path) -> None:
    """Reject stream calls that violate mode or lifecycle.

    Args:
        tmp_path: Per-test temporary directory.

    """
    target: Path = tmp_path / "payload.zst"
    writer: Zstd = Zstd(file=target)
    with pytest.raises(expected_exception=RuntimeError, match="not open"):
        _ = writer.write(data=b"x")
    with pytest.raises(expected_exception=RuntimeError, match="not open"):
        writer.flush()
    with pytest.raises(expected_exception=RuntimeError, match="not readable"):
        _ = writer.readinto(buffer=bytearray(4))
    reader: Zstd = Zstd(file=target, mode="r")
    with pytest.raises(expected_exception=RuntimeError, match="not writable"):
        _ = reader.write(data=b"x")
    with pytest.raises(expected_exception=RuntimeError, match="not writable"):
        reader.flush()
    _roundtrip(target=target, payload=b"short")
    writer.open()
    with pytest.raises(expected_exception=RuntimeError, match="not readable"):
        _ = writer.read()
    writer.close()


def test_missing_compressed_input(tmp_path: Path) -> None:
    """Raise ``FileNotFoundError`` for absent compressed input.

    Args:
        tmp_path: Per-test temporary directory.

    """
    reader: Zstd = Zstd(file=tmp_path / "absent.zst", mode="r")
    with pytest.raises(expected_exception=FileNotFoundError):
        reader.open()
    existing: Path = tmp_path / "existing.zst"
    existing.write_bytes(data=b"x")
    with pytest.raises(expected_exception=FileExistsError):
        Zstd(file=existing, overwrite=False).open()


def test_partial_read_close(tmp_path: Path) -> None:
    """Terminate the decompressor on early caller-driven close.

    Args:
        tmp_path: Per-test temporary directory.

    """
    target: Path = tmp_path / "payload.zst"
    _roundtrip(target=target)
    reader: Zstd = Zstd(file=target, mode="r")
    reader.open()
    expect(
        actual=len(reader.read(size=Constants.read_chunk)),
        expected=Constants.read_chunk,
    )
    reader.close()
    check(condition=reader.closed, reason="Stream must be closed.")
    check(
        condition=reader.bytes_read == Constants.read_chunk,
        reason="Counter stops at the partial read.",
    )


def test_expected_size_mismatch(tmp_path: Path) -> None:
    """Fail close when written bytes differ from ``expected_size``.

    Args:
        tmp_path: Per-test temporary directory.

    """
    writer: Zstd = Zstd(
        file=tmp_path / "payload.zst",
        options=ZstdOptions(expected_size=len(Constants.payload) + 1),
    )
    writer.open()
    _ = writer.write(data=Constants.payload)
    with pytest.raises(expected_exception=RuntimeError, match="Expected"):
        writer.close()


def test_corrupt_stream_fails(tmp_path: Path) -> None:
    """Fail close when the decompressor exits nonzero.

    Args:
        tmp_path: Per-test temporary directory.

    """
    target: Path = tmp_path / "broken.zst"
    target.write_bytes(data=b"not-a-zstandard-frame" * 4096)
    reader: Zstd = Zstd(file=target, mode="r")
    reader.open()
    _ = reader.read()
    with pytest.raises(expected_exception=RuntimeError, match="zstd failed"):
        reader.close()


def test_context_exit_failures(tmp_path: Path) -> None:
    """Propagate close failures and preserve active exceptions.

    Args:
        tmp_path: Per-test temporary directory.

    """
    target: Path = tmp_path / "broken.zst"
    target.write_bytes(data=b"still-not-a-frame")
    with (
        pytest.raises(expected_exception=RuntimeError),
        Zstd(file=target, mode="r") as reader,
    ):
        _ = reader.read()
    reader: Zstd = Zstd(file=target, mode="r")
    reader.open()
    _ = reader.read()
    with pytest.raises(expected_exception=RuntimeError):
        _call(
            owner=reader,
            helper="__exit__",
            _exc_type=None,
            _exc_value=None,
            _traceback=None,
        )
    reader = Zstd(file=target, mode="r")
    reader.open()
    _ = reader.read()
    propagated: object = _call(
        owner=reader,
        helper="__exit__",
        _exc_type=AssertionError,
        _exc_value=AssertionError(),
        _traceback=None,
    )
    expect(actual=propagated, expected=False)


def test_executable_cache(tmp_path: Path) -> None:
    """Extract the native binary once and reuse the digest cache.

    Args:
        tmp_path: Per-test temporary directory.

    """
    cache: Path = tmp_path / "cache"
    for _ in range(2):
        executable: object = _call(
            owner=Zstd(file=tmp_path / "p.zst", cache=cache),
            helper="_get_executable",
        )
        check(
            condition=isinstance(executable, Path),
            reason="Executable path must be a Path.",
        )
        path: Path = Path(str(object=executable))
        check(condition=path.is_file(), reason="Extracted executable missing.")
        check(
            condition=bool(path.stat().st_mode & S_IXUSR),
            reason="Extracted executable lacks the user-exec bit.",
        )
        check(
            condition=(path.parent / ".sha256").is_file(),
            reason="Digest marker missing.",
        )


def test_unsupported_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject unknown CPU, OS, and target combinations.

    Args:
        monkeypatch: Fixture patching platform probes.

    """
    monkeypatch.setattr(
        target=zstd_module,
        name="machine",
        value=lambda: "mips64",
    )
    with pytest.raises(
        expected_exception=RuntimeError,
        match="Unsupported CPU",
    ):
        _call(owner=Zstd(file="x.zst"), helper="_get_executable")
    monkeypatch.setattr(
        target=zstd_module,
        name="machine",
        value=lambda: "x86_64",
    )
    monkeypatch.setattr(
        target=zstd_module,
        name="system",
        value=lambda: "Haiku",
    )
    with pytest.raises(
        expected_exception=RuntimeError,
        match="Unsupported OS",
    ):
        _call(owner=Zstd(file="x.zst"), helper="_get_executable")
    monkeypatch.setattr(
        target=zstd_module,
        name="system",
        value=lambda: "Darwin",
    )
    targets: dict[tuple[str, str], tuple[str, str]] = cast(
        typ="dict[tuple[str, str], tuple[str, str]]",
        val=_attribute(owner=zstd_module.Constants, name="targets"),
    )
    monkeypatch.setitem(
        dic=targets,
        name=("darwin", "x86_64"),
        value=("missing-payload.zip", "zstd"),
    )
    with pytest.raises(
        expected_exception=RuntimeError,
        match="Missing native payload",
    ):
        _call(owner=Zstd(file="x.zst"), helper="_get_executable")
    monkeypatch.delitem(dic=targets, name=("darwin", "x86_64"))
    with pytest.raises(
        expected_exception=RuntimeError,
        match="Unsupported platform",
    ):
        _call(owner=Zstd(file="x.zst"), helper="_get_executable")


def _fake_package(*, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Retarget the payload root at a synthetic package.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Fixture retargeting the package root.

    Returns:
        The synthetic ``bin`` directory.

    """
    package: Path = tmp_path / "zstd_portable"
    payload_dir: Path = package / "bin"
    payload_dir.mkdir(parents=True)
    monkeypatch.setattr(
        target=zstd_module,
        name="__file__",
        value=str(object=package / "__init__.py"),
    )
    monkeypatch.setattr(
        target=Zstd,
        name="_target",
        value=staticmethod(lambda: ("linux", "x86_64")),
    )
    return payload_dir


def _write_sums(*, directory: Path, name: str, content: bytes) -> None:
    """Write a matching ``SHA256SUMS`` entry for test content.

    Args:
        directory: Directory holding the manifest.
        name: Archive file name.
        content: Bytes whose digest is pinned.

    """
    (directory / "SHA256SUMS").write_text(
        data=f"{sha256(data=content).hexdigest()}  {name}\n",
        encoding="ascii",
    )


def test_invalid_payload_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject malformed or overfilled native payload archives.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Fixture retargeting the payload root.

    """
    payload_dir: Path = _fake_package(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    archive: Path = payload_dir / "linux-x86_64.zip"
    garbage: bytes = b"definitely not a zip"
    archive.write_bytes(data=garbage)
    _write_sums(directory=payload_dir, name=archive.name, content=garbage)
    owner: Zstd = Zstd(file="x.zst", cache=tmp_path / "cache")
    with pytest.raises(
        expected_exception=RuntimeError,
        match="Invalid native payload",
    ):
        _call(owner=owner, helper="_get_executable")
    with ZipFile(file=archive, mode="w") as zipped:
        zipped.writestr(zinfo_or_arcname="zstd", data=b"bin")
        zipped.writestr(zinfo_or_arcname="extra.txt", data=b"no")
    _write_sums(
        directory=payload_dir,
        name=archive.name,
        content=archive.read_bytes(),
    )
    with pytest.raises(
        expected_exception=RuntimeError,
        match="must contain only",
    ):
        _call(owner=owner, helper="_get_executable")


def test_loader_refuses_archive_with_wrong_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse extraction when the archive digest is not pinned.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Fixture retargeting the payload root.

    """
    payload_dir: Path = _fake_package(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    archive: Path = payload_dir / "linux-x86_64.zip"
    with ZipFile(file=archive, mode="w") as zipped:
        zipped.writestr(zinfo_or_arcname="zstd", data=b"bin")
    (payload_dir / "SHA256SUMS").write_text(
        data=f"{'0' * 64}  {archive.name}\n",
        encoding="ascii",
    )
    owner: Zstd = Zstd(file="x.zst", cache=tmp_path / "cache")
    with pytest.raises(
        expected_exception=RuntimeError,
        match="Digest mismatch",
    ):
        _call(owner=owner, helper="_get_executable")
    (payload_dir / "SHA256SUMS").unlink()
    with pytest.raises(
        expected_exception=RuntimeError,
        match="Missing checksum manifest",
    ):
        _call(owner=owner, helper="_get_executable")
    _write_sums(directory=payload_dir, name="other.zip", content=b"x")
    with pytest.raises(
        expected_exception=RuntimeError,
        match="No pinned digest",
    ):
        _call(owner=owner, helper="_get_executable")


def test_archives_match_sha256sums() -> None:
    """Every shipped archive matches its pinned SHA-256 digest."""
    payload_dir: Path = (
        Path(zstd_module.__file__ or "").resolve().parent / "bin"
    )
    targets: dict[tuple[str, str], tuple[str, str]] = cast(
        typ="dict[tuple[str, str], tuple[str, str]]",
        val=_attribute(owner=zstd_module.Constants, name="targets"),
    )
    names: set[str] = {archive for archive, _ in targets.values()}
    expect(actual=len(names), expected=6)
    for name in sorted(names):
        archive: Path = payload_dir / name
        expect(
            actual=sha256(data=archive.read_bytes()).hexdigest(),
            expected=_call(
                owner=zstd_module,
                helper="_expected_digest",
                name=name,
            ),
        )


def test_every_platform_has_its_expected_member() -> None:
    """Each archive holds exactly its expected executable member."""
    payload_dir: Path = (
        Path(zstd_module.__file__ or "").resolve().parent / "bin"
    )
    targets: dict[tuple[str, str], tuple[str, str]] = cast(
        typ="dict[tuple[str, str], tuple[str, str]]",
        val=_attribute(owner=zstd_module.Constants, name="targets"),
    )
    for archive_name, member in sorted(set(targets.values())):
        with ZipFile(file=payload_dir / archive_name, mode="r") as zipped:
            members: list[str] = [
                name for name in zipped.namelist() if not name.endswith("/")
            ]
        expect(actual=members, expected=[member])


def test_feed_and_stderr_without_process(tmp_path: Path) -> None:
    """Return early when feed and drain helpers lack a process.

    Args:
        tmp_path: Per-test temporary directory.

    """
    instance: Zstd = Zstd(file=tmp_path / "p.zst", mode="r")
    _call(owner=instance, helper="_feed_compressed_input")
    _call(owner=instance, helper="_read_stderr")
    expect(
        actual=_call(owner=instance, helper="_error_message"),
        expected="zstd failed.",
    )


def test_stderr_details_in_failure(tmp_path: Path) -> None:
    """Surface child stderr inside the close failure message.

    Args:
        tmp_path: Per-test temporary directory.

    """
    target: Path = tmp_path / "broken.zst"
    target.write_bytes(data=b"not-a-zstandard-frame" * 2048)
    reader: Zstd = Zstd(file=target, mode="r")
    reader.open()
    _ = reader.read()
    with pytest.raises(expected_exception=RuntimeError) as captured:
        reader.close()
    check(
        condition=str(object=captured.value) != "",
        reason="Failure message must not be empty.",
    )


def test_large_corrupt_input_feeds_until_pipe_breaks(tmp_path: Path) -> None:
    """Report input-streaming failure on a dead decompressor.

    Args:
        tmp_path: Per-test temporary directory.

    """
    target: Path = tmp_path / "huge-broken.zst"
    target.write_bytes(data=b"0" * (8 * Constants.one_mib))
    reader: Zstd = Zstd(file=target, mode="r")
    reader.open()
    _ = reader.read()
    with pytest.raises(expected_exception=RuntimeError):
        reader.close()


def test_export_binary(tmp_path: Path) -> None:
    """Stage a native CLI binary for an explicit target platform.

    Args:
        tmp_path: Per-test temporary directory.

    """
    staged: Path = export_binary(
        os_name="linux",
        machine="amd64",
        target=tmp_path / "linux",
    )
    expect(actual=staged.name, expected="zstd")
    check(condition=staged.is_file(), reason="Binary must be staged.")
    check(
        condition=staged.stat().st_mode & S_IXUSR != 0,
        reason="Binary must be executable.",
    )
    windows: Path = export_binary(
        os_name="windows",
        machine="x86_64",
        target=tmp_path / "windows",
    )
    expect(actual=windows.name, expected="zstd.exe")
    check(condition=windows.is_file(), reason="Windows binary staged.")


def test_export_binary_rejects_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject platforms without a normalized payload entry.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Fixture retargeting a payload entry.

    """
    with pytest.raises(
        expected_exception=RuntimeError,
        match="Unsupported OS",
    ):
        export_binary(os_name="haiku", machine="amd64", target=tmp_path / "os")
    with pytest.raises(
        expected_exception=RuntimeError,
        match="Unsupported CPU",
    ):
        export_binary(
            os_name="linux",
            machine="mips64",
            target=tmp_path / "cpu",
        )
    with pytest.raises(
        expected_exception=RuntimeError,
        match="Unsupported platform",
    ):
        export_binary(
            os_name="linux",
            machine="x86",
            target=tmp_path / "target",
        )
    targets: dict[tuple[str, str], tuple[str, str]] = cast(
        typ="dict[tuple[str, str], tuple[str, str]]",
        val=_attribute(owner=zstd_module.Constants, name="targets"),
    )
    monkeypatch.setitem(
        dic=targets,
        name=("windows", "arm64"),
        value=("missing-payload.zip", "zstd.exe"),
    )
    with pytest.raises(
        expected_exception=RuntimeError,
        match="Missing native payload",
    ):
        export_binary(
            os_name="windows",
            machine="arm64",
            target=tmp_path / "missing",
        )
