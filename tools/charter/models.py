# Copyright (c) 2026 Project contributors.
"""Typed values shared by the charter backends."""

from __future__ import annotations

from dataclasses import dataclass
from subprocess import Popen, TimeoutExpired
from typing import TYPE_CHECKING, Final, Self

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType
type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)


class CharterConstants:
    """Group charter constants.

    Attributes:
        backends: Accepted charter backend names.
        ngrok_api: Local ngrok inspection API listing active tunnels.
        ngrok_download: Official ngrok tarball URL, with an ``{arch}`` slot.
        ngrok_architectures: Linux CPU names mapped to ngrok's.
        shortener: Anonymous URL shortening endpoint.
        url_timeout: Seconds to wait for a log-discovered charter URL.
        ngrok_timeout: Seconds to wait for ngrok to establish a charter.
        stop_timeout: Seconds to wait after asking a charter to stop.

    """

    backends: Final[frozenset[str]] = frozenset(
        {"direct", "ngrok", "pinggy", "tunnl", "runlocal"},
    )
    ngrok_api: Final[str] = "http://127.0.0.1:4040/api/tunnels"
    ngrok_download: Final[str] = (
        "https://bin.ngrok.com/c/bNyj1mQVY4c/ngrok-v3-stable-linux-{arch}.tgz"
    )
    ngrok_architectures: Final[dict[str, str]] = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    shortener: Final[str] = "https://shrtr.top/api/v1/shorten"
    url_timeout: Final[float] = 20.0
    ngrok_timeout: Final[float] = 15.0
    stop_timeout: Final[float] = 5.0


@dataclass(slots=True)
class Charter:
    """Own one charter subprocess and its public HTTPS endpoint.

    Attributes:
        process: The backend process.
        public_url: Public HTTPS URL, without a trailing slash.
        log_path: Temporary log the URL was read from, removed on close.
        stop_timeout_seconds: Seconds to wait after asking the backend
            process to stop.
        keep_alive: Whether the charter outlives :meth:`close` and its
            parent process. Only :meth:`stop` (or an expiry) ends it.

    """

    process: Popen[bytes]
    public_url: str
    log_path: Path | None = None
    stop_timeout_seconds: float = CharterConstants.stop_timeout
    keep_alive: bool = False

    def close(self) -> None:
        """End a scoped charter; leave a keep-alive charter running.

        ``with`` blocks and cleanup paths call this, so a charter started
        with ``keep_alive`` survives them. Use :meth:`stop` to end it.
        """
        if not self.keep_alive:
            self.stop()

    def stop(self) -> None:
        """Stop the backend now and remove its temporary log."""
        if self.process.poll() is None:
            self.process.terminate()
            try:
                _ = self.process.wait(timeout=self.stop_timeout_seconds)
            except TimeoutExpired:
                self.process.kill()
                _ = self.process.wait(timeout=self.stop_timeout_seconds)
        if self.log_path is not None:
            self.log_path.unlink(missing_ok=True)

    def __enter__(self) -> Self:
        """Return the running charter for ``with`` blocks.

        Returns:
            This charter.

        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop the backend when a ``with`` block ends.

        Args:
            exc_type: Exception type being propagated, if any.
            exc_value: Exception being propagated, if any.
            traceback: Traceback being propagated, if any.

        """
        _ = (exc_type, exc_value, traceback)
        self.close()
