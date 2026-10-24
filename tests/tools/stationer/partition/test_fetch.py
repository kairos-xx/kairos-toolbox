# Copyright (c) 2026 Project contributors.
"""Tests for the bounded HTTPS fetch transport."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import TYPE_CHECKING, ClassVar

import pytest
from stationer.partition import fetch as fetch_module
from stationer.partition.fetch import FetchError, FetchOptions, fetch

from tests.tools.stationer.helpers import check, expect

if TYPE_CHECKING:
    from pathlib import Path


class _Handler(BaseHTTPRequestHandler):
    """Serve scripted responses configured per test.

    Attributes:
        steps: Status codes replayed in order; the last repeats.
        payload: Response body sent on success.
        drop_after: Bytes sent before aborting mid-body; ``None``
            sends the complete payload.

    """

    steps: ClassVar[list[int]] = [HTTPStatus.OK]
    payload: ClassVar[bytes] = b"payload"
    drop_after: ClassVar[int | None] = None
    calls: ClassVar[int] = 0

    def do_GET(self) -> None:
        """Serve the scripted response for this attempt."""
        index: int = min(type(self).calls, len(type(self).steps) - 1)
        type(self).calls += 1
        status: int = type(self).steps[index]
        if status != HTTPStatus.OK:
            self.send_error(code=status)
            return
        body: bytes = type(self).payload
        self.send_response(code=HTTPStatus.OK)
        self.send_header("Content-Length", str(object=len(body)))
        self.end_headers()
        if type(self).drop_after is None:
            _ = self.wfile.write(body)
        else:
            _ = self.wfile.write(body[: type(self).drop_after])
        self.wfile.flush()


def _server(
    *,
    steps: list[int] | None = None,
    payload: bytes = b"payload",
    drop_after: int | None = None,
) -> ThreadingHTTPServer:
    """Start a local HTTP server with scripted responses.

    Args:
        steps: Status codes per attempt; the last repeats.
        payload: Body returned on success.
        drop_after: Abort mid-body after this many bytes.

    Returns:
        Running server; caller invokes ``shutdown``/``server_close``.

    """
    _Handler.steps = steps if steps is not None else [200]
    _Handler.payload = payload
    _Handler.drop_after = drop_after
    _Handler.calls = 0
    server: ThreadingHTTPServer = ThreadingHTTPServer(
        server_address=("127.0.0.1", 0),
        RequestHandlerClass=_Handler,
    )
    thread: Thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _url(server: ThreadingHTTPServer) -> str:
    """Build the request URL for a test server.

    Args:
        server: Running test server.

    Returns:
        Loopback HTTP URL.

    """
    host, port = server.server_address[:2]
    return f"http://{host}:{port}/resource"


def _options(*, max_bytes: int = 1 << 30, retries: int = 1) -> FetchOptions:
    """Test options: loopback HTTP allowed, tuned attempts.

    Args:
        max_bytes: Response size cap.
        retries: Request attempts including the first.

    Returns:
        FetchOptions for the local test server.

    """
    return FetchOptions(
        timeout_seconds=5,
        max_bytes=max_bytes,
        retries=retries,
        allowed_schemes=frozenset({"http", "https"}),
    )


def _no_sleep(_delay: float) -> None:
    """Stand in for retry backoff during tests.

    Args:
        _delay: Ignored delay in seconds.

    """


def test_fetch_rejects_non_https_scheme(tmp_path: Path) -> None:
    """Refuse URLs outside the default HTTPS allowlist.

    Args:
        tmp_path: Per-test temporary directory.

    """
    for url in ("http://example.invalid/x", "file:///etc/passwd"):
        with pytest.raises(
            expected_exception=FetchError,
            match="Refusing URL",
        ):
            fetch(
                url=url,
                destination=tmp_path / "out.bin",
                options=FetchOptions(timeout_seconds=1),
            )


def test_fetch_enforces_size_cap(tmp_path: Path) -> None:
    """Abort oversized responses and leave no destination file.

    Args:
        tmp_path: Per-test temporary directory.

    """
    server: ThreadingHTTPServer = _server(payload=b"x" * 4096)
    try:
        destination: Path = tmp_path / "big.bin"
        with pytest.raises(
            expected_exception=FetchError,
            match="exceeded",
        ):
            fetch(
                url=_url(server=server),
                destination=destination,
                options=_options(max_bytes=1024),
            )
    finally:
        server.shutdown()
        server.server_close()
    check(
        condition=not destination.exists(),
        reason="Oversized response left a destination file.",
    )
    check(
        condition=not list(tmp_path.glob(pattern=".*")),
        reason="Aborted fetch left a temporary file.",
    )


def test_fetch_retries_then_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry transient statuses, then surface the last failure.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Fixture neutralizing retry backoff.

    """
    monkeypatch.setattr(
        target=fetch_module,
        name="sleep",
        value=_no_sleep,
    )
    server: ThreadingHTTPServer = _server(steps=[503, 503, 200])
    try:
        destination: Path = tmp_path / "ok.bin"
        result: Path = fetch(
            url=_url(server=server),
            destination=destination,
            options=_options(retries=3),
        )
        expect(actual=result.read_bytes(), expected=b"payload")
        expect(actual=_Handler.calls, expected=3)
    finally:
        server.shutdown()
        server.server_close()
    server = _server(steps=[503])
    try:
        with pytest.raises(expected_exception=FetchError, match="HTTP 503"):
            fetch(
                url=_url(server=server),
                destination=tmp_path / "never.bin",
                options=_options(retries=2),
            )
        expect(actual=_Handler.calls, expected=2)
    finally:
        server.shutdown()
        server.server_close()


def test_fetch_writes_atomically(tmp_path: Path) -> None:
    """Leave no destination when the stream aborts mid-body.

    Args:
        tmp_path: Per-test temporary directory.

    """
    server: ThreadingHTTPServer = _server(
        payload=b"y" * 8192,
        drop_after=256,
    )
    destination: Path = tmp_path / "partial.bin"
    try:
        with pytest.raises(expected_exception=FetchError):
            fetch(
                url=_url(server=server),
                destination=destination,
                options=_options(),
            )
    finally:
        server.shutdown()
        server.server_close()
    check(
        condition=not destination.exists(),
        reason="Interrupted stream left a destination file.",
    )
    check(
        condition=not list(tmp_path.iterdir()),
        reason="Interrupted stream left temporary files.",
    )


def test_fetch_success_writes_body(tmp_path: Path) -> None:
    """Write the complete body to the destination path.

    Args:
        tmp_path: Per-test temporary directory.

    """
    server: ThreadingHTTPServer = _server(payload=b"hello-world")
    destination: Path = tmp_path / "nested" / "out.bin"
    try:
        result: Path = fetch(
            url=_url(server=server),
            destination=destination,
            options=_options(),
        )
    finally:
        server.shutdown()
        server.server_close()
    expect(actual=result, expected=destination)
    expect(actual=destination.read_bytes(), expected=b"hello-world")
