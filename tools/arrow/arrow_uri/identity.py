"""Caller provenance and executable authorization."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .errors import AuthorizationError

if TYPE_CHECKING:
    from .models import CallerInfo


class PeerIdentityProvider(Protocol):
    """Transport hook that returns verified caller provenance."""

    def caller(self) -> CallerInfo:
        """Return the trusted caller for this connection.

        Returns:
            Verified caller provenance.

        """
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutablePolicy:
    """Authorize callers by executable path or SHA-256.

    Attributes:
        allowed_paths: Permitted executable paths.
        allowed_sha256: Permitted executable digests.

    """

    allowed_paths: frozenset[Path] = frozenset()
    allowed_sha256: frozenset[str] = frozenset()

    @staticmethod
    def digest(*, path: Path) -> str:
        """Return the SHA-256 digest of an executable.

        Args:
            path: Executable path.

        Returns:
            Hex digest of the file contents.

        """
        return sha256(data=path.read_bytes()).hexdigest()

    def authorize(self, *, caller: CallerInfo) -> None:
        """Authorize one caller's executable.

        Args:
            caller: Transport-established caller provenance.

        Raises:
            AuthorizationError: When the caller is not authorized.

        """
        if caller.executable is None:
            if self.allowed_paths or self.allowed_sha256:
                msg: str = "Caller executable is unavailable."
                raise AuthorizationError(msg)
            return
        executable: Path = Path(caller.executable)
        if executable in self.allowed_paths:
            return
        if self.allowed_sha256:
            digest: str = self.digest(path=executable)
            if digest in self.allowed_sha256:
                return
        msg = f"Executable {caller.executable} is not authorized."
        raise AuthorizationError(msg)
