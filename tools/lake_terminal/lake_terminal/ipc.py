# Copyright (c) 2026 Project contributors.
"""Authenticated local IPC for event publication and subscription."""

from __future__ import annotations

from dataclasses import dataclass
from multiprocessing.connection import Client, Listener
from os import name as os_name
from pathlib import Path
from secrets import token_bytes
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, Final, Protocol

from .product import secure_private_file, state_directory

if TYPE_CHECKING:
    from collections.abc import Callable
type IpcScalar = None | bool | int | float | str | bytes
type IpcPayload = dict[str, IpcScalar]
type IpcMessage = IpcScalar | IpcPayload
type IpcRequest = dict[str, str]


class IpcConnection(Protocol):
    """Operations required from a local IPC connection."""

    def send(self, obj: IpcMessage) -> None:
        """Send one IPC message.

        Args:
            obj: Message to send.

        """
        ...

    def recv(self) -> IpcMessage:
        """Receive one IPC message.

        Returns:
            Received IPC message.

        """
        ...

    def poll(self, timeout: float = 0.0) -> bool:
        """Return whether input is available.

        Args:
            timeout: Maximum time to wait.

        Returns:
            Whether input is available.

        """
        ...

    def close(self) -> None:
        """Close the connection."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class IpcEndpoint:
    """Address and authentication material for the local Lake Terminal daemon.

    Attributes:
        address: Address.
        family: Family.
        authkey: Authkey.

    """

    address: str
    family: str
    authkey: bytes

    @classmethod
    def for_home(cls, *, home: Path) -> IpcEndpoint:
        """Resolve or create the current user's IPC endpoint.

        Args:
            home: Home.

        Returns:
            The resulting value.

        """
        directory: Path = state_directory(home=home)
        directory.mkdir(parents=True, exist_ok=True)
        key_path: Path = directory / "ipc_key"
        if key_path.is_file():
            key: bytes = bytes.fromhex(
                key_path.read_text(
                    encoding="ascii",
                ).strip(),
            )
        else:
            key = token_bytes(nbytes=32)
            _ = key_path.write_text(
                data=key.hex() + "\n",
                encoding="ascii",
            )
        secure_private_file(path=key_path)
        if os_name == "nt":
            resolved: str = str(object=directory.resolve())
            suffix: str = str(object=abs(hash(resolved)))[:12]
            return cls(
                address=rf"\\.\pipe\lake-terminal-{suffix}",
                family="AF_PIPE",
                authkey=key,
            )
        return cls(
            address=str(object=directory / "daemon.sock"),
            family="AF_UNIX",
            authkey=key,
        )


class EventPublisher:
    """Best-effort client used by short-lived Lake Terminal processes."""

    def __init__(self, *, home: Path) -> None:
        """Store the local daemon endpoint.

        Args:
            home: Home.

        """
        self._endpoint: IpcEndpoint = IpcEndpoint.for_home(
            home=home,
        )

    def publish(self, *, payload: str) -> bool:
        """Publish one event when a daemon is listening.

        Args:
            payload: Payload.

        Returns:
            Whether the daemon accepted the event.

        """
        try:
            connection: IpcConnection = Client(
                address=self._endpoint.address,
                family=self._endpoint.family,
                authkey=self._endpoint.authkey,
            )
            try:
                request: IpcPayload = {
                    "op": "publish",
                    "payload": payload,
                }
                connection.send(obj=request)
                response: IpcMessage = connection.recv()
                return response == "OK"
            finally:
                connection.close()
        except (ConnectionError, FileNotFoundError, OSError, EOFError):
            return False


class EventSubscriber:
    """Receive daemon-broadcast events over an authenticated local channel."""

    def __init__(self, *, home: Path) -> None:
        """Connect to the local daemon and subscribe.

        Args:
            home: Home.

        Raises:
            ConnectionError: When the operation cannot complete.

        """
        endpoint: IpcEndpoint = IpcEndpoint.for_home(home=home)
        self._connection: IpcConnection = Client(
            address=endpoint.address,
            family=endpoint.family,
            authkey=endpoint.authkey,
        )
        request: IpcPayload = {"op": "subscribe"}
        self._connection.send(obj=request)
        response: IpcMessage = self._connection.recv()
        if response != "SUBSCRIBED":
            self._connection.close()
            msg: str = "Lake Terminal daemon rejected event subscription."
            raise ConnectionError(msg)

    def receive(self, *, timeout: float = 1.0) -> str | None:
        """Receive the next event if available before timeout.

        Args:
            timeout: Timeout.

        Returns:
            The next event payload when one is available.

        """
        if not self._connection.poll(timeout=timeout):
            return None
        return self._text(
            value=self._connection.recv(),
        )

    def close(self) -> None:
        """Close the subscription connection."""
        self._connection.close()

    @staticmethod
    def _text(*, value: IpcMessage) -> str:
        """Convert a received IPC message to text.

        Args:
            value: Received IPC value.

        Returns:
            Text representation of the value.

        """
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode(
                encoding="utf-8",
                errors="replace",
            )
        return str(object=value)


class EventServer:
    """Broadcast local events to persistent subscribers.

    Attributes:
        backlog: Backlog.

    """

    backlog: Final[int] = 64

    def __init__(
        self,
        *,
        home: Path,
        on_publish: Callable[[str], None] | None = None,
        handler: Callable[[IpcRequest], str | None] | None = None,
    ) -> None:
        """Initialize daemon listener state.

        Args:
            home: Home.
            on_publish: On publish.
            handler: Handler.

        """
        self._endpoint: IpcEndpoint = IpcEndpoint.for_home(
            home=home,
        )
        self.on_publish: Callable[[str], None] | None = on_publish
        self.handler: Callable[[IpcRequest], str | None] | None = handler
        self._subscribers: list[IpcConnection] = []
        self._lock: Lock = Lock()
        self._stop: Event = Event()
        self._listener: Listener | None = None

    @classmethod
    def request(
        cls,
        *,
        home: Path,
        operation: str,
        values: dict[str, str] | None = None,
    ) -> str:
        """Send a one-shot control request to the daemon.

        Args:
            home: Home.
            operation: Operation.
            values: Values.

        Returns:
            The daemon response.

        """
        endpoint: IpcEndpoint = IpcEndpoint.for_home(home=home)
        connection: IpcConnection = Client(
            address=endpoint.address,
            family=endpoint.family,
            authkey=endpoint.authkey,
        )
        try:
            request: IpcPayload = {"op": operation}
            if values is not None:
                request.update(values)
            connection.send(obj=request)
            return cls._response_text(
                value=connection.recv(),
            )
        finally:
            connection.close()

    def serve(self) -> None:
        """Accept clients until a stop request is received."""
        if self._endpoint.family == "AF_UNIX":
            Path(self._endpoint.address).unlink(
                missing_ok=True,
            )
        listener: Listener = Listener(
            address=self._endpoint.address,
            family=self._endpoint.family,
            backlog=self.backlog,
            authkey=self._endpoint.authkey,
        )
        self._listener = listener
        try:
            while not self._stop.is_set():
                try:
                    connection: IpcConnection = listener.accept()
                except OSError:
                    if self._stop.is_set():
                        break
                    raise
                _ = Thread(
                    target=self.handle,
                    kwargs={"connection": connection},
                    daemon=True,
                ).start()
        finally:
            listener.close()
            if self._endpoint.family == "AF_UNIX":
                Path(self._endpoint.address).unlink(
                    missing_ok=True,
                )

    def handle(self, *, connection: IpcConnection) -> None:
        """Handle one client request.

        Args:
            connection: Connection.

        """
        subscribed: bool = False
        try:
            request: IpcRequest | None = self._request(
                value=connection.recv(),
            )
            if request is None:
                connection.send(obj="INVALID")
                return
            operation: str = request.get("op", "")
            if operation == "subscribe":
                self._subscribe(connection=connection)
                subscribed = True
                return
            self._handle_request(
                connection=connection,
                operation=operation,
                request=request,
            )
        except (EOFError, OSError):
            return
        finally:
            if not subscribed:
                connection.close()

    def broadcast(self, *, payload: str) -> None:
        """Send one event to every live subscriber.

        Args:
            payload: Payload.

        """
        stale: list[IpcConnection] = []
        with self._lock:
            for subscriber in self._subscribers:
                try:
                    subscriber.send(obj=payload)
                except (BrokenPipeError, EOFError, OSError):
                    stale.append(subscriber)
            for subscriber in stale:
                subscriber.close()
                self._subscribers.remove(subscriber)

    @classmethod
    def _request(cls, *, value: IpcMessage) -> IpcRequest | None:
        """Normalize a received IPC request.

        Args:
            value: Received IPC value.

        Returns:
            String request mapping, or ``None`` for invalid input.

        """
        if not isinstance(value, dict):
            return None
        request: IpcRequest = {}
        for key, item in value.items():
            request[key] = cls._response_text(
                value=item,
            )
        return request

    @staticmethod
    def _response_text(*, value: IpcMessage) -> str:
        """Convert one received IPC value to text.

        Args:
            value: Received IPC value.

        Returns:
            Text representation.

        """
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode(
                encoding="utf-8",
                errors="replace",
            )
        return str(object=value)

    def _subscribe(self, *, connection: IpcConnection) -> None:
        """Register one persistent subscriber.

        Args:
            connection: Subscriber connection.

        """
        with self._lock:
            self._subscribers.append(connection)
        connection.send(obj="SUBSCRIBED")

    def _handle_request(
        self,
        *,
        connection: IpcConnection,
        operation: str,
        request: IpcRequest,
    ) -> None:
        """Handle one validated non-subscription request.

        Args:
            connection: Client connection.
            operation: Requested operation.
            request: Validated request.

        """
        if operation == "publish":
            self._publish(
                connection=connection,
                request=request,
            )
            return
        if operation == "ping":
            connection.send(obj="PONG")
            return
        if operation == "stop":
            connection.send(obj="STOPPING")
            self._stop.set()
            self._wake_listener()
            return
        self._custom(
            connection=connection,
            request=request,
        )

    def _wake_listener(self) -> None:
        """Wake a blocking listener after a stop request."""
        try:
            connection: IpcConnection = Client(
                address=self._endpoint.address,
                family=self._endpoint.family,
                authkey=self._endpoint.authkey,
            )
        except OSError:
            return
        connection.close()

    def _publish(
        self,
        *,
        connection: IpcConnection,
        request: IpcRequest,
    ) -> None:
        """Process one publication request.

        Args:
            connection: Client connection.
            request: Validated request.

        """
        payload: str = request.get("payload", "")
        if self.on_publish is not None:
            self.on_publish(payload)
        self.broadcast(payload=payload)
        connection.send(obj="OK")

    def _custom(
        self,
        *,
        connection: IpcConnection,
        request: IpcRequest,
    ) -> None:
        """Process one optional custom request.

        Args:
            connection: Client connection.
            request: Validated request.

        """
        if self.handler is None:
            connection.send(obj="UNKNOWN")
            return
        response: str | None = self.handler(request)
        connection.send(
            obj=response if response is not None else "UNKNOWN",
        )
