# Copyright (c) 2026 Project contributors.
"""HTTP handlers for host facts, district commands and URIs."""

from __future__ import annotations

from asyncio import to_thread
from threading import Lock
from time import time
from typing import TYPE_CHECKING, Final, cast, final

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .district_commands import CommandError, DistrictCommands
from .host_info import HostInfo

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .json_config import JsonValue
    from .uri_registry import UriRegistry
    from .widgets import CommandResult
type Body = dict[str, JsonValue]


@final
class ApiConstants:
    """Group API constants.

    Attributes:
        bad_request: Status for a malformed body.
        not_implemented: Status when URI support is not installed.
        no_store: Header that keeps live data out of caches.
        process_rows: Processes listed with the host facts.
        default_ttl: Lifetime of a URI when the caller does not choose.

    """

    bad_request: Final[int] = 400
    not_implemented: Final[int] = 501
    no_store: Final[dict[str, str]] = {"Cache-Control": "no-store"}
    process_rows: Final[int] = 5
    default_ttl: Final[int] = 600


@final
class ApiRoutes:
    """Serve the endpoints behind the command palette and the URI screen."""

    def __init__(
        self,
        *,
        commands: DistrictCommands,
        uri_factory: Callable[[], UriRegistry],
        home: Path,
    ) -> None:
        """Bind the handlers to the layers they serve.

        The URI registry needs the optional Bowman package, so it is built the
        first time a URI endpoint is used rather than when the server starts.

        Args:
            commands: The command layer.
            uri_factory: Builds the URI registry on first use.
            home: Home directory holding the district state.

        """
        self._commands: DistrictCommands = commands
        self._uri_factory: Callable[[], UriRegistry] = uri_factory
        self._uris: UriRegistry | None = None
        self._lock: Lock = Lock()
        self._home: Path = home

    async def host(self) -> JSONResponse:
        """Describe the machine the district runs on.

        Returns:
            The host snapshot.

        """
        snapshot: Body = (
            await to_thread(
                HostInfo.collect,
                home=self._home,
                process_limit=ApiConstants.process_rows,
                now=time(),
            )
        ).as_json()
        return self._json(body=snapshot)

    async def catalog(self) -> JSONResponse:
        """List every command with its inputs.

        Returns:
            The catalogue.

        """
        return self._json(
            body={
                "commands": [
                    spec.as_json() for spec in DistrictCommands.catalog()
                ],
            },
        )

    async def run_command(self, name: str, request: Request) -> JSONResponse:
        """Run one command.

        Args:
            name: Command name.
            request: Request whose body carries ``values`` and ``confirmed``.

        Returns:
            The command's widgets.

        Raises:
            HTTPException: When the request or command input is invalid.

        """
        body: Body = await self._body(request=request)
        values: dict[str, str] = self._values(body=body)
        confirmed: JsonValue = body.get("confirmed", False)
        if not isinstance(confirmed, bool):
            raise HTTPException(
                status_code=ApiConstants.bad_request,
                detail="'confirmed' must be true or false.",
            )
        return await self._result(
            work=lambda: self._commands.run(
                name=name,
                values=values,
                confirmed=confirmed,
            ),
        )

    async def list_uris(self) -> JSONResponse:
        """List the issued URIs.

        Returns:
            Every ticket, newest first.

        """
        return self._json(
            body={
                "items": [item.as_json() for item in self._registry().items()],
            },
        )

    async def create_uri(self, request: Request) -> JSONResponse:
        """Issue a URI.

        Args:
            request: Request whose body names the command and its input.

        Returns:
            The issued ticket.

        Raises:
            HTTPException: When the request or URI command is invalid.
            _fail: When URI creation reports a command failure.

        """
        body: Body = await self._body(request=request)
        command: JsonValue = body.get("command", "")
        ttl: JsonValue = body.get("ttlSeconds", ApiConstants.default_ttl)
        confirmed: JsonValue = body.get("confirmed", False)
        if (
            not isinstance(command, str)
            or not isinstance(ttl, int)
            or isinstance(ttl, bool)
            or not isinstance(confirmed, bool)
        ):
            raise HTTPException(
                status_code=ApiConstants.bad_request,
                detail="Give a command, a lifetime in seconds and a "
                "true or false confirmation.",
            )
        try:
            issued: Body = (
                self._registry()
                .create(
                    command=command,
                    values=self._values(body=body),
                    ttl_seconds=ttl,
                    confirmed=confirmed,
                )
                .as_json()
            )
        except CommandError as error:
            raise self._fail(error=error) from error
        return self._json(body=issued)

    async def revoke_uri(self, uri_id: str) -> JSONResponse:
        """Cancel a URI.

        Args:
            uri_id: Ticket identifier.

        Returns:
            The ticket, now revoked.

        Raises:
            HTTPException: When the URI cannot be revoked.
            _fail: When URI revocation reports a command failure.

        """
        try:
            revoked: Body = self._registry().revoke(uri_id=uri_id).as_json()
        except CommandError as error:
            raise self._fail(error=error) from error
        return self._json(body=revoked)

    async def run_uri(self, uri_id: str) -> JSONResponse:
        """Run a URI's command once.

        Args:
            uri_id: Ticket identifier.

        Returns:
            The command's widgets.

        """
        return await self._result(
            work=lambda: self._registry().run(uri_id=uri_id),
        )

    async def redeem_uri(self, request: Request) -> JSONResponse:
        """Verify and run a URI that was pasted in.

        Args:
            request: Request whose body carries the ``uri``.

        Returns:
            The command's widgets.

        Raises:
            HTTPException: When the request or pasted URI is invalid.

        """
        body: Body = await self._body(request=request)
        uri: JsonValue = body.get("uri", "")
        if not isinstance(uri, str) or not uri:
            raise HTTPException(
                status_code=ApiConstants.bad_request,
                detail="Paste a URI to redeem.",
            )
        return await self._result(
            work=lambda: self._registry().redeem(uri=uri),
        )

    @staticmethod
    async def _body(*, request: Request) -> Body:
        """Read a request body that must be a JSON object.

        Args:
            request: The request.

        Returns:
            The decoded object.

        Raises:
            HTTPException: When the body is not a JSON object.

        """
        try:
            decoded: JsonValue = cast(
                typ="JsonValue",
                val=await request.json(),
            )
        except ValueError as error:
            raise HTTPException(
                status_code=ApiConstants.bad_request,
                detail="The request body must be JSON.",
            ) from error
        if not isinstance(decoded, dict):
            raise HTTPException(
                status_code=ApiConstants.bad_request,
                detail="The request body must be a JSON object.",
            )
        return decoded

    @staticmethod
    def _values(*, body: Body) -> dict[str, str]:
        """Read the ``values`` object of a body as text.

        Args:
            body: The decoded body.

        Returns:
            Each value as text.

        Raises:
            HTTPException: When ``values`` is not an object.

        """
        raw: JsonValue = body.get("values", {})
        if not isinstance(raw, dict):
            raise HTTPException(
                status_code=ApiConstants.bad_request,
                detail="'values' must be an object.",
            )
        return {
            name: "" if value is None else str(object=value)
            for name, value in raw.items()
        }

    @staticmethod
    def _fail(*, error: CommandError) -> HTTPException:
        """Turn a command failure into an HTTP error.

        Args:
            error: The failure.

        Returns:
            The error to raise.

        """
        return HTTPException(status_code=error.status, detail=error.message)

    @staticmethod
    def _json(*, body: Body) -> JSONResponse:
        """Wrap live data so no cache keeps it.

        Args:
            body: The data.

        Returns:
            The response.

        """
        return JSONResponse(content=body, headers=ApiConstants.no_store)

    def _registry(self) -> UriRegistry:
        """Return the one URI registry, building it on first use.

        Returns:
            The registry shared by every request.

        Raises:
            HTTPException: When URI support is not installed.

        """
        with self._lock:
            if self._uris is None:
                try:
                    self._uris = self._uri_factory()
                except ImportError as error:
                    raise HTTPException(
                        status_code=ApiConstants.not_implemented,
                        detail=("URI support needs the bowman package."),
                    ) from error
            return self._uris

    async def _result(
        self,
        *,
        work: Callable[[], CommandResult],
    ) -> JSONResponse:
        """Run blocking command work off the event loop.

        Args:
            work: The work to run.

        Returns:
            The widgets the work produced.

        Raises:
            HTTPException: When the command work fails.
            _fail: When command execution reports a command failure.

        """
        try:
            result: CommandResult = await to_thread(work)
        except CommandError as error:
            raise self._fail(error=error) from error
        return self._json(body=result.as_json())
