# Copyright (c) 2026 Project contributors.
"""Bounded, retrying HTTPS fetch to atomic destinations."""

from __future__ import annotations

from dataclasses import dataclass
from http.client import IncompleteRead
from importlib import import_module
from json import loads as json_loads
from os import fdopen as os_fdopen
from pathlib import Path
from tempfile import TemporaryDirectory, mkstemp
from time import sleep
from typing import TYPE_CHECKING, Final, Protocol, cast, final
from urllib.error import HTTPError, URLError
from urllib.parse import ParseResult, urlparse

if TYPE_CHECKING:
    from types import ModuleType
    from urllib.request import OpenerDirector, Request
    from urllib.response import addinfourl

    from .json_types import JsonObject


class FetchError(RuntimeError):
    """Failed to download a remote resource."""


@final
class Constants:
    """Group module-level constants.

    Attributes:
        user_agent: Grouped module constant.
        retryable: Grouped module constant.
        chunk: Grouped module constant.
        backoff: Grouped module constant.

    """

    user_agent: Final[str] = "kairos-toolbox-partition/1.0"
    retryable: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
    chunk: Final[int] = 64 * 1024
    backoff: Final[float] = 0.5


@dataclass(frozen=True, slots=True, kw_only=True)
class FetchOptions:
    """Optional tuning for one ``fetch`` transfer.

    Attributes:
        timeout_seconds: Socket timeout in seconds.
        max_bytes: Maximum response size in bytes.
        retries: Maximum request attempts including the first.
        allowed_schemes: URL schemes permitted for the request. The
            default is HTTPS only; tests inject ``http`` for local
            servers.

    """

    timeout_seconds: int = 30
    max_bytes: int = 1 << 30
    retries: int = 3
    allowed_schemes: frozenset[str] = frozenset({"https"})


class ResponseReader(Protocol):
    """Common positional read interface of urllib response streams."""

    def read(self, size: int, /) -> bytes:
        """Read at most the requested number of response bytes.

        Args:
            size: Maximum number of bytes to consume.

        Returns:
            Available bytes, or empty bytes at end of stream.

        """
        ...


def _open(*, url: str, options: FetchOptions) -> addinfourl:
    """Open ``url`` with bounded retries on transient failures.

    Args:
        url: Request URL, already scheme-validated.
        options: Tuning for timeout and attempts.

    Returns:
        Open response stream.

    Raises:
        FetchError: When every attempt fails or one fails permanently.

    """
    module: ModuleType = import_module(name="urllib.request")
    prepared: Request = module.Request(
        url=url,
        headers={
            "User-Agent": Constants.user_agent,
            "Accept-Encoding": "identity",
        },
    )
    opener: OpenerDirector = module.build_opener()
    delay: float = Constants.backoff
    failure: FetchError = FetchError(f"No attempts configured for {url}.")
    for attempt in range(options.retries):
        if attempt:
            sleep(delay)
            delay = min(delay * 2.0, 10.0)
        try:
            return cast(
                typ="addinfourl",
                val=opener.open(
                    fullurl=prepared,
                    timeout=options.timeout_seconds,
                ),
            )
        except HTTPError as http_error:
            failure = FetchError(
                f"HTTP {http_error.code} for {url}: {http_error.reason}",
            )
            if http_error.code not in Constants.retryable:
                raise failure from http_error
        except URLError as url_error:
            failure = FetchError(
                f"Request failed for {url}: {url_error.reason}",
            )
    raise failure


def fetch(
    *,
    url: str,
    destination: Path,
    options: FetchOptions | None = None,
) -> Path:
    """Stream ``url`` to ``destination`` atomically with a size cap.

    Args:
        url: URL to download; HTTPS unless ``options`` widens the
            scheme allowlist.
        destination: Final path for the response body.
        options: Tuning for timeout, response-size limit, attempts,
            and permitted schemes.

    Returns:
        The destination path on success.

    Raises:
        FetchError: When the URL is refused, every attempt fails, the
            response exceeds ``max_bytes``, or the stream ends early.

    """
    resolved: FetchOptions = options if options is not None else FetchOptions()
    parsed: ParseResult = urlparse(url=url)
    if (
        parsed.scheme.lower() not in resolved.allowed_schemes
        or not parsed.netloc
    ):
        msg: str = f"Refusing URL: {url!r}."
        raise FetchError(msg)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
    )
    temporary: Path = Path(temporary_name)
    received: int = 0
    declared: int | None = None
    try:
        response: addinfourl = _open(url=url, options=resolved)
        length: str | None = response.headers.get("Content-Length")
        if length is not None:
            declared = int(length)
        reader: ResponseReader = cast(typ="ResponseReader", val=response)
        with response, os_fdopen(fd=descriptor, mode="wb") as stream:
            while chunk := reader.read(
                min(Constants.chunk, resolved.max_bytes - received + 1),
            ):
                received += len(chunk)
                if received > resolved.max_bytes:
                    break
                _ = stream.write(chunk)
            stream.flush()
    except FetchError:
        temporary.unlink(missing_ok=True)
        raise
    except (IncompleteRead, ConnectionError, OSError) as stream_error:
        temporary.unlink(missing_ok=True)
        msg = f"Interrupted response from {url}: {stream_error}"
        raise FetchError(msg) from stream_error
    if received > resolved.max_bytes or (
        declared is not None and received != declared
    ):
        temporary.unlink(missing_ok=True)
        msg = (
            f"Response from {url} exceeded {resolved.max_bytes} bytes."
            if received > resolved.max_bytes
            else (
                f"Truncated response from "
                f"{url}: {received} of {declared} bytes."
            )
        )
        raise FetchError(msg)
    temporary.replace(target=destination)
    return destination


def fetch_bytes(*, url: str, options: FetchOptions | None = None) -> bytes:
    """Download ``url`` and return its body bytes.

    Args:
        url: URL to download.
        options: Tuning for timeout, size limit, attempts, schemes.

    Returns:
        Response body bytes.

    """
    with TemporaryDirectory(prefix="partition-fetch-") as folder:
        destination: Path = fetch(
            url=url,
            destination=Path(folder) / "body",
            options=options,
        )
        return destination.read_bytes()


def fetch_json(*, url: str, options: FetchOptions | None = None) -> JsonObject:
    """GET and decode a JSON document over the bounded transport.

    Args:
        url: URL to download.
        options: Tuning for timeout, size limit, attempts, schemes.

    Returns:
        Decoded JSON value.

    """
    body: bytes = fetch_bytes(url=url, options=options)
    return cast(typ="JsonObject", val=json_loads(s=body))
