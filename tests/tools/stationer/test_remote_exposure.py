# Copyright (c) 2026 Project contributors.
"""Tests for the exposure facade over ``tools.charter``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, final

from tests.tools.charter.fakes import FakeProcess
from tools.charter import CharterConstants, CharterError, Settings
from tools.stationer import remote_exposure
from tools.stationer.remote_exposure import (
    Charter,
    ExposureConstants,
    ExposureError,
    install_ngrok,
    shorten_url,
    start_charter,
    start_ngrok,
)

from .helpers import check, expect

if TYPE_CHECKING:
    import pytest


@dataclass(slots=True, kw_only=True)
class _Call:
    """One recorded facade call.

    Attributes:
        port: Port the caller exposed.
        settings: Settings the facade resolved.
        backend: Backend name, when forwarded.
        url: URL, when forwarded.
        alias: Requested alias, when forwarded.

    """

    port: int | None = None
    settings: Settings | None = None
    backend: str | None = None
    url: str | None = None
    alias: str | None = None


@final
class _Recorder:
    """Record a call and return a canned charter."""

    def __init__(self) -> None:
        """Create the recorder."""
        self.calls: list[_Call] = []
        self.charter: Charter = Charter(
            process=FakeProcess().as_popen(),
            public_url="https://x.tunnl.gg",
            stop_timeout_seconds=0.01,
        )

    def open(
        self,
        *,
        backend: str | None = None,
        port: int,
        settings: Settings | None = None,
    ) -> Charter | None:
        """Record an ``open_charter`` call.

        Args:
            backend: Backend override.
            port: Local port.
            settings: Resolved settings.

        Returns:
            The canned charter.

        """
        self.calls.append(
            _Call(port=port, backend=backend, settings=settings),
        )
        return self.charter

    def start_ngrok(
        self,
        *,
        port: int,
        settings: Settings | None = None,
    ) -> Charter:
        """Record a ``start_ngrok`` call.

        Args:
            port: Local port.
            settings: Resolved settings.

        Returns:
            The canned charter.

        """
        self.calls.append(_Call(port=port, settings=settings))
        return self.charter

    def shorten(
        self,
        *,
        url: str,
        alias: str | None = None,
        settings: Settings | None = None,
    ) -> str:
        """Record a ``shorten_url`` call.

        Args:
            url: URL to shorten.
            alias: Requested alias.
            settings: Resolved settings.

        Returns:
            A fake short URL.

        """
        self.calls.append(_Call(url=url, alias=alias, settings=settings))
        return "https://shrtr.top/x"


def test_the_facade_reexports_the_charter_types() -> None:
    """``ExposureError`` and ``Charter`` are the charter package types."""
    check(condition=ExposureError is CharterError, reason="error alias")
    check(
        condition=ExposureConstants.backends == CharterConstants.backends,
        reason="backends alias",
    )
    check(
        condition=remote_exposure.Charter.__name__ == "Charter",
        reason="charter type",
    )


def test_start_charter_forwards_the_backend_and_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``start_charter`` opens the charter and folds in the authtoken.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    recorder: _Recorder = _Recorder()
    monkeypatch.setattr(
        target=remote_exposure,
        name="open_charter",
        value=recorder.open,
    )
    result: Charter | None = start_charter(
        backend="pinggy",
        port=8765,
        authtoken="tok",
    )
    expect(actual=result, expected=recorder.charter)
    call: _Call = recorder.calls[0]
    expect(actual=call.backend, expected="pinggy")
    expect(actual=call.port, expected=8765)
    settings: Settings | None = call.settings
    check(
        condition=settings is not None and settings.ngrok_authtoken == "tok",
        reason="authtoken folded into settings",
    )


def test_start_ngrok_forwards_the_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``start_ngrok`` keeps its classic signature.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    recorder: _Recorder = _Recorder()
    monkeypatch.setattr(
        target=remote_exposure,
        name="charter_start_ngrok",
        value=recorder.start_ngrok,
    )
    charter: Charter = start_ngrok(port=1, authtoken=None)
    expect(actual=charter, expected=recorder.charter)
    expect(actual=recorder.calls[0].port, expected=1)
    check(
        condition=isinstance(recorder.calls[0].settings, Settings),
        reason="settings resolved",
    )


def test_install_ngrok_uses_the_configured_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``install_ngrok`` resolves settings before delegating.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    seen: list[Settings] = []

    def fake(*, settings: Settings) -> Path:
        """Record the settings.

        Args:
            settings: Resolved charter settings.

        Returns:
            A fake executable path.

        """
        seen.append(settings)
        return Path("/bin/ngrok")

    monkeypatch.setattr(
        target=remote_exposure,
        name="charter_install_ngrok",
        value=fake,
    )
    expect(actual=install_ngrok(), expected=Path("/bin/ngrok"))
    check(condition=len(seen) == 1, reason="settings forwarded")


def test_shorten_url_forwards_url_and_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``shorten_url`` forwards the URL and optional alias.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    recorder: _Recorder = _Recorder()
    monkeypatch.setattr(
        target=remote_exposure,
        name="charter_shorten_url",
        value=recorder.shorten,
    )
    expect(
        actual=shorten_url(url="https://long", alias="mine"),
        expected="https://shrtr.top/x",
    )
    call: _Call = recorder.calls[0]
    expect(actual=call.url, expected="https://long")
    expect(actual=call.alias, expected="mine")
