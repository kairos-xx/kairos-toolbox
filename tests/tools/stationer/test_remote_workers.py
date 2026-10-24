# Copyright (c) 2026 Project contributors.
"""Tests for the rendered remote build workers."""

from __future__ import annotations

from base64 import b64decode
from json import loads
from re import Match, search
from shutil import which
from subprocess import run
from typing import TYPE_CHECKING, Final, final

import pytest

from tools.stationer.conjure import (
    Credentials,
    Payload,
    RemoteSpec,
    RemoteStep,
)
from tools.stationer.remote_workers import (
    WorkerConstants,
    posix_worker,
    windows_worker,
)

from .helpers import check, expect, present

if TYPE_CHECKING:
    from pathlib import Path


@final
class Constants:
    """Group module-level constants.

    Attributes:
        spec: Grouped module constant.
        credentials: Grouped module constant.

    """

    spec: Final[RemoteSpec] = RemoteSpec(
        steps=(
            RemoteStep(
                message="make venv",
                argv=("{python}", "-m", "venv", "{venv}"),
            ),
            RemoteStep(message="build", argv=("{venv_python}", "-c", "pass")),
        ),
    )
    credentials: Final[Credentials] = Credentials.create()


def _payload(*, tmp_path: Path) -> Payload:
    """Create a payload descriptor for a small archive.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        The payload.

    """
    archive: Path = tmp_path / "p.zip"
    archive.write_bytes(data=b"zip")
    return Payload.of(path=archive)


def _spec_from(*, script: str) -> dict[str, object]:
    """Decode the base64 spec embedded in a rendered worker.

    Args:
        script: Rendered worker text.

    Returns:
        The decoded spec document.

    """
    found: Match[str] = present(
        value=search(pattern="FromBase64String\\('([^']+)'", string=script),
    )
    return loads(s=b64decode(s=found.group(1)))


def test_windows_worker_embeds_job_data_and_no_placeholders(
    tmp_path: Path,
) -> None:
    """The PowerShell worker carries the token, digest and spec.

    Args:
        tmp_path: Pytest temporary directory.

    """
    payload: Payload = _payload(tmp_path=tmp_path)
    script: str = windows_worker(
        base_url="http://10.0.2.2:8765/",
        credentials=Constants.credentials,
        payload=payload,
        spec=Constants.spec,
    )
    check(condition="@@" not in script, reason="unfilled placeholder")
    check(condition="'http://10.0.2.2:8765'" in script, reason="base url")
    check(
        condition=f"'{Constants.credentials.token}'" in script,
        reason="token",
    )
    check(condition=f"'{payload.sha256}'" in script, reason="payload digest")
    check(
        condition=Constants.credentials.bootstrap not in script,
        reason="the bootstrap path secret must not be inside the script",
    )
    for needle in ("/v1/payload", "/v1/progress", "/v1/error", "/v1/result"):
        check(condition=needle in script, reason=needle)
    for needle in (
        "Get-NativeArchitecture",
        "Get-TargetArchitecture",
        "$Spec.architecture",
    ):
        check(condition=needle in script, reason=needle)
    expect(actual=_spec_from(script=script)["architecture"], expected="native")
    expect(
        actual=_spec_from(script=script)["steps"],
        expected=loads(s=Constants.spec.json())["steps"],
    )


def test_powershell_quoting_survives_apostrophes(tmp_path: Path) -> None:
    """A quote in a value cannot terminate the PowerShell literal.

    Args:
        tmp_path: Pytest temporary directory.

    """
    script: str = windows_worker(
        base_url="http://host/it's",
        credentials=Constants.credentials,
        payload=_payload(tmp_path=tmp_path),
        spec=Constants.spec,
    )
    check(condition="'http://host/it''s'" in script, reason=script[:400])


def test_posix_worker_is_valid_bash_and_python(tmp_path: Path) -> None:
    """The Bash wrapper parses and the embedded program compiles.

    Args:
        tmp_path: Pytest temporary directory.

    """
    script: str = posix_worker(
        base_url="http://127.0.0.1:1/",
        credentials=Constants.credentials,
        payload=_payload(tmp_path=tmp_path),
        spec=Constants.spec,
        os_name="linux",
    )
    check(condition="@@" not in script, reason="unfilled placeholder")
    check(condition=script.startswith("#!/bin/bash\n"), reason=script[:30])
    check(
        condition=Constants.credentials.bootstrap not in script,
        reason="the bootstrap path secret must not be inside the script",
    )
    opener, closer = WorkerConstants.posix_markers
    program: str = script.split(sep=opener + "\n", maxsplit=1)[1].rsplit(
        sep="\n" + closer,
        maxsplit=1,
    )[0]
    _ = compile(source=program, filename="worker", mode="exec")
    bash: str | None = which(cmd="bash")
    if bash is not None:
        written: Path = tmp_path / "worker.sh"
        written.write_text(data=script, encoding="utf-8")
        expect(
            actual=run(
                args=(bash, "-n", str(object=written)),
                check=False,
            ).returncode,
            expected=0,
        )


@pytest.mark.parametrize("os_name", ["linux", "macos"])
def test_posix_worker_reports_its_os(tmp_path: Path, os_name: str) -> None:
    """The reported OS comes from the host's choice of endpoint.

    Args:
        tmp_path: Pytest temporary directory.
        os_name: Operating system under test.

    """
    script: str = posix_worker(
        base_url="http://h",
        credentials=Constants.credentials,
        payload=_payload(tmp_path=tmp_path),
        spec=Constants.spec,
        os_name=os_name,
    )
    check(condition=f'OS_NAME = "{os_name}"' in script, reason=script[-300:])
