# Copyright (c) 2026 Project contributors.
"""Lake Terminal Arrow adapter tests."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from arrow_uri.errors import AuthorizationError

from tests.tools.lake_terminal.helpers import check, expect
from tools.lake_terminal.lake_terminal.arrow_api import (
    LakeArrow,
    LakeArrowResult,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_arrow_ping_uri_uses_existing_ipc(tmp_path: Path) -> None:
    """A validated Arrow URI dispatches to the existing IPC boundary.

    Args:
        tmp_path: Temporary test directory.

    """
    api: LakeArrow = LakeArrow(home=tmp_path)
    uri: str = api.create_uri(operation="daemon.ping")
    with patch(
        target="tools.lake_terminal.lake_terminal.arrow_api.EventServer.request",
        return_value="PONG",
    ) as request:
        result: LakeArrowResult = api.execute_uri(uri=uri)
    expect(actual=result.response, expected="PONG")
    expect(actual=result.context.request.operation, expected="daemon.ping")
    check(
        condition=result.context.validity.valid,
        reason="validated URIs must produce valid contexts",
    )
    check(
        condition=result.context.security.authenticated,
        reason="validated URIs must authenticate the context",
    )
    request.assert_called_once_with(
        home=tmp_path,
        operation="ping",
        values={},
    )


def test_arrow_uri_is_one_use(tmp_path: Path) -> None:
    """A consumed control URI cannot be replayed.

    Args:
        tmp_path: Temporary test directory.

    """
    api: LakeArrow = LakeArrow(home=tmp_path)
    uri: str = api.create_uri(operation="daemon.ping")
    with patch(
        target="tools.lake_terminal.lake_terminal.arrow_api.EventServer.request",
        return_value="PONG",
    ):
        _ = api.execute_uri(uri=uri)
        with pytest.raises(expected_exception=AuthorizationError):
            api.execute_uri(uri=uri)


def test_arrow_rejects_unexposed_operation(tmp_path: Path) -> None:
    """The adapter never exposes arbitrary IPC operation names.

    Args:
        tmp_path: Temporary test directory.

    """
    api: LakeArrow = LakeArrow(home=tmp_path)
    with pytest.raises(expected_exception=KeyError):
        api.create_uri(operation="shell.execute")


def test_arrow_ipc_values_passthrough(tmp_path: Path) -> None:
    """Scalar URI values reach IPC as strings.

    Args:
        tmp_path: Temporary test directory.

    """
    api: LakeArrow = LakeArrow(home=tmp_path)
    uri: str = api.create_uri(
        operation="conpty.write",
        values={"pid": 42, "data": "x", "flag": True, "empty": None},
    )
    with patch(
        target="tools.lake_terminal.lake_terminal.arrow_api.EventServer.request",
        return_value="OK",
    ) as request:
        result: LakeArrowResult = api.execute_uri(uri=uri)
    expect(actual=result.response, expected="OK")
    expect(
        actual=request.call_args.kwargs["values"],
        expected={
            "pid": "42",
            "data": "x",
            "flag": "1",
            "empty": "",
        },
    )
