# Copyright (c) 2026 Project contributors.
"""Tests for the minimal HTTP request seam."""

from __future__ import annotations

from typing import final

import pytest

from tools.charter import http
from tools.charter.errors import CharterError
from tools.charter.http import http_request

from .helpers import check, expect


@final
class _Response:
    """A context-manager response returning a fixed body."""

    def __init__(self, *, body: bytes) -> None:
        """Store the body.

        Args:
            body: Bytes ``read`` returns.

        """
        self._body: bytes = body

    def read(self) -> bytes:
        """Return the fixed body.

        Returns:
            The canned bytes.

        """
        return self._body

    def __enter__(self) -> _Response:
        """Enter the response context.

        Returns:
            This response.

        """
        return self

    def __exit__(self, *args: object) -> None:
        """Leave the response context.

        Args:
            args: Unused exception details.

        """
        _ = args


@final
class _Opener:
    """An opener whose ``open`` returns a canned response."""

    def __init__(self, *, body: bytes, seen: list[str]) -> None:
        """Store the canned body and recorder.

        Args:
            body: Bytes the response returns.
            seen: Recorder for the requested URL.

        """
        self._body: bytes = body
        self._seen: list[str] = seen

    def open(self, *, fullurl: _Request, timeout: float) -> _Response:
        """Pretend to open a request.

        Args:
            fullurl: The prepared request.
            timeout: Ignored timeout.

        Returns:
            The canned response.

        """
        _ = timeout
        self._seen.append(str(object=fullurl.full_url))
        return _Response(body=self._body)


@final
class _Request:
    """A ``urllib.request.Request`` stand-in holding the URL."""

    def __init__(
        self,
        *,
        url: str,
        data: bytes | None,
        headers: dict[str, str],
    ) -> None:
        """Store the request fields.

        Args:
            url: Requested URL.
            data: Request body.
            headers: Request headers.

        """
        self.full_url: str = url
        self.data: bytes | None = data
        self.headers: dict[str, str] = headers


@final
class _Urllib:
    """A minimal ``urllib.request`` stand-in."""

    def __init__(self, *, body: bytes, seen: list[str]) -> None:
        """Create the fake module surface.

        Args:
            body: Bytes the response returns.
            seen: Recorder for the requested URL.

        """
        self.Request = _Request
        self._body: bytes = body
        self._seen: list[str] = seen

    def build_opener(self) -> _Opener:
        """Return the canned opener.

        Returns:
            The fake opener.

        """
        return _Opener(body=self._body, seen=self._seen)


def test_request_only_speaks_http() -> None:
    """The HTTP seam refuses other URL schemes."""
    with pytest.raises(expected_exception=CharterError, match="scheme"):
        _ = http_request(url="file:///etc/passwd", timeout=1)


def test_request_returns_the_response_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful request returns the response bytes.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    seen: list[str] = []

    def fake_import(*, name: str) -> _Urllib:
        """Return the canned urllib module.

        Args:
            name: Ignored module name.

        Returns:
            The fake module.

        """
        _ = name
        return _Urllib(body=b"ok", seen=seen)

    monkeypatch.setattr(
        target=http,
        name="import_module",
        value=fake_import,
    )
    expect(
        actual=http_request(url="https://example.com/x", timeout=1),
        expected=b"ok",
    )
    check(condition="example.com" in seen[0], reason="URL sent")
