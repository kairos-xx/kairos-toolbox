# Copyright (c) 2026 Project contributors.
"""Demand-driven FastAPI dashboard for Lake session observability."""

from __future__ import annotations

from asyncio import CancelledError, Task, create_task, sleep
from collections import deque
from datetime import UTC, datetime
from http.client import HTTPConnection, HTTPResponse
from json import dumps, loads
from os import getpid
from pathlib import Path
from socket import AF_INET, SOCK_STREAM, socket
from subprocess import CalledProcessError
from time import monotonic
from typing import TYPE_CHECKING, Final, cast
from urllib.parse import SplitResult, urlsplit
from webbrowser import open as open_browser

from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from uvicorn import Config, Server

from .identity import ProcessIdentity
from .monitoring import Monitor, SessionSnapshot
from .product import Product
from .resources import (
    ResourceCollector,
    ResourceSnapshot,
)
from .storage import LakeDatabase

type JsonValue = (
    str | int | float | bool | list[JsonValue] | dict[str, JsonValue]
)
if TYPE_CHECKING:
    from .core import ExecResult, LakeTerminal
    from .models import SessionEntry


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


class WebDashboard:
    """Serve one reusable, demand-driven Lake web dashboard."""

    def __init__(
        self,
        *,
        service: LakeTerminal,
        home: Path | None = None,
    ) -> None:
        """Initialize the dashboard.

        Args:
            service: Lake Terminal service.
            home: Lake Terminal state home.

        """
        self.service: LakeTerminal = service
        self._home: Path = home or Path.home()
        self.database: LakeDatabase = LakeDatabase(home=self._home)
        self._assets: Path = Path(__file__).parent / "web_assets"
        self.consumers: int = 0
        self._sampler: Task[None] | None = None
        self._started_at: float = monotonic()
        self._started_stamp: str = datetime.now(tz=UTC).isoformat()
        self.history: dict[str, dict[str, deque[float]]] = {}
        self.payload_cache: str = self._idle_payload()

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

    def application(self) -> FastAPI:
        """Build the FastAPI application.

        Returns:
            Configured FastAPI application.

        """
        app: FastAPI = FastAPI(
            title="Lake Terminal Control Plane",
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
        )
        app.mount(
            path="/assets",
            app=StaticFiles(directory=self._assets),
            name="assets",
        )
        app.add_api_route(
            path="/",
            endpoint=self._index,
            methods=["GET"],
        )
        app.add_api_route(
            path="/health",
            endpoint=self._health,
            methods=["GET"],
        )
        app.add_api_route(
            path="/api/sessions/{session_id}/exec",
            endpoint=self.execute,
            methods=["POST"],
        )
        app.add_api_route(
            path="/api/sessions/{session_id}/send",
            endpoint=self.send,
            methods=["POST"],
        )
        app.add_api_route(
            path="/api/sessions/{session_id}/{action}",
            endpoint=self.action,
            methods=["POST"],
        )
        app.add_api_websocket_route(
            path="/ws",
            endpoint=self._websocket_endpoint,
        )
        return app

    def run(
        self,
        *,
        host: str,
        port: int,
        browser: bool = True,
    ) -> int:
        """Reuse a healthy server or run the singleton web server.

        Args:
            host: Host.
            port: Port.
            browser: Whether to open a browser.

        Returns:
            Process exit code.

        Raises:
            ValueError: When the host is not a loopback host.

        """
        if host not in WebConstants.loopback_hosts:
            msg: str = (
                "Lake Terminal web dashboard only accepts loopback hosts. "
                "Remote exposure requires an authenticated reverse proxy."
            )
            raise ValueError(
                msg,
            )
        existing: str | None = self.running_url()
        if existing is not None:
            if browser:
                _ = open_browser(url=existing, new=2)
            return 0
        selected_port: int = self.port(
            host=host,
            requested=port,
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
            host=host,
            port=selected_port,
            url=url,
        )
        if browser:
            _ = open_browser(url=url, new=2)
        config: Config = Config(
            app=self.application(),
            host=host,
            port=selected_port,
            log_level="warning",
        )
        server: Server = Server(config=config)
        try:
            server.run()
        finally:
            self.database.clear_web_server()
        return 0

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
            "attach": f"lake {session_id}",
            "view": f"lake --view {session_id}",
            "history": f"lake --history {session_id}",
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

    async def _index(self) -> FileResponse:
        """Return the dashboard document.

        Returns:
            Dashboard HTML response.

        """
        return FileResponse(path=self._assets / "index.html")

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
        await websocket.accept()
        self._consumer_joined()
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

    def _consumer_joined(self) -> None:
        """Wake live sampling for the first browser consumer."""
        self.consumers += 1
        self.payload_cache = self.payload()
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
                self.payload_cache = self.payload()
                await sleep(delay=WebConstants.refresh_seconds)
        except CancelledError:
            return
