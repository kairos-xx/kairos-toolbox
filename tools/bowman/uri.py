# Copyright (c) 2026 Project contributors.
"""Signed, expiring, optionally one-use local URI envelopes."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest, new
from json import JSONDecodeError, dumps, loads
from secrets import token_urlsafe
from typing import TYPE_CHECKING, Final, cast, final
from urllib.parse import SplitResult, urlsplit

from .errors import AuthorizationError, SessionError
from .validity import ValidityPolicy, ValidityResult, ValidityWindow

if TYPE_CHECKING:
    from .security import NonceStore
type URIValue = None | bool | int | float | str
type URIValues = dict[str, URIValue]
type JSONValue = URIValue | list[JSONValue] | dict[str, JSONValue]


@final
class Constants:
    """Group module-level constants.

    Attributes:
        signed_token_parts: Grouped module constant.

    """

    signed_token_parts: Final[int] = 2


@dataclass(frozen=True, slots=True, kw_only=True)
class SignedURIRequest:
    """Validated local URI request.

    Attributes:
        request_id: Unique request identifier.
        operation: Requested operation name.
        values: Scalar operation arguments.
        subject: Authenticated/local subject label.
        issued_at: Issue timestamp.
        expires_at: Expiry timestamp.
        trace_id: Trace identifier.

    """

    request_id: str
    operation: str
    values: URIValues
    subject: str
    issued_at: datetime
    expires_at: datetime
    trace_id: str


@dataclass(slots=True, kw_only=True)
class SignedURIManager:
    """Create and validate signed local URI requests.

    Attributes:
        scheme: URI scheme the manager owns.
        issuer: Required URI issuer.
        audience: Required URI audience.
        key: HMAC signing key.
        nonces: Replay-protection store.
        default_ttl: Default URI lifetime.

    """

    scheme: str
    issuer: str
    audience: str
    key: bytes
    nonces: NonceStore
    default_ttl: timedelta = timedelta(minutes=2)

    def create(
        self,
        *,
        operation: str,
        values: URIValues | None = None,
        subject: str = "local",
        trace_id: str | None = None,
        ttl: timedelta | None = None,
    ) -> str:
        """Create a canonical signed request URI.

        Args:
            operation: Requested operation name.
            values: Scalar operation arguments.
            subject: Authenticated/local subject label.
            trace_id: Optional trace identifier.
            ttl: Optional validity duration.

        Returns:
            The signed request URI.

        """
        now: datetime = datetime.now(tz=UTC)
        request_id: str = token_urlsafe(nbytes=18)
        payload: URIValues = {
            "v": 1,
            "rid": request_id,
            "op": operation,
            "sub": subject,
            "trace": trace_id or token_urlsafe(nbytes=18),
            "iss": self.issuer,
            "aud": self.audience,
            "iat": now.timestamp(),
            "nbf": (now - timedelta(seconds=5)).timestamp(),
            "exp": (now + (ttl or self.default_ttl)).timestamp(),
        }
        encoded_values: str = dumps(
            obj=values or {},
            separators=(",", ":"),
            sort_keys=True,
        )
        payload["values"] = encoded_values
        body: bytes = dumps(
            obj=payload,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        signature: bytes = new(
            key=self.key,
            msg=body,
            digestmod=sha256,
        ).digest()
        return f"{self.scheme}://request/v1/{self._encode(value=body)}.{self._encode(value=signature)}"

    def validate(self, *, uri: str, consume: bool = True) -> SignedURIRequest:
        """Validate a signed request and optionally consume its nonce.

        Args:
            uri: Signed request URI.
            consume: Whether the request is consumed on validation.

        Returns:
            The validated request.

        Raises:
            AuthorizationError: When the URI is malformed, forged, expired,
                replayed, or bound to another target.

        """
        parsed: SplitResult = urlsplit(url=uri)
        if parsed.scheme != self.scheme or parsed.netloc != "request":
            msg: str = "Signed URI target is invalid."
            raise AuthorizationError(msg)
        prefix: str = "/v1/"
        if not parsed.path.startswith(prefix):
            msg = "Signed URI version is invalid."
            raise AuthorizationError(msg)
        token: str = parsed.path[len(prefix) :]
        parts: list[str] = token.split(sep=".")
        if len(parts) != Constants.signed_token_parts:
            msg = "Signed URI token is malformed."
            raise AuthorizationError(msg)
        body: bytes = self._decode(value=parts[0])
        signature: bytes = self._decode(value=parts[1])
        expected: bytes = new(
            key=self.key,
            msg=body,
            digestmod=sha256,
        ).digest()
        if not compare_digest(signature, expected):
            msg = "Signed URI signature is invalid."
            raise AuthorizationError(msg)
        raw: dict[str, JSONValue] = self._mapping(value=body)
        request_id: str = self._text(raw=raw, name="rid")
        issued_at: datetime = self._time(raw=raw, name="iat")
        expires_at: datetime = self._time(raw=raw, name="exp")
        window: ValidityWindow = ValidityWindow(
            issued_at=issued_at,
            not_before=self._time(raw=raw, name="nbf"),
            expires_at=expires_at,
            issuer=self._text(raw=raw, name="iss"),
            audience=self._text(raw=raw, name="aud"),
        )
        result: ValidityResult = ValidityPolicy(
            issuer=self.issuer,
            audience=self.audience,
        ).evaluate(window=window)
        if not result.valid:
            msg = f"Signed URI validity failed: {result.code.value}."
            raise AuthorizationError(msg)
        if consume:
            try:
                self.nonces.consume(nonce=request_id)
            except SessionError as error:
                msg = "Signed URI replay detected."
                raise AuthorizationError(msg) from error
        values_text: str = self._text(raw=raw, name="values")
        values_raw: dict[str, JSONValue] = self._mapping(
            value=values_text.encode(),
        )
        values: URIValues = {}
        name: str
        value: JSONValue
        for name, value in values_raw.items():
            if isinstance(value, (list, dict)):
                msg = "URI values must be scalar."
                raise AuthorizationError(msg)
            values[name] = value
        return SignedURIRequest(
            request_id=request_id,
            operation=self._text(raw=raw, name="op"),
            values=values,
            subject=self._text(raw=raw, name="sub"),
            issued_at=issued_at,
            expires_at=expires_at,
            trace_id=self._text(raw=raw, name="trace"),
        )

    @staticmethod
    def _mapping(*, value: bytes) -> dict[str, JSONValue]:
        """Decode a JSON object from bytes.

        Args:
            value: Encoded JSON bytes.

        Returns:
            The decoded JSON object.

        Raises:
            AuthorizationError: When the payload is not a JSON object.

        """
        try:
            decoded: JSONValue = cast(
                typ="JSONValue",
                val=loads(s=value.decode()),
            )
        except (JSONDecodeError, UnicodeDecodeError) as error:
            msg: str = "Signed URI payload is invalid."
            raise AuthorizationError(msg) from error
        if not isinstance(decoded, dict):
            msg = "Signed URI payload must be an object."
            raise AuthorizationError(msg)
        return decoded

    @staticmethod
    def _text(*, raw: dict[str, JSONValue], name: str) -> str:
        """Read a required string field.

        Args:
            raw: Decoded JSON object.
            name: Field name.

        Returns:
            The field value.

        Raises:
            AuthorizationError: When the field is not a string.

        """
        value: JSONValue = raw.get(name)
        if not isinstance(value, str):
            msg: str = f"Signed URI field {name!r} is invalid."
            raise AuthorizationError(msg)
        return value

    @staticmethod
    def _time(*, raw: dict[str, JSONValue], name: str) -> datetime:
        """Read a required timestamp field.

        Args:
            raw: Decoded JSON object.
            name: Field name.

        Returns:
            The field value as a UTC timestamp.

        Raises:
            AuthorizationError: When the field is not numeric.

        """
        value: JSONValue = raw.get(name)
        if not isinstance(value, (int, float)):
            msg: str = f"Signed URI field {name!r} is invalid."
            raise AuthorizationError(msg)
        return datetime.fromtimestamp(timestamp=float(value), tz=UTC)

    @staticmethod
    def _encode(*, value: bytes) -> str:
        """Encode bytes as unpadded URL-safe base64.

        Args:
            value: Bytes to encode.

        Returns:
            Unpadded URL-safe base64 text.

        """
        return urlsafe_b64encode(s=value).rstrip(b"=").decode()

    @staticmethod
    def _decode(*, value: str) -> bytes:
        """Decode unpadded URL-safe base64.

        Args:
            value: Encoded text.

        Returns:
            Decoded bytes.

        Raises:
            AuthorizationError: When the value is not valid base64.

        """
        padding: str = "=" * (-len(value) % 4)
        try:
            return urlsafe_b64decode(s=(value + padding).encode())
        except ValueError as error:
            msg: str = "Signed URI encoding is invalid."
            raise AuthorizationError(msg) from error
