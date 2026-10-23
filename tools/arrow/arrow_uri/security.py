"""Security policies, OAuth bearer validation, and signed local sessions."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest, new
from json import dumps, loads
from secrets import token_urlsafe
from threading import Lock
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Protocol, cast, final

from .errors import AuthenticationError, AuthorizationError, SessionError
from .models import SecurityContext

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .json_types import JSONValue


@final
class Constants:
    """Group module-level constants.

    Attributes:
        signed_token_parts: Grouped module constant.

    """

    signed_token_parts: Final[int] = 2


@dataclass(frozen=True, slots=True, kw_only=True)
class OAuthToken:
    """Validated OAuth 2.x bearer-token result.

    Attributes:
        subject: Authenticated subject.
        issuer: Token issuer.
        audience: Intended audience.
        scopes: Granted scopes.
        expires_at: Expiry timestamp.
        claims: Additional verified claims.

    """

    subject: str
    issuer: str
    audience: str
    scopes: frozenset[str]
    expires_at: datetime
    claims: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType(mapping={}),
    )

    @property
    def valid(self) -> bool:
        """Return whether the token is currently valid.

        Returns:
            Whether the expiry timestamp is still in the future.

        """
        return datetime.now(tz=UTC) < self.expires_at


class OAuthTokenValidator(Protocol):
    """Pluggable OAuth/OIDC token validator."""

    def validate(
        self,
        *,
        token: str,
        required_scopes: frozenset[str],
    ) -> OAuthToken:
        """Validate bearer token, issuer, audience, expiry, and scopes.

        Args:
            token: Bearer token material.
            required_scopes: Scopes the token must grant.

        Returns:
            The validated token.

        """
        ...


@dataclass(slots=True, kw_only=True)
class StaticOAuthValidator:
    """Deterministic validator useful for local brokers and tests.

    Attributes:
        tokens: Known token material to validated tokens.

    """

    tokens: Mapping[str, OAuthToken]

    def validate(
        self,
        *,
        token: str,
        required_scopes: frozenset[str],
    ) -> OAuthToken:
        """Validate a pre-established OAuth token.

        Args:
            token: Bearer token material.
            required_scopes: Scopes the token must grant.

        Returns:
            The validated token.

        Raises:
            AuthenticationError: When the token is unknown or expired.
            AuthorizationError: When required scopes are missing.

        """
        validated: OAuthToken | None = self.tokens.get(token)
        if validated is None or not validated.valid:
            msg: str = "Invalid or expired OAuth token."
            raise AuthenticationError(msg)
        missing: frozenset[str] = required_scopes - validated.scopes
        if missing:
            raise AuthorizationError(
                "Missing OAuth scopes: " + ", ".join(sorted(missing)),
            )
        return validated


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthorizationPolicy:
    """Route authorization requirements.

    Attributes:
        capabilities: Required capability names.
        scopes: Required OAuth-style scopes.
        require_authentication: Whether a valid identity is required.
        require_elevation: Whether OS elevation is required.

    """

    capabilities: frozenset[str] = frozenset()
    scopes: frozenset[str] = frozenset()
    require_authentication: bool = False
    require_elevation: bool = False

    def authorize(self, *, security: SecurityContext) -> None:
        """Authorize a security context.

        Args:
            security: Authenticated identity and authorization state.

        Raises:
            AuthenticationError: When authentication is required but absent.
            AuthorizationError: When capabilities, scopes, or elevation are
                missing.

        """
        if self.require_authentication and (not security.valid):
            msg: str = "A valid authenticated identity is required."
            raise AuthenticationError(msg)
        missing_capabilities: frozenset[str] = (
            self.capabilities - security.capabilities
        )
        if missing_capabilities:
            raise AuthorizationError(
                "Missing capabilities: "
                + ", ".join(sorted(missing_capabilities)),
            )
        missing_scopes: frozenset[str] = self.scopes - security.scopes
        if missing_scopes:
            raise AuthorizationError(
                "Missing scopes: " + ", ".join(sorted(missing_scopes)),
            )
        if self.require_elevation and (not security.elevated):
            msg = "OS privilege elevation is required."
            raise AuthorizationError(msg)


@dataclass(slots=True, kw_only=True)
class NonceStore:
    """Bounded-time replay protection.

    Attributes:
        retention: How long consumed nonces are remembered.

    """

    retention: timedelta = timedelta(minutes=10)
    _seen: dict[str, datetime] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def consume(self, *, nonce: str) -> None:
        """Consume a nonce once.

        Args:
            nonce: Single-use nonce value.

        Raises:
            SessionError: When the nonce was already consumed.

        """
        now: datetime = datetime.now(tz=UTC)
        cutoff: datetime = now - self.retention
        with self._lock:
            expired: tuple[str, ...] = tuple(
                (key for key, seen in self._seen.items() if seen < cutoff),
            )
            for key in expired:
                del self._seen[key]
            if nonce in self._seen:
                msg: str = "Replay detected."
                raise SessionError(msg)
            self._seen[nonce] = now


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionClaims:
    """URI-local cookie/session claims.

    Attributes:
        session_id: Unique session identifier.
        subject: Authenticated subject.
        issued_at: Issue timestamp.
        expires_at: Expiry timestamp.
        scopes: Granted scopes.
        capabilities: Granted capabilities.

    """

    session_id: str
    subject: str
    issued_at: datetime
    expires_at: datetime
    scopes: frozenset[str]
    capabilities: frozenset[str]

    @property
    def valid(self) -> bool:
        """Return whether the session remains valid.

        Returns:
            Whether the current time is inside the session window.

        """
        now: datetime = datetime.now(tz=UTC)
        return self.issued_at <= now < self.expires_at


@dataclass(slots=True, kw_only=True)
class SignedSessionManager:
    """Issue and validate signed local session tokens.

    These are Arrow's cookie equivalent. They are opaque to callers, scoped,
    expiring, integrity-protected, and replay-aware where one-time use is
    requested. They are not browser cookies and are never put into OS-global
    cookie stores.

    Attributes:
        key: HMAC signing key.
        issuer: Required session issuer.
        default_ttl: Default session lifetime.
        nonces: Replay-protection store for one-time sessions.

    """

    key: bytes
    issuer: str
    default_ttl: timedelta = timedelta(minutes=30)
    nonces: NonceStore = field(default_factory=NonceStore)

    @staticmethod
    def security_context(*, claims: SessionClaims) -> SecurityContext:
        """Convert validated session claims into authorization state.

        Args:
            claims: Validated session claims.

        Returns:
            Authorization state for the session.

        """
        return SecurityContext(
            authenticated=True,
            principal=claims.subject,
            authentication_method="signed_session",
            capabilities=claims.capabilities,
            scopes=claims.scopes,
            expires_at=claims.expires_at,
        )

    def issue(
        self,
        *,
        subject: str,
        scopes: frozenset[str] = frozenset(),
        capabilities: frozenset[str] = frozenset(),
        ttl: timedelta | None = None,
    ) -> str:
        """Issue a signed, expiring local session.

        Args:
            subject: Authenticated subject.
            scopes: Scopes to grant.
            capabilities: Capabilities to grant.
            ttl: Optional lifetime override.

        Returns:
            The signed session token.

        """
        now: datetime = datetime.now(tz=UTC)
        expires: datetime = now + (ttl or self.default_ttl)
        payload: dict[str, JSONValue] = {
            "v": 1,
            "iss": self.issuer,
            "sid": token_urlsafe(nbytes=18),
            "sub": subject,
            "iat": now.timestamp(),
            "exp": expires.timestamp(),
            "scp": cast(typ="list[JSONValue]", val=sorted(scopes)),
            "cap": cast(typ="list[JSONValue]", val=sorted(capabilities)),
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

    def validate(
        self,
        *,
        token: str,
        consume_once: bool = False,
    ) -> SessionClaims:
        """Validate signature, issuer, timestamps, and optional replay.

        Args:
            token: Signed session token.
            consume_once: Whether the token may only be validated once.

        Returns:
            The validated session claims.

        Raises:
            SessionError: When the token is malformed, forged, expired, or
                replayed.

        """
        parts: list[str] = token.split(sep=".")
        if len(parts) != Constants.signed_token_parts:
            msg: str = "Malformed local session."
            raise SessionError(msg)
        body: bytes = self._decode(value=parts[0])
        supplied: bytes = self._decode(value=parts[1])
        expected: bytes = new(
            key=self.key,
            msg=body,
            digestmod=sha256,
        ).digest()
        if not compare_digest(expected, supplied):
            msg = "Invalid local session signature."
            raise SessionError(msg)
        raw: JSONValue = cast(
            typ="JSONValue",
            val=loads(s=body.decode(encoding="utf-8")),
        )
        if not isinstance(raw, dict) or raw.get("iss") != self.issuer:
            msg = "Invalid local session issuer."
            raise SessionError(msg)
        claims: SessionClaims = self._claims(raw=raw)
        if not claims.valid:
            msg = "Local session expired or not yet valid."
            raise SessionError(msg)
        if consume_once:
            self.nonces.consume(nonce=claims.session_id)
        return claims

    @staticmethod
    def _claims(*, raw: Mapping[str, JSONValue]) -> SessionClaims:
        """Extract typed claims from a decoded session payload.

        Args:
            raw: Decoded session payload.

        Returns:
            The extracted session claims.

        Raises:
            SessionError: When required claims are missing or invalid.

        """
        try:
            issued: JSONValue = raw["iat"]
            expires: JSONValue = raw["exp"]
            scope_values: JSONValue = raw.get("scp", [])
            capability_values: JSONValue = raw.get("cap", [])
            if (
                not isinstance(issued, (int, float))
                or not isinstance(expires, (int, float))
                or (not isinstance(scope_values, list))
                or (not isinstance(capability_values, list))
            ):
                msg: str = "Invalid local session claims."
                raise SessionError(msg)
            return SessionClaims(
                session_id=str(object=raw["sid"]),
                subject=str(object=raw["sub"]),
                issued_at=datetime.fromtimestamp(float(issued), tz=UTC),
                expires_at=datetime.fromtimestamp(float(expires), tz=UTC),
                scopes=frozenset(str(object=value) for value in scope_values),
                capabilities=frozenset(
                    str(object=value) for value in capability_values
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            msg = "Invalid local session claims."
            raise SessionError(msg) from error

    @staticmethod
    def _encode(*, value: bytes) -> str:
        """Encode bytes as unpadded URL-safe base64.

        Args:
            value: Bytes to encode.

        Returns:
            Unpadded URL-safe base64 text.

        """
        return urlsafe_b64encode(s=value).rstrip(b"=").decode(encoding="ascii")

    @staticmethod
    def _decode(*, value: str) -> bytes:
        """Decode unpadded URL-safe base64.

        Args:
            value: Encoded text.

        Returns:
            Decoded bytes.

        """
        padding: str = "=" * (-len(value) % 4)
        return urlsafe_b64decode(s=(value + padding).encode(encoding="ascii"))
