# Copyright (c) 2026 Project contributors.
"""Anonymous URL shortening for exposed endpoints."""

from __future__ import annotations

from json import JSONDecodeError, dumps, loads
from typing import TYPE_CHECKING

from tools.charter.errors import CharterError
from tools.charter.http import http_request
from tools.charter.settings import Settings, SettingsLoader

if TYPE_CHECKING:
    from tools.charter.models import JsonValue


def shorten_url(
    *,
    url: str,
    alias: str | None = None,
    settings: Settings | None = None,
) -> str:
    """Create a short URL without an API key.

    Args:
        url: URL to shorten.
        alias: Requested custom alias, if any.
        settings: Resolved settings; loaded from ``pyproject.toml`` when
            omitted. ``shortener`` selects the endpoint.

    Returns:
        The HTTPS short URL.

    Raises:
        CharterError: If shortening fails or the response is malformed.

    """
    resolved: Settings = settings or SettingsLoader.load()
    body: dict[str, str] = {"url": url}
    if alias:
        body["alias"] = alias
    try:
        document: JsonValue = loads(
            s=http_request(
                url=resolved.shortener,
                data=dumps(obj=body).encode(),
                headers={"Content-Type": "application/json"},
                timeout=10,
            ),
        )
    except (OSError, ValueError, JSONDecodeError) as error:
        msg: str = f"URL shortening failed: {error}"
        raise CharterError(msg) from error
    short: JsonValue = (
        document.get("short_url") if isinstance(document, dict) else None
    )
    if not isinstance(short, str) or not short.startswith("https://"):
        msg = "URL shortening response did not contain a short_url."
        raise CharterError(msg)
    return short
