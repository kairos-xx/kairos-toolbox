# Copyright (c) 2026 Project contributors.
"""Command-line interface for the charter backends."""

from __future__ import annotations

from argparse import (
    SUPPRESS,
    ArgumentParser,
    Namespace,
)
from dataclasses import asdict
from json import dumps
from pathlib import Path
from sys import stderr as sys_stderr
from sys import stdout as sys_stdout
from time import sleep
from typing import TYPE_CHECKING

from tools.charter.backends import install_ngrok, open_charter
from tools.charter.errors import CharterError
from tools.charter.lifecycle import CharterRecord, CharterRegistry
from tools.charter.models import Charter, CharterConstants
from tools.charter.settings import Settings, SettingsLoader
from tools.charter.shorten import shorten_url

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from subprocess import Popen
type RowValue = str | int | float | bool | Path | None
type Row = Mapping[str, RowValue]


def _emit_rows(*, rows: tuple[Row, ...], settings: Settings) -> None:
    """Print rows in text or JSON mode.

    Args:
        rows: Records to render.
        settings: Resolved ``charter`` settings.

    """
    if settings.json_output:
        sys_stdout.write(dumps(obj=list(rows), default=str) + "\n")
        return
    for row in rows:
        sys_stdout.write(
            "  ".join(str(object=value) for value in row.values()) + "\n",
        )


def _wait(*, charter: Charter) -> None:
    """Keep a charter alive until it or the user stops it.

    Args:
        charter: The running charter.

    """
    try:
        process: Popen[bytes] = charter.process
        _ = process.wait()
    except KeyboardInterrupt:
        pass
    finally:
        charter.close()


def _expose(*, args: Namespace, settings: Settings) -> None:
    """Expose one local port and keep the charter alive.

    Args:
        args: Parsed ``expose`` arguments.
        settings: Resolved ``charter`` settings.

    Raises:
        CharterError: Propagated when shortening the URL fails.

    """
    charter: Charter | None = open_charter(
        port=args.port,
        settings=settings,
    )
    short_url: str | None = (
        shorten_url(
            url=charter.public_url,
            alias=args.short or None,
            settings=settings,
        )
        if charter is not None and args.short is not None
        else None
    )
    if settings.json_output:
        sys_stdout.write(
            dumps(
                obj={
                    "backend": settings.backend,
                    "port": args.port,
                    "public_url": (
                        charter.public_url if charter is not None else None
                    ),
                    "short_url": short_url,
                },
            )
            + "\n",
        )
    else:
        sys_stdout.write(
            f"{charter.public_url}\n"
            if charter is not None
            else (f"direct: nothing to expose on port {args.port}\n"),
        )
        if short_url is not None:
            sys_stdout.write(f"{short_url}\n")
    if charter is not None and not args.background:
        _wait(charter=charter)


def _registry(*, settings: Settings) -> CharterRegistry:
    """Open the registry of kept-alive tunnels.

    Args:
        settings: Resolved ``charter`` settings.

    Returns:
        The registry at the configured path.

    """
    return CharterRegistry(
        path=(
            Path(settings.registry_path)
            if settings.registry_path is not None
            else None
        ),
    )


def _list(*, args: Namespace, settings: Settings) -> None:
    """List the kept-alive tunnels that are still running.

    Args:
        args: Parsed ``list`` arguments.
        settings: Resolved ``charter`` settings.

    """
    _ = args
    _emit_rows(
        rows=tuple(
            {
                "id": record.identifier,
                "backend": record.backend,
                "port": record.port,
                "url": record.public_url,
                "expires_at": record.expires_at,
            }
            for record in _registry(settings=settings).records()
        ),
        settings=settings,
    )


def _stop(*, args: Namespace, settings: Settings) -> None:
    """Stop kept-alive tunnels on command.

    Args:
        args: Parsed ``stop`` arguments.
        settings: Resolved ``charter`` settings.

    Raises:
        CharterError: If neither an id nor ``--all`` was given, or the id is
            unknown.

    """
    registry: CharterRegistry = _registry(settings=settings)
    identifiers: tuple[str, ...] = (
        tuple(record.identifier for record in registry.records())
        if args.all
        else tuple(args.identifiers)
    )
    if not identifiers and not args.all:
        msg: str = "Give a charter id, or --all."
        raise CharterError(msg)
    for identifier in identifiers:
        record: CharterRecord = registry.stop(
            identifier=identifier,
            timeout_seconds=settings.stop_timeout_seconds,
        )
        sys_stdout.write(f"stopped {record.identifier} {record.public_url}\n")


def _expire(*, args: Namespace, settings: Settings) -> None:
    """Stop a charter process after a delay (the detached watchdog).

    Args:
        args: Parsed ``expire`` arguments.
        settings: Resolved ``charter`` settings.

    """
    sleep(args.seconds)
    _registry(settings=settings).stop_pid(
        pid=args.pid,
        timeout_seconds=settings.stop_timeout_seconds,
    )


def _backends(*, args: Namespace, settings: Settings) -> None:
    """List the accepted backend names.

    Args:
        args: Parsed ``backends`` arguments.
        settings: Resolved ``charter`` settings.

    """
    _ = args
    _emit_rows(
        rows=tuple(
            {"backend": name} for name in sorted(CharterConstants.backends)
        ),
        settings=settings,
    )


def _settings(*, args: Namespace, settings: Settings) -> None:
    """Print the resolved settings.

    Args:
        args: Parsed ``settings`` arguments.
        settings: Resolved ``charter`` settings.

    """
    _ = args
    _emit_rows(
        rows=tuple(
            {"name": name, "value": value}
            for name, value in asdict(obj=settings).items()
        ),
        settings=settings,
    )


def _shorten(*, args: Namespace, settings: Settings) -> None:
    """Shorten one URL and print the result.

    Args:
        args: Parsed ``shorten`` arguments.
        settings: Resolved ``charter`` settings.

    """
    short: str = shorten_url(
        url=args.url,
        alias=args.alias,
        settings=settings,
    )
    sys_stdout.write(f"{short}\n")


def _install_ngrok(*, args: Namespace, settings: Settings) -> None:
    """Find or install the ngrok executable and print its path.

    Args:
        args: Parsed ``install-ngrok`` arguments.
        settings: Resolved ``charter`` settings.

    """
    _ = args
    executable: Path = install_ngrok(settings=settings)
    sys_stdout.write(f"{executable}\n")


def _parser() -> ArgumentParser:
    """Build the full charter argument parser.

    Returns:
        Top-level parser with all commands.

    """
    parser: ArgumentParser = ArgumentParser(prog="charter")
    parser.add_argument(
        "--pyproject",
        type=Path,
        help="Explicit pyproject.toml to load settings from.",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=None,
        help="Emit machine-readable JSON output.",
    )
    parser.add_argument(
        "--no-json",
        dest="json_output",
        action="store_false",
        default=SUPPRESS,
        help="Emit human-readable output.",
    )
    add_subparser: Callable[..., ArgumentParser] = parser.add_subparsers(
        dest="group",
        required=True,
    ).add_parser
    expose: ArgumentParser = add_subparser("expose")
    expose.add_argument(
        "--port",
        type=int,
        required=True,
        help="Local port to expose.",
    )
    expose.add_argument(
        "--backend",
        choices=tuple(sorted(CharterConstants.backends)),
        help="Charter backend; defaults to the configured backend.",
    )
    expose.add_argument(
        "--authtoken",
        help="ngrok authtoken; overrides settings and NGROK_AUTHTOKEN.",
    )
    expose.add_argument(
        "--short",
        nargs="?",
        const="",
        metavar="ALIAS",
        help="Also print a shortened URL (optional alias).",
    )
    expose.add_argument(
        "--background",
        action="store_true",
        help="Keep the charter running after this command returns.",
    )
    expose.add_argument(
        "--ttl",
        type=float,
        metavar="SECONDS",
        help="Stop the charter automatically after this many seconds.",
    )
    expose.set_defaults(handler=_expose)
    list_cmd: ArgumentParser = add_subparser("list")
    list_cmd.set_defaults(handler=_list)
    stop_cmd: ArgumentParser = add_subparser("stop")
    stop_cmd.add_argument("identifiers", nargs="*", metavar="ID")
    stop_cmd.add_argument("--all", action="store_true")
    stop_cmd.set_defaults(handler=_stop)
    expire_cmd: ArgumentParser = add_subparser("expire")
    expire_cmd.add_argument("--pid", type=int, required=True)
    expire_cmd.add_argument("--seconds", type=float, required=True)
    expire_cmd.set_defaults(handler=_expire)
    backends_cmd: ArgumentParser = add_subparser("backends")
    backends_cmd.set_defaults(handler=_backends)
    settings_cmd: ArgumentParser = add_subparser("settings")
    settings_cmd.set_defaults(handler=_settings)
    shorten_cmd: ArgumentParser = add_subparser("shorten")
    shorten_cmd.add_argument("url", help="URL to shorten.")
    shorten_cmd.add_argument("--alias", help="Requested custom alias.")
    shorten_cmd.set_defaults(handler=_shorten)
    ngrok_cmd: ArgumentParser = add_subparser("install-ngrok")
    ngrok_cmd.set_defaults(handler=_install_ngrok)
    return parser


def main(*, arguments: tuple[str, ...] | None = None) -> int:
    """Parse arguments and dispatch one charter command.

    Args:
        arguments: Argument vector without the program name; ``sys.argv``
            is used when omitted.

    Returns:
        ``0`` on success, ``1`` on a handled failure.

    """
    parser: ArgumentParser = _parser()
    args: Namespace = parser.parse_args(
        args=None if arguments is None else list(arguments),
    )
    try:
        settings: Settings = SettingsLoader.load(
            pyproject=args.pyproject,
            overrides={
                "json_output": args.json_output,
                "backend": getattr(args, "backend", None),
                "ngrok_authtoken": getattr(args, "authtoken", None),
                "keep_alive": True
                if getattr(args, "background", False)
                else None,
                "ttl_seconds": getattr(args, "ttl", None),
            },
        )
        args.handler(args=args, settings=settings)
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
        sys_stderr.write(f"charter: {error}\n")
        return 1
    return 0
