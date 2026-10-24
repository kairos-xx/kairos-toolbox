# Copyright (c) 2026 Project contributors.
"""Shared installer-payload helpers for every native builder.

Every installer (DEB, APK, PKG and Inno Setup) ships the same payload:
the frozen executable, the platform ``.pyz`` archive, a ``<name>-pyz``
shim, and an *ensure-python* script that guarantees an interpreter new
enough to run the archive — a suitable system Python when present, else a
python-build-standalone (POSIX) or NuGet (Windows) download into the
install directory.  Keeping the text here means the remote pull-workers,
the QEMU guest path and the Docker/local builders all embed identical
logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final


class InstallConstants:
    """Group installer-support constants.

    Attributes:
        want_major: Minimum Python major version the ``.pyz`` needs.
        want_minor: Minimum Python minor version the ``.pyz`` needs.
        pbs_python: python-build-standalone interpreter version fetched
            when no system interpreter qualifies.
        pbs_release: python-build-standalone release tag.
        nuget_python: NuGet ``python``/``pythonarm64`` package version the
            Windows fallback installs.
        interpreter: Shebang baked into ``.pyz`` archives.
        libdir: POSIX library root installed packages are staged under.

    """

    want_major: Final[int] = 3
    want_minor: Final[int] = 13
    pbs_python: Final[str] = "3.13.17"
    pbs_release: Final[str] = "20261021"
    nuget_python: Final[str] = "3.13.9"
    interpreter: Final[str] = "/usr/bin/env python3"
    libdir: Final[str] = "usr/local/lib"


@dataclass(frozen=True, slots=True, kw_only=True)
class PyzPayload:
    """Describe the ``.pyz`` an installer bundles and ships.

    Attributes:
        archive: Release asset name, such as
            ``district-1.0.1-linux-amd64-glibc.pyz``.
        bundled: File name inside the installed application directory,
            such as ``district-1.0.1.pyz``.
        bootstrap: Project-root-relative path of the script that becomes
            the archive's ``__main__.py``.

    """

    archive: str
    bundled: str
    bootstrap: str


def template(*, name: str) -> str:
    """Read a template shipped beside this package.

    Args:
        name: Template file name.

    Returns:
        The template text.

    """
    return (Path(__file__).parent / "templates" / name).read_text(
        encoding="utf-8",
    )


def render(*, source: str, values: dict[str, str]) -> str:
    """Substitute ``@@NAME@@`` placeholders.

    Args:
        source: Template text.
        values: Replacement text per placeholder name.

    Returns:
        The rendered text.

    """
    rendered: str = source
    for name, value in values.items():
        rendered = rendered.replace(f"@@{name}@@", value)
    return rendered


def make_pyz_source() -> str:
    """Return the worker-side ``.pyz`` builder script.

    Returns:
        Rendered ``make_pyz.py`` text.

    """
    return render(
        source=template(name="make_pyz.py.tmpl"),
        values={"INTERPRETER": InstallConstants.interpreter},
    )


def ensure_python_sh() -> str:
    """Return the POSIX ensure-python helper script.

    Returns:
        Rendered ``ensure-python.sh`` text.

    """
    return render(
        source=template(name="ensure-python.sh.tmpl"),
        values={
            "WANT_MAJOR": str(object=InstallConstants.want_major),
            "WANT_MINOR": str(object=InstallConstants.want_minor),
            "PYVER": InstallConstants.pbs_python,
            "TAG": InstallConstants.pbs_release,
        },
    )


def ensure_python_ps1() -> str:
    """Return the Windows ensure-python helper script.

    Returns:
        Rendered ``ensure-python.ps1`` text.

    """
    return render(
        source=template(name="ensure-python.ps1.tmpl"),
        values={
            "WANT_MAJOR": str(object=InstallConstants.want_major),
            "WANT_MINOR": str(object=InstallConstants.want_minor),
            "NUGET": InstallConstants.nuget_python,
        },
    )


def shim_sh(*, name: str, pyz: PyzPayload) -> str:
    """Return the POSIX ``<name>-pyz`` launcher script.

    Args:
        name: Project (executable) name.
        pyz: Bundled ``.pyz`` description.

    Returns:
        Rendered ``district-pyz.sh`` text.

    """
    return render(
        source=template(name="district-pyz.sh.tmpl"),
        values={
            "NAME": name,
            "PYZ": pyz.bundled,
            "LIBDIR": f"{InstallConstants.libdir}/{name}",
            "WANT_MAJOR": str(object=InstallConstants.want_major),
            "WANT_MINOR": str(object=InstallConstants.want_minor),
        },
    )


def shim_cmd(*, name: str, pyz: PyzPayload) -> str:
    """Return the Windows ``<name>-pyz.cmd`` launcher script.

    Args:
        name: Project (executable) name.
        pyz: Bundled ``.pyz`` description.

    Returns:
        Rendered ``district-pyz.cmd`` text.

    """
    return render(
        source=template(name="district-pyz.cmd.tmpl"),
        values={"NAME": name, "PYZ": pyz.bundled},
    )


def deb_postinst(*, name: str) -> str:
    """Return the DEB ``postinst`` maintainer script.

    Args:
        name: Project (package) name.

    Returns:
        Post-install shell script text.

    """
    libdir: str = f"{InstallConstants.libdir}/{name}"
    return (
        "#!/bin/sh\n"
        "set -e\n"
        f"chmod 755 /usr/local/bin/{name} "
        f"/usr/local/bin/{name}-pyz 2>/dev/null || true\n"
        f"/{libdir}/ensure-python.sh /{libdir} || true\n"
        "exit 0\n"
    )


def apk_post_install(*, name: str) -> str:
    """Return the APK ``.post-install`` script.

    Args:
        name: Project (package) name.

    Returns:
        Post-install shell script text.

    """
    libdir: str = f"{InstallConstants.libdir}/{name}"
    return (
        "#!/bin/sh\n"
        "apk add --no-cache python3 curl ca-certificates "
        ">/dev/null 2>&1 || true\n"
        f"/{libdir}/ensure-python.sh /{libdir} || true\n"
        "exit 0\n"
    )


def pkg_postinstall(*, name: str) -> str:
    """Return the PKG ``postinstall`` script.

    Args:
        name: Project (package) name.

    Returns:
        Post-install shell script text.

    """
    libdir: str = f"{InstallConstants.libdir}/{name}"
    return f"#!/bin/sh\n/{libdir}/ensure-python.sh /{libdir} || true\nexit 0\n"


def pyz_payload(
    *,
    pyz_name: str,
    version: str,
    tag: str,
    bootstrap: str,
) -> PyzPayload:
    """Describe the ``.pyz`` for one artifact cell.

    Args:
        pyz_name: ``[pyz] name`` prefix.
        version: Project version.
        tag: Release platform tag (``windows-amd64`` etc.).
        bootstrap: Project-root-relative bootstrap path.

    Returns:
        The payload description.

    """
    return PyzPayload(
        archive=f"{pyz_name}-{version}-{tag}.pyz",
        bundled=f"{pyz_name}-{version}.pyz",
        bootstrap=bootstrap,
    )
