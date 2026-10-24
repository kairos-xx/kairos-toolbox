# Copyright (c) 2026 Project contributors.
"""Tests for optional public exposure of the web dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, cast, final

import pytest
from district import cli as cli_module
from district import exposure as exposure_module
from district import web as web_module
from district.cli import Cli
from district.core import (
    District,
    SessionRepository,
)
from district.exposure import (
    Exposure,
    ExposureConstants,
    ExposureError,
    PublicEndpoint,
    discover_pyproject,
    load_exposure,
    open_exposure,
)
from district.web import WebDashboard

from .helpers import check, expect, present

if TYPE_CHECKING:
    from types import ModuleType


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class FakeCharterSettings:
    """Stand-in for ``tools.charter.Settings`` (a dataclass).

    Attributes:
        backend: Selected backend.
        ngrok_authtoken: ngrok account token.

    """

    backend: str = "direct"
    ngrok_authtoken: str | None = None


@final
class FakeSettingsLoader:
    """Return fixed ``tools.charter`` settings."""

    @staticmethod
    def load() -> FakeCharterSettings:
        """Return default fake settings.

        Returns:
            Fresh fake settings.

        """
        return FakeCharterSettings()


@final
class FakeCharter:
    """Record ``close`` and report a fixed public URL."""

    def __init__(self) -> None:
        """Initialize the fake charter."""
        self.public_url: str = "https://unit.test.charter"
        self.closed: bool = False

    def close(self) -> None:
        """Record the stop."""
        self.closed = True


class FakeCharterError(Exception):
    """Stand-in for ``tools.charter.CharterError``."""


@final
class FakeApi:
    """A minimal ``tools.charter`` module stand-in."""

    def __init__(
        self,
        *,
        charter: FakeCharter,
        fails: bool = False,
    ) -> None:
        """Initialize the fake module.

        Args:
            charter: Charter returned by ``open_charter``.
            fails: When true, ``open_charter`` raises the fake error.

        """
        self.charter: FakeCharter = charter
        self.fails: bool = fails
        self.calls: list[dict[str, object]] = []
        self.SettingsLoader: type[FakeSettingsLoader] = FakeSettingsLoader
        self.CharterError: type[BaseException] = FakeCharterError

    def open_charter(self, **kwargs: object) -> FakeCharter:
        """Record the call and return the fake charter.

        Args:
            kwargs: ``port`` and merged ``settings``.

        Returns:
            The fake charter.

        Raises:
            FakeCharterError: When ``fails`` is set.

        """
        self.calls.append(kwargs)
        if self.fails:
            msg: str = "backend failed"
            raise FakeCharterError(msg)
        return self.charter

    def shorten_url(self, **kwargs: object) -> str:
        """Record the call and return a short URL.

        Args:
            kwargs: ``url``, ``alias`` and ``settings``.

        Returns:
            A fixed short URL.

        """
        self.calls.append(kwargs)
        return "https://sho.rt/district"


def _api(monkeypatch: pytest.MonkeyPatch, api: FakeApi) -> None:
    """Point ``open_exposure`` at the fake ``tools.charter``.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        api: Fake module.

    """

    def fake_api() -> ModuleType:
        """Return the fake module.

        Returns:
            The fake ``tools.charter`` module.

        """
        return cast(typ="ModuleType", val=api)

    monkeypatch.setattr(
        target=exposure_module,
        name="charter_api",
        value=fake_api,
    )


def _settings(api: FakeApi) -> FakeCharterSettings:
    """Return the settings ``open_charter`` received.

    Args:
        api: Fake module.

    Returns:
        The merged settings passed to the fake backend.

    """
    settings: object = api.calls[0]["settings"]
    return cast(typ="FakeCharterSettings", val=settings)


def _dashboard(home: Path) -> WebDashboard:
    """Build a dashboard rooted in a scratch home.

    Args:
        home: Temporary home directory.

    Returns:
        A dashboard instance.

    """
    return WebDashboard(
        service=District(repository=SessionRepository(home=home)),
        home=home,
    )


def test_exposure_defaults_to_direct() -> None:
    """No configuration means no public exposure."""
    exposure: Exposure = load_exposure(pyproject=Path("does-not-exist.toml"))
    expect(actual=exposure.backend, expected="direct")


def test_exposure_reads_the_district_charter_table(
    tmp_path: Path,
) -> None:
    """``[tool.district.charter]`` feeds the resolved exposure.

    Args:
        tmp_path: Temporary home directory.

    """
    pyproject: Path = tmp_path / "pyproject.toml"
    _ = pyproject.write_text(
        data=(
            "[tool.district.charter]\n"
            'backend = "ngrok"\n'
            'ngrok_authtoken = "tok"\n'
            'alias = "district"\n'
            "shorten = true\n"
        ),
        encoding="utf-8",
    )
    exposure: Exposure = load_exposure(pyproject=pyproject)
    expect(actual=exposure.backend, expected="ngrok")
    expect(actual=exposure.ngrok_authtoken, expected="tok")
    expect(actual=exposure.alias, expected="district")
    expect(actual=exposure.shorten, expected=True)


def test_exposure_overrides_win_over_pyproject(tmp_path: Path) -> None:
    """Explicit overrides (the CLI) beat the project file.

    Args:
        tmp_path: Temporary home directory.

    """
    pyproject: Path = tmp_path / "pyproject.toml"
    _ = pyproject.write_text(
        data='[tool.district.charter]\nbackend = "pinggy"\n',
        encoding="utf-8",
    )
    exposure: Exposure = load_exposure(
        pyproject=pyproject,
        overrides={"backend": "runlocal", "shorten": True},
    )
    expect(actual=exposure.backend, expected="runlocal")
    expect(actual=exposure.shorten, expected=True)


def test_exposure_rejects_unknown_keys(tmp_path: Path) -> None:
    """Typos in the table or overrides fail fast.

    Args:
        tmp_path: Temporary home directory.

    """
    pyproject: Path = tmp_path / "pyproject.toml"
    _ = pyproject.write_text(
        data='[tool.district.charter]\nbakcend = "ngrok"\n',
        encoding="utf-8",
    )
    with pytest.raises(expected_exception=ValueError, match="Unknown"):
        _ = load_exposure(pyproject=pyproject)
    with pytest.raises(expected_exception=ValueError, match="Unknown"):
        _ = load_exposure(
            pyproject=pyproject,
            overrides={"bogus": "x"},
        )


def test_exposure_rejects_bad_values(tmp_path: Path) -> None:
    """Wrong types and unknown backends are rejected.

    Args:
        tmp_path: Temporary home directory.

    """
    pyproject: Path = tmp_path / "pyproject.toml"
    _ = pyproject.write_text(
        data='[tool.district.charter]\nbackend = "bogus"\n',
        encoding="utf-8",
    )
    with pytest.raises(expected_exception=ValueError, match="backend"):
        _ = load_exposure(pyproject=pyproject)
    _ = pyproject.write_text(
        data="[tool.district.charter]\nshorten = 1\n",
        encoding="utf-8",
    )
    with pytest.raises(expected_exception=TypeError, match="boolean"):
        _ = load_exposure(pyproject=pyproject)
    _ = pyproject.write_text(
        data="[tool.district]\ncharter = 1\n",
        encoding="utf-8",
    )
    with pytest.raises(expected_exception=TypeError, match="table"):
        _ = load_exposure(pyproject=pyproject)


def test_exposure_ignores_unrelated_tables(tmp_path: Path) -> None:
    """Projects without ``[tool.district.charter]`` get defaults.

    Args:
        tmp_path: Temporary home directory.

    """
    pyproject: Path = tmp_path / "pyproject.toml"
    _ = pyproject.write_text(data="", encoding="utf-8")
    expect(
        actual=load_exposure(pyproject=pyproject).backend,
        expected="direct",
    )
    _ = pyproject.write_text(
        data='[tool]\nother = "x"\n',
        encoding="utf-8",
    )
    expect(
        actual=load_exposure(pyproject=pyproject).backend,
        expected="direct",
    )
    _ = pyproject.write_text(
        data='[tool]\ndistrict = "x"\n',
        encoding="utf-8",
    )
    expect(
        actual=load_exposure(pyproject=pyproject).backend,
        expected="direct",
    )


def test_exposure_rejects_non_string_fields(tmp_path: Path) -> None:
    """``backend`` and the optional strings must be strings.

    Args:
        tmp_path: Temporary home directory.

    """
    pyproject: Path = tmp_path / "pyproject.toml"
    _ = pyproject.write_text(
        data="[tool.district.charter]\nbackend = 1\n",
        encoding="utf-8",
    )
    with pytest.raises(expected_exception=TypeError, match="string"):
        _ = load_exposure(pyproject=pyproject)
    _ = pyproject.write_text(
        data="[tool.district.charter]\nalias = 1\n",
        encoding="utf-8",
    )
    with pytest.raises(expected_exception=TypeError, match="string"):
        _ = load_exposure(pyproject=pyproject)


def test_exposure_override_none_is_ignored(tmp_path: Path) -> None:
    """``None`` overrides leave the configured value alone.

    Args:
        tmp_path: Temporary home directory.

    """
    pyproject: Path = tmp_path / "pyproject.toml"
    _ = pyproject.write_text(
        data='[tool.district.charter]\nbackend = "ngrok"\n',
        encoding="utf-8",
    )
    exposure: Exposure = load_exposure(
        pyproject=pyproject,
        overrides={"backend": None, "alias": "district"},
    )
    expect(actual=exposure.backend, expected="ngrok")
    expect(actual=exposure.alias, expected="district")


def test_discover_pyproject_walks_up(tmp_path: Path) -> None:
    """The nearest ``pyproject.toml`` above a path is found.

    Args:
        tmp_path: Temporary home directory.

    """
    nested: Path = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    target: Path = tmp_path / "pyproject.toml"
    _ = target.write_text(data="[project]\nname = 'x'\n", encoding="utf-8")
    expect(actual=discover_pyproject(start=nested), expected=target)
    expect(actual=discover_pyproject(start=Path("/")), expected=None)


def test_open_exposure_direct_returns_none() -> None:
    """``direct`` never touches ``tools.charter``."""
    check(
        condition=open_exposure(port=8765, exposure=Exposure()) is None,
        reason="direct must not open a charter",
    )


def test_open_exposure_starts_the_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backend spawns a charter on the dashboard port.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    api: FakeApi = FakeApi(charter=FakeCharter())
    _api(monkeypatch=monkeypatch, api=api)
    endpoint: PublicEndpoint = present(
        value=open_exposure(
            port=9999,
            exposure=Exposure(backend="tunnl", ngrok_authtoken="tok"),
        ),
    )
    expect(actual=endpoint.url, expected=api.charter.public_url)
    expect(actual=api.calls[0]["port"], expected=9999)
    merged: FakeCharterSettings = _settings(api=api)
    expect(actual=merged.backend, expected="tunnl")
    expect(actual=merged.ngrok_authtoken, expected="tok")


def test_open_exposure_shortens_when_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``shorten`` routes the public URL through the shortener.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    api: FakeApi = FakeApi(charter=FakeCharter())
    _api(monkeypatch=monkeypatch, api=api)
    endpoint: PublicEndpoint = present(
        value=open_exposure(
            port=8765,
            exposure=Exposure(
                backend="pinggy",
                shorten=True,
                alias="district",
            ),
        ),
    )
    expect(actual=endpoint.url, expected="https://sho.rt/district")
    expect(actual=endpoint.charter_url, expected=api.charter.public_url)
    shortened: dict[str, object] = api.calls[-1]
    expect(actual=shortened["alias"], expected="district")


def test_open_exposure_wraps_backend_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend errors surface as ``ExposureError``.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    _api(
        monkeypatch=monkeypatch,
        api=FakeApi(charter=FakeCharter(), fails=True),
    )
    with pytest.raises(expected_exception=ExposureError, match="failed"):
        _ = open_exposure(port=8765, exposure=Exposure(backend="ngrok"))


def test_charter_api_without_tools_charter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installed builds report the missing package cleanly.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """

    def missing(*, name: str) -> ModuleType:
        """Raise ``ImportError`` like an installed build.

        Args:
            name: Module name.

        Returns:
            Never returns.

        Raises:
            ImportError: Always.

        """
        raise ImportError(name)

    monkeypatch.setattr(
        target=exposure_module,
        name="import_module",
        value=missing,
    )
    with pytest.raises(
        expected_exception=ExposureError,
        match=r"tools\.charter",
    ):
        _ = exposure_module.charter_api()


def test_web_run_opens_and_closes_the_charter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run`` exposes the port and closes the charter afterwards.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    opened: list[int] = []
    charter: FakeCharter = FakeCharter()

    def fake_open(*, port: int, exposure: Exposure | None) -> PublicEndpoint:
        """Record the port and return a fixed endpoint.

        Args:
            port: Local dashboard port.
            exposure: Ignored resolved choices.

        Returns:
            A fake public endpoint.

        """
        _ = exposure
        opened.append(port)
        return PublicEndpoint(
            url="https://sho.rt/district",
            charter_url=charter.public_url,
            charter=charter,
        )

    class _Server:
        """A uvicorn server that returns immediately."""

        def __init__(self, *, config: object) -> None:
            """Store nothing.

            Args:
                config: Ignored server configuration.

            """
            _ = config

        def run(self) -> None:
            """Return immediately."""

    out: StringIO = StringIO()
    monkeypatch.setattr(target=web_module, name="stdout", value=out)
    monkeypatch.setattr(target=web_module, name="Server", value=_Server)
    monkeypatch.setattr(
        target=web_module,
        name="open_exposure",
        value=fake_open,
    )
    status: int = _dashboard(home=tmp_path).run(
        host="127.0.0.1",
        port=0,
        browser=False,
        exposure=Exposure(backend="ngrok"),
    )
    expect(actual=status, expected=0)
    expect(actual=len(opened), expected=1)
    check(condition=charter.closed, reason="charter must close after run")
    check(
        condition="https://sho.rt/district" in out.getvalue(),
        reason="the public URL must be announced",
    )


def test_web_options_fold_cli_flags_into_exposure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI maps charter flags onto the resolved exposure.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(
        target=cli_module,
        name="load_exposure",
        value=_direct,
    )
    parsed: tuple[str, int, bool, Exposure] | None = Cli.web_options(
        args=(
            "--expose",
            "ngrok",
            "--charter-short",
            "--charter-alias",
            "district",
            "--charter-authtoken",
            "tok",
            "--no-browser",
        ),
    )
    host, port, browser, exposure = present(value=parsed)
    expect(actual=host, expected="127.0.0.1")
    expect(actual=browser, expected=False)
    expect(actual=exposure.backend, expected="ngrok")
    expect(actual=exposure.shorten, expected=True)
    expect(actual=exposure.alias, expected="district")
    expect(actual=exposure.ngrok_authtoken, expected="tok")
    _ = port


def test_web_options_reject_bad_exposure_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown backends, missing values, and bad pyproject fail.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(
        target=cli_module,
        name="stderr",
        value=StringIO(),
    )
    monkeypatch.setattr(
        target=cli_module,
        name="load_exposure",
        value=_direct,
    )
    check(
        condition=Cli.web_options(args=("--expose", "bogus")) is None,
        reason="unknown backend",
    )
    check(
        condition=Cli.web_options(args=("--charter-alias",)) is None,
        reason="missing value",
    )

    def broken() -> Exposure:
        """Raise like a malformed project file.

        Returns:
            Never returns.

        Raises:
            ValueError: Always.

        """
        msg: str = "bad"
        raise ValueError(msg)

    monkeypatch.setattr(
        target=cli_module,
        name="load_exposure",
        value=broken,
    )
    check(
        condition=Cli.web_options(args=()) is None,
        reason="invalid pyproject",
    )


@final
class FakeDashboard:
    """Stand-in dashboard recording ``run`` arguments."""

    def __init__(self) -> None:
        """Initialize empty recording state."""
        self.kwargs: dict[str, object] = {}
        self.fails: bool = False

    def run(self, **kwargs: object) -> int:
        """Record or raise.

        Args:
            kwargs: ``host``, ``port``, ``browser`` and ``exposure``.

        Returns:
            Exit status.

        Raises:
            ExposureError: When ``fails`` is set.

        """
        self.kwargs = kwargs
        if self.fails:
            msg: str = "charter down"
            raise ExposureError(msg)
        return 0


def test_web_cli_dispatches_exposure_to_the_dashboard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``district --web --expose`` reaches ``WebDashboard.run``.

    Args:
        tmp_path: Temporary home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    monkeypatch.setattr(
        target=cli_module,
        name="stderr",
        value=StringIO(),
    )
    monkeypatch.setattr(
        target=cli_module,
        name="load_exposure",
        value=_direct,
    )
    dashboard: FakeDashboard = FakeDashboard()

    def fake_dashboard(**kwargs: object) -> FakeDashboard:
        """Return the fixed fake dashboard.

        Args:
            kwargs: ``service`` and ``home``.

        Returns:
            The fake dashboard.

        """
        _ = kwargs
        return dashboard

    monkeypatch.setattr(
        target=cli_module,
        name="WebDashboard",
        value=fake_dashboard,
    )
    cli: Cli = Cli(
        service=District(repository=SessionRepository(home=tmp_path)),
    )
    status: int = cli.run(
        arguments=("--web", "--expose", "pinggy", "--no-browser"),
    )
    expect(actual=status, expected=0)
    exposure: object = dashboard.kwargs["exposure"]
    check(
        condition=isinstance(exposure, Exposure)
        and exposure.backend == "pinggy",
        reason="the CLI exposure reaches the dashboard",
    )
    dashboard.fails = True
    status = cli.run(
        arguments=("--web", "--expose", "ngrok"),
    )
    expect(actual=status, expected=1)


def _direct() -> Exposure:
    """Return the default (unexposed) exposure choices.

    Returns:
        A ``direct`` exposure.

    """
    return Exposure()


def test_exposure_constants_cover_every_backend() -> None:
    """All four real backends plus ``direct`` are exposed."""
    expect(
        actual=ExposureConstants.backends,
        expected={"direct", "ngrok", "pinggy", "tunnl", "runlocal"},
    )
