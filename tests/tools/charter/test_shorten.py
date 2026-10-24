# Copyright (c) 2026 Project contributors.
"""Tests for anonymous URL shortening."""

from __future__ import annotations

from json import dumps, loads

import pytest

from tools.charter import shorten
from tools.charter.errors import CharterError
from tools.charter.settings import Settings
from tools.charter.shorten import shorten_url

from .fakes import Answers
from .helpers import expect, present


@pytest.mark.parametrize("alias", [None, "mine"])
def test_shorten_url_posts_the_target(
    monkeypatch: pytest.MonkeyPatch,
    alias: str | None,
) -> None:
    """The short URL service gets the target (and alias) as JSON.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        alias: Requested alias, if any.

    """
    answers: Answers = Answers(
        answers=[dumps(obj={"short_url": "https://shrtr.top/x"}).encode()],
    )
    monkeypatch.setattr(
        target=shorten,
        name="http_request",
        value=answers,
    )
    expect(
        actual=shorten_url(
            url="https://long",
            alias=alias,
            settings=Settings(),
        ),
        expected="https://shrtr.top/x",
    )
    sent: bytes = present(value=answers.bodies[0])
    expect(
        actual=loads(s=sent),
        expected={
            "url": "https://long",
            **({"alias": alias} if alias else {}),
        },
    )


@pytest.mark.parametrize(
    "answer",
    [b"not json", b"[]", dumps(obj={"short_url": "http://insecure"}).encode()],
)
def test_shorten_url_rejects_bad_responses(
    monkeypatch: pytest.MonkeyPatch,
    answer: bytes,
) -> None:
    """Malformed or non-HTTPS responses are errors.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        answer: Canned response body.

    """
    monkeypatch.setattr(
        target=shorten,
        name="http_request",
        value=Answers(answers=[answer]),
    )
    with pytest.raises(expected_exception=CharterError, match="shorten"):
        _ = shorten_url(url="https://long", settings=Settings())


def test_the_configured_shortener_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``settings.shortener`` selects the endpoint.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    answers: Answers = Answers(
        answers=[dumps(obj={"short_url": "https://s.example/x"}).encode()],
    )
    monkeypatch.setattr(
        target=shorten,
        name="http_request",
        value=answers,
    )
    _ = shorten_url(
        url="https://long",
        settings=Settings(shortener="https://s.example/api"),
    )
    expect(actual=answers.urls[0], expected="https://s.example/api")
