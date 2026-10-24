# Copyright (c) 2026 Project contributors.
"""Job types and result verification for remote pull-worker builds."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
from io import BytesIO
from json import dumps, loads
from pathlib import Path, PurePosixPath
from secrets import token_urlsafe
from shutil import rmtree
from zipfile import ZipFile


class ResultError(ValueError):
    """Report a malformed, unsafe or unverifiable result bundle."""


type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)
type JsonObject = dict[str, JsonValue]


@dataclass(frozen=True, slots=True, kw_only=True)
class RemoteStep:
    """One command a worker runs, with a progress message.

    Arguments may contain ``{python}`` (the worker's bootstrap interpreter),
    ``{venv}``, ``{venv_python}``, ``{source}``, ``{dist}`` and ``{work}``,
    which the worker expands to its own paths.

    Attributes:
        message: Progress text reported before the command runs.
        argv: Command and arguments; never interpreted by a shell.
        cwd: Directory the command runs in: ``source`` or ``work``.
        weight: Relative share of the build this step takes, which sizes
            its slice of the host's progress bar.

    """

    message: str
    argv: tuple[str, ...]
    cwd: str = "source"
    weight: int = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class RemoteSpec:
    """Describe what a remote worker must do.

    Both worker flavours (PowerShell and POSIX) only interpret this spec:
    verify and unpack the payload, run ``steps`` in order, then package every
    file under ``{dist}`` and upload it. All build logic stays on the host.

    Attributes:
        steps: Commands to run, in order.
        files: Files written under ``{work}`` before the steps run; names
            and contents may use the same ``{python}``, ``{venv}``,
            ``{venv_python}``, ``{source}``, ``{dist}``, ``{work}`` and
            ``{architecture}`` placeholders as step arguments.
        python_version: Interpreter version bootstrapped on Windows workers.
        python_bootstrap: Whether a worker may install Python itself.
        architecture: ``native`` builds for the worker's CPU; ``arm64`` or
            ``amd64`` makes a Windows worker use that Python, so an ARM64
            guest can also produce an x64 executable through emulation.
        timeout_seconds: Maximum wall-clock build duration.
        max_result_bytes: Maximum accepted result bundle size.

    """

    steps: tuple[RemoteStep, ...]
    files: dict[str, str] = field(default_factory=dict)
    python_version: str = "3.13.9"
    python_bootstrap: bool = True
    architecture: str = "native"
    timeout_seconds: int = 1800
    max_result_bytes: int = 2_147_483_648

    def json(self) -> str:
        """Return a deterministic JSON representation.

        Returns:
            Sorted, compact JSON of the spec.

        """
        return dumps(
            obj=asdict(obj=self),
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Payload:
    """Describe one immutable source payload.

    Attributes:
        path: Archive path on the host.
        sha256: Hex digest of the archive bytes.
        size: Archive size in bytes.

    """

    path: Path
    sha256: str
    size: int

    @classmethod
    def of(cls, *, path: Path) -> Payload:
        """Describe an existing archive.

        Args:
            path: Archive to describe.

        Returns:
            The payload with its digest and size.

        """
        data: bytes = path.read_bytes()
        return cls(
            path=path,
            sha256=sha256(data=data).hexdigest(),
            size=len(data),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Credentials:
    """Carry the secrets of one build job.

    The bootstrap secret is a path segment: the worker scripts embed the
    bearer token, so they are only served under an unguessable URL.

    Attributes:
        token: High-entropy bearer token for the job's API.
        bootstrap: Unguessable path segment that serves worker scripts.

    """

    token: str
    bootstrap: str

    @classmethod
    def create(cls) -> Credentials:
        """Create fresh, independent secrets.

        Returns:
            New credentials.

        """
        return cls(
            token=token_urlsafe(nbytes=48),
            bootstrap=token_urlsafe(nbytes=24),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class MaterializedArtifact:
    """Describe one verified artifact written on the host.

    Attributes:
        path: Local artifact path.
        sha256: Verified hex digest of the artifact bytes.
        size: Artifact size in bytes.

    """

    path: Path
    sha256: str
    size: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ResultBundle:
    """Describe a verified and materialized worker result.

    Attributes:
        artifacts: Every artifact written, in manifest order.
        os_name: Operating system the worker reported.
        architecture: CPU architecture the worker reported.

    """

    artifacts: tuple[MaterializedArtifact, ...]
    os_name: str
    architecture: str


def _safe_relative(*, value: str) -> PurePosixPath:
    """Validate one portable result path.

    Args:
        value: Manifest-declared relative artifact path.

    Returns:
        The normalized relative path.

    Raises:
        ResultError: If the path is absolute, empty or escapes the root.

    """
    path: PurePosixPath = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        msg: str = f"Unsafe artifact path: {value}"
        raise ResultError(msg)
    return path


def _record(*, raw: JsonValue) -> tuple[PurePosixPath, int, str]:
    """Validate the shape of one manifest artifact record.

    Args:
        raw: Decoded manifest record.

    Returns:
        The safe relative path, declared size and lower-case digest.

    Raises:
        ResultError: If the record is malformed or its path is unsafe.

    """
    if not isinstance(raw, dict):
        msg: str = "Result manifest contains a malformed artifact record."
        raise ResultError(msg)
    path: JsonValue = raw.get("path")
    size: JsonValue = raw.get("size")
    digest: JsonValue = raw.get("sha256")
    if (
        not isinstance(path, str)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or not isinstance(digest, str)
    ):
        msg = "Result manifest contains a malformed artifact record."
        raise ResultError(msg)
    return _safe_relative(value=path), size, digest.lower()


def _verified(
    *,
    archive: ZipFile,
    raw: JsonValue,
) -> tuple[PurePosixPath, bytes]:
    """Read and check one declared artifact.

    Args:
        archive: Open result bundle.
        raw: Manifest record for the artifact.

    Returns:
        The relative path and the verified bytes.

    Raises:
        ResultError: If the member is missing or its size or digest differs.

    """
    relative, size, digest = _record(raw=raw)
    member: str = f"artifacts/{relative.as_posix()}"
    if member not in archive.namelist():
        msg: str = f"Result bundle is missing {member}."
        raise ResultError(msg)
    data: bytes = archive.read(name=member)
    if len(data) != size or sha256(data=data).hexdigest() != digest:
        msg = f"Artifact verification failed: {relative.as_posix()}"
        raise ResultError(msg)
    return relative, data


def _manifest(*, archive: ZipFile) -> JsonObject:
    """Read the bundle's manifest object.

    Args:
        archive: Open result bundle.

    Returns:
        The decoded manifest.

    Raises:
        ResultError: If the manifest is absent or not an object.

    """
    if "manifest.json" not in archive.namelist():
        msg: str = "Result bundle does not contain manifest.json."
        raise ResultError(msg)
    document: JsonValue = loads(s=archive.read(name="manifest.json"))
    if not isinstance(document, dict):
        msg = "Result manifest is not a JSON object."
        raise ResultError(msg)
    return document


def _text(*, manifest: JsonObject, key: str) -> str:
    """Read an optional text field of the manifest.

    Args:
        manifest: Decoded manifest.
        key: Field name.

    Returns:
        The text, or an empty string when absent or not text.

    """
    value: JsonValue = manifest.get(key)
    return value if isinstance(value, str) else ""


def materialize_result(*, bundle: bytes, output: Path) -> ResultBundle:
    """Verify a worker bundle and write its artifacts into ``output``.

    Nothing on disk changes until every artifact has been verified.

    Args:
        bundle: Result ZIP bytes uploaded by the worker.
        output: Directory replaced by the verified artifacts.

    Returns:
        The materialized artifacts and the worker's platform description.

    Raises:
        ResultError: If the manifest is absent or empty, an artifact path is
            unsafe or missing, or a size or digest check fails.

    """
    with ZipFile(file=BytesIO(initial_bytes=bundle), mode="r") as archive:
        manifest: JsonObject = _manifest(archive=archive)
        records: JsonValue = manifest.get("artifacts")
        if not isinstance(records, list) or not records:
            msg: str = "Result manifest contains no artifacts."
            raise ResultError(msg)
        verified: list[tuple[PurePosixPath, bytes]] = [
            _verified(archive=archive, raw=raw) for raw in records
        ]
    destination: Path = output.resolve()
    if destination.exists():
        rmtree(path=destination)
    destination.mkdir(parents=True, exist_ok=True)
    written: list[MaterializedArtifact] = []
    for relative, data in verified:
        target: Path = destination.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_bytes(data=data)
        written.append(
            MaterializedArtifact(
                path=target,
                sha256=sha256(data=data).hexdigest(),
                size=len(data),
            ),
        )
    return ResultBundle(
        artifacts=tuple(written),
        os_name=_text(manifest=manifest, key="os"),
        architecture=_text(manifest=manifest, key="architecture"),
    )
