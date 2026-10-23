# Copyright (c) 2026 Project contributors.
"""Handle safe lake-terminal clipboard URLs on macOS."""

from __future__ import annotations

from pathlib import Path
from re import fullmatch
from subprocess import CompletedProcess, run
from sys import argv
from typing import Final
from urllib.parse import ParseResult, parse_qs, urlparse


class Constants:
    """Immutable clipboard-handler constants.

    Attributes:
        scheme: Scheme.
        host: Host.
        alias_file: Alias file.
        default_alias: Default alias.

    """

    scheme: Final[str] = "lake-terminal"
    host: Final[str] = "copy"
    alias_file: Final[Path] = Path("/usr/local/lib/lake-terminal/alias")
    default_alias: Final[str] = "lake"


def command_from_url(*, url: str) -> str | None:
    """Return the safe reconnect command encoded by a clipboard URL.

    Args:
        url: Url.

    Returns:
        The resulting value.

    """
    parsed: ParseResult = urlparse(url=url)
    query: dict[str, list[str]] = parse_qs(
        qs=parsed.query,
        strict_parsing=False,
    )
    values: list[str] = query.get("id", [])
    if (
        parsed.scheme != Constants.scheme
        or parsed.netloc != Constants.host
        or parsed.path not in ("", "/")
        or set(query) != {"id"}
        or len(values) != 1
        or fullmatch(pattern=r"[0-9a-f]{6}", string=values[0]) is None
    ):
        return None
    alias: str = Constants.default_alias
    if Constants.alias_file.is_file():
        stored: str = Constants.alias_file.read_text(encoding="utf-8").strip()
        if stored:
            alias = stored
    return f"{alias} {values[0]}"


def main(*, arguments: tuple[str, ...] | None = None) -> int:
    """Copy a validated lake reconnect command to the macOS clipboard.

    Args:
        arguments: Arguments.

    Returns:
        The resulting value.

    """
    values: tuple[str, ...] = (
        tuple(argv[1:]) if arguments is None else arguments
    )
    if (
        len(values) != 1
        or (command := command_from_url(url=values[0])) is None
    ):
        return 2
    completed: CompletedProcess[str] = run(
        args=("/usr/bin/pbcopy",),
        input=command,
        text=True,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
