# Copyright (c) 2026 Project contributors.
"""Portable Zstandard CLI wrapper with lazy zipped-binary extraction."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from platform import machine, system
from stat import S_IXGRP, S_IXOTH, S_IXUSR
from subprocess import PIPE, Popen
from tempfile import gettempdir
from threading import Lock, Thread
from typing import (
    TYPE_CHECKING,
    BinaryIO,
    Final,
    Literal,
    Protocol,
    Self,
    final,
)
from zipfile import BadZipFile, ZipFile

if TYPE_CHECKING:
    from _hashlib import HASH
    from types import TracebackType
    from typing import IO
type OpenMode = Literal["r", "rb", "w", "wb"]


@final
class Constants:
    """Group module-level constants.

    Attributes:
        version: Grouped module constant.
        max_level: Grouped module constant.
        targets: Grouped module constant.

    """

    version: Final[str] = "1.5.7"
    max_level: Final[int] = 22
    targets: Final[dict[tuple[str, str], tuple[str, str]]] = {
        ("darwin", "arm64"): ("macos-universal2.zip", "zstd"),
        ("darwin", "x86_64"): ("macos-universal2.zip", "zstd"),
        ("linux", "arm64"): ("linux-arm64.zip", "zstd"),
        ("linux", "x86_64"): ("linux-x86_64.zip", "zstd"),
        ("windows", "arm64"): ("windows-arm64.zip", "zstd.exe"),
        ("windows", "x86"): ("windows-x86.zip", "zstd.exe"),
        ("windows", "x86_64"): ("windows-x86_64.zip", "zstd.exe"),
    }


def _normalize(*, os_name: str, machine: str) -> tuple[str, str]:
    """Normalize a platform pair into a ``_TARGETS`` key.

    Args:
        os_name: Operating system name such as ``darwin``.
        machine: CPU name such as ``amd64`` or ``aarch64``.

    Returns:
        ``(os, arch)`` key into ``_TARGETS``.

    Raises:
        RuntimeError: When the CPU or OS is unsupported.

    """
    aliases: dict[str, str] = {
        "aarch64": "arm64",
        "arm64": "arm64",
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "i386": "x86",
        "i686": "x86",
        "x86": "x86",
    }
    if machine not in aliases:
        msg: str = f"Unsupported CPU: {machine}"
        raise RuntimeError(msg)
    if os_name not in {"darwin", "linux", "windows"}:
        msg = f"Unsupported OS: {os_name}"
        raise RuntimeError(msg)
    return (os_name, aliases[machine])


def _sha256_file(*, file: Path) -> str:
    """Return SHA-256 for a file.

    Args:
        file: File to hash in one-megabyte chunks.

    Returns:
        Lowercase hexadecimal digest.

    """
    digest: HASH = sha256()
    with file.open(mode="rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_digest(*, name: str) -> str:
    """Return the pinned SHA-256 for one payload archive.

    Args:
        name: Archive file name.

    Returns:
        Pinned lowercase hexadecimal digest.

    Raises:
        RuntimeError: When the checksum manifest or the entry is missing.

    """
    manifest: Path = Path(__file__).resolve().parent / "bin" / "SHA256SUMS"
    if not manifest.is_file():
        msg: str = f"Missing checksum manifest: {manifest}"
        raise RuntimeError(msg)
    for line in manifest.read_text(encoding="ascii").splitlines():
        digest, _, filename = line.partition(" ")
        if filename.lstrip("*").strip() == name:
            return digest
    msg = f"No pinned digest for payload: {name}"
    raise RuntimeError(msg)


def _payload(*, target: tuple[str, str]) -> tuple[Path, str]:
    """Resolve the payload archive for a normalized platform.

    Args:
        target: ``(os, arch)`` key into ``_TARGETS``.

    Returns:
        ``(archive, member)`` for the platform's zipped CLI.

    Raises:
        RuntimeError: When the platform is unsupported or the
            payload archive is missing.

    """
    try:
        archive_name, member = Constants.targets[target]
    except KeyError as exc:
        msg: str = f"Unsupported platform: {target[0]}-{target[1]}"
        raise RuntimeError(msg) from exc
    archive: Path = Path(__file__).resolve().parent / "bin" / archive_name
    if not archive.is_file():
        msg = f"Missing native payload: {archive}"
        raise RuntimeError(msg)
    digest: str = _sha256_file(file=archive)
    expected: str = _expected_digest(name=archive.name)
    if digest != expected:
        msg = f"Digest mismatch for {archive.name}: {digest}"
        raise RuntimeError(msg)
    return (archive, member)


def _read_member(*, archive: Path, member: str) -> bytes:
    """Read the single executable member from a payload archive.

    Args:
        archive: Zipped payload holding exactly one member.
        member: Expected member name.

    Returns:
        The executable's raw bytes.

    Raises:
        RuntimeError: When the archive is invalid or holds
            unexpected members.

    """
    try:
        with ZipFile(file=archive, mode="r") as zipped:
            members: list[str] = [
                name for name in zipped.namelist() if not name.endswith("/")
            ]
            if members != [member]:
                msg: str = (
                    f"{archive.name} must contain only {member!r}; found"
                    f" {members!r}."
                )
                raise RuntimeError(msg)
            return zipped.read(name=member)
    except BadZipFile as exc:
        msg = f"Invalid native payload: {archive}"
        raise RuntimeError(msg) from exc


def export_binary(*, os_name: str, machine: str, target: Path) -> Path:
    """Extract the native CLI binary for an explicit platform.

    Args:
        os_name: Target operating system such as ``linux``.
        machine: Target CPU such as ``amd64`` or ``aarch64``.
        target: Directory receiving the executable.

    Returns:
        Path of the staged executable inside ``target``.

    """
    normalized: tuple[str, str] = _normalize(os_name=os_name, machine=machine)
    archive, member = _payload(target=normalized)
    target.mkdir(parents=True, exist_ok=True)
    executable: Path = target / member
    executable.write_bytes(data=_read_member(archive=archive, member=member))
    if normalized[0] != "windows":
        mode: int = executable.stat().st_mode | S_IXUSR | S_IXGRP | S_IXOTH
        executable.chmod(mode=mode)
    return executable


class ProgressCallback(Protocol):
    """Progress sink invoked with a percent and a status label."""

    def __call__(self, percent: float | None, status: str) -> None:
        """Receive progress for one compression step.

        Args:
            percent: Completion percent, or ``None`` when unknown.
            status: Short status label such as ``compressing``.

        """


@dataclass(frozen=True, slots=True)
class ZstdOptions:
    """Compression and progress options for :class:`Zstd`.

    Attributes:
        level: Compression level between 1 and ``_MAX_LEVEL``.
        threads: Worker threads; ``0`` lets the CLI decide.
        progress: Optional callback receiving percent and status.
        expected_size: Optional total source bytes used to compute
            percent progress and to validate the byte count on close.

    """

    level: int = 3
    threads: int = 0
    progress: ProgressCallback | None = None
    expected_size: int | None = None


class Zstd:
    """Stream data through a bundled native Zstandard CLI."""

    _extract_lock: Lock = Lock()

    def __init__(
        self,
        *,
        file: str | Path,
        mode: OpenMode = "w",
        options: ZstdOptions | None = None,
        overwrite: bool = True,
        cache: str | Path | None = None,
    ) -> None:
        """Initialize a streaming Zstandard reader or writer.

        Args:
            file: Compressed ``.zst`` path.
            mode: ``"r"``/``"rb"`` to decompress or
                ``"w"``/``"wb"`` to compress.
            options: Compression and progress options.
            overwrite: ``False`` raises when ``file`` already exists.
            cache: Directory caching the extracted CLI binary;
                defaults to ``<tempdir>/zstd-cli``.

        Raises:
            ValueError: When ``mode`` is unsupported or an option is
                out of range.

        """
        if mode not in {"r", "rb", "w", "wb"}:
            msg: str = f"Unsupported mode: {mode!r}."
            raise ValueError(msg)
        normalized_mode: Literal["r", "w"] = (
            "r" if mode.startswith("r") else "w"
        )
        resolved: ZstdOptions = options or ZstdOptions()
        if not 1 <= resolved.level <= Constants.max_level:
            msg = f"level must be between 1 and {Constants.max_level}."
            raise ValueError(msg)
        if resolved.threads < 0:
            msg = "threads must be >= 0."
            raise ValueError(msg)
        if resolved.expected_size is not None and resolved.expected_size < 0:
            msg = "expected_size must be >= 0."
            raise ValueError(msg)
        self._file: Path = Path(file)
        self._mode: Literal["r", "w"] = normalized_mode
        self._level: int = resolved.level
        self._threads: int = resolved.threads
        self._callback: ProgressCallback | None = resolved.progress
        self._expected_size: int | None = resolved.expected_size
        self._overwrite: bool = overwrite
        self._cache: Path = (
            Path(cache)
            if cache is not None
            else Path(gettempdir()) / "zstd-cli"
        )
        self._process: Popen[bytes] | None = None
        self._stdin: IO[bytes] | None = None
        self._stdout: IO[bytes] | None = None
        self._stderr_thread: Thread | None = None
        self._input_thread: Thread | None = None
        self._input_error: BaseException | None = None
        self._input_size: int | None = None
        self._input_consumed: int = 0
        self._stderr: list[str] = []
        self._bytes_written: int = 0
        self._bytes_read: int = 0
        self._read_eof: bool = False
        self._closed: bool = False

    @property
    def bytes_written(self) -> int:
        """Source bytes written.

        Returns:
            Count of uncompressed bytes accepted by the compressor.

        """
        return self._bytes_written

    @property
    def progress(self) -> float | None:
        """Source-input progress from zero through one hundred.

        Returns:
            Percentage of the known input processed, or ``None``
            when the total size is unknown.

        """
        if self._mode == "r":
            total: int | None = self._input_size
            consumed: int = self._input_consumed
        else:
            total = self._expected_size
            consumed = self._bytes_written
        if total is None:
            return None
        if total == 0:
            return 100.0
        return min(consumed / total * 100.0, 100.0)

    @property
    def bytes_read(self) -> int:
        """Decompressed bytes read.

        Returns:
            Count of decompressed bytes produced so far.

        """
        return self._bytes_read

    @property
    def closed(self) -> bool:
        """Whether this stream is closed.

        Returns:
            ``True`` after :meth:`close` has run.

        """
        return self._closed

    def readable(self) -> bool:
        """Return whether this stream supports reading.

        Returns:
            ``True`` when opened in ``"r"`` mode.

        """
        return self._mode == "r"

    def writable(self) -> bool:
        """Return whether this stream supports writing.

        Returns:
            ``True`` when opened in ``"w"`` mode.

        """
        return self._mode == "w"

    def open(self) -> None:
        """Start the bundled compressor or decompressor.

        Raises:
            RuntimeError: When the stream is already open or closed,
                or when the child pipes cannot be created.
            FileNotFoundError: When the compressed input file does
                not exist.

        """
        if self._process is not None:
            msg: str = "Zstd is already open."
            raise RuntimeError(msg)
        if self._closed:
            msg = "Zstd is closed."
            raise RuntimeError(msg)
        executable: Path = self._get_executable()
        if self._mode == "w":
            self._file.parent.mkdir(parents=True, exist_ok=True)
            command: list[str] = [
                str(object=executable),
                f"-{self._level}",
                "--stdout",
                "--no-progress",
            ]
            if self._threads:
                command.append(f"-T{self._threads}")
            output: BinaryIO = self._file.open(
                mode="wb" if self._overwrite else "xb",
            )
            try:
                self._process = Popen(
                    args=command,
                    stdin=PIPE,
                    stdout=output,
                    stderr=PIPE,
                )
            finally:
                output.close()
            if self._process.stdin is None or self._process.stderr is None:
                msg = "Unable to create zstd compression pipes."
                raise RuntimeError(msg)
            self._stdin = self._process.stdin
        else:
            if not self._file.is_file():
                msg = f"Compressed file does not exist: {self._file}"
                raise FileNotFoundError(msg)
            self._input_size = self._file.stat().st_size
            command = [
                str(object=executable),
                "--decompress",
                "--stdout",
                "--no-progress",
            ]
            self._process = Popen(
                args=command,
                stdin=PIPE,
                stdout=PIPE,
                stderr=PIPE,
            )
            if (
                self._process.stdin is None
                or self._process.stdout is None
                or self._process.stderr is None
            ):
                msg = "Unable to create zstd decompression pipes."
                raise RuntimeError(msg)
            self._stdin = self._process.stdin
            self._stdout = self._process.stdout
        self._stderr_thread = Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()
        if self._mode == "r":
            self._input_thread = Thread(
                target=self._feed_compressed_input,
                daemon=True,
            )
            self._input_thread.start()
        self._emit(status="started")

    def write(self, data: bytes | bytearray | memoryview) -> int:
        """Write uncompressed bytes to a compression stream.

        Args:
            data: Uncompressed bytes to compress.

        Returns:
            Count of bytes accepted by the compressor pipe.

        Raises:
            RuntimeError: When the stream is not writable or open.

        """
        if self._mode != "w":
            msg: str = "Zstd is not writable."
            raise RuntimeError(msg)
        if self._process is None or self._stdin is None:
            msg = "Zstd is not open."
            raise RuntimeError(msg)
        written: int = self._stdin.write(data)
        self._bytes_written += written
        self._emit(status="compressing")
        return written

    def read(self, size: int = -1) -> bytes:
        """Read decompressed bytes from a decompression stream.

        Args:
            size: Maximum bytes to return; ``-1`` reads all
                remaining output.

        Returns:
            Decompressed bytes, empty at end of stream.

        Raises:
            RuntimeError: When the stream is not readable or open.

        """
        if self._mode != "r":
            msg: str = "Zstd is not readable."
            raise RuntimeError(msg)
        if self._process is None or self._stdout is None:
            msg = "Zstd is not open."
            raise RuntimeError(msg)
        data: bytes = self._stdout.read(size)
        if not data:
            self._read_eof = True
        self._bytes_read += len(data)
        self._emit(status="decompressing")
        return data

    def readinto(self, buffer: bytearray | memoryview) -> int:
        """Read decompressed bytes directly into a writable buffer.

        Args:
            buffer: Writable buffer receiving decompressed bytes.

        Returns:
            Count of bytes stored in ``buffer``.

        """
        data: bytes = self.read(size=len(buffer))
        buffer[: len(data)] = data
        return len(data)

    def flush(self) -> None:
        """Flush pending Python-side input to the compressor pipe.

        Raises:
            RuntimeError: When the stream is not writable or open.

        """
        if self._mode != "w":
            msg: str = "Zstd is not writable."
            raise RuntimeError(msg)
        if self._stdin is None:
            msg = "Zstd is not open."
            raise RuntimeError(msg)
        self._stdin.flush()

    def close(self) -> None:
        """Finalize compression or decompression."""
        if self._closed:
            return
        self._closed = True
        if self._process is None:
            return
        partial_read: bool = self._mode == "r" and (not self._read_eof)
        if self._mode == "w" and self._stdin is not None:
            self._stdin.close()
            self._stdin = None
        if self._stdout is not None:
            self._stdout.close()
            self._stdout = None
        if partial_read:
            self._process.terminate()
            if self._stdin is not None:
                self._stdin.close()
                self._stdin = None
        if self._input_thread is not None:
            self._input_thread.join()
        returncode: int = self._process.wait()
        self._process = None
        if self._stderr_thread is not None:
            self._stderr_thread.join()
        self._validate(returncode=returncode, partial_read=partial_read)
        self._emit(status="complete", complete=True)

    def __enter__(self) -> Self:
        """Open this writer.

        Returns:
            The open writer.

        """
        self.open()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool:
        """Finalize this writer.

        Args:
            _exc_type: Active exception type, if any.
            _exc_value: Active exception instance, if any.
            _traceback: Active exception traceback, if any.

        Returns:
            ``False`` so active exceptions always propagate.

        """
        try:
            self.close()
        except BaseException:
            if _exc_value is None:
                raise
        return False

    @staticmethod
    def _target() -> tuple[str, str]:
        """Normalize the current platform.

        Returns:
            ``(os, arch)`` key into ``_TARGETS``.

        """
        return _normalize(os_name=system().lower(), machine=machine().lower())

    def _validate(self, *, returncode: int, partial_read: bool) -> None:
        """Raise when the finalized stream failed.

        Args:
            returncode: Child process exit status.
            partial_read: ``True`` when decompression ended early by
                the caller rather than the stream.

        Raises:
            RuntimeError: When input streaming failed, the child
                exited nonzero, or written bytes differ from
                ``expected_size``.

        """
        if self._input_error is not None and (not partial_read):
            msg: str = (
                f"Unable to stream compressed input: {self._input_error}"
            )
            raise RuntimeError(msg) from self._input_error
        if returncode != 0 and (not partial_read):
            raise RuntimeError(self._error_message())
        if (
            self._mode == "w"
            and self._expected_size is not None
            and (self._bytes_written != self._expected_size)
        ):
            msg = (
                f"Expected {self._expected_size} bytes; received"
                f" {self._bytes_written}."
            )
            raise RuntimeError(msg)

    def _get_executable(self) -> Path:
        """Extract/cache the executable for this platform.

        Returns:
            Path to the verified platform CLI binary.

        """
        target: tuple[str, str] = self._target()
        archive, executable_name = _payload(target=target)
        digest: str = _sha256_file(file=archive)
        directory: Path = (
            self._cache / Constants.version / archive.stem / digest[:16]
        )
        executable: Path = directory / executable_name
        marker: Path = directory / ".sha256"
        with self._extract_lock:
            if (
                executable.is_file()
                and marker.is_file()
                and (marker.read_text(encoding="ascii").strip() == digest)
            ):
                return executable
            directory.mkdir(parents=True, exist_ok=True)
            temporary: Path = directory / f"{executable_name}.tmp"
            temporary.write_bytes(
                data=_read_member(archive=archive, member=executable_name),
            )
            if target[0] != "windows":
                mode: int = (
                    temporary.stat().st_mode | S_IXUSR | S_IXGRP | S_IXOTH
                )
                temporary.chmod(mode=mode)
            temporary.replace(target=executable)
            marker.write_text(data=digest, encoding="ascii")
        return executable

    def _feed_compressed_input(self) -> None:
        """Feed compressed input to zstd while tracking source progress."""
        stream: IO[bytes] | None = self._stdin
        if stream is None:
            return
        try:
            with self._file.open(mode="rb") as source:
                while chunk := source.read(1024 * 1024):
                    stream.write(chunk)
                    self._input_consumed += len(chunk)
                    self._emit(status="decompressing")
            stream.close()
            self._stdin = None
        except (BrokenPipeError, OSError, ValueError) as exc:
            if not self._closed:
                self._input_error = exc
            with suppress(OSError):
                stream.close()
            self._stdin = None

    def _read_stderr(self) -> None:
        """Drain child stderr."""
        process: Popen[bytes] | None = self._process
        if process is None or process.stderr is None:
            return
        for raw in iter(process.stderr.readline, b""):
            self._stderr.append(
                raw.decode(encoding="utf-8", errors="replace").rstrip(),
            )
        process.stderr.close()

    def _emit(self, status: str, *, complete: bool = False) -> None:
        """Emit application progress.

        Args:
            status: Short status label.
            complete: ``True`` forces the reported percent to 100.

        """
        if self._callback is not None:
            self._callback(
                percent=100.0 if complete else self.progress,
                status=status,
            )

    def _error_message(self) -> str:
        """Format child diagnostics.

        Returns:
            The captured stderr lines or a generic failure message.

        """
        details: str = "\n".join(line for line in self._stderr if line)
        return f"zstd failed:\n{details}" if details else "zstd failed."
