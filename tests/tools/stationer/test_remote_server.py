# Copyright (c) 2026 Project contributors.
"""Tests for the authenticated one-job build server."""

from __future__ import annotations

from hashlib import sha256
from http.client import HTTPConnection, HTTPResponse
from io import BytesIO
from json import dumps
from threading import Event, Thread
from typing import TYPE_CHECKING, Final, final
from urllib.parse import SplitResult, urlsplit
from zipfile import ZipFile

import pytest

from tools.stationer.conjure import (
    Credentials,
    Payload,
    RemoteSpec,
    RemoteStep,
)
from tools.stationer.remote_server import (
    JobHandler,
    JobServer,
    JobState,
)

from .helpers import check, expect, present

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType


def job_state(*, tmp_path: Path, maximum: int = 1_000_000) -> JobState:
    """Create the state of one job with a tiny payload.

    Args:
        tmp_path: Pytest temporary directory.
        maximum: Largest accepted result bundle in bytes.

    Returns:
        The job state.

    """
    archive: Path = tmp_path / "payload.zip"
    archive.write_bytes(data=b"payload-bytes")
    return JobState(
        spec=RemoteSpec(
            steps=(RemoteStep(message="m", argv=("{python}",)),),
            max_result_bytes=maximum,
        ),
        credentials=Credentials.create(),
        payload=Payload.of(path=archive),
        output=tmp_path / "out",
        public_url="http://127.0.0.1:0",
        target="linux",
        done=Event(),
        say=messages.append,
    )


messages: list[str] = []


@final
class Serving:
    """Run a real server on an ephemeral port for one test."""

    def __init__(self, *, state: JobState) -> None:
        """Prepare the server without starting it.

        Args:
            state: Job state to serve.

        """
        self.server: Final[JobServer] = JobServer(("127.0.0.1", 0), JobHandler)
        self.server.state = state
        self.thread: Final[Thread] = Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )

    def __enter__(self) -> str:
        """Start serving.

        Returns:
            The server's base URL.

        """
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop serving.

        Args:
            exc_type: Ignored exception type.
            exc_value: Ignored exception.
            traceback: Ignored traceback.

        """
        _ = (exc_type, exc_value, traceback)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def serving(*, state: JobState) -> Serving:
    """Create a server context for one job.

    Args:
        state: Job state to serve.

    Returns:
        The context manager that serves it.

    """
    return Serving(state=state)


def call(
    *,
    url: str,
    method: str = "GET",
    token: str | None = None,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    """Send one request and return the status and body.

    Args:
        url: Full URL.
        method: HTTP method.
        token: Bearer token, if any.
        data: Request body.
        headers: Extra headers.

    Returns:
        Status code and response bytes (also for error statuses).

    """
    merged: dict[str, str] = dict(headers or {})
    if token is not None:
        merged["Authorization"] = f"Bearer {token}"
    parts: SplitResult = urlsplit(url=url)
    connection: HTTPConnection = HTTPConnection(
        host=parts.hostname or "",
        port=parts.port,
        timeout=10,
    )
    try:
        connection.request(
            method=method,
            url=parts.path,
            body=data,
            headers=merged,
        )
        response: HTTPResponse = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _bundle(*, data: bytes = b"binary") -> bytes:
    """Build a valid result ZIP.

    Args:
        data: Artifact content.

    Returns:
        The ZIP bytes.

    """
    buffer: BytesIO = BytesIO()
    archive: ZipFile
    with ZipFile(file=buffer, mode="w") as archive:
        archive.writestr(
            zinfo_or_arcname="manifest.json",
            data=dumps(
                obj={
                    "schema": 1,
                    "os": "linux",
                    "architecture": "arm64",
                    "artifacts": [
                        {
                            "path": "app",
                            "size": len(data),
                            "sha256": sha256(data=data).hexdigest(),
                        },
                    ],
                },
            ),
        )
        archive.writestr(zinfo_or_arcname="artifacts/app", data=data)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def reset_messages() -> None:
    """Start every test with an empty progress log."""
    messages.clear()


def test_bootstrap_scripts_are_only_served_under_the_secret(
    tmp_path: Path,
) -> None:
    """The token-bearing scripts need the unguessable path segment.

    Args:
        tmp_path: Pytest temporary directory.

    """
    state: JobState = job_state(tmp_path=tmp_path)
    secret: str = state.credentials.bootstrap
    with serving(state=state) as base:
        for name in ("install.ps1", "install-linux.sh", "install-macos.sh"):
            status, body = call(url=f"{base}/{secret}/{name}")
            expect(actual=status, expected=200)
            check(
                condition=state.credentials.token.encode() in body,
                reason=f"{name} must carry the job token",
            )
            expect(actual=call(url=f"{base}/{name}")[0], expected=404)
            expect(actual=call(url=f"{base}/wrong/{name}")[0], expected=404)
        _, macos = call(url=f"{base}/{secret}/install-macos.sh")
        check(condition=b'OS_NAME = "macos"' in macos, reason="macos worker")


def test_payload_requires_the_bearer_token(tmp_path: Path) -> None:
    """Only a request with the job token receives the source payload.

    Args:
        tmp_path: Pytest temporary directory.

    """
    state: JobState = job_state(tmp_path=tmp_path)
    with serving(state=state) as base:
        url: str = f"{base}/v1/payload"
        expect(actual=call(url=url)[0], expected=401)
        expect(
            actual=call(url=url, token=state.credentials.token[::-1])[0],
            expected=401,
        )
        status, body = call(url=url, token=state.credentials.token)
    expect(actual=status, expected=200)
    expect(actual=body, expected=b"payload-bytes")


def test_progress_is_authenticated_and_forwarded(tmp_path: Path) -> None:
    """Authenticated progress reaches the host's sink; others are refused.

    Args:
        tmp_path: Pytest temporary directory.

    """
    state: JobState = job_state(tmp_path=tmp_path)
    with serving(state=state) as base:
        url: str = f"{base}/v1/progress"
        expect(
            actual=call(url=url, method="POST", data=b"x")[0],
            expected=401,
        )
        expect(
            actual=call(
                url=url,
                method="POST",
                token=state.credentials.token,
                data=b"  building  ",
            )[0],
            expected=200,
        )
    expect(actual=messages, expected=["building"])


def test_error_report_finishes_the_job(tmp_path: Path) -> None:
    """A worker failure is recorded and ends the wait.

    Args:
        tmp_path: Pytest temporary directory.

    """
    state: JobState = job_state(tmp_path=tmp_path)
    with serving(state=state) as base:
        expect(
            actual=call(
                url=f"{base}/v1/error",
                method="POST",
                token=state.credentials.token,
                data=b"boom",
            )[0],
            expected=200,
        )
    expect(actual=state.error, expected="boom")
    check(condition=state.done.is_set(), reason="job must be finished")


def test_valid_result_is_materialized_and_finishes_the_job(
    tmp_path: Path,
) -> None:
    """A verified bundle is written out before the job is marked done.

    Args:
        tmp_path: Pytest temporary directory.

    """
    state: JobState = job_state(tmp_path=tmp_path)
    bundle: bytes = _bundle()
    with serving(state=state) as base:
        status, _ = call(
            url=f"{base}/v1/result",
            method="PUT",
            token=state.credentials.token,
            data=bundle,
            headers={"X-Artifact-SHA256": sha256(data=bundle).hexdigest()},
        )
    expect(actual=status, expected=200)
    expect(actual=(state.output / "app").read_bytes(), expected=b"binary")
    expect(actual=present(value=state.result).architecture, expected="arm64")
    check(condition=state.done.is_set(), reason="job must be finished")


@pytest.mark.parametrize(
    "case",
    ["no-auth", "bad-hash", "not-a-zip", "too-large", "wrong-path"],
)
def test_bad_results_are_rejected_without_finishing(
    tmp_path: Path,
    case: str,
) -> None:
    """Unauthenticated, tampered, malformed or oversized uploads fail.

    Args:
        tmp_path: Pytest temporary directory.
        case: Scenario under test.

    """
    _state: JobState = job_state(
        tmp_path=tmp_path,
        maximum=10 if case == "too-large" else 1_000_000,
    )
    bundle: bytes = b"not a zip" if case == "not-a-zip" else _bundle()
    digest: str = (
        "0" * 64 if case == "bad-hash" else sha256(data=bundle).hexdigest()
    )
    token: str | None = None if case == "no-auth" else _state.credentials.token
    path: str = "/v1/elsewhere" if case == "wrong-path" else "/v1/result"
    with serving(state=_state) as base:
        status, _ = call(
            url=base + path,
            method="PUT",
            token=token,
            data=bundle,
            headers={"X-Artifact-SHA256": digest},
        )
    expected_status: int = {"no-auth": 401, "wrong-path": 404}.get(case, 400)
    expect(actual=status, expected=expected_status)
    check(condition=not _state.done.is_set(), reason="must not finish")
    check(condition=_state.result is None, reason="nothing accepted")


def test_unknown_routes_are_not_found(tmp_path: Path) -> None:
    """Anything else is a 404, and request logs stay quiet.

    Args:
        tmp_path: Pytest temporary directory.

    """
    state: JobState = job_state(tmp_path=tmp_path)
    with serving(state=state) as base:
        expect(actual=call(url=f"{base}/nothing")[0], expected=404)
        expect(
            actual=call(
                url=f"{base}/v1/unknown",
                method="POST",
                token=state.credentials.token,
                data=b"",
            )[0],
            expected=404,
        )
