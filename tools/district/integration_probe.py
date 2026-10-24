# Copyright (c) 2026 Project contributors.
"""Deterministic workload used to qualify District Terminal integrations."""

from __future__ import annotations

from dataclasses import dataclass
from json import dump
from os import environ, getpid
from pathlib import Path
from socket import AF_INET, SOCK_STREAM, socket
from subprocess import Popen
from sys import argv, executable, stdout
from threading import Thread
from time import monotonic, sleep
from typing import Final

from .agent import AgentDetector
from .runtime import RuntimePlatform


class Constants:
    """Group immutable integration-probe configuration.

    Attributes:
        poll_seconds: Delay between lifetime checks.

    """

    poll_seconds: Final[float] = 0.05


@dataclass(frozen=True, slots=True, kw_only=True)
class ProbeArguments:
    """Describe typed integration-probe arguments.

    Attributes:
        token: Correlation token.
        output: Evidence output path.
        duration: Probe lifetime in seconds.
        children: Number of child processes to create.
        http: Whether to open a loopback listener.

    """

    token: str
    output: Path
    duration: float = 2.0
    children: int = 2
    http: bool = False


def _serve_once(*, listener: socket) -> None:
    """Serve one minimal loopback HTTP response.

    Args:
        listener: Bound listening socket.

    """
    try:
        connection, _address = listener.accept()
    except OSError:
        return
    with connection:
        _ = connection.recv(4096)
        _ = connection.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Length: 23\r\n\r\n"
            b"district-integration-probe\n",
        )


def parse_arguments(*, arguments: tuple[str, ...]) -> ProbeArguments:
    """Parse the small internal probe argument surface.

    Args:
        arguments: Probe command arguments.

    Returns:
        Typed probe arguments.

    Raises:
        ValueError: When required or numeric arguments are invalid.

    """
    values: dict[str, str] = {}
    http: bool = False
    index: int = 0
    while index < len(arguments):
        option: str = arguments[index]
        if option == "--http":
            http = True
            index += 1
            continue
        if option not in {
            "--token",
            "--output",
            "--duration",
            "--children",
        } or index + 1 >= len(arguments):
            msg: str = f"Invalid integration probe argument: {option}"
            raise ValueError(msg)
        values[option] = arguments[index + 1]
        index += 2
    token: str = values.get("--token", "")
    output: str = values.get("--output", "")
    if not token or not output:
        msg = "Integration probe requires --token and --output."
        raise ValueError(msg)
    return ProbeArguments(
        token=token,
        output=Path(output),
        duration=float(values.get("--duration", "2.0")),
        children=int(values.get("--children", "2")),
        http=http,
    )


def main(*, arguments: tuple[str, ...] | None = None) -> int:
    """Run the deterministic integration workload.

    Args:
        arguments: Explicit probe arguments.

    Returns:
        Exit status.

    """
    args: ProbeArguments = parse_arguments(
        arguments=(tuple(argv[1:]) if arguments is None else arguments),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    children: list[Popen[str]] = [
        Popen(
            args=(
                executable,
                "-c",
                "import time; print('district-child'); time.sleep(1.0)",
            ),
            text=True,
        )
        for _ in range(args.children)
    ]
    listener: socket | None = None
    server_thread: Thread | None = None
    port: int = 0
    if args.http:
        listener = socket(family=AF_INET, type=SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(max(0.5, args.duration + 0.5))
        port = int(listener.getsockname()[1])
        server_thread = Thread(
            target=_serve_once,
            kwargs={"listener": listener},
            daemon=True,
        )
        server_thread.start()
    agent: str = environ.get("DISTRICT_AGENT", "")
    detected: tuple[str, str] | None = (
        AgentDetector.detect() if not agent else None
    )
    declared: str = "" if agent == "human" else agent
    detected_provider: str = declared or (
        detected[0] if detected is not None else "unknown"
    )
    detected_source: str = environ.get(
        "DISTRICT_ATTRIBUTION",
        "environment"
        if declared
        else detected[1]
        if detected is not None
        else "unknown",
    )
    payload: dict[str, str | int | list[int]] = {
        "token": args.token,
        "pid": getpid(),
        "children": [child.pid for child in children],
        "provider": detected_provider,
        "provider_session": environ.get("DISTRICT_AGENT_SESSION", ""),
        "surface": RuntimePlatform.surface(),
        "editor": RuntimePlatform.editor(),
        "workspace": environ.get("DISTRICT_WORKSPACE", str(object=Path.cwd())),
        "attribution": detected_source,
        "port": port,
    }
    with args.output.open(mode="w", encoding="utf-8") as stream:
        dump(obj=payload, fp=stream, indent=2)
        _ = stream.write("\n")
    _ = stdout.write(f"DISTRICT_PROBE_READY {args.token}\n")
    deadline: float = monotonic() + max(0.0, args.duration)
    while monotonic() < deadline:
        sleep(Constants.poll_seconds)
    if listener is not None:
        listener.close()
    if server_thread is not None:
        server_thread.join(timeout=1.0)
    for child in children:
        _ = child.wait(timeout=10.0)
    _ = stdout.write(f"DISTRICT_PROBE_DONE {args.token}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
