# Copyright (c) 2026 Project contributors.
"""Render the Windows and POSIX remote build workers."""

from __future__ import annotations

from base64 import b64encode
from json import dumps
from pathlib import Path
from typing import TYPE_CHECKING, Final, final

if TYPE_CHECKING:
    from collections.abc import Callable

    from tools.stationer.conjure import (
        Credentials,
        Payload,
        RemoteSpec,
    )


@final
class WorkerConstants:
    """Group worker-template constants.

    Attributes:
        folder: Directory containing template files.
        windows_template: PowerShell template file name.
        posix_template: Python template file name for Linux and macOS.
        posix_markers: Heredoc delimiters wrapping the Python program.

    """

    folder: Final[Path] = Path(__file__).parent / "templates"
    windows_template: Final[str] = "worker_windows.ps1.tmpl"
    posix_template: Final[str] = "worker_posix.py.tmpl"
    posix_markers: Final[tuple[str, str]] = (
        "<<'STATIONER_PY'",
        "STATIONER_PY",
    )


def _template(*, name: str) -> str:
    """Read a worker template shipped beside this module.

    Args:
        name: Template file name.

    Returns:
        The template text.

    """
    return WorkerConstants.folder.joinpath(name).read_text(encoding="utf-8")


def _render(*, template: str, values: dict[str, str]) -> str:
    """Substitute ``@@NAME@@`` placeholders.

    Args:
        template: Template text.
        values: Replacement text per placeholder name.

    Returns:
        The rendered text.

    """
    rendered: str = template
    for name, value in values.items():
        rendered = rendered.replace(f"@@{name}@@", value)
    return rendered


def _python_literal(value: str) -> str:
    """Quote text as a Python string literal.

    Args:
        value: Text to quote.

    Returns:
        A literal that evaluates to ``value``.

    """
    return dumps(obj=value)


def _powershell_literal(value: str) -> str:
    """Quote text as a PowerShell single-quoted string.

    Args:
        value: Text to quote.

    Returns:
        A literal that evaluates to ``value``.

    """
    return "'" + value.replace("'", "''") + "'"


def _raw(
    *,
    base_url: str,
    credentials: Credentials,
    payload: Payload,
    spec: RemoteSpec,
    os_name: str,
) -> dict[str, str]:
    """Collect the unquoted placeholder values shared by both workers.

    Args:
        base_url: URL the worker reaches the host at.
        credentials: Job secrets; only the bearer token is embedded.
        payload: Source payload descriptor.
        spec: What the worker must do.
        os_name: Operating system the worker reports.

    Returns:
        Replacement text per placeholder name.

    """
    return {
        "BASE": base_url.rstrip("/"),
        "TOKEN": credentials.token,
        "EXPECTED": payload.sha256,
        "OS": os_name,
        "SPEC": b64encode(s=spec.json().encode()).decode(),
    }


def _quoted(
    *,
    raw: dict[str, str],
    literal: Callable[[str], str],
) -> dict[str, str]:
    """Quote every value as a literal of the target language.

    Args:
        raw: Unquoted values.
        literal: Quoting function for the target language.

    Returns:
        The quoted values.

    """
    return {name: literal(value) for name, value in raw.items()}


def windows_worker(
    *,
    base_url: str,
    credentials: Credentials,
    payload: Payload,
    spec: RemoteSpec,
) -> str:
    """Render the PowerShell worker for a Windows machine.

    Args:
        base_url: URL the worker reaches the host at.
        credentials: Job secrets; only the bearer token is embedded.
        payload: Source payload descriptor.
        spec: What the worker must do.

    Returns:
        PowerShell source.

    """
    return _render(
        template=_template(name=WorkerConstants.windows_template),
        values=_quoted(
            raw=_raw(
                base_url=base_url,
                credentials=credentials,
                payload=payload,
                spec=spec,
                os_name="windows",
            ),
            literal=_powershell_literal,
        ),
    )


def posix_worker(
    *,
    base_url: str,
    credentials: Credentials,
    payload: Payload,
    spec: RemoteSpec,
    os_name: str,
) -> str:
    """Render the Bash entry point for a Linux or macOS machine.

    The script finds Python 3 and runs the standard-library worker program
    from a here-document, so it works as ``curl … | bash``.

    Args:
        base_url: URL the worker reaches the host at.
        credentials: Job secrets; only the bearer token is embedded.
        payload: Source payload descriptor.
        spec: What the worker must do.
        os_name: ``linux`` or ``macos``.

    Returns:
        Bash source.

    """
    opener, closer = WorkerConstants.posix_markers
    program: str = _render(
        template=_template(name=WorkerConstants.posix_template),
        values=_quoted(
            raw=_raw(
                base_url=base_url,
                credentials=credentials,
                payload=payload,
                spec=spec,
                os_name=os_name,
            ),
            literal=_python_literal,
        ),
    )
    return (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        'PYTHON="$(command -v python3 || command -v python || true)"\n'
        '[ -n "$PYTHON" ] || { echo "Python 3 is required." >&2; exit 1; }\n'
        f'exec "$PYTHON" - {opener}\n'
        f"{program}\n"
        f"{closer}\n"
    )
