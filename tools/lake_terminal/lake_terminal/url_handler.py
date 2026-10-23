# Copyright (c) 2026 Project contributors.
"""Handle Lake Terminal custom URIs through Arrow or the legacy copy route."""

from __future__ import annotations

from os import environ
from pathlib import Path
from sys import argv, stdout
from urllib.parse import SplitResult, urlsplit

from arrow_uri.errors import AuthorizationError

from .arrow_api import LakeArrow, LakeArrowResult
from .copy_url import main as copy_main
from .product import Product


def main(*, arguments: tuple[str, ...] | None = None) -> int:
    """Dispatch one OS-opened Lake Terminal URI.

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
    if parsed.netloc == "copy":
        return copy_main(arguments=(uri,))
    home: Path = Path(environ.get("HOME", str(object=Path.home())))
    try:
        result: LakeArrowResult = LakeArrow(home=home).execute_uri(uri=uri)
    except (AuthorizationError, KeyError, ConnectionError, OSError, EOFError):
        return 3
    if result.response:
        _ = stdout.write(result.response + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
