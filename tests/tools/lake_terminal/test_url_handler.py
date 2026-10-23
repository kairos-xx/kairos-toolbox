# Copyright (c) 2026 Project contributors.
"""Lake Terminal URI handler tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import Final, final
from unittest.mock import patch

from arrow_uri.context import Deadline, RequestContext, RequestIdentity
from arrow_uri.errors import AuthorizationError
from arrow_uri.models import CallerInfo, SecurityContext
from arrow_uri.trace import Trace
from arrow_uri.trust import Principal
from arrow_uri.validity import ValidityCode, ValidityResult

from tests.tools.lake_terminal.helpers import check, expect
from tools.lake_terminal.lake_terminal.arrow_api import LakeArrowResult
from tools.lake_terminal.lake_terminal.url_handler import main


@final
class Constants:
    """Group module-level constants.

    Attributes:
        usage_exit: Grouped module constant.
        execution_exit: Grouped module constant.

    """

    usage_exit: Final[int] = 2
    execution_exit: Final[int] = 3


def _fake_result(*, response: str) -> LakeArrowResult:
    """Build an Arrow result with a fabricated context.

    Args:
        response: IPC response text.

    Returns:
        The fabricated Arrow result.

    """
    return LakeArrowResult(
        response=response,
        context=RequestContext(
            request=RequestIdentity(
                request_id="r",
                operation="daemon.ping",
                uri="lake-terminal://request/v1/token",
            ),
            caller=CallerInfo(),
            principal=Principal(),
            security=SecurityContext(authenticated=True),
            validity=ValidityResult(
                valid=True,
                code=ValidityCode.VALID,
                reason="ok",
            ),
            trace=Trace(),
            deadline=Deadline(at=datetime.now(tz=UTC) + timedelta(minutes=1)),
        ),
    )


def test_main_rejects_bad_argument_count() -> None:
    """Zero or multiple URI arguments are a usage error."""
    expect(actual=main(arguments=()), expected=Constants.usage_exit)
    expect(
        actual=main(arguments=("lake-terminal://request/v1/t", "extra")),
        expected=Constants.usage_exit,
    )


def test_main_rejects_foreign_scheme() -> None:
    """URIs with another scheme are a usage error."""
    expect(
        actual=main(arguments=("other://request/v1/token",)),
        expected=Constants.usage_exit,
    )


def test_copy_netloc_is_not_a_special_route() -> None:
    """The retired ``copy`` host is handled like any other Arrow URI."""
    uri: str = "lake-terminal://copy/x"
    with patch(
        target="tools.lake_terminal.lake_terminal.url_handler.LakeArrow",
    ) as arrow:
        arrow.return_value.execute_uri.return_value = _fake_result(
            response="",
        )
        status: int = main(arguments=(uri,))
    expect(actual=status, expected=0)
    arrow.return_value.execute_uri.assert_called_once_with(uri=uri)


def test_main_executes_arrow_and_writes_response() -> None:
    """Valid Arrow URIs execute and print their response."""
    with patch(
        target="tools.lake_terminal.lake_terminal.url_handler.LakeArrow",
    ) as arrow:
        arrow.return_value.execute_uri.return_value = _fake_result(
            response="PONG",
        )
        stream: StringIO = StringIO()
        with patch(
            target="tools.lake_terminal.lake_terminal.url_handler.stdout",
            new=stream,
        ):
            status: int = main(arguments=("lake-terminal://request/v1/token",))
    expect(actual=status, expected=0)
    expect(actual=stream.getvalue(), expected="PONG\n")


def test_main_skips_empty_response() -> None:
    """Empty IPC responses produce no output."""
    with patch(
        target="tools.lake_terminal.lake_terminal.url_handler.LakeArrow",
    ) as arrow:
        arrow.return_value.execute_uri.return_value = _fake_result(response="")
        with patch(
            target="tools.lake_terminal.lake_terminal.url_handler.stdout",
        ) as out:
            status: int = main(arguments=("lake-terminal://request/v1/token",))
    expect(actual=status, expected=0)
    check(
        condition=not out.write.called,
        reason="empty responses must not be written",
    )


def test_main_maps_handled_errors_to_exit_three() -> None:
    """Known execution failures become exit code three."""
    failures: tuple[BaseException, ...] = (
        AuthorizationError("denied"),
        KeyError("op"),
        ConnectionError("down"),
        OSError("io"),
        EOFError("closed"),
    )
    for failure in failures:
        with patch(
            target="tools.lake_terminal.lake_terminal.url_handler.LakeArrow",
        ) as arrow:
            arrow.return_value.execute_uri.side_effect = failure
            status: int = main(arguments=("lake-terminal://request/v1/token",))
        expect(actual=status, expected=Constants.execution_exit)
