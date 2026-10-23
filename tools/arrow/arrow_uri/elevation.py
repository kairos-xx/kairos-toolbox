"""Explicit OS-native privilege elevation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from os import name
from platform import system
from secrets import token_urlsafe
from shutil import which
from subprocess import CompletedProcess, run
from typing import TYPE_CHECKING, Final, final

from .errors import ElevationError

if TYPE_CHECKING:
    from pathlib import Path


@final
class Constants:
    """Group module-level constants.

    Attributes:
        default_grant_ttl: Grouped module constant.

    """

    default_grant_ttl: Final[timedelta] = timedelta(minutes=5)


@dataclass(frozen=True, slots=True, kw_only=True)
class ElevationGrant:
    """Short-lived authorization to request an elevated operation.

    Attributes:
        grant_id: Unique grant identifier.
        issued_at: Issue timestamp.
        expires_at: Expiry timestamp.
        capabilities: Authorized capability names.
        reason: Human-readable reason shown to the user.

    """

    grant_id: str
    issued_at: datetime
    expires_at: datetime
    capabilities: frozenset[str]
    reason: str

    @property
    def valid(self) -> bool:
        """Return whether the grant is still within its validity window.

        Returns:
            Whether the current time is inside the grant window.

        """
        now: datetime = datetime.now(tz=UTC)
        return self.issued_at <= now < self.expires_at


class PrivilegeBroker:
    """Invoke a narrowly defined command through official OS consent UI.

    The broker never accepts or stores passwords. The operating system owns
    credential collection and user consent.
    """

    @classmethod
    def execute(
        cls,
        *,
        executable: Path,
        arguments: tuple[str, ...],
        grant: ElevationGrant,
        required_capability: str,
    ) -> int:
        """Request OS elevation and execute only the supplied argv.

        Args:
            executable: Registered helper executable.
            arguments: Fixed positional arguments.
            grant: Live elevation grant.
            required_capability: Capability the grant must authorize.

        Returns:
            The helper process exit code.

        Raises:
            ElevationError: When the grant is expired, lacks the capability,
                or the platform is unsupported.

        """
        if not grant.valid:
            msg: str = "Elevation grant expired."
            raise ElevationError(msg)
        if required_capability not in grant.capabilities:
            msg = "Elevation grant does not authorize this capability."
            raise ElevationError(
                msg,
            )
        platform_name: str = system()
        if platform_name == "Linux":
            return cls.linux(
                executable=executable,
                arguments=arguments,
            )
        if platform_name == "Darwin":
            return cls.macos(
                executable=executable,
                arguments=arguments,
            )
        if name == "nt":
            return cls.windows(
                executable=executable,
                arguments=arguments,
            )
        msg = f"Unsupported operating system: {platform_name}."
        raise ElevationError(msg)

    @classmethod
    def macos(cls, *, executable: Path, arguments: tuple[str, ...]) -> int:
        """Use macOS's system-owned administrator authentication dialog.

        Args:
            executable: Helper executable.
            arguments: Fixed positional arguments.

        Returns:
            The helper process exit code.

        Raises:
            ElevationError: When osascript is unavailable.

        """
        osascript: str | None = which(cmd="osascript")
        if osascript is None:
            msg: str = "osascript is not available."
            raise ElevationError(msg)
        command: str = cls.shell_command(
            executable=executable,
            arguments=arguments,
        )
        script: str = (
            "do shell script "
            + cls.apple_string(value=command)
            + " with administrator privileges"
        )
        completed: CompletedProcess[bytes] = run(
            args=(osascript, "-e", script),
            check=False,
        )
        return completed.returncode

    @classmethod
    def windows(cls, *, executable: Path, arguments: tuple[str, ...]) -> int:
        """Use PowerShell Start-Process -Verb RunAs for native UAC.

        Args:
            executable: Helper executable.
            arguments: Fixed positional arguments.

        Returns:
            The helper process exit code.

        """
        parameters: str = ",".join(
            cls.powershell_literal(value=value) for value in arguments
        )
        script: str = (
            "$process = Start-Process -FilePath "
            + cls.powershell_literal(value=str(object=executable))
            + " -ArgumentList @("
            + parameters
            + ") -Verb RunAs -Wait "
            + "-PassThru; exit $process.ExitCode"
        )
        completed: CompletedProcess[bytes] = run(
            args=(
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ),
            check=False,
        )
        return completed.returncode

    @staticmethod
    def grant(
        *,
        capabilities: frozenset[str],
        reason: str,
        ttl: timedelta = Constants.default_grant_ttl,
    ) -> ElevationGrant:
        """Create a short-lived pre-elevation grant.

        Args:
            capabilities: Capabilities the grant authorizes.
            reason: Human-readable reason shown to the user.
            ttl: Grant lifetime.

        Returns:
            The short-lived elevation grant.

        """
        now: datetime = datetime.now(tz=UTC)
        return ElevationGrant(
            grant_id=token_urlsafe(nbytes=24),
            issued_at=now,
            expires_at=now + ttl,
            capabilities=capabilities,
            reason=reason,
        )

    @staticmethod
    def linux(*, executable: Path, arguments: tuple[str, ...]) -> int:
        """Use polkit's pkexec authentication agent.

        Args:
            executable: Helper executable.
            arguments: Fixed positional arguments.

        Returns:
            The helper process exit code.

        Raises:
            ElevationError: When polkit is unavailable.

        """
        pkexec: str | None = which(cmd="pkexec")
        if pkexec is None:
            msg: str = "pkexec/polkit is not available."
            raise ElevationError(msg)
        completed: CompletedProcess[bytes] = run(
            args=(pkexec, str(object=executable), *arguments),
            check=False,
        )
        return completed.returncode

    @staticmethod
    def powershell_literal(*, value: str) -> str:
        """Encode one PowerShell single-quoted literal.

        Args:
            value: Raw argument value.

        Returns:
            The escaped PowerShell literal.

        """
        return "'" + value.replace("'", "''") + "'"

    @staticmethod
    def shell_command(*, executable: Path, arguments: tuple[str, ...]) -> str:
        """Build a POSIX command using strict single-quote escaping.

        Args:
            executable: Helper executable.
            arguments: Fixed positional arguments.

        Returns:
            The shell-escaped command text.

        """
        values: tuple[str, ...] = (str(object=executable), *arguments)
        return " ".join(
            "'" + value.replace("'", "'\\''") + "'" for value in values
        )

    @staticmethod
    def apple_string(*, value: str) -> str:
        """Encode a string literal for AppleScript.

        Args:
            value: Raw string value.

        Returns:
            The escaped AppleScript literal.

        """
        escaped: str = value.replace("\\", "\\\\").replace('"', '\\"')
        return '"' + escaped + '"'

    @staticmethod
    def windows_quote(*, value: str) -> str:
        """Quote one Windows command-line argument.

        Args:
            value: Raw argument value.

        Returns:
            The quoted argument value.

        """
        escaped: str = value.replace('"', '\\"')
        return '"' + escaped + '"'
