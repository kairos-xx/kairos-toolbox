# Copyright (c) 2026 Project contributors.
"""Authenticated one-job HTTP control plane for remote build workers."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from hmac import compare_digest
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Final, cast, final, override
from urllib.parse import urlparse
from zipfile import BadZipFile

from tools.stationer.conjure import materialize_result
from tools.stationer.progress import StepMarker
from tools.stationer.remote_workers import posix_worker, windows_worker

if TYPE_CHECKING:
    from collections.abc import Callable
    from io import BufferedIOBase
    from pathlib import Path
    from threading import Event

    from tools.stationer.conjure import (
        Credentials,
        Payload,
        RemoteSpec,
        ResultBundle,
    )
    from tools.stationer.progress import ProgressTracker


class ServerConstants:
    """Group server constants.

    Attributes:
        progress_limit: Largest accepted progress message or output chunk
            in bytes.
        error_limit: Largest accepted failure report in bytes.
        identity_path: HTTP path exposing the server identity probe.
        identity: Response payload returned by the server identity probe.
        scripts: Worker script names mapped to the OS they build for.

    """

    progress_limit: Final[int] = 16_384
    error_limit: Final[int] = 1_048_576
    identity_path: Final[str] = "/.well-known/stationer-job-server"
    identity: Final[bytes] = b"stationer-job-server-v1\n"
    scripts: Final[dict[str, str]] = {
        "install.ps1": "windows",
        "install-linux.sh": "linux",
        "install-macos.sh": "macos",
    }


@dataclass(slots=True, kw_only=True)
class JobState:
    """Hold the state of the single running build job.

    Attributes:
        spec: What the worker must do.
        credentials: Job secrets.
        payload: Source payload served to the worker.
        output: Directory that receives the verified artifacts.
        public_url: URL the worker reaches this server at.
        target: Operating system the job builds for.
        done: Set when the job ended, successfully or not.
        say: Sink for worker progress messages.
        error: Failure report from the worker, if any.
        result: Verified result, once accepted.
        track: Interprets step events and forwarded step output into
            progress updates; plain messages go to ``say`` without it.

    """

    spec: RemoteSpec
    credentials: Credentials
    payload: Payload
    output: Path
    public_url: str
    target: str
    done: Event
    say: Callable[[str], None]
    error: str | None = None
    result: ResultBundle | None = None
    track: ProgressTracker | None = None


@final
class JobServer(ThreadingHTTPServer):
    """Serve exactly one authenticated build job.

    Attributes:
        state: The job being served; assigned after construction.

    """

    state: JobState


@final
class JobHandler(BaseHTTPRequestHandler):
    """Serve worker scripts and payload, and receive progress and results."""

    @override
    def log_message(self, format: str, *args: str | int) -> None:
        """Suppress request logging, which would expose URLs and tokens.

        Args:
            format: Unused log format.
            *args: Unused log arguments.

        """

    def __getattr__(self, name: str) -> Callable[[], None]:
        """Resolve the handler names ``http.server`` dispatches to.

        The standard library looks request handlers up by the exact names
        ``do_GET``, ``do_PUT`` and ``do_POST``; they are served from the
        lowercase methods of this class.

        Args:
            name: Attribute being looked up.

        Returns:
            The handler for ``name``.

        Raises:
            AttributeError: For any other name, so unsupported methods are
                answered with ``501 Not Implemented``.

        """
        routes: dict[str, Callable[[], None]] = {
            "do_GET": self._get,
            "do_PUT": self._put,
            "do_POST": self._post,
        }
        if name not in routes:
            raise AttributeError(name)
        return routes[name]

    @staticmethod
    def _progress(*, state: JobState, text: str) -> None:
        """Route a progress post to the tracker or the plain message sink.

        Args:
            state: The running job.
            text: Request body: a JSON step event or a plain message.

        """
        event: tuple[int, str] | None = StepMarker.event(body=text)
        if event is not None and state.track is not None:
            state.track.step(index=event[0], message=event[1])
        elif event is not None:
            state.say(event[1])
        else:
            state.say(text)

    def _get(self) -> None:
        """Serve a bootstrap script or the authenticated payload."""
        path: str = urlparse(url=self.path).path
        if path == ServerConstants.identity_path:
            self._send(
                body=ServerConstants.identity,
                content_type="text/plain",
            )
            return
        state: JobState = self._job()
        if path == "/v1/payload":
            if self._authorized():
                self._send(
                    body=state.payload.path.read_bytes(),
                    content_type="application/zip",
                )
            else:
                self.send_error(code=HTTPStatus.UNAUTHORIZED)
            return
        script: str | None = self._script(path=path)
        if script is None:
            self.send_error(code=HTTPStatus.NOT_FOUND)
            return
        self._send(body=script.encode(), content_type="text/plain")

    def _put(self) -> None:
        """Receive and verify the worker's result bundle."""
        if urlparse(url=self.path).path != "/v1/result":
            self.send_error(code=HTTPStatus.NOT_FOUND)
            return
        if not self._authorized():
            self.send_error(code=HTTPStatus.UNAUTHORIZED)
            return
        try:
            self._accept_result()
        except (BadZipFile, KeyError, ValueError) as error:
            self.send_error(
                code=HTTPStatus.BAD_REQUEST,
                message=str(object=error),
            )
            return
        self._job().done.set()
        self._send(body=b"validated\n", content_type="text/plain")

    def _post(self) -> None:
        """Receive a progress event, forwarded output or a failure report."""
        path: str = urlparse(url=self.path).path
        if path not in {"/v1/progress", "/v1/output", "/v1/error"}:
            self.send_error(code=HTTPStatus.NOT_FOUND)
            return
        if not self._authorized():
            self.send_error(code=HTTPStatus.UNAUTHORIZED)
            return
        state: JobState = self._job()
        failure: bool = path == "/v1/error"
        try:
            text: str = (
                self._body(
                    maximum=ServerConstants.error_limit
                    if failure
                    else ServerConstants.progress_limit,
                )
                .decode(errors="replace")
                .strip()
            )
        except ValueError as error:
            self.send_error(
                code=HTTPStatus.BAD_REQUEST,
                message=str(object=error),
            )
            return
        if failure:
            state.error = text
            state.done.set()
        elif path == "/v1/output":
            if state.track is not None:
                state.track.output(text=text)
        else:
            self._progress(state=state, text=text)
        self._send(body=b"ok\n", content_type="text/plain")

    def _job(self) -> JobState:
        """Return the job this handler's server is running.

        Returns:
            The single job state.

        """
        server: JobServer = cast(typ="JobServer", val=self.server)
        return server.state

    def _accept_result(self) -> None:
        """Verify and store the uploaded result bundle.

        Raises:
            ValueError: If the transport digest or the bundle is invalid.

        """
        state: JobState = self._job()
        data: bytes = self._body(maximum=state.spec.max_result_bytes)
        expected: str = self.headers.get("X-Artifact-SHA256", "").lower()
        if not expected or not compare_digest(
            sha256(data=data).hexdigest(),
            expected,
        ):
            msg: str = "Result SHA-256 verification failed."
            raise ValueError(msg)
        state.result = materialize_result(bundle=data, output=state.output)

    def _script(self, *, path: str) -> str | None:
        """Render the worker script a secret-prefixed path asks for.

        Args:
            path: Request path.

        Returns:
            The script text, or ``None`` when the path is not a worker route.

        """
        state: JobState = self._job()
        parts: list[str] = path.strip("/").split(sep="/")
        if (
            len(parts) != len(("secret", "script"))
            or not compare_digest(parts[0], state.credentials.bootstrap)
            or parts[1] not in ServerConstants.scripts
        ):
            return None
        os_name: str = ServerConstants.scripts[parts[1]]
        if os_name == "windows":
            return windows_worker(
                base_url=state.public_url,
                credentials=state.credentials,
                payload=state.payload,
                spec=state.spec,
            )
        return posix_worker(
            base_url=state.public_url,
            credentials=state.credentials,
            payload=state.payload,
            spec=state.spec,
            os_name=os_name,
        )

    def _authorized(self) -> bool:
        """Return whether the request carries the job's bearer token.

        Returns:
            ``True`` for a matching token.

        """
        supplied: str = self.headers.get("Authorization", "")
        expected: str = f"Bearer {self._job().credentials.token}"
        return compare_digest(supplied, expected)

    def _body(self, *, maximum: int) -> bytes:
        """Read the request body, refusing oversized ones unread.

        Args:
            maximum: Largest accepted body in bytes.

        Returns:
            The body bytes.

        Raises:
            ValueError: If the declared length is invalid or too large.

        """
        length: int = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > maximum:
            msg: str = "Request body exceeds the configured limit."
            raise ValueError(msg)
        stream: BufferedIOBase = self.rfile
        return stream.read(length)

    def _send(self, *, body: bytes, content_type: str) -> None:
        """Write a 200 response that must not be cached.

        Args:
            body: Response bytes.
            content_type: MIME type.

        """
        self.send_response(code=HTTPStatus.OK)
        self.send_header(keyword="Content-Type", value=content_type)
        self.send_header(keyword="Content-Length", value=str(object=len(body)))
        self.send_header(keyword="Cache-Control", value="no-store")
        self.end_headers()
        _ = self.wfile.write(body)
