# Copyright (c) 2026 Project contributors.
"""Minimal HTTP(S) requests used by the charter backends."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast
from urllib.parse import urlparse

from tools.charter.errors import CharterError

if TYPE_CHECKING:
    from types import ModuleType
    from urllib.request import OpenerDirector, Request


def http_request(
    *,
    url: str,
    timeout: float,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> bytes:
    """Perform one HTTP(S) request and return the response body.

    Args:
        url: ``http`` or ``https`` URL.
        timeout: Seconds before giving up.
        data: Request body; makes the request a ``POST``.
        headers: Extra request headers.

    Returns:
        The response body.

    Raises:
        CharterError: If the URL scheme is not HTTP(S).

    """
    if urlparse(url=url).scheme not in {"http", "https"}:
        msg: str = f"Refusing URL with unsupported scheme: {url}"
        raise CharterError(msg)
    module: ModuleType = import_module(name="urllib.request")
    request: Request = module.Request(
        url=url,
        data=data,
        headers=headers or {},
    )
    opener: OpenerDirector = module.build_opener()
    with opener.open(fullurl=request, timeout=timeout) as response:
        return cast(typ="bytes", val=response.read())
