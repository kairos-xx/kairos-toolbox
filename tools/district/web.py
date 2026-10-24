# Copyright (c) 2026 Project contributors.
"""Demand-driven FastAPI dashboard for District session observability."""

from __future__ import annotations

from asyncio import CancelledError, Task, create_task, sleep, to_thread
from collections import deque
from datetime import UTC, datetime
from hashlib import sha256
from http.client import HTTPConnection, HTTPResponse
from importlib import import_module
from json import dumps, loads
from os import environ, getpid
from pathlib import Path
from shutil import get_terminal_size
from socket import AF_INET, SO_REUSEADDR, SOCK_STREAM, SOL_SOCKET, socket
from subprocess import CalledProcessError
from sys import stderr, stdout
from time import monotonic
from typing import TYPE_CHECKING, Final, cast, final, override
from urllib.parse import SplitResult, urlsplit
from webbrowser import open as open_browser

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from uvicorn import Config, Server

from .district_commands import DistrictCommands
from .exposure import Exposure, PublicEndpoint, open_exposure
from .identity import ProcessIdentity
from .monitoring import Monitor, SessionSnapshot
from .pairing import (
    LanAddress,
    LanNetwork,
    PairingAuth,
    PairingConstants,
    PairingKind,
    PairingUnavailableError,
    PeerKind,
)
from .privacy import PrivacyPolicy
from .product import Product
from .qr import PairingBanner
from .resources import (
    ResourceCollector,
    ResourceSnapshot,
)
from .storage import DistrictDatabase
from .web_api import ApiRoutes

type JsonValue = (
    str | int | float | bool | list[JsonValue] | dict[str, JsonValue]
)
if TYPE_CHECKING:
    from collections.abc import Mapping
    from os import PathLike, stat_result
    from types import ModuleType

    from fastapi.params import Depends as DependsMarker
    from starlette.responses import Response as StarletteResponse
    from starlette.types import Scope

    from .core import District, ExecResult
    from .models import SessionEntry
    from .uri_registry import UriRegistry


class WebConstants:
    """Group web-dashboard constants.

    Attributes:
        default_host: Default host.
        local_host: Wildcard IPv4 host.
        loopback_hosts: Allowed loopback hosts.
        default_port: Default port.
        refresh_seconds: Refresh seconds.
        health_timeout_seconds: Health timeout seconds.
        response_ok: Successful HTTP status code.
        health_path: Health-check endpoint.
        history_limit: Per-session metric history length.
        event_limit: Maximum activity events per payload.
        command_limit: Maximum command records per payload.
        pair_path: Route that exchanges a pairing token for a session.
        favicon_path: Route answering the browser's unprompted icon request.
        unauthorized: Status for a request without a valid session.
        no_content: Status of a favicon the server does not ship.
        forbidden: Status for a cross-origin or unusable pairing request.
        policy_close: WebSocket close code for a refused handshake.
        redirect: Status of the redirect that follows a pairing.
        transcript_limit: Most commands one transcript request returns.
        versioned_assets: Assets whose links in a page carry a version.
        version_length: Characters of the asset fingerprint kept.

    """

    default_host: Final[str] = "127.0.0.1"
    local_host: Final[str] = "0." * 4
    loopback_hosts: Final[frozenset[str]] = frozenset(
        {"127.0.0.1", "localhost", "::1"},
    )
    default_port: Final[int] = 8765
    refresh_seconds: Final[float] = 0.5
    health_timeout_seconds: Final[float] = 0.35
    response_ok: Final[int] = 200
    health_path: Final[str] = "/health"
    history_limit: Final[int] = 120
    event_limit: Final[int] = 40
    command_limit: Final[int] = 40
    pair_path: Final[str] = "/pair"
    favicon_path: Final[str] = "/favicon.ico"
    unauthorized: Final[int] = 401
    no_content: Final[int] = 204
    forbidden: Final[int] = 403
    policy_close: Final[int] = 1008
    redirect: Final[int] = 303
    transcript_limit: Final[int] = 30
    versioned_assets: Final[tuple[str, ...]] = (
        "js/app.js",
        "js/console.js",
        "css/styles.css",
        "css/console.css",
    )
    version_length: Final[int] = 10


@final
class RevalidatedFiles(StaticFiles):
    """Serve static files that a browser must revalidate before reuse.

    Without a ``Cache-Control`` header a browser may reuse a script for hours
    from its ``Last-Modified`` age alone, so a phone would keep running the
    previous console after an upgrade. ``no-cache`` keeps the ETag, so an
    unchanged file still costs only a 304.
    """

    @override
    def file_response(
        self,
        full_path: PathLike[str] | str,
        stat_result: stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> StarletteResponse:
        """Build the file response and mark it revalidate-always.

        Args:
            full_path: File being served.
            stat_result: Its ``os.stat`` result.
            scope: ASGI scope of the request.
            status_code: Response status.

        Returns:
            The file (or ``304``) response.

        """
        response: StarletteResponse = super().file_response(
            full_path=full_path,
            stat_result=stat_result,
            scope=scope,
            status_code=status_code,
        )
        response.headers["Cache-Control"] = "no-cache"
        return response


@final
class PairingPages:
    """Group the small HTML pages shown outside a paired session.

    Attributes:
        required: Shown to a device that has not been paired.
        refused: Shown when a pairing link cannot be used.

    """

    required: Final[str] = (
        "<!doctype html><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>District Terminal</title>"
        "<body style='font:16px system-ui;margin:2rem;max-width:32rem'>"
        "<h1>Pair this device</h1>"
        "<p>This console only opens for devices you pair. On the computer "
        "that runs District Terminal, run "
        "<code>district --web --qr</code> and scan "
        "the code with this device's camera.</p></body>"
    )
    refused: Final[str] = (
        "<!doctype html><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>District Terminal</title>"
        "<body style='font:16px system-ui;margin:2rem;max-width:32rem'>"
        "<h1>This link cannot be used</h1>"
        "<p>It has expired, was already used, or was opened from a network "
        "other than the computer's. Run "
        "<code>district --web --qr</code> again "
        "and scan the new code from the same Wi-Fi network.</p></body>"
    )


class WebDashboard:
    """Serve one reusable, demand-driven District web dashboard."""

    def __init__(
        self,
        *,
        service: District,
        home: Path | None = None,
        assets: Path | None = None,
    ) -> None:
        """Initialize the dashboard.

        Args:
            service: District Terminal service.
            home: District Terminal state home.
            assets: Web assets directory; the packaged one when omitted.

        """
        self.service: District = service
        self._home: Path = home or Path.home()
        self.database: DistrictDatabase = DistrictDatabase(home=self._home)
        self._assets: Path = assets or Path(__file__).parent / "web"
        self.consumers: int = 0
        self._sampler: Task[None] | None = None
        self._started_at: float = monotonic()
        self._started_stamp: str = datetime.now(tz=UTC).isoformat()
        self.history: dict[str, dict[str, deque[float]]] = {}
        self.payload_cache: str = self._idle_payload()
        # Pairing is switched on by ``run``; without it (library use and
        # tests) every route is open, exactly as before.
        self.auth: PairingAuth | None = None
        self.public_url: str | None = None
        commands: DistrictCommands = DistrictCommands(
            service=service,
            home=self._home,
        )
        self.api: ApiRoutes = ApiRoutes(
            commands=commands,
            uri_factory=lambda: self._uri_registry(commands=commands),
            home=self._home,
        )

    @staticmethod
    def port(*, host: str, requested: int) -> int:
        """Resolve an available TCP port.

        Args:
            host: Host.
            requested: Requested port.

        Returns:
            Requested or dynamically allocated port.

        """
        if requested > 0:
            return requested
        with socket(family=AF_INET, type=SOCK_STREAM) as probe:
            probe.bind((host, 0))
            address: tuple[str, int] = cast(
                typ="tuple[str, int]",
                val=probe.getsockname(),
            )
            return address[1]

    @staticmethod
    def session_payload(
        *,
        entry: SessionEntry,
    ) -> dict[str, JsonValue]:
        """Build one session dashboard payload.

        Args:
            entry: Session entry.

        Returns:
            JSON-compatible session payload.

        """
        snapshot: SessionSnapshot = Monitor.snapshot(entry=entry)
        resources: ResourceSnapshot = ResourceCollector.collect(
            entry=entry,
        )
        return {
            "id": entry.id,
            "agent": entry.agent,
            "agentSession": entry.agent_session,
            "agentState": entry.agent_state,
            "state": snapshot.state,
            "command": snapshot.current_command,
            "cwd": entry.cwd,
            "surface": entry.surface,
            "editor": entry.editor,
            "backend": entry.backend,
            "workspace": entry.workspace,
            "attribution": entry.attribution,
            "hostId": entry.host_id,
            "output": snapshot.output,
            "clients": snapshot.clients,
            "ageSeconds": snapshot.age_seconds,
            "lastActivityAt": entry.last_activity_at,
            "reconnectable": snapshot.reconnectable,
            "processCount": resources.process_count,
            "cpuPercent": resources.cpu_percent,
            "rssBytes": resources.rss_bytes,
            "vmsBytes": resources.vms_bytes,
            "readBytes": resources.read_bytes,
            "writeBytes": resources.write_bytes,
            "ports": cast(
                typ="JsonValue",
                val=list(resources.listening_ports),
            ),
            "connections": cast(
                typ="JsonValue",
                val=list(resources.connections),
            ),
            "networkBytesSupported": resources.network_bytes_supported,
            "partialResources": resources.partial,
            "processes": [
                {
                    "pid": item.pid,
                    "parentPid": item.parent_pid,
                    "name": item.name,
                    "command": item.command,
                    "cpuPercent": item.cpu_percent,
                    "rssBytes": item.rss_bytes,
                    "readBytes": item.read_bytes,
                    "writeBytes": item.write_bytes,
                    "ports": list(item.listening_ports),
                }
                for item in resources.processes
            ],
        }

    @staticmethod
    def health_check(*, url: str) -> bool:
        """Check the local dashboard health endpoint over HTTP.

        Args:
            url: Registered dashboard URL.

        Returns:
            Whether the dashboard responds successfully.

        """
        parsed: SplitResult = urlsplit(url=url)
        if parsed.scheme != "http":
            return False
        hostname: str | None = parsed.hostname
        if hostname is None:
            return False
        try:
            port: int = parsed.port or WebConstants.default_port
        except ValueError:
            return False
        connection: HTTPConnection = HTTPConnection(
            host=hostname,
            port=port,
            timeout=WebConstants.health_timeout_seconds,
        )
        try:
            connection.request(
                method="GET",
                url=WebConstants.health_path,
            )
            response: HTTPResponse = connection.getresponse()
            return response.status == WebConstants.response_ok
        finally:
            connection.close()

    @staticmethod
    def same_origin(*, headers: Mapping[str, str]) -> bool:
        """Check that a browser request did not come from another site.

        Browsers attach ``Origin`` to cross-site requests and WebSocket
        handshakes; it must name the host the request was sent to.

        Args:
            headers: Request headers, lower-case keys.

        Returns:
            ``True`` when there is no ``Origin`` or it matches ``Host``.

        """
        origin: str | None = headers.get("origin")
        if not origin:
            return True
        return urlsplit(url=origin).netloc == headers.get("host", "")

    def application(self) -> FastAPI:
        """Build the FastAPI application.

        When pairing is active (``self.auth`` is set by :meth:`run`) every
        route except the static assets, ``/health``, ``/pair`` and
        ``/favicon.ico`` needs a paired browser's session cookie.

        Returns:
            Configured FastAPI application.

        """
        app: FastAPI = FastAPI(
            title="District Terminal Control Plane",
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
        )
        app.mount(
            path="/assets",
            app=RevalidatedFiles(directory=self._assets),
            name="assets",
        )
        guard: list[DependsMarker] = [
            Depends(dependency=self.require_session),
        ]
        app.add_api_route(
            path="/",
            endpoint=self._index,
            methods=["GET"],
            response_model=None,
        )
        app.add_api_route(
            path="/console",
            endpoint=self._console,
            methods=["GET"],
            response_model=None,
        )
        app.add_api_route(
            path=WebConstants.favicon_path,
            endpoint=self._favicon,
            methods=["GET"],
            response_model=None,
        )
        app.add_api_route(
            path=WebConstants.pair_path,
            endpoint=self._pair,
            methods=["GET"],
            response_model=None,
        )
        app.add_api_route(
            path="/health",
            endpoint=self._health,
            methods=["GET"],
        )
        app.add_api_route(
            path="/api/sessions/{session_id}/transcript",
            endpoint=self.transcript,
            methods=["GET"],
            dependencies=guard,
            response_model=None,
        )
        app.add_api_route(
            path="/api/sessions/{session_id}/exec",
            endpoint=self.execute,
            methods=["POST"],
            dependencies=guard,
        )
        app.add_api_route(
            path="/api/sessions/{session_id}/send",
            endpoint=self.send,
            methods=["POST"],
            dependencies=guard,
        )
        app.add_api_route(
            path="/api/sessions/{session_id}/{action}",
            endpoint=self.action,
            methods=["POST"],
            dependencies=guard,
        )
        for path, endpoint, methods in (
            ("/api/host", self.api.host, ["GET"]),
            ("/api/commands", self.api.catalog, ["GET"]),
            ("/api/commands/{name}", self.api.run_command, ["POST"]),
            ("/api/uris", self.api.list_uris, ["GET"]),
            ("/api/uris", self.api.create_uri, ["POST"]),
            ("/api/uris/redeem", self.api.redeem_uri, ["POST"]),
            ("/api/uris/{uri_id}/run", self.api.run_uri, ["POST"]),
            ("/api/uris/{uri_id}/revoke", self.api.revoke_uri, ["POST"]),
        ):
            app.add_api_route(
                path=path,
                endpoint=endpoint,
                methods=methods,
                dependencies=guard,
                response_model=None,
            )
        app.add_api_websocket_route(
            path="/ws",
            endpoint=self._websocket_endpoint,
        )
        return app

    def authorized(
        self,
        *,
        headers: Mapping[str, str],
        cookies: Mapping[str, str],
    ) -> bool:
        """Decide whether a request belongs to a paired device.

        Args:
            headers: Request headers, lower-case keys.
            cookies: Request cookies.

        Returns:
            ``True`` when pairing is off, or the session cookie is valid
            and the request is not cross-site.

        """
        if self.auth is None:
            return True
        return self.same_origin(headers=headers) and self.auth.valid_session(
            cookie=cookies.get(PairingConstants.cookie),
        )

    async def require_session(self, request: Request) -> None:
        """Reject API calls from devices that are not paired.

        Args:
            request: Incoming request.

        Raises:
            HTTPException: With 401 when there is no valid session, or 403
                for a cross-site request.

        """
        if self.auth is None:
            return
        if not self.same_origin(headers=request.headers):
            raise HTTPException(
                status_code=WebConstants.forbidden,
                detail="Cross-origin request refused",
            )
        if not self.authorized(
            headers=request.headers,
            cookies=request.cookies,
        ):
            raise HTTPException(
                status_code=WebConstants.unauthorized,
                detail="Pair this device first",
            )

    def run(
        self,
        *,
        host: str,
        port: int,
        browser: bool = True,
        exposure: Exposure | None = None,
        lan: bool = False,
    ) -> int:
        """Reuse a healthy server or run the singleton web server.

        Every device must be paired before it can use the console: the
        browser opened here receives a one-time local link, and ``lan``
        additionally listens on the host's local network and prints a QR
        code that pairs a phone on that network.

        Args:
            host: Host.
            port: Port.
            browser: Whether to open a browser.
            exposure: Public-exposure choices; ``direct`` (or the
                ``[tool.district.charter]`` settings) when omitted.
            lan: Listen on the local network and print a pairing QR code.

        Returns:
            Process exit code.

        Raises:
            ValueError: When the host is not a loopback host.
            ExposureError: When the selected charter backend fails.
            PairingUnavailableError: When ``lan`` is requested but the host
                has no local network to listen on.

        """
        if host not in WebConstants.loopback_hosts:
            msg: str = (
                "District Terminal web dashboard only accepts loopback hosts. "
                "Use --qr to listen on the local network."
            )
            raise ValueError(
                msg,
            )
        auth: PairingAuth = PairingAuth.for_home(home=self._home)
        self.auth = auth
        existing: str | None = self.running_url()
        if existing is not None:
            return self._reuse(existing=existing, browser=browser, lan=lan)
        address: LanAddress | None = self._lan_address(lan=lan)
        selected_port: int = self.port(
            host=host,
            requested=port,
        )
        endpoint: PublicEndpoint | None = open_exposure(
            port=selected_port,
            exposure=exposure,
        )
        self.public_url = (
            endpoint.charter_url.rstrip("/") if endpoint is not None else None
        )
        url_host: str = (
            WebConstants.default_host
            if host in {WebConstants.local_host, "::"}
            else host
        )
        url: str = f"http://{url_host}:{selected_port}/"
        pid: int = getpid()
        identity: ProcessIdentity = ProcessIdentity.capture(pid=pid)
        self.database.set_web_server(
            pid=pid,
            started_token=identity.started_token,
            host=host if address is None else address.address,
            port=selected_port,
            url=url,
        )
        if endpoint is not None:
            _ = stdout.write(f"Public URL: {endpoint.url}\n")
        if address is not None:
            self._announce(
                link=self.pair_link(
                    base=f"http://{address.address}:{selected_port}/",
                    kind=PairingKind.lan,
                ),
            )
        if browser:
            _ = open_browser(
                url=self.pair_link(base=url, kind=PairingKind.local),
                new=2,
            )
        config: Config = Config(
            app=self.application(),
            host=host,
            port=selected_port,
            log_level="warning",
            proxy_headers=False,
        )
        server: Server = Server(config=config)
        sockets: list[socket] = (
            []
            if address is None
            else self._sockets(host=host, address=address, port=selected_port)
        )
        try:
            if sockets:
                server.run(sockets=sockets)
            else:
                server.run()
        finally:
            for bound in sockets:
                bound.close()
            if endpoint is not None:
                endpoint.charter.close()
            self.database.clear_web_server()
        return 0

    def pair_link(self, *, base: str, kind: PairingKind) -> str:
        """Compose a one-time link that pairs the device that opens it.

        Args:
            base: Origin the link points at, with a trailing slash.
            kind: ``local`` for a browser on this computer, ``lan`` for a
                phone on the same network.

        Returns:
            The full ``/pair`` URL.

        """
        auth: PairingAuth = cast(typ="PairingAuth", val=self.auth)
        return (
            f"{base.rstrip('/')}{WebConstants.pair_path}"
            f"?t={auth.mint(kind=kind)}"
        )

    def running_url(self) -> str | None:
        """Return the validated singleton server URL when healthy.

        Returns:
            Valid running dashboard URL, if available.

        """
        record: tuple[int, str, str, int, str] | None = (
            self.database.web_server()
        )
        if record is None:
            return None
        pid, token, _, _, url = record
        try:
            identity: ProcessIdentity = ProcessIdentity.capture(pid=pid)
            if token and identity.started_token != token:
                self.database.clear_web_server()
                return None
            if self.health_check(url=url):
                return url
        except OSError:
            pass
        self.database.clear_web_server()
        return None

    def payload(self) -> str:
        """Serialize live sessions, history, and activity for delivery.

        Returns:
            Serialized dashboard payload.

        """
        entries: tuple[SessionEntry, ...] = self.service.list_entries()
        live_ids: set[str] = {entry.id for entry in entries}
        for stale in tuple(key for key in self.history if key not in live_ids):
            del self.history[stale]
        sessions: list[dict[str, JsonValue]] = []
        for entry in entries:
            session: dict[str, JsonValue] = self.session_payload(
                entry=entry,
            )
            history: dict[str, list[float]] = self._record(
                entry_id=entry.id,
                cpu=cast(typ="float", val=session["cpuPercent"]),
                rss=cast(typ="float", val=session["rssBytes"]),
            )
            session["cpuHistory"] = cast(
                typ="JsonValue",
                val=history["cpu"],
            )
            session["rssHistory"] = cast(
                typ="JsonValue",
                val=history["rss"],
            )
            sessions.append(session)
        return dumps(
            obj={
                "sessions": sessions,
                "server": self._server_payload(mode="LIVE"),
                "totals": self._totals(sessions=sessions),
                "events": self._recent_events(),
                "commands": self._recent_commands(),
            },
            separators=(",", ":"),
        )

    async def transcript(self, session_id: str) -> Response:
        """Return a session's recent commands with their output.

        Hook-backed sessions have no terminal pane to mirror, so the console
        shows what they ran instead. The output is read on request rather
        than carried in every live update.

        Args:
            session_id: Session identifier; one that has ended still answers.

        Returns:
            The newest commands, oldest first, and whether the privacy
            policy saves their output at all.

        """
        rows: tuple[str, ...] = await to_thread(
            self.database.command_transcript,
            session_id=session_id,
            limit=WebConstants.transcript_limit,
        )
        return JSONResponse(
            content={
                "session": session_id,
                "persistOutput": PrivacyPolicy.load(
                    home=self._home,
                ).persist_output,
                "commands": [loads(s=row) for row in rows],
            },
            headers={"Cache-Control": "no-store"},
        )

    async def execute(
        self,
        session_id: str,
        request: Request,
    ) -> dict[str, JsonValue]:
        """Run one shell command inside a session from the dashboard.

        Args:
            session_id: Session identifier.
            request: HTTP request carrying the JSON body.

        Returns:
            Captured output and exit status.

        Raises:
            HTTPException: On invalid input, unknown session, or timeout.

        """
        _ = self._entry(session_id=session_id)
        body: JsonValue = cast(
            typ="JsonValue",
            val=await request.json(),
        )
        command_value: JsonValue = (
            body.get("command", "") if isinstance(body, dict) else ""
        )
        command: str = command_value if isinstance(command_value, str) else ""
        if not command.strip():
            raise HTTPException(
                status_code=400,
                detail="Command is required",
            )
        try:
            result: ExecResult = self.service.execute(
                session_id=session_id,
                command=command.strip(),
            )
        except KeyError as error:
            raise HTTPException(
                status_code=404,
                detail="Session ended during execution",
            ) from error
        except TimeoutError as error:
            raise HTTPException(
                status_code=504,
                detail="Command timed out",
            ) from error
        return {
            "status": "ok",
            "session": session_id,
            "output": result.output,
            "exitStatus": result.exit_status,
        }

    async def send(
        self,
        session_id: str,
        request: Request,
    ) -> dict[str, JsonValue]:
        """Inject literal text and key presses into a session pane.

        Args:
            session_id: Session identifier.
            request: HTTP request carrying the JSON body.

        Returns:
            Send acknowledgement.

        Raises:
            HTTPException: On invalid input, unknown session, or backend
                rejection.

        """
        _ = self._entry(session_id=session_id)
        body: JsonValue = cast(
            typ="JsonValue",
            val=await request.json(),
        )
        mapping: dict[str, JsonValue] = body if isinstance(body, dict) else {}
        text_value: JsonValue = mapping.get("text", "")
        if not isinstance(text_value, str):
            raise HTTPException(
                status_code=400,
                detail="'text' must be a string",
            )
        key_value: JsonValue = mapping.get("key", "")
        if not isinstance(key_value, str):
            raise HTTPException(
                status_code=400,
                detail="'key' must be a string",
            )
        enter_value: JsonValue | None = mapping.get("enter")
        if enter_value is not None and not isinstance(enter_value, bool):
            raise HTTPException(
                status_code=400,
                detail="'enter' must be a boolean",
            )
        keys: list[str] = [key_value] if key_value else []
        enter: bool = bool(text_value) if enter_value is None else enter_value
        if enter:
            keys.append("Enter")
        if not text_value and not keys:
            raise HTTPException(
                status_code=400,
                detail="Provide 'text', 'key' or 'enter'",
            )
        try:
            self.service.send(
                session_id=session_id,
                text=text_value,
                keys=tuple(keys),
            )
        except KeyError as error:
            raise HTTPException(
                status_code=404,
                detail="Session ended",
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail=str(object=error),
            ) from error
        except (RuntimeError, CalledProcessError, OSError) as error:
            raise HTTPException(
                status_code=502,
                detail="Backend rejected input",
            ) from error
        return {
            "status": "ok",
            "session": session_id,
        }

    async def action(
        self,
        session_id: str,
        action: str,
    ) -> dict[str, str]:
        """Handle a dashboard session action.

        Args:
            session_id: Session identifier.
            action: Requested action.

        Returns:
            Action result.

        Raises:
            HTTPException: If the session or action is invalid.

        """
        entry: SessionEntry = self._entry(session_id=session_id)
        if action == "kill":
            self.service.kill(session_id=session_id)
            return {
                "status": "killed",
                "session": session_id,
            }
        commands: dict[str, str] = {
            "attach": f"district {session_id}",
            "view": f"district --view {session_id}",
            "history": f"district --history {session_id}",
        }
        command: str | None = commands.get(action)
        if command is None:
            raise HTTPException(
                status_code=400,
                detail="Unsupported action",
            )
        if action == "attach" and not entry.reconnectable:
            raise HTTPException(
                status_code=409,
                detail="Session is not reconnectable",
            )
        return {
            "status": "command",
            "command": command,
        }

    def asset_version(self) -> str:
        """Fingerprint the web assets by name, size and modification time.

        Returns:
            A short hex string that changes whenever an asset changes.

        """
        parts: list[str] = []
        for path in sorted(self._assets.rglob(pattern="*")):
            if path.is_file():
                info: stat_result = path.stat()
                parts.append(
                    f"{path.relative_to(self._assets)}:{info.st_size}:{info.st_mtime_ns}",
                )
        digest: str = sha256(data="|".join(parts).encode()).hexdigest()
        return digest[: WebConstants.version_length]

    @staticmethod
    def _lan_address(*, lan: bool) -> LanAddress | None:
        """Pick the network address a phone would use, when LAN is on.

        Args:
            lan: Whether LAN pairing was requested.

        Returns:
            The host's address on its local network, or ``None`` when LAN
            pairing is off.

        Raises:
            PairingUnavailableError: When LAN pairing is on but the host is
                not on a private network.

        """
        if not lan:
            return None
        address: LanAddress | None = LanNetwork.primary()
        if address is None:
            msg: str = (
                "This computer is not on a private network (Wi-Fi or "
                "Ethernet), so a phone cannot reach it. Connect and retry."
            )
            raise PairingUnavailableError(msg)
        return address

    @staticmethod
    def _sockets(
        *,
        host: str,
        address: LanAddress,
        port: int,
    ) -> list[socket]:
        """Bind the loopback address and the LAN address, nothing wider.

        The loopback listener serves the local browser and any charter agent;
        the LAN listener serves paired phones. No wildcard address is bound,
        so other interfaces (VPNs, bridges) are not exposed.

        Args:
            host: Loopback host.
            address: The host's LAN address.
            port: Port to bind on both addresses.

        Returns:
            The bound sockets.

        """
        bound: list[socket] = []
        for where in (host, address.address):
            listener: socket = socket(family=AF_INET, type=SOCK_STREAM)
            listener.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
            listener.bind((where, port))
            bound.append(listener)
        return bound

    @staticmethod
    def _totals(
        *,
        sessions: list[dict[str, JsonValue]],
    ) -> dict[str, JsonValue]:
        """Aggregate session counts and resource consumption.

        Args:
            sessions: Serialized session payloads.

        Returns:
            Aggregate dashboard totals.

        """
        states: list[str] = [
            str(object=session["state"]) for session in sessions
        ]
        return {
            "sessions": len(sessions),
            "active": sum(state not in {"IDLE", "DEAD"} for state in states),
            "idle": states.count("IDLE"),
            "dead": states.count("DEAD"),
            "cpuPercent": round(
                number=sum(
                    cast(typ="float", val=session["cpuPercent"])
                    for session in sessions
                ),
                ndigits=1,
            ),
            "rssBytes": sum(
                cast(typ="int", val=session["rssBytes"])
                for session in sessions
            ),
            "processes": sum(
                cast(typ="int", val=session["processCount"])
                for session in sessions
            ),
            "clients": sum(
                cast(typ="int", val=session["clients"]) for session in sessions
            ),
        }

    @staticmethod
    def _private(*, response: Response) -> Response:
        """Keep a pairing response out of caches and referrers.

        Args:
            response: The response to harden.

        Returns:
            The same response.

        """
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @staticmethod
    async def _favicon() -> Response:
        """Answer the icon every browser requests unprompted.

        The control plane ships no icon, and an empty answer keeps the
        browser console free of a 404 on every page load.

        Returns:
            An empty ``204 No Content`` response.

        """
        return Response(status_code=WebConstants.no_content)

    def _reuse(self, *, existing: str, browser: bool, lan: bool) -> int:
        """Use the dashboard that is already running.

        Pairing links are signed with the shared key, so this process can
        mint them for the running server without talking to it.

        Args:
            existing: Base URL of the running dashboard.
            browser: Whether to open a browser.
            lan: Whether a phone pairing QR code was requested.

        Returns:
            Process exit code.

        """
        record: tuple[int, str, str, int, str] | None = (
            self.database.web_server()
        )
        if lan:
            if record is None or record[2] in WebConstants.loopback_hosts:
                _ = stderr.write(
                    "district --web: the running dashboard listens on "
                    "loopback only. Stop it, then run "
                    "`district --web --qr`.\n",
                )
                return 1
            self._announce(
                link=self.pair_link(
                    base=f"http://{record[2]}:{record[3]}/",
                    kind=PairingKind.lan,
                ),
            )
            return 0
        if browser:
            _ = open_browser(
                url=self.pair_link(base=existing, kind=PairingKind.local),
                new=2,
            )
        return 0

    def _announce(self, *, link: str) -> None:
        """Print the pairing QR code with its link beside it.

        Args:
            link: The one-time LAN pairing link.

        """
        minutes: int = int(PairingConstants.pairing_seconds // 60)
        headline: list[str] = [
            "District Terminal remote console",
            "Scan with your phone's camera.",
            "Works only on the same Wi-Fi/LAN as this computer.",
            f"One use, valid for {minutes} minutes.",
        ]
        if self.public_url is not None:
            headline.append(f"Then continues at {self.public_url}")
        _ = stdout.write(
            PairingBanner.render(
                url=link,
                headline=headline,
                columns=get_terminal_size(fallback=(100, 24)).columns,
                color=stdout.isatty() and "NO_COLOR" not in environ,
            ),
        )

    def _idle_payload(self) -> str:
        """Serialize the idle dashboard state.

        Returns:
            Serialized idle payload.

        """
        return dumps(
            obj={
                "sessions": [],
                "server": self._server_payload(mode="IDLE"),
                "totals": self._totals(sessions=[]),
                "events": self._recent_events(),
                "commands": self._recent_commands(),
            },
            separators=(",", ":"),
        )

    def _server_payload(self, *, mode: str) -> dict[str, JsonValue]:
        """Serialize server identity and liveness metadata.

        Args:
            mode: Sampling mode.

        Returns:
            Server metadata payload.

        """
        return {
            "mode": mode,
            "consumers": self.consumers,
            "version": Product.version,
            "pid": getpid(),
            "startedAt": self._started_stamp,
            "uptimeSeconds": round(
                number=monotonic() - self._started_at,
                ndigits=1,
            ),
            "sampleMs": int(WebConstants.refresh_seconds * 1000),
        }

    def _recent_events(self) -> list[JsonValue]:
        """Return the newest persisted observability events.

        Returns:
            Parsed event payloads, oldest first.

        """
        return [
            cast(typ="JsonValue", val=loads(s=row))
            for row in self.database.event_json()[-WebConstants.event_limit :]
        ]

    def _recent_commands(self) -> list[JsonValue]:
        """Return the newest persisted command records.

        Returns:
            Parsed command payloads, oldest first.

        """
        return [
            cast(typ="JsonValue", val=loads(s=row))
            for row in self.database.command_json()[
                -WebConstants.command_limit :
            ]
        ]

    def _record(
        self,
        *,
        entry_id: str,
        cpu: float,
        rss: float,
    ) -> dict[str, list[float]]:
        """Append one sample to the per-session metric history.

        Args:
            entry_id: Session identifier.
            cpu: CPU percent sample.
            rss: RSS byte sample.

        Returns:
            Metric history arrays for the session.

        """
        history: dict[str, deque[float]] = self.history.setdefault(
            entry_id,
            {
                "cpu": deque(maxlen=WebConstants.history_limit),
                "rss": deque(maxlen=WebConstants.history_limit),
            },
        )
        history["cpu"].append(cpu)
        history["rss"].append(rss)
        return {
            "cpu": list(history["cpu"]),
            "rss": list(history["rss"]),
        }

    def _uri_registry(self, *, commands: DistrictCommands) -> UriRegistry:
        """Build the URI registry, loading the Bowman package only now.

        Args:
            commands: The command layer URIs run through.

        Returns:
            The registry.

        """
        module: ModuleType = import_module(
            name=".uri_registry",
            package=__package__,
        )
        return cast(
            typ="UriRegistry",
            val=module.UriRegistry(
                commands=commands,
                home=self._home,
                clock=lambda: datetime.now(tz=UTC),
            ),
        )

    async def _index(self, request: Request) -> Response:
        """Return the dashboard document to a paired device.

        Args:
            request: Incoming request.

        Returns:
            The dashboard, or a page explaining how to pair.

        """
        return self._page(request=request, name="index.html")

    async def _console(self, request: Request) -> Response:
        """Return the full-screen console document to a paired device.

        Args:
            request: Incoming request.

        Returns:
            The console, or a page explaining how to pair.

        """
        return self._page(request=request, name="console.html")

    def _page(self, *, request: Request, name: str) -> Response:
        """Serve one HTML document, or the pairing page without a session.

        The document's asset links carry a version taken from the asset
        files, so a browser holding an older copy of a script fetches the new
        one instead of running it against the new markup.

        Args:
            request: Incoming request.
            name: File name inside the web assets.

        Returns:
            The document, or a page explaining how to pair.

        """
        if not self.authorized(
            headers=request.headers,
            cookies=request.cookies,
        ):
            return HTMLResponse(
                content=PairingPages.required,
                status_code=WebConstants.unauthorized,
            )
        document: str = (self._assets / name).read_text(encoding="utf-8")
        version: str = self.asset_version()
        for asset in WebConstants.versioned_assets:
            document = document.replace(
                f'"/assets/{asset}"',
                f'"/assets/{asset}?v={version}"',
            )
        return HTMLResponse(
            content=document,
            headers={"Cache-Control": "no-cache"},
        )

    async def _pair(self, request: Request, t: str | None = None) -> Response:
        """Exchange a one-time link for a signed-in browser.

        A ``lan`` token is accepted only from a device on the host's local
        network and a ``local`` token only from the host itself. After a LAN
        device proves it is nearby, a configured charter moves it on to the
        public origin through a short-lived hand-off token, so the console
        finally opens there already signed in.

        Args:
            request: Incoming request.
            t: The token from the link.

        Returns:
            A redirect into the console, or a page explaining the refusal.

        """
        auth: PairingAuth | None = self.auth
        if auth is None:
            return RedirectResponse(
                url="/",
                status_code=WebConstants.redirect,
            )
        peer: PeerKind = LanNetwork.classify(
            peer=request.client.host if request.client else None,
        )
        allowed: set[PairingKind] = {PairingKind.handoff}
        if peer is PeerKind.loopback:
            allowed.add(PairingKind.local)
        if peer is PeerKind.lan:
            allowed.add(PairingKind.lan)
        kind: PairingKind | None = (
            auth.redeem(token=t, kinds=frozenset(allowed)) if t else None
        )
        if kind is None:
            return self._private(
                response=HTMLResponse(
                    content=PairingPages.refused,
                    status_code=WebConstants.forbidden,
                ),
            )
        if kind is PairingKind.lan and self.public_url is not None:
            handoff: str = auth.mint(kind=PairingKind.handoff)
            return self._private(
                response=RedirectResponse(
                    url=f"{self.public_url}{WebConstants.pair_path}"
                    f"?t={handoff}",
                    status_code=WebConstants.redirect,
                ),
            )
        signed_in: RedirectResponse = RedirectResponse(
            url="/",
            status_code=WebConstants.redirect,
        )
        signed_in.set_cookie(
            key=PairingConstants.cookie,
            value=auth.mint(kind=PairingKind.session),
            max_age=int(PairingConstants.session_seconds),
            httponly=True,
            samesite="lax",
            secure=request.headers.get("x-forwarded-proto") == "https",
            path="/",
        )
        return self._private(response=signed_in)

    async def _health(self) -> dict[str, str | int | float]:
        """Return web-server health information.

        Returns:
            Current server status.

        """
        return {
            "status": "ok",
            "mode": "LIVE" if self.consumers else "IDLE",
            "consumers": self.consumers,
            "version": Product.version,
            "uptimeSeconds": round(
                number=monotonic() - self._started_at,
                ndigits=1,
            ),
        }

    def _entry(self, *, session_id: str) -> SessionEntry:
        """Resolve one registered session.

        Args:
            session_id: Session identifier.

        Returns:
            Matching session.

        Raises:
            HTTPException: If no matching session exists.

        """
        entries: tuple[SessionEntry, ...] = self.service.list_entries()
        entry: SessionEntry | None = next(
            (item for item in entries if item.id == session_id),
            None,
        )
        if entry is None:
            raise HTTPException(
                status_code=404,
                detail="Unknown session",
            )
        return entry

    async def _websocket_endpoint(
        self,
        websocket: WebSocket,
    ) -> None:
        """Serve live dashboard state over one WebSocket.

        Args:
            websocket: Connected WebSocket.

        """
        if not self.authorized(
            headers=websocket.headers,
            cookies=websocket.cookies,
        ):
            await websocket.close(code=WebConstants.policy_close)
            return
        await websocket.accept()
        await self._consumer_joined()
        previous: str = ""
        try:
            while True:
                payload: str = self.payload_cache
                if payload != previous:
                    await websocket.send_text(data=payload)
                    previous = payload
                await sleep(delay=WebConstants.refresh_seconds)
        except WebSocketDisconnect:
            return
        finally:
            self._consumer_left()

    async def _consumer_joined(self) -> None:
        """Wake live sampling for the first browser consumer.

        Sampling walks the process table, so it runs on a worker thread and
        never stalls the event loop that serves every other request.
        """
        self.consumers += 1
        self.payload_cache = await to_thread(self.payload)
        if self._sampler is None or self._sampler.done():
            self._sampler = create_task(coro=self._sample_loop())

    def _consumer_left(self) -> None:
        """Stop expensive sampling after the final consumer leaves."""
        self.consumers = max(0, self.consumers - 1)
        if self.consumers == 0 and self._sampler is not None:
            _ = self._sampler.cancel()
            self._sampler = None
            self.payload_cache = self._idle_payload()

    async def _sample_loop(self) -> None:
        """Sample once per interval regardless of browser client count."""
        try:
            while self.consumers > 0:
                self.payload_cache = await to_thread(self.payload)
                await sleep(delay=WebConstants.refresh_seconds)
        except CancelledError:
            return
