# Copyright (c) 2026 Project contributors.
"""Tests for the installer payload helpers shared by every builder."""

from __future__ import annotations

from subprocess import PIPE, CompletedProcess, run
from typing import TYPE_CHECKING, Final, final

import pytest

from tools.stationer import installer_support
from tools.stationer.installer_support import (
    InstallConstants,
    PyzPayload,
    apk_post_install,
    deb_postinst,
    ensure_python_ps1,
    ensure_python_sh,
    make_pyz_source,
    pkg_postinstall,
    pyz_payload,
    render,
    shim_cmd,
    shim_sh,
    template,
)

from .helpers import check, expect

if TYPE_CHECKING:
    from pathlib import Path


@final
class Constants:
    """Group test constants.

    Attributes:
        payload: A bundled ``.pyz`` description used by the shim tests.
        libdir: Installed library directory of the ``demo`` package.

    """

    payload: Final[PyzPayload] = PyzPayload(
        archive="demo-1.2.3-linux-amd64-glibc.pyz",
        bundled="demo-1.2.3.pyz",
        bootstrap="src/demo/__main__.py",
    )
    libdir: Final[str] = "/usr/local/lib/demo"


def _syntax_error(*, script: str, tmp_path: Path) -> str:
    """Return ``sh -n``'s complaint about a script, empty when it parses.

    Args:
        script: Shell script text.
        tmp_path: Directory that receives the script file.

    Returns:
        The diagnostic text, or an empty string for valid syntax.

    """
    target: Path = tmp_path / "script.sh"
    target.write_text(data=script, encoding="utf-8")
    finished: CompletedProcess[str] = run(
        args=("sh", "-n", str(object=target)),
        check=False,
        stderr=PIPE,
        text=True,
    )
    return finished.stderr if finished.returncode else ""


def test_render_replaces_every_occurrence_of_each_placeholder() -> None:
    """Placeholders are substituted everywhere; other text is untouched."""
    rendered: str = render(
        source="@@A@@ and @@A@@ then @@B@@ but not @@C@@ or {D}",
        values={"A": "1", "B": "two"},
    )
    expect(actual=rendered, expected="1 and 1 then two but not @@C@@ or {D}")


def test_template_reads_a_shipped_file() -> None:
    """A template that ships beside the package can be read by name."""
    check(
        condition="@@NAME@@" in template(name="district-pyz.cmd.tmpl"),
        reason="the cmd shim keeps its placeholders until rendered",
    )
    with pytest.raises(expected_exception=FileNotFoundError):
        _ = template(name="there-is-no-such-template")


def test_the_pyz_builder_script_embeds_the_interpreter() -> None:
    """The worker-side builder gets the shebang every archive carries."""
    source: str = make_pyz_source()
    check(condition="@@" not in source, reason="unrendered placeholder")
    check(
        condition=InstallConstants.interpreter in source,
        reason="the archive shebang must be embedded",
    )


def test_ensure_python_scripts_pin_the_interpreter_floor(
    tmp_path: Path,
) -> None:
    """Both ensure-python scripts render the version floor and a download.

    Args:
        tmp_path: Pytest temporary directory.

    """
    posix: str = ensure_python_sh()
    windows: str = ensure_python_ps1()
    for text in (posix, windows):
        check(condition="@@" not in text, reason="unrendered placeholder")
        check(
            condition=str(object=InstallConstants.want_minor) in text,
            reason="the minor version floor must be present",
        )
    check(condition=InstallConstants.pbs_python in posix, reason=posix[:300])
    check(condition=InstallConstants.pbs_release in posix, reason=posix[:300])
    check(condition=InstallConstants.nuget_python in windows, reason="nuget")
    expect(actual=_syntax_error(script=posix, tmp_path=tmp_path), expected="")


def test_the_posix_shim_launches_the_bundled_archive(tmp_path: Path) -> None:
    """The ``<name>-pyz`` shim names the archive and its install directory.

    Args:
        tmp_path: Pytest temporary directory.

    """
    shim: str = shim_sh(name="demo", pyz=Constants.payload)
    check(condition="@@" not in shim, reason="unrendered placeholder")
    check(condition=Constants.payload.bundled in shim, reason=shim)
    check(condition=Constants.libdir.removeprefix("/") in shim, reason=shim)
    expect(actual=_syntax_error(script=shim, tmp_path=tmp_path), expected="")


def test_the_windows_shim_launches_the_bundled_archive() -> None:
    """The ``<name>-pyz.cmd`` shim names the project and its archive."""
    shim: str = shim_cmd(name="demo", pyz=Constants.payload)
    check(condition="@@" not in shim, reason="unrendered placeholder")
    check(condition=Constants.payload.bundled in shim, reason=shim)
    check(condition="demo" in shim, reason=shim)


def test_post_install_scripts_run_ensure_python_and_never_fail(
    tmp_path: Path,
) -> None:
    """DEB, APK and PKG hooks call the helper and always exit cleanly.

    Args:
        tmp_path: Pytest temporary directory.

    """
    scripts: dict[str, str] = {
        "deb": deb_postinst(name="demo"),
        "apk": apk_post_install(name="demo"),
        "pkg": pkg_postinstall(name="demo"),
    }
    for kind, script in scripts.items():
        check(condition=script.startswith("#!/bin/sh\n"), reason=kind)
        check(
            condition=f"{Constants.libdir}/ensure-python.sh" in script,
            reason=f"{kind}: {script}",
        )
        check(condition=script.endswith("exit 0\n"), reason=kind)
        expect(
            actual=_syntax_error(script=script, tmp_path=tmp_path),
            expected="",
        )
    check(
        condition="chmod 755" in scripts["deb"]
        and "/usr/local/bin/demo-pyz" in scripts["deb"],
        reason="the DEB hook makes the shim executable",
    )
    check(
        condition="apk add --no-cache python3" in scripts["apk"],
        reason="the APK hook installs a system interpreter first",
    )


def test_pyz_payload_names_the_archive_and_the_bundled_copy() -> None:
    """One cell's release asset and installed file names derive from it."""
    payload: PyzPayload = pyz_payload(
        pyz_name="demo",
        version="1.2.3",
        tag="windows-arm64",
        bootstrap="boot.py",
    )
    expect(actual=payload.archive, expected="demo-1.2.3-windows-arm64.pyz")
    expect(actual=payload.bundled, expected="demo-1.2.3.pyz")
    expect(actual=payload.bootstrap, expected="boot.py")


def test_module_exposes_its_public_helpers() -> None:
    """The names the remote builder imports stay importable."""
    for name in (
        "apk_post_install",
        "deb_postinst",
        "ensure_python_ps1",
        "ensure_python_sh",
        "make_pyz_source",
        "pkg_postinstall",
        "pyz_payload",
        "shim_cmd",
        "shim_sh",
    ):
        check(
            condition=callable(getattr(installer_support, name, None)),
            reason=name,
        )
