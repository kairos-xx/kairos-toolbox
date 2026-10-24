# Copyright (c) 2026 Project contributors.
"""Structured ``district --test`` self-check emitting a JSON report."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from json import dumps
from pathlib import Path
from sqlite3 import Error as SqliteError
from sys import stderr, stdout, version_info
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Final

from .daemon import DistrictDaemon
from .mcp_registration import McpRegistration
from .product import Product, state_directory
from .runtime import RuntimePlatform
from .storage import DistrictDatabase

if TYPE_CHECKING:
    from .json_config import JsonObject
    from .runtime import Capabilities


@dataclass(frozen=True, slots=True, kw_only=True)
class Check:
    """One self-test outcome.

    Attributes:
        name: Stable machine-readable check name.
        ok: Whether the check passed.
        detail: Human-readable result or failure reason.

    """

    name: str
    ok: bool
    detail: str


class SelfTest:
    """Verify a District Terminal installation end to end.

    Every check is read-only or touches a temporary file, so the report is
    safe to run right after an installer finishes on a bare machine.

    Attributes:
        dependencies: ``(requirement name, importable module)`` pairs the
            install must provide.

    """

    # (requirement name, importable module) pairs.
    dependencies: Final[tuple[tuple[str, str], ...]] = (
        ("bowman", "bowman"),
        ("fastapi", "fastapi"),
        ("psutil", "psutil"),
        ("uvicorn", "uvicorn"),
        ("websockets", "websockets"),
    )

    @classmethod
    def report(cls, *, home: Path) -> JsonObject:
        """Run every check and return the JSON-serializable report.

        Args:
            home: Home directory the checks operate under.

        Returns:
            ``{"ok", "version", "python", "checks", "passed", "failed"}``.

        """
        checks: list[Check] = [
            cls._python(),
            cls._platform(),
            *cls._dependencies(),
            cls._state_writable(home=home),
            cls._database(home=home),
            cls._daemon(home=home),
            cls._mcp(home=home),
        ]
        failed: int = sum(not check.ok for check in checks)
        return {
            "tool": "district",
            "version": Product.version,
            "ok": failed == 0,
            "passed": len(checks) - failed,
            "failed": failed,
            "checks": [
                {
                    "name": check.name,
                    "ok": check.ok,
                    "detail": check.detail,
                }
                for check in checks
            ],
        }

    @classmethod
    def _dependencies(cls) -> list[Check]:
        """Verify every declared dependency is importable.

        Returns:
            One check per dependency module.

        """
        return [
            Check(
                name=f"dependency:{requirement}",
                ok=find_spec(name=module) is not None,
                detail=(
                    "importable"
                    if find_spec(name=module) is not None
                    else f"module {module} is missing"
                ),
            )
            for requirement, module in cls.dependencies
        ]

    @staticmethod
    def _python() -> Check:
        """Report the interpreter version and the required floor.

        Returns:
            Check result.

        """
        version: str = ".".join(str(object=part) for part in version_info[:3])
        return Check(
            name="python_runtime",
            ok=version_info >= (3, 13),
            detail=f"{version} (requires >=3.13)",
        )

    @staticmethod
    def _platform() -> Check:
        """Report OS, architecture and session backend detection.

        Returns:
            Check result.

        """
        runtime: RuntimePlatform = RuntimePlatform.detect()
        caps: Capabilities = runtime.capabilities
        return Check(
            name="runtime_platform",
            ok=True,
            detail=(
                f"{runtime.os} {runtime.architecture} "
                f"backend={runtime.backend} "
                f"persistent_sessions={caps.persistent_sessions}"
            ),
        )

    @staticmethod
    def _state_writable(*, home: Path) -> Check:
        """Verify the state directory can be created and written.

        Args:
            home: Home directory.

        Returns:
            Check result.

        """
        try:
            directory: Path = state_directory(home=home)
            directory.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                dir=directory,
                prefix=".district-test-",
                delete=True,
            ):
                pass
        except OSError as error:
            return Check(
                name="state_writable",
                ok=False,
                detail=str(object=error),
            )
        return Check(
            name="state_writable",
            ok=True,
            detail=str(object=directory),
        )

    @staticmethod
    def _database(*, home: Path) -> Check:
        """Open the database and verify its integrity.

        Args:
            home: Home directory.

        Returns:
            Check result.

        """
        try:
            database: DistrictDatabase = DistrictDatabase(home=home)
            integrity: str = database.integrity()
        except (OSError, RuntimeError, SqliteError) as error:
            return Check(name="database", ok=False, detail=str(object=error))
        ok: bool = integrity == "ok"
        return Check(
            name="database",
            ok=ok,
            detail=(
                f"schema v{database.schema_version()} integrity={integrity}"
            ),
        )

    @staticmethod
    def _daemon(*, home: Path) -> Check:
        """Report whether the session daemon answers.

        Args:
            home: Home directory.

        Returns:
            Check result; a stopped daemon is a note, not a failure.

        """
        running: bool = DistrictDaemon.ping(home=home)
        return Check(
            name="daemon",
            ok=True,
            detail="running" if running else "stopped",
        )

    @staticmethod
    def _mcp(*, home: Path) -> Check:
        """Report whether the Claude MCP registration exists.

        Args:
            home: Home directory.

        Returns:
            Check result; a missing registration is a note, not a failure.

        """
        installed: bool = McpRegistration.installed(home=home)
        return Check(
            name="mcp_registration",
            ok=True,
            detail="registered" if installed else "not registered",
        )


def self_test(*, home: Path | None = None) -> int:
    """Print the self-test report as JSON to stdout.

    Args:
        home: Home directory; the real home when omitted.

    Returns:
        ``0`` when every required check passed, otherwise ``1``.

    """
    report: JsonObject = SelfTest.report(home=home or Path.home())
    text: str = dumps(obj=report, indent=2, sort_keys=True)
    _ = stdout.write(text + "\n")
    _ = stderr.write(
        f"district --test: {report['passed']} passed, "
        f"{report['failed']} failed\n",
    )
    return 0 if report["ok"] else 1
