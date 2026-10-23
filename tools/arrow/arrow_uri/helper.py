"""One-time signed grants for a minimal privileged helper."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest, new
from json import dumps, loads
from secrets import token_urlsafe
from typing import TYPE_CHECKING, Final, cast, final

from .errors import ElevationError

if TYPE_CHECKING:
    from .json_types import JSONValue
    from .security import NonceStore


@final
class Constants:
    """Group module-level constants.

    Attributes:
        signed_token_parts: Grouped module constant.

    """

    signed_token_parts: Final[int] = 2


@dataclass(frozen=True, slots=True, kw_only=True)
class HelperGrant:
    """Validated privileged-helper grant.

    Attributes:
        grant_id: Unique grant identifier.
        operation: Operation the grant authorizes.
        subject: Subject the grant was issued to.
        expires_at: Grant expiry timestamp.

    """

    grant_id: str
    operation: str
    subject: str
    expires_at: datetime


@dataclass(slots=True, kw_only=True)
class HelperGrantSigner:
    """Issue and consume one-time helper grants.

    Attributes:
        key: HMAC signing key.
        issuer: Required grant issuer.
        nonces: Replay-protection store.
        ttl: Grant lifetime.

    """

    key: bytes
    issuer: str
    nonces: NonceStore
    ttl: timedelta = timedelta(minutes=2)

    def issue(self, *, operation: str, subject: str) -> str:
        """Issue a short-lived signed helper grant.

        Args:
            operation: Operation the grant authorizes.
            subject: Subject to issue the grant to.

        Returns:
            The signed grant token.

        """
        now: datetime = datetime.now(tz=UTC)
        payload: dict[str, JSONValue] = {
            "iss": self.issuer,
            "gid": token_urlsafe(nbytes=24),
            "op": operation,
            "sub": subject,
            "exp": (now + self.ttl).timestamp(),
        }
        body: bytes = dumps(
            obj=payload,
            separators=(",", ":"),
            sort_keys=True,
        ).encode(encoding="utf-8")
        signature: bytes = new(
            key=self.key,
            msg=body,
            digestmod=sha256,
        ).digest()
        return self._encode(value=body) + "." + self._encode(value=signature)

    def consume(self, *, token: str, operation: str) -> HelperGrant:
        """Validate and consume a grant exactly once.

        Args:
            token: Signed grant token.
            operation: Operation the grant must authorize.

        Returns:
            The validated grant.

        Raises:
            ElevationError: When the token is malformed, forged, expired, or
                replayed.

        """
        parts: list[str] = token.split(sep=".")
        if len(parts) != Constants.signed_token_parts:
            msg: str = "Malformed helper grant."
            raise ElevationError(msg)
        body: bytes = self._decode(value=parts[0])
        supplied: bytes = self._decode(value=parts[1])
        expected: bytes = new(
            key=self.key,
            msg=body,
            digestmod=sha256,
        ).digest()
        if not compare_digest(supplied, expected):
            msg = "Invalid helper grant signature."
            raise ElevationError(msg)
        raw: JSONValue = cast(
            typ="JSONValue",
            val=loads(s=body.decode(encoding="utf-8")),
        )
        if not isinstance(raw, dict) or raw.get("iss") != self.issuer:
            msg = "Invalid helper grant issuer."
            raise ElevationError(msg)
        expiry: JSONValue = raw.get("exp", 0)
        if not isinstance(expiry, (int, float)):
            msg = "Invalid helper grant expiry."
            raise ElevationError(msg)
        grant_id: str = str(object=raw.get("gid", ""))
        grant_operation: str = str(object=raw.get("op", ""))
        subject: str = str(object=raw.get("sub", ""))
        expires_at: datetime = datetime.fromtimestamp(float(expiry), tz=UTC)
        if grant_operation != operation:
            msg = "Helper grant operation mismatch."
            raise ElevationError(msg)
        if datetime.now(tz=UTC) >= expires_at:
            msg = "Helper grant expired."
            raise ElevationError(msg)
        try:
            self.nonces.consume(nonce=grant_id)
        except Exception as error:
            msg = "Helper grant replay detected."
            raise ElevationError(msg) from error
        return HelperGrant(
            grant_id=grant_id,
            operation=grant_operation,
            subject=subject,
            expires_at=expires_at,
        )

    @staticmethod
    def _encode(*, value: bytes) -> str:
        """Encode bytes as unpadded URL-safe base64.

        Args:
            value: Bytes to encode.

        Returns:
            Unpadded URL-safe base64 text.

        """
        return urlsafe_b64encode(s=value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode(*, value: str) -> bytes:
        """Decode unpadded URL-safe base64.

        Args:
            value: Encoded text.

        Returns:
            Decoded bytes.

        """
        padding: str = "=" * (-len(value) % 4)
        return urlsafe_b64decode(s=(value + padding).encode("ascii"))
