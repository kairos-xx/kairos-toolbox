# Copyright (c) 2026 Project contributors.
"""Tests for layered charter settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tools.charter.settings import Settings, SettingsLoader

from .helpers import expect

if TYPE_CHECKING:
    from pathlib import Path


def _pyproject(*, tmp_path: Path, body: str) -> Path:
    """Write a ``pyproject.toml`` and return its path.

    Args:
        tmp_path: Pytest temporary directory.
        body: File contents.

    Returns:
        The written file.

    """
    path: Path = tmp_path / "pyproject.toml"
    _ = path.write_text(data=body, encoding="utf-8")
    return path


def test_defaults_apply_without_configuration(tmp_path: Path) -> None:
    """Absent ``tool.charter`` tables leave every default in place.

    Args:
        tmp_path: Pytest temporary directory.

    """
    pyproject: Path = _pyproject(
        tmp_path=tmp_path,
        body="[project]\nname = 'demo'\n",
    )
    settings: Settings = SettingsLoader.load(pyproject=pyproject)
    expect(actual=settings, expected=Settings())


def test_the_charter_table_configures_backends(tmp_path: Path) -> None:
    """``[tool.charter]`` keys become typed settings fields.

    Args:
        tmp_path: Pytest temporary directory.

    """
    pyproject: Path = _pyproject(
        tmp_path=tmp_path,
        body=(
            "[tool.charter]\n"
            'backend = "pinggy"\n'
            'ngrok_authtoken = "tok"\n'
            'ngrok_executable = "/opt/ngrok"\n'
            'ssh_executable = "/usr/bin/ssh"\n'
            'expect_executable = "expect5"\n'
            'npx_executable = "npx19"\n'
            "url_timeout_seconds = 7\n"
            "ngrok_timeout_seconds = 3.5\n"
            "stop_timeout_seconds = 1\n"
            'shortener = "https://s.example/api"\n'
            "json_output = true\n"
        ),
    )
    settings: Settings = SettingsLoader.load(pyproject=pyproject)
    expect(actual=settings.backend, expected="pinggy")
    expect(actual=settings.ngrok_authtoken, expected="tok")
    expect(actual=settings.ngrok_executable, expected="/opt/ngrok")
    expect(actual=settings.ssh_executable, expected="/usr/bin/ssh")
    expect(actual=settings.expect_executable, expected="expect5")
    expect(actual=settings.npx_executable, expected="npx19")
    expect(actual=settings.url_timeout_seconds, expected=7.0)
    expect(actual=settings.ngrok_timeout_seconds, expected=3.5)
    expect(actual=settings.stop_timeout_seconds, expected=1.0)
    expect(actual=settings.shortener, expected="https://s.example/api")
    expect(actual=settings.json_output, expected=True)


def test_overrides_win_over_the_project_file(tmp_path: Path) -> None:
    """Explicit overrides replace configured values; ``None`` is skipped.

    Args:
        tmp_path: Pytest temporary directory.

    """
    pyproject: Path = _pyproject(
        tmp_path=tmp_path,
        body='[tool.charter]\nbackend = "ngrok"\nshortener = "https://a"\n',
    )
    settings: Settings = SettingsLoader.load(
        pyproject=pyproject,
        overrides={"backend": "tunnl", "shortener": None},
    )
    expect(actual=settings.backend, expected="tunnl")
    expect(actual=settings.shortener, expected="https://a")


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    """Typos in the project file or overrides raise ``ValueError``.

    Args:
        tmp_path: Pytest temporary directory.

    """
    pyproject: Path = _pyproject(
        tmp_path=tmp_path,
        body='[tool.charter]\nbackend = "ngrok"\nbogus = 1\n',
    )
    with pytest.raises(
        expected_exception=ValueError,
        match="Unknown charter setting: bogus",
    ):
        _ = SettingsLoader.load(pyproject=pyproject)
    with pytest.raises(
        expected_exception=ValueError,
        match="Unknown charter setting: nope",
    ):
        _ = SettingsLoader.load(
            pyproject=pyproject.parent / "absent.toml",
            overrides={"nope": 1},
        )


def test_the_table_must_be_a_table(tmp_path: Path) -> None:
    """A non-table ``tool.charter`` raises ``TypeError``.

    Args:
        tmp_path: Pytest temporary directory.

    """
    pyproject: Path = _pyproject(
        tmp_path=tmp_path,
        body='tool.charter = "x"\n',
    )
    with pytest.raises(
        expected_exception=TypeError,
        match=r"tool\.charter must be a table",
    ):
        _ = SettingsLoader.load(pyproject=pyproject)


def test_an_unknown_backend_is_rejected(tmp_path: Path) -> None:
    """``backend`` must name a real backend.

    Args:
        tmp_path: Pytest temporary directory.

    """
    pyproject: Path = _pyproject(
        tmp_path=tmp_path,
        body='[tool.charter]\nbackend = "carrier-pigeon"\n',
    )
    with pytest.raises(
        expected_exception=ValueError,
        match="backend must be one of",
    ):
        _ = SettingsLoader.load(pyproject=pyproject)


@pytest.mark.parametrize(
    "body",
    [
        "[tool.charter]\njson_output = 'yes'\n",
        "[tool.charter]\nbackend = 1\n",
        "[tool.charter]\nngrok_authtoken = 1\n",
        "[tool.charter]\nurl_timeout_seconds = 'x'\n",
    ],
)
def test_mistyped_values_are_rejected(tmp_path: Path, body: str) -> None:
    """Wrongly typed values raise ``TypeError``.

    Args:
        tmp_path: Pytest temporary directory.
        body: Project file contents.

    """
    pyproject: Path = _pyproject(tmp_path=tmp_path, body=body)
    with pytest.raises(expected_exception=TypeError, match="must be"):
        _ = SettingsLoader.load(pyproject=pyproject)


def test_discovery_walks_upwards(tmp_path: Path) -> None:
    """The nearest ``pyproject.toml`` at or above the start wins.

    Args:
        tmp_path: Pytest temporary directory.

    """
    pyproject: Path = _pyproject(
        tmp_path=tmp_path,
        body='[tool.charter]\nbackend = "runlocal"\n',
    )
    nested: Path = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    discovered: Path | None = SettingsLoader.discover_pyproject(start=nested)
    expect(actual=discovered, expected=pyproject)
    expect(
        actual=SettingsLoader.load().backend,
        expected="direct",
    )
    file_start: Path = nested / "notes.txt"
    expect(
        actual=SettingsLoader.discover_pyproject(start=file_start),
        expected=pyproject,
    )


def test_this_repository_configures_the_default_backend() -> None:
    """The workspace ``pyproject.toml`` resolves through plain discovery."""
    settings: Settings = SettingsLoader.load()
    expect(actual=settings.backend, expected="direct")
