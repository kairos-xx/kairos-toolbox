# Copyright (c) 2026 Project contributors.
"""Tests for the charter command-line interface."""

from __future__ import annotations

from io import StringIO
from json import loads
from pathlib import Path
from typing import TYPE_CHECKING, final

from tools.charter import cli
from tools.charter.cli import main
from tools.charter.errors import CharterError
from tools.charter.models import Charter
from tools.charter.settings import Settings

from .fakes import FakeProcess
from .helpers import check, expect

if TYPE_CHECKING:
    import pytest


def _capture(
    *,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[StringIO, StringIO]:
    """Swap the CLI's output streams for in-memory buffers.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The stdout and stderr buffers.

    """
    out: StringIO = StringIO()
    err: StringIO = StringIO()
    monkeypatch.setattr(target=cli, name="sys_stdout", value=out)
    monkeypatch.setattr(target=cli, name="sys_stderr", value=err)
    return out, err


def _const_short_url(
    *,
    url: str,
    alias: str | None = None,
    settings: Settings | None = None,
) -> str:
    """Return a fixed short URL.

    Args:
        url: Ignored URL.
        alias: Ignored alias.
        settings: Ignored settings.

    Returns:
        A fixed short URL.

    """
    _ = (url, alias, settings)
    return "https://s/x"


def _const_ngrok(*, settings: Settings | None = None) -> Path:
    """Return a fixed ngrok path.

    Args:
        settings: Ignored settings.

    Returns:
        A fake executable path.

    """
    _ = settings
    return Path("/bin/ngrok")


def test_backends_lists_every_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``backends`` prints one backend name per line.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    out, _ = _capture(monkeypatch=monkeypatch)
    expect(actual=main(arguments=("backends",)), expected=0)
    names: set[str] = set(out.getvalue().split())
    expect(
        actual=names,
        expected={"direct", "ngrok", "pinggy", "runlocal", "tunnl"},
    )


def test_backends_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--json backends`` prints a JSON list of records.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    out, _ = _capture(monkeypatch=monkeypatch)
    expect(actual=main(arguments=("--json", "backends")), expected=0)
    rows: object = loads(s=out.getvalue())
    check(condition="ngrok" in str(object=rows), reason="ngrok listed")


def test_settings_prints_the_resolved_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``settings`` prints every resolved field.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    out, _ = _capture(monkeypatch=monkeypatch)
    expect(actual=main(arguments=("settings",)), expected=0)
    check(condition="backend" in out.getvalue(), reason="backend row")
    check(condition="shortener" in out.getvalue(), reason="shortener row")


def test_expose_direct_reports_nothing_to_do(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``expose --backend direct`` explains that nothing is exposed.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    out, _ = _capture(monkeypatch=monkeypatch)
    expect(
        actual=main(arguments=("expose", "--port", "9999")),
        expected=0,
    )
    check(
        condition="nothing to expose" in out.getvalue(),
        reason="direct note",
    )


@final
class _FakeOpenCharter:
    """Record the call and return a fake charter."""

    def __init__(self, *, process: FakeProcess | None = None) -> None:
        """Create the recorder.

        Args:
            process: Fake backend process to hand the charter.

        """
        self.calls: list[dict[str, object]] = []
        self.process: FakeProcess = process or FakeProcess()
        self.charter: Charter = Charter(
            process=self.process.as_popen(),
            public_url="https://abc.tunnl.gg",
            stop_timeout_seconds=0.01,
        )

    def __call__(
        self,
        *,
        port: int,
        backend: str | None = None,
        settings: Settings | None = None,
    ) -> Charter | None:
        """Record the arguments and return the fake charter.

        Args:
            port: Local port.
            backend: Backend override.
            settings: Resolved settings.

        Returns:
            The fake charter.

        """
        self.calls.append(
            {"port": port, "backend": backend, "settings": settings},
        )
        return self.charter


def test_expose_prints_the_public_url_and_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``expose`` announces the URL and holds the charter open.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    process: FakeProcess = FakeProcess()
    opener: _FakeOpenCharter = _FakeOpenCharter(process=process)
    monkeypatch.setattr(target=cli, name="open_charter", value=opener)
    out, _ = _capture(monkeypatch=monkeypatch)
    expect(
        actual=main(
            arguments=("expose", "--port", "8080", "--backend", "tunnl"),
        ),
        expected=0,
    )
    check(
        condition="https://abc.tunnl.gg" in out.getvalue(),
        reason="URL printed",
    )
    check(condition=process.waited, reason="waited for the charter")
    check(condition=process.terminated, reason="charter closed")
    settings: object = opener.calls[0]["settings"]
    check(
        condition=isinstance(settings, Settings)
        and settings.backend == "tunnl",
        reason="backend override applied",
    )


def test_expose_survives_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ctrl-C closes the charter cleanly and still reports success.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    process: FakeProcess = FakeProcess(interrupt=True)
    monkeypatch.setattr(
        target=cli,
        name="open_charter",
        value=_FakeOpenCharter(process=process),
    )
    expect(
        actual=main(arguments=("expose", "--port", "8080")),
        expected=0,
    )
    check(condition=process.terminated, reason="charter closed")


def test_expose_short_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--short ALIAS`` also prints the shortened URL.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(
        target=cli,
        name="open_charter",
        value=_FakeOpenCharter(),
    )
    seen: list[dict[str, object]] = []

    def fake_shorten(
        *,
        url: str,
        alias: str | None,
        settings: Settings | None,
    ) -> str:
        """Record the request.

        Args:
            url: URL to shorten.
            alias: Requested alias.
            settings: Resolved settings.

        Returns:
            A fake short URL.

        """
        _ = settings
        seen.append({"url": url, "alias": alias})
        return "https://shrtr.top/mine"

    monkeypatch.setattr(
        target=cli,
        name="shorten_url",
        value=fake_shorten,
    )
    out, _ = _capture(monkeypatch=monkeypatch)
    expect(
        actual=main(
            arguments=(
                "expose",
                "--port",
                "8080",
                "--short",
                "mine",
            ),
        ),
        expected=0,
    )
    check(
        condition="https://shrtr.top/mine" in out.getvalue(),
        reason="short printed",
    )
    expect(actual=seen[0]["alias"], expected="mine")


def test_expose_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--json expose`` prints a machine-readable record.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(
        target=cli,
        name="open_charter",
        value=_FakeOpenCharter(),
    )
    out, _ = _capture(monkeypatch=monkeypatch)
    expect(
        actual=main(
            arguments=("--json", "expose", "--port", "8080"),
        ),
        expected=0,
    )
    row: dict[str, object] = loads(s=out.getvalue())
    expect(actual=row["public_url"], expected="https://abc.tunnl.gg")
    expect(actual=row["port"], expected=8080)


def test_shorten_prints_the_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``shorten`` prints the short URL.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(
        target=cli,
        name="shorten_url",
        value=_const_short_url,
    )
    out, _ = _capture(monkeypatch=monkeypatch)
    expect(
        actual=main(arguments=("shorten", "https://long")),
        expected=0,
    )
    expect(actual=out.getvalue().strip(), expected="https://s/x")


def test_install_ngrok_prints_the_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``install-ngrok`` prints the resolved executable path.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(
        target=cli,
        name="install_ngrok",
        value=_const_ngrok,
    )
    out, _ = _capture(monkeypatch=monkeypatch)
    expect(actual=main(arguments=("install-ngrok",)), expected=0)
    expect(actual=out.getvalue().strip(), expected="/bin/ngrok")


def test_errors_are_reported_with_exit_status_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend failures become a stderr message and exit status ``1``.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """

    def fail(**kwargs: object) -> Charter | None:
        """Raise a charter error unconditionally.

        Args:
            kwargs: Ignored arguments.

        Returns:
            Never returns.

        Raises:
            CharterError: Always.

        """
        _ = kwargs
        msg: str = "no way"
        raise CharterError(msg)

    monkeypatch.setattr(target=cli, name="open_charter", value=fail)
    _, err = _capture(monkeypatch=monkeypatch)
    expect(
        actual=main(arguments=("expose", "--port", "8080")),
        expected=1,
    )
    check(
        condition="no way" in err.getvalue(),
        reason="error on stderr",
    )


def test_the_pyproject_flag_feeds_the_loader(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``--pyproject`` selects the configuration file.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory.

    """
    pyproject: Path = tmp_path / "pyproject.toml"
    _ = pyproject.write_text(
        data='[tool.charter]\nbackend = "ngrok"\n',
        encoding="utf-8",
    )
    opener: _FakeOpenCharter = _FakeOpenCharter()
    monkeypatch.setattr(target=cli, name="open_charter", value=opener)
    monkeypatch.setattr(
        target=cli,
        name="shorten_url",
        value=_const_short_url,
    )
    _capture_out, _ = _capture(monkeypatch=monkeypatch)
    _ = _capture_out
    expect(
        actual=main(
            arguments=(
                "--pyproject",
                str(object=pyproject),
                "expose",
                "--port",
                "8080",
            ),
        ),
        expected=0,
    )
    settings: object = opener.calls[0]["settings"]
    check(
        condition=isinstance(settings, Settings)
        and settings.backend == "ngrok",
        reason="pyproject backend applied",
    )
