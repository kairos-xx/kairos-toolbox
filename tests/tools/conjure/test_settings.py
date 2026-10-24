"""Tests for layered conjure settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.conjure.settings import Constants, Settings, SettingsLoader

from .helpers import expect


def test_defaults_are_used_without_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fall back to defaults without a project file.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.chdir(path=tmp_path)
    settings: Settings = SettingsLoader.load()
    expect(actual=settings.ssh_host, expected="127.0.0.1")
    expect(actual=settings.docker_stop_timeout_seconds, expected=10)


def test_pyproject_overrides_defaults(tmp_path: Path) -> None:
    """Project settings replace defaults.

    Args:
        tmp_path: Pytest temporary directory.

    """
    project: Path = tmp_path / "pyproject.toml"
    project.write_text(
        data="[tool.conjure.docker]\nimage='python:3.13'\n"
        "stop_timeout_seconds=30\n"
        "[tool.conjure.ssh]\nhost='localhost'\n"
        "scp_executable='custom-scp'\n",
        encoding="utf-8",
    )
    settings: Settings = SettingsLoader.load(pyproject=project)
    expect(actual=settings.docker_image, expected="python:3.13")
    expect(actual=settings.docker_stop_timeout_seconds, expected=30)
    expect(actual=settings.ssh_host, expected="localhost")
    expect(
        actual=settings.scp_executable,
        expected=Path("custom-scp"),
    )


def test_cli_overrides_pyproject(tmp_path: Path) -> None:
    """CLI overrides replace project values.

    Args:
        tmp_path: Pytest temporary directory.

    """
    project: Path = tmp_path / "pyproject.toml"
    project.write_text(
        data="[tool.conjure.ssh]\nport=2200\n",
        encoding="utf-8",
    )
    settings: Settings = SettingsLoader.load(
        pyproject=project,
        overrides={"ssh_port": 3300},
    )
    expect(actual=settings.ssh_port, expected=3300)


def test_none_cli_override_does_not_replace_project(
    tmp_path: Path,
) -> None:
    """An omitted CLI option never overwrites a project value.

    Args:
        tmp_path: Pytest temporary directory.

    """
    project: Path = tmp_path / "pyproject.toml"
    project.write_text(
        data="[tool.conjure.ssh]\nhost='example'\n",
        encoding="utf-8",
    )
    settings: Settings = SettingsLoader.load(
        pyproject=project,
        overrides={"ssh_host": None},
    )
    expect(actual=settings.ssh_host, expected="example")


def test_user_settings_apply_without_project(tmp_path: Path) -> None:
    """The user-level file supplies values below project settings.

    Args:
        tmp_path: Pytest temporary directory.

    """
    user: Path = tmp_path / "settings.toml"
    user.write_text(
        data="[tool.conjure.qemu]\nsnapshot_image='/disk.qcow2'\n"
        "baseline_snapshot='baseline'\n[tool.conjure.ssh]\nhost='vm'\n",
        encoding="utf-8",
    )
    settings: Settings = SettingsLoader.load(
        pyproject=tmp_path / "pyproject.toml",
        user_settings=user,
    )
    expect(
        actual=settings.qemu_snapshot_image,
        expected=Path("/disk.qcow2"),
    )
    expect(actual=settings.qemu_baseline_snapshot, expected="baseline")
    expect(actual=settings.ssh_host, expected="vm")


def test_pyproject_overrides_user_settings(tmp_path: Path) -> None:
    """Project values replace user-level values.

    Args:
        tmp_path: Pytest temporary directory.

    """
    user: Path = tmp_path / "settings.toml"
    user.write_text(
        data="[tool.conjure.ssh]\nhost='user-host'\nport=2200\n",
        encoding="utf-8",
    )
    project: Path = tmp_path / "pyproject.toml"
    project.write_text(
        data="[tool.conjure.ssh]\nhost='project-host'\n",
        encoding="utf-8",
    )
    settings: Settings = SettingsLoader.load(
        pyproject=project,
        user_settings=user,
    )
    expect(actual=settings.ssh_host, expected="project-host")
    expect(actual=settings.ssh_port, expected=2200)


def test_cli_overrides_user_settings(tmp_path: Path) -> None:
    """Explicit overrides replace user-level values.

    Args:
        tmp_path: Pytest temporary directory.

    """
    user: Path = tmp_path / "settings.toml"
    user.write_text(
        data="[tool.conjure.ssh]\nhost='user-host'\n",
        encoding="utf-8",
    )
    settings: Settings = SettingsLoader.load(
        user_settings=user,
        overrides={"ssh_host": "cli-host"},
    )
    expect(actual=settings.ssh_host, expected="cli-host")


def test_default_user_settings_path_is_used(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An omitted user file falls back to ``~/.conjure/settings.toml``.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    user: Path = tmp_path / "settings.toml"
    user.write_text(
        data="[tool.conjure.ssh]\nhost='default-user'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        target=Constants,
        name="default_user_settings",
        value=user,
    )
    monkeypatch.chdir(path=tmp_path)
    settings: Settings = SettingsLoader.load()
    expect(actual=settings.ssh_host, expected="default-user")


def test_unknown_setting_is_rejected(tmp_path: Path) -> None:
    """Unknown conjure keys are rejected.

    Args:
        tmp_path: Pytest temporary directory.

    """
    project: Path = tmp_path / "pyproject.toml"
    project.write_text(
        data="[tool.conjure.qemu]\nwrong=true\n",
        encoding="utf-8",
    )
    with pytest.raises(
        expected_exception=ValueError,
        match="Unknown conjure setting",
    ):
        SettingsLoader.load(pyproject=project)


def test_discovers_nearest_pyproject(tmp_path: Path) -> None:
    """Search the current directory and each parent.

    Args:
        tmp_path: Pytest temporary directory.

    """
    project: Path = tmp_path / "pyproject.toml"
    project.write_text(
        data="[tool.conjure.general]\njson_output=true\n",
        encoding="utf-8",
    )
    child: Path = tmp_path / "a" / "b"
    child.mkdir(parents=True)
    expect(
        actual=SettingsLoader.discover_pyproject(start=child),
        expected=project,
    )


def test_discovery_accepts_a_file_start(tmp_path: Path) -> None:
    """Discovery also resolves from an explicit file path.

    Args:
        tmp_path: Pytest temporary directory.

    """
    project: Path = tmp_path / "pyproject.toml"
    project.write_text(data="[project]\nname='x'\n", encoding="utf-8")
    nested: Path = tmp_path / "leaf.py"
    nested.write_text(data="", encoding="utf-8")
    expect(
        actual=SettingsLoader.discover_pyproject(start=nested),
        expected=project,
    )


def test_project_without_tool_table(tmp_path: Path) -> None:
    """A project lacking ``tool.conjure`` contributes defaults.

    Args:
        tmp_path: Pytest temporary directory.

    """
    project: Path = tmp_path / "pyproject.toml"
    project.write_text(
        data="[project]\nname='x'\n[tool.other]\nvalue=1\n",
        encoding="utf-8",
    )
    settings: Settings = SettingsLoader.load(pyproject=project)
    expect(actual=settings.ssh_host, expected="127.0.0.1")


def test_conjure_must_be_a_table(tmp_path: Path) -> None:
    """A scalar ``tool.conjure`` is rejected.

    Args:
        tmp_path: Pytest temporary directory.

    """
    project: Path = tmp_path / "pyproject.toml"
    project.write_text(
        data="[tool]\nconjure='x'\n",
        encoding="utf-8",
    )
    with pytest.raises(
        expected_exception=TypeError,
        match="must be a table",
    ):
        SettingsLoader.load(pyproject=project)


def test_unknown_override_is_rejected() -> None:
    """Unknown explicit overrides are rejected."""
    with pytest.raises(
        expected_exception=ValueError,
        match="Unknown conjure setting",
    ):
        SettingsLoader.load(overrides={"bogus": 1})


def test_non_table_section_is_rejected(tmp_path: Path) -> None:
    """A scalar ``conjure`` section is rejected.

    Args:
        tmp_path: Pytest temporary directory.

    """
    project: Path = tmp_path / "pyproject.toml"
    project.write_text(
        data="[tool.conjure]\ngeneral='x'\n",
        encoding="utf-8",
    )
    with pytest.raises(
        expected_exception=ValueError,
        match="Unknown conjure setting",
    ):
        SettingsLoader.load(pyproject=project)


def test_boolean_field_must_be_boolean() -> None:
    """Non-boolean values are rejected for boolean fields."""
    with pytest.raises(
        expected_exception=TypeError,
        match="must be a boolean",
    ):
        SettingsLoader.load(overrides={"json_output": "yes"})


def test_integer_field_must_be_integer() -> None:
    """Non-integer values are rejected for integer fields."""
    with pytest.raises(
        expected_exception=TypeError,
        match="must be an integer",
    ):
        SettingsLoader.load(
            overrides={"docker_stop_timeout_seconds": "x"},
        )


def test_optional_integer_field_must_be_integer() -> None:
    """Non-integer values are rejected for optional integers."""
    with pytest.raises(
        expected_exception=TypeError,
        match="must be an integer or null",
    ):
        SettingsLoader.load(overrides={"ssh_port": "x"})


def test_numeric_field_must_be_numeric() -> None:
    """Non-numeric values are rejected for float fields."""
    with pytest.raises(
        expected_exception=TypeError,
        match="must be numeric",
    ):
        SettingsLoader.load(
            overrides={"qemu_startup_timeout_seconds": "x"},
        )


def test_string_field_must_be_string() -> None:
    """Non-string values are rejected for string fields."""
    with pytest.raises(
        expected_exception=TypeError,
        match="must be a string",
    ):
        SettingsLoader.load(overrides={"qemu_network_device": 1})


def test_optional_string_field_must_be_string() -> None:
    """Non-string values are rejected for optional strings."""
    with pytest.raises(
        expected_exception=TypeError,
        match="must be a string or null",
    ):
        SettingsLoader.load(overrides={"docker_image": 1})


def test_path_field_accepts_strings_and_paths() -> None:
    """Path fields coerce strings and preserve paths."""
    settings: Settings = SettingsLoader.load(
        overrides={
            "registry_path": Path("registry.json"),
            "docker_executable": "podman",
            "local_working_directory": "work",
        },
    )
    expect(
        actual=settings.registry_path,
        expected=Path("registry.json"),
    )
    expect(
        actual=settings.docker_executable,
        expected=Path("podman"),
    )
    expect(
        actual=settings.local_working_directory,
        expected=Path("work"),
    )


def test_path_field_must_be_string_or_path() -> None:
    """Non-string values are rejected for path fields."""
    with pytest.raises(
        expected_exception=TypeError,
        match="must be a string or path",
    ):
        SettingsLoader.load(overrides={"registry_path": 1})
    with pytest.raises(
        expected_exception=TypeError,
        match="must be a string, path, or null",
    ):
        SettingsLoader.load(overrides={"local_working_directory": 1})


def test_string_sequences_must_be_lists_of_strings() -> None:
    """Sequence fields require homogeneous string entries."""
    settings: Settings = SettingsLoader.load(
        overrides={
            "docker_command": ("sleep", "infinity"),
            "guest_shutdown_command": ["poweroff"],
        },
    )
    expect(
        actual=settings.docker_command,
        expected=("sleep", "infinity"),
    )
    expect(
        actual=settings.guest_shutdown_command,
        expected=("poweroff",),
    )
    with pytest.raises(
        expected_exception=TypeError,
        match="must be a string list",
    ):
        SettingsLoader.load(overrides={"docker_command": "x"})
    with pytest.raises(
        expected_exception=TypeError,
        match="must be a string list",
    ):
        SettingsLoader.load(overrides={"docker_command": [1]})
    with pytest.raises(
        expected_exception=TypeError,
        match="must be a string list or null",
    ):
        SettingsLoader.load(overrides={"guest_shutdown_command": 1})


def test_path_sequences_accept_strings_and_paths() -> None:
    """Path-sequence fields coerce each entry."""
    settings: Settings = SettingsLoader.load(
        overrides={"qemu_image_roots": ["a", Path("b")]},
    )
    expect(
        actual=settings.qemu_image_roots,
        expected=(Path("a"), Path("b")),
    )
    with pytest.raises(
        expected_exception=TypeError,
        match="must be a string list",
    ):
        SettingsLoader.load(overrides={"qemu_image_roots": "x"})
    with pytest.raises(
        expected_exception=TypeError,
        match="entries must be strings or paths",
    ):
        SettingsLoader.load(overrides={"qemu_image_roots": [1]})
