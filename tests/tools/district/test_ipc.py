# Copyright (c) 2026 Project contributors.
"""Tests for the authenticated local IPC layer."""

from __future__ import annotations

from multiprocessing.connection import Client
from pathlib import Path
from tempfile import mkdtemp
from threading import Thread
from time import sleep
from typing import Final, final

import pytest
from district.ipc import (
    EventPublisher,
    EventServer,
    EventSubscriber,
    IpcConnection,
    IpcEndpoint,
)

from .helpers import check, expect


@final
class Constants:
    """Group module-level constants.

    Attributes:
        ipc_wait_seconds: Grouped module constant.
        recv_wait_seconds: Grouped module constant.

    """

    ipc_wait_seconds: Final[float] = 5.0
    recv_wait_seconds: Final[float] = 1.0


@pytest.fixture
def sock_home() -> Path:
    """Yield a short home path under the AF_UNIX path limit.

    Returns:
        The resulting value.

    """
    return Path(mkdtemp(prefix="ipc"))


def _serve(server: EventServer) -> Thread:
    """Run one event server on a background thread.

    Args:
        server: Server.

    Returns:
        The resulting value.

    """
    thread: Thread = Thread(target=server.serve, daemon=True)
    thread.start()
    return thread


def _wait_ready(*, home: Path) -> None:
    """Block until the daemon answers one ping request.

    Args:
        home: Home.

    """
    deadline_ok: bool = False
    for _attempt in range(50):
        try:
            if EventServer.request(home=home, operation="ping") == "PONG":
                deadline_ok = True
                break
        except (ConnectionError, FileNotFoundError, OSError, EOFError):
            sleep(0.05)
    check(condition=deadline_ok, reason="server became ready")


def test_endpoint_for_home(sock_home: Path) -> None:
    """Create and reuse a stable authenticated endpoint.

    Args:
        sock_home: Tmp path.

    """
    endpoint: IpcEndpoint = IpcEndpoint.for_home(home=sock_home)
    expect(actual=endpoint.family, expected="AF_UNIX")
    check(
        condition=endpoint.address.endswith("daemon.sock"),
        reason="unix socket path inside state dir",
    )
    key_path: Path = sock_home / ".district" / "ipc_key"
    check(condition=key_path.is_file(), reason="auth key persisted")
    expect(actual=len(endpoint.authkey), expected=32)
    again: IpcEndpoint = IpcEndpoint.for_home(home=sock_home)
    expect(actual=again.authkey, expected=endpoint.authkey)


def test_publish_without_daemon(sock_home: Path) -> None:
    """Fail quietly when no daemon listens.

    Args:
        sock_home: Tmp path.

    """
    publisher: EventPublisher = EventPublisher(home=sock_home)
    check(
        condition=not publisher.publish(payload="x"),
        reason="publish fails without a daemon",
    )


def test_server_round_trip(sock_home: Path) -> None:
    """Publish, subscribe, ping, and stop over a real unix socket.

    Args:
        sock_home: Tmp path.

    """
    published: list[str] = []

    def on_publish(payload: str) -> None:
        """Collect published payloads.

        Args:
            payload: Payload.

        """
        published.append(payload)

    def handler(request: dict[str, str]) -> str:
        """Answer custom requests.

        Args:
            request: Request.

        Returns:
            The resulting value.

        """
        return f"handled:{request.get('op', '')}"

    server: EventServer = EventServer(
        home=sock_home,
        on_publish=on_publish,
        handler=handler,
    )
    thread: Thread = _serve(server=server)
    try:
        _wait_ready(home=sock_home)
        expect(
            actual=EventServer.request(home=sock_home, operation="ping"),
            expected="PONG",
        )
        subscriber: EventSubscriber = EventSubscriber(home=sock_home)
        publisher: EventPublisher = EventPublisher(home=sock_home)
        check(
            condition=publisher.publish(payload="hello-event"),
            reason="publish accepted by daemon",
        )
        received: str | None = subscriber.receive(
            timeout=Constants.recv_wait_seconds,
        )
        expect(actual=received, expected="hello-event")
        expect(actual=published, expected=["hello-event"])
        expect(
            actual=EventServer.request(home=sock_home, operation="custom-op"),
            expected="handled:custom-op",
        )
        subscriber.close()
        check(
            condition=publisher.publish(payload="after-close"),
            reason="broadcast survives closed subscribers",
        )
        expect(
            actual=EventServer.request(home=sock_home, operation="stop"),
            expected="STOPPING",
        )
        thread.join(timeout=Constants.ipc_wait_seconds)
        check(condition=not thread.is_alive(), reason="server exited on stop")
    finally:
        if thread.is_alive():
            _ = EventServer.request(home=sock_home, operation="stop")


def test_invalid_request(sock_home: Path) -> None:
    """Reject non-mapping IPC payloads.

    Args:
        sock_home: Tmp path.

    """
    server: EventServer = EventServer(home=sock_home)
    thread: Thread = _serve(server=server)
    try:
        _wait_ready(home=sock_home)
        endpoint: IpcEndpoint = IpcEndpoint.for_home(home=sock_home)
        connection: IpcConnection = Client(
            address=endpoint.address,
            family=endpoint.family,
            authkey=endpoint.authkey,
        )
        try:
            connection.send(obj="not-a-dict")
            expect(actual=connection.recv(), expected="INVALID")
        finally:
            connection.close()
    finally:
        _ = EventServer.request(home=sock_home, operation="stop")
        thread.join(timeout=Constants.ipc_wait_seconds)


def test_subscriber_receive_timeout(sock_home: Path) -> None:
    """Return None when no event arrives before the deadline.

    Args:
        sock_home: Tmp path.

    """
    server: EventServer = EventServer(home=sock_home)
    thread: Thread = _serve(server=server)
    try:
        _wait_ready(home=sock_home)
        subscriber: EventSubscriber = EventSubscriber(home=sock_home)
        expect(actual=subscriber.receive(timeout=0.1), expected=None)
        subscriber.close()
    finally:
        _ = EventServer.request(home=sock_home, operation="stop")
        thread.join(timeout=Constants.ipc_wait_seconds)


def test_unknown_operation(sock_home: Path) -> None:
    """Answer UNKNOWN for operations without a handler.

    Args:
        sock_home: Tmp path.

    """
    server: EventServer = EventServer(home=sock_home)
    thread: Thread = _serve(server=server)
    try:
        _wait_ready(home=sock_home)
        expect(
            actual=EventServer.request(home=sock_home, operation="mystery"),
            expected="UNKNOWN",
        )
    finally:
        _ = EventServer.request(home=sock_home, operation="stop")
        thread.join(timeout=Constants.ipc_wait_seconds)
