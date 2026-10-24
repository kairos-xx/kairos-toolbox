# Copyright (c) 2026 Project contributors.
"""Tests for remote job types and result-bundle verification."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from json import dumps, loads
from typing import TYPE_CHECKING, Final, cast, final
from zipfile import ZipFile

import pytest

from tools.stationer.conjure import (
    Credentials,
    Payload,
    RemoteSpec,
    RemoteStep,
    ResultBundle,
    materialize_result,
)

from .helpers import check, expect

if TYPE_CHECKING:
    from pathlib import Path


@final
class Constants:
    """Group module-level constants.

    Attributes:
        minimum_token: Grouped module constant.
        minimum_bootstrap: Grouped module constant.

    """

    minimum_token: Final[int] = 64
    minimum_bootstrap: Final[int] = 32


def _bundle(
    *,
    files: dict[str, bytes],
    records: list[dict[str, object]] | None = None,
    manifest: bool = True,
) -> bytes:
    """Build a result ZIP the way a worker does.

    Args:
        files: Artifact bytes keyed by relative path.
        records: Manifest artifact records; derived from ``files`` if omitted.
        manifest: Whether to include ``manifest.json``.

    Returns:
        The ZIP bytes.

    """
    derived: list[dict[str, object]] = [
        {
            "path": path,
            "size": len(data),
            "sha256": sha256(data=data).hexdigest(),
        }
        for path, data in files.items()
    ]
    buffer: BytesIO = BytesIO()
    archive: ZipFile
    with ZipFile(file=buffer, mode="w") as archive:
        if manifest:
            archive.writestr(
                zinfo_or_arcname="manifest.json",
                data=dumps(
                    obj={
                        "schema": 1,
                        "os": "linux",
                        "architecture": "arm64",
                        "artifacts": records
                        if records is not None
                        else derived,
                    },
                ),
            )
        for path, data in files.items():
            archive.writestr(zinfo_or_arcname=f"artifacts/{path}", data=data)
    return buffer.getvalue()


def test_spec_json_is_deterministic_and_complete() -> None:
    """The spec serialises with sorted keys and every worker field."""
    spec: RemoteSpec = RemoteSpec(
        steps=(
            RemoteStep(message="venv", argv=("{python}", "-m", "venv")),
            RemoteStep(message="build", argv=("{venv_python}",), cwd="work"),
        ),
    )
    document: dict[str, object] = loads(s=spec.json())
    expect(actual=list(document), expected=sorted(document))
    steps: list[dict[str, object]] = cast(
        typ="list[dict[str, object]]",
        val=document["steps"],
    )
    expect(actual=steps[0]["argv"], expected=["{python}", "-m", "venv"])
    expect(actual=steps[1]["cwd"], expected="work")
    expect(actual=spec.json(), expected=spec.json())
    check(
        condition=", " not in spec.json() and ": " not in spec.json(),
        reason="compact separators",
    )


def test_payload_describes_an_archive(tmp_path: Path) -> None:
    """The payload carries the archive's size and SHA-256.

    Args:
        tmp_path: Pytest temporary directory.

    """
    archive: Path = tmp_path / "p.zip"
    archive.write_bytes(data=b"payload")
    payload: Payload = Payload.of(path=archive)
    expect(actual=payload.size, expected=7)
    expect(actual=payload.sha256, expected=sha256(data=b"payload").hexdigest())


def test_credentials_are_unique_and_high_entropy() -> None:
    """Every job gets a fresh bearer token and bootstrap path secret."""
    first: Credentials = Credentials.create()
    second: Credentials = Credentials.create()
    check(condition=first.token != second.token, reason="tokens differ")
    check(condition=first.bootstrap != second.bootstrap, reason="ids differ")
    check(
        condition=len(first.token) >= Constants.minimum_token,
        reason=first.token,
    )
    check(
        condition=len(first.bootstrap) >= Constants.minimum_bootstrap,
        reason=first.bootstrap,
    )
    check(condition=first.token != first.bootstrap, reason="independent")


def test_materialize_writes_verified_artifacts(tmp_path: Path) -> None:
    """A valid bundle is written under the output directory.

    Args:
        tmp_path: Pytest temporary directory.

    """
    output: Path = tmp_path / "out"
    result: ResultBundle = materialize_result(
        bundle=_bundle(files={"app": b"binary", "lib/x.so": b"so"}),
        output=output,
    )
    expect(actual=result.os_name, expected="linux")
    expect(actual=result.architecture, expected="arm64")
    expect(
        actual=sorted(
            a.path.relative_to(output).as_posix() for a in result.artifacts
        ),
        expected=["app", "lib/x.so"],
    )
    expect(actual=(output / "app").read_bytes(), expected=b"binary")
    expect(
        actual=result.artifacts[0].sha256,
        expected=sha256(
            data=result.artifacts[0].path.read_bytes(),
        ).hexdigest(),
    )


def test_materialize_replaces_stale_output_only_after_verification(
    tmp_path: Path,
) -> None:
    """A bad bundle never destroys the previous good output.

    Args:
        tmp_path: Pytest temporary directory.

    """
    output: Path = tmp_path / "out"
    output.mkdir()
    (output / "old").write_bytes(data=b"keep")
    with pytest.raises(expected_exception=ValueError, match="manifest"):
        _ = materialize_result(
            bundle=_bundle(files={"a": b"x"}, manifest=False),
            output=output,
        )
    expect(actual=(output / "old").read_bytes(), expected=b"keep")
    _ = materialize_result(bundle=_bundle(files={"a": b"x"}), output=output)
    check(
        condition=not (output / "old").exists(),
        reason="replaced on success",
    )


@pytest.mark.parametrize(
    ("records", "message"),
    [
        ([], "no artifacts"),
        ([{"path": "/abs", "size": 1, "sha256": "0"}], "Unsafe"),
        ([{"path": "../up", "size": 1, "sha256": "0"}], "Unsafe"),
        ([{"path": "gone", "size": 1, "sha256": "0"}], "missing"),
    ],
)
def test_materialize_rejects_bad_manifests(
    tmp_path: Path,
    records: list[dict[str, object]],
    message: str,
) -> None:
    """Empty, unsafe and dangling manifests are refused.

    Args:
        tmp_path: Pytest temporary directory.
        records: Manifest records under test.
        message: Text the error must contain.

    """
    with pytest.raises(expected_exception=ValueError, match=message):
        _ = materialize_result(
            bundle=_bundle(files={"a": b"x"}, records=records),
            output=tmp_path / "out",
        )


@pytest.mark.parametrize("field", ["size", "sha256"])
def test_materialize_rejects_tampered_artifacts(
    tmp_path: Path,
    field: str,
) -> None:
    """A size or digest that disagrees with the bytes fails verification.

    Args:
        tmp_path: Pytest temporary directory.
        field: Manifest field to corrupt.

    """
    record: dict[str, object] = {
        "path": "a",
        "size": 1,
        "sha256": sha256(data=b"x").hexdigest(),
    }
    record[field] = 99 if field == "size" else "f" * 64
    with pytest.raises(expected_exception=ValueError, match="verification"):
        _ = materialize_result(
            bundle=_bundle(files={"a": b"x"}, records=[record]),
            output=tmp_path / "out",
        )
