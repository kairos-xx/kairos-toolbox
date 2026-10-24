# Copyright (c) 2026 Project contributors.
"""Public exposure and URL shortening for the build host.

The charter implementations live in the independent ``tools.charter``
package; this facade keeps the stationer's names working and folds
``[tool.charter]`` settings in behind the explicit arguments.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Final

from tools.charter import (
    Charter,
    CharterConstants,
    CharterError,
    Settings,
    SettingsLoader,
    http_request,
    open_charter,
)
from tools.charter import install_ngrok as charter_install_ngrok
from tools.charter import shorten_url as charter_shorten_url
from tools.charter import start_ngrok as charter_start_ngrok

if TYPE_CHECKING:
    from pathlib import Path
__all__: tuple[str, ...] = (
    "Charter",
    "ExposureConstants",
    "ExposureError",
    "http_request",
    "install_ngrok",
    "open_charter",
    "shorten_url",
    "start_charter",
    "start_ngrok",
)
ExposureError: type[CharterError] = CharterError


class ExposureConstants:
    """Group exposure constants.

    Attributes:
        backends: Accepted exposure backend names.
        ngrok_api: Local ngrok inspection API listing active tunnels.
        ngrok_download: Official ngrok tarball URL, with an ``{arch}`` slot.
        ngrok_architectures: Linux CPU names mapped to ngrok's.
        shortener: Anonymous URL shortening endpoint.
        url_timeout: Seconds to wait for a log-discovered charter URL.
        ngrok_timeout: Seconds to wait for ngrok to establish a charter.
        stop_timeout: Seconds to wait after asking a charter to stop.

    """

    backends: Final[frozenset[str]] = CharterConstants.backends
    ngrok_api: Final[str] = CharterConstants.ngrok_api
    ngrok_download: Final[str] = CharterConstants.ngrok_download
    ngrok_architectures: Final[dict[str, str]] = (
        CharterConstants.ngrok_architectures
    )
    shortener: Final[str] = CharterConstants.shortener
    url_timeout: Final[float] = CharterConstants.url_timeout
    ngrok_timeout: Final[float] = CharterConstants.ngrok_timeout
    stop_timeout: Final[float] = CharterConstants.stop_timeout


def _settings(*, authtoken: str | None) -> Settings:
    """Load ``[tool.charter]`` settings, overriding the ngrok authtoken.

    Args:
        authtoken: Explicit authtoken; configuration and ``NGROK_AUTHTOKEN``
            apply when ``None``.

    Returns:
        Resolved charter settings.

    """
    loaded: Settings = SettingsLoader.load()
    if authtoken is None:
        return loaded
    return replace(loaded, ngrok_authtoken=authtoken)


def install_ngrok() -> Path:
    """Find ngrok, installing it with an official method when absent.

    Returns:
        The ngrok executable.

    Raises:
        ExposureError: If ngrok cannot be found or installed.

    """
    return charter_install_ngrok(settings=SettingsLoader.load())


def start_ngrok(*, port: int, authtoken: str | None) -> Charter:
    """Install and start ngrok, then wait for its public HTTPS URL.

    Args:
        port: Local port to expose.
        authtoken: Explicit authtoken; otherwise the configuration, the
            environment or the existing ngrok configuration is used.

    Returns:
        The running charter.

    Raises:
        ExposureError: If no charter is established in time.

    """
    return charter_start_ngrok(
        port=port,
        settings=_settings(authtoken=authtoken),
    )


def start_charter(
    *,
    backend: str,
    port: int,
    authtoken: str | None,
) -> Charter | None:
    """Start the selected exposure backend.

    Args:
        backend: ``direct``, ``ngrok``, ``pinggy``, ``tunnl`` or ``runlocal``.
        port: Local port to expose.
        authtoken: ngrok authtoken, used only by the ``ngrok`` backend.

    Returns:
        The charter, or ``None`` for ``direct`` (nothing to expose).

    Raises:
        ExposureError: If the backend is unknown or fails to start.

    """
    return open_charter(
        backend=backend,
        port=port,
        settings=_settings(authtoken=authtoken),
    )


def shorten_url(*, url: str, alias: str | None = None) -> str:
    """Create a short URL without an API key.

    Args:
        url: URL to shorten.
        alias: Requested custom alias, if any.

    Returns:
        The HTTPS short URL.

    Raises:
        ExposureError: If shortening fails or the response is malformed.

    """
    return charter_shorten_url(
        url=url,
        alias=alias,
        settings=SettingsLoader.load(),
    )
