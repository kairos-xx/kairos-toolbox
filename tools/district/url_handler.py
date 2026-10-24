# Copyright (c) 2026 Project contributors.
"""Handle District Terminal custom URIs through Bowman."""

from __future__ import annotations

from os import environ
from pathlib import Path
from sys import argv, stdout
from urllib.parse import SplitResult, urlsplit

from bowman.errors import AuthorizationError

from .bowman_api import DistrictBowman, DistrictBowmanResult
from .product import Product


def main(*, arguments: tuple[str, ...] | None = None) -> int:
    """Dispatch one OS-opened District Terminal URI.

    Args:
        arguments: Explicit URI arguments.

    Returns:
        Process exit status.

    """
    values: tuple[str, ...] = (
        tuple(argv[1:]) if arguments is None else arguments
    )
    if len(values) != 1:
        return 2
    uri: str = values[0]
    parsed: SplitResult = urlsplit(url=uri)
    if parsed.scheme != Product.url_scheme:
        return 2
    home: Path = Path(environ.get("HOME", str(object=Path.home())))
    try:
        result: DistrictBowmanResult = DistrictBowman(
            home=home,
        ).execute_uri(uri=uri)
    except (AuthorizationError, KeyError, ConnectionError, OSError, EOFError):
        return 3
    if result.response:
        _ = stdout.write(result.response + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
