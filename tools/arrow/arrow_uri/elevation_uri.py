"""Signed one-use elevation URIs backed by native OS authentication."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest, new
from json import dumps, loads
from os import name as os_name
from platform import system
from secrets import token_urlsafe
from subprocess import CompletedProcess, run
from typing import TYPE_CHECKING, Final, cast, final
from urllib.parse import SplitResult, quote, urlsplit

from .elevation import ElevationGrant, PrivilegeBroker
from .errors import ElevationError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from .json_types import JSONValue
    from .security import NonceStore
type ArgumentValue = str | int | float | bool


@final
class Constants:
    """Group module-level constants.

    Attributes:
        signed_token_parts: Grouped module constant.
        clock_skew: Grouped module constant.

    """

    signed_token_parts: Final[int] = 2
    clock_skew: Final[timedelta] = timedelta(seconds=30)


@dataclass(frozen=True, slots=True, kw_only=True)
class PrivilegedOperation:
    """One allowlisted operation reachable through elevation.

    Attributes:
        name: Public operation name.
        executable: Registered helper executable.
        argument_names: Fixed positional argument names.
        capability: Capability required to run the operation.

    """

    name: str
    executable: Path
    argument_names: tuple[str, ...]
    capability: str


class PrivilegedRegistry:
    """Registry of explicitly allowlisted privileged operations."""

    def __init__(self) -> None:
        """Create an empty privileged-operation registry."""
        self._operations: dict[str, PrivilegedOperation] = {}

    def register(
        self,
        *,
        name: str,
        executable_path: Path,
        argument_names: tuple[str, ...] = (),
        capability: str,
    ) -> PrivilegedOperation:
        """Register a fixed executable and argument schema.

        Args:
            name: Public operation name.
            executable_path: Helper executable to resolve.
            argument_names: Fixed positional argument names.
            capability: Capability required to run the operation.

        Returns:
            The registered operation.

        Raises:
            ElevationError: When the operation name already exists.

        """
        if name in self._operations:
            msg: str = f"Privileged operation {name!r} exists."
            raise ElevationError(msg)
        operation: PrivilegedOperation = PrivilegedOperation(
            name=name,
            executable=executable_path.resolve(),
            argument_names=argument_names,
            capability=capability,
        )
        self._operations[name] = operation
        return operation

    def get(self, *, name: str) -> PrivilegedOperation:
        """Resolve an allowlisted operation.

        Args:
            name: Public operation name.

        Returns:
            The registered operation.

        Raises:
            ElevationError: When the operation is not registered.

        """
        operation: PrivilegedOperation | None = self._operations.get(name)
        if operation is None:
            msg: str = f"Unknown privileged operation {name!r}."
            raise ElevationError(msg)
        return operation


@dataclass(frozen=True, slots=True, kw_only=True)
class ElevationEnvelope:
    """Validated elevation request.

    Attributes:
        grant_id: Unique grant identifier.
        issuer: Envelope issuer.
        audience: Envelope audience.
        subject: Requesting subject.
        operation: Public operation name.
        arguments: Positional operation arguments by name.
        reason: Human-readable reason shown to the user.
        issued_at: Issue timestamp.
        not_before: Earliest-use timestamp.
        expires_at: Expiry timestamp.
        capability: Capability required to run the operation.

    """

    grant_id: str
    issuer: str
    audience: str
    subject: str
    operation: str
    arguments: Mapping[str, ArgumentValue]
    reason: str
    issued_at: datetime
    not_before: datetime
    expires_at: datetime
    capability: str


@dataclass(slots=True, kw_only=True)
class ElevationURIManager:
    """Create, validate, open, and execute native-auth elevation URIs.

    Attributes:
        key: HMAC signing key.
        issuer: Required URI issuer.
        audience: Required URI audience.
        scheme: URI scheme the manager owns.
        registry: Allowlisted privileged operations.
        nonces: Replay-protection store.
        default_ttl: Default URI lifetime.

    """

    key: bytes
    issuer: str
    audience: str
    scheme: str
    registry: PrivilegedRegistry
    nonces: NonceStore
    default_ttl: timedelta = timedelta(minutes=2)

    @staticmethod
    def timestamp(*, raw: Mapping[str, JSONValue], name: str) -> datetime:
        """Read a required timestamp field.

        Args:
            raw: Decoded envelope payload.
            name: Timestamp field name.

        Returns:
            The field value as a UTC timestamp.

        Raises:
            ElevationError: When the field is not numeric.

        """
        value: JSONValue = raw.get(name)
        if not isinstance(value, (int, float)):
            msg: str = f"Invalid elevation timestamp {name!r}."
            raise ElevationError(msg)
        return datetime.fromtimestamp(float(value), tz=UTC)

    @staticmethod
    def open(*, uri: str) -> int:
        """Open an elevation URI through the platform URI launcher.

        Args:
            uri: Signed elevation URI.

        Returns:
            The platform launcher exit code.

        Raises:
            ElevationError: When the operating system is unsupported.

        """
        platform_name: str = system()
        argv: tuple[str, ...]
        if platform_name == "Darwin":
            argv = ("open", uri)
        elif platform_name == "Linux":
            argv = ("xdg-open", uri)
        elif os_name == "nt":
            argv = (
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "Start-Process",
                uri,
            )
        else:
            msg: str = "Unsupported operating system."
            raise ElevationError(msg)
        completed: CompletedProcess[bytes] = run(args=argv, check=False)
        return completed.returncode

    def create(
        self,
        *,
        operation: str,
        arguments: Mapping[str, ArgumentValue],
        subject: str,
        reason: str,
        ttl: timedelta | None = None,
    ) -> str:
        """Create a signed elevation URI.

        Args:
            operation: Registered operation name.
            arguments: Positional arguments by name.
            subject: Requesting subject.
            reason: Human-readable reason shown to the user.
            ttl: Optional validity duration.

        Returns:
            The signed elevation URI.

        Raises:
            ElevationError: When the operation is unknown or the arguments
                do not match the registered schema.

        """
        registered: PrivilegedOperation = self.registry.get(name=operation)
        supplied_names: frozenset[str] = frozenset(arguments)
        expected_names: frozenset[str] = frozenset(registered.argument_names)
        if supplied_names != expected_names:
            msg: str = "Elevation arguments do not match the operation schema."
            raise ElevationError(msg)
        now: datetime = datetime.now(tz=UTC)
        expires_at: datetime = now + (ttl or self.default_ttl)
        payload: dict[str, JSONValue] = {
            "v": 1,
            "gid": token_urlsafe(nbytes=24),
            "iss": self.issuer,
            "aud": self.audience,
            "sub": subject,
            "op": operation,
            "args": cast(typ="dict[str, JSONValue]", val=dict(arguments)),
            "reason": reason,
            "iat": now.timestamp(),
            "nbf": (now - timedelta(seconds=5)).timestamp(),
            "exp": expires_at.timestamp(),
            "cap": registered.capability,
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
        token: str = (
            self._encode(value=body) + "." + self._encode(value=signature)
        )
        return f"{self.scheme}://elevate/v1/{quote(string=token, safe='')}"

    def validate(self, *, uri: str) -> ElevationEnvelope:
        """Validate signature, time, audience, operation, and arguments.

        Args:
            uri: Signed elevation URI.

        Returns:
            The validated elevation envelope.

        Raises:
            ElevationError: When the URI is malformed, forged, expired,
                mistargeted, or violates the registered schema.

        """
        token: str = self._token(uri=uri)
        raw: dict[str, JSONValue] = self._payload(token=token)
        if raw.get("iss") != self.issuer:
            msg: str = "Elevation URI issuer mismatch."
            raise ElevationError(msg)
        if raw.get("aud") != self.audience:
            msg = "Elevation URI audience mismatch."
            raise ElevationError(msg)
        operation_name: str = str(object=raw.get("op", ""))
        operation: PrivilegedOperation = self.registry.get(name=operation_name)
        arguments: dict[str, ArgumentValue] = self._arguments(
            raw=raw.get("args"),
            operation=operation,
        )
        issued_at: datetime = self.timestamp(raw=raw, name="iat")
        not_before: datetime = self.timestamp(raw=raw, name="nbf")
        expires_at: datetime = self.timestamp(raw=raw, name="exp")
        self._window(
            issued_at=issued_at,
            not_before=not_before,
            expires_at=expires_at,
        )
        capability: str = str(object=raw.get("cap", ""))
        if capability != operation.capability:
            msg = "Elevation capability mismatch."
            raise ElevationError(msg)
        return ElevationEnvelope(
            grant_id=str(object=raw.get("gid", "")),
            issuer=self.issuer,
            audience=self.audience,
            subject=str(object=raw.get("sub", "")),
            operation=operation_name,
            arguments=arguments,
            reason=str(object=raw.get("reason", "")),
            issued_at=issued_at,
            not_before=not_before,
            expires_at=expires_at,
            capability=capability,
        )

    def execute(self, *, uri: str) -> int:
        """Validate and request native OS authentication/elevation.

        Args:
            uri: Signed elevation URI.

        Returns:
            The helper process exit code.

        Raises:
            ElevationError: When the URI is invalid or replayed.

        """
        envelope: ElevationEnvelope = self.validate(uri=uri)
        operation: PrivilegedOperation = self.registry.get(
            name=envelope.operation,
        )
        self.nonces.consume(nonce=envelope.grant_id)
        ordered_arguments: tuple[str, ...] = tuple(
            str(object=envelope.arguments[name])
            for name in operation.argument_names
        )
        grant: ElevationGrant = PrivilegeBroker.grant(
            capabilities=frozenset({operation.capability}),
            reason=envelope.reason,
            ttl=envelope.expires_at - datetime.now(tz=UTC),
        )
        return PrivilegeBroker.execute(
            executable=operation.executable,
            arguments=ordered_arguments,
            grant=grant,
            required_capability=operation.capability,
        )

    @staticmethod
    def _arguments(
        *,
        raw: JSONValue,
        operation: PrivilegedOperation,
    ) -> dict[str, ArgumentValue]:
        """Decode and schema-check positional arguments.

        Args:
            raw: Decoded `args` payload field.
            operation: Registered operation schema.

        Returns:
            Typed positional arguments by name.

        Raises:
            ElevationError: When the arguments are invalid or do not match
                the registered schema.

        """
        if not isinstance(raw, dict):
            msg: str = "Invalid elevation arguments."
            raise ElevationError(msg)
        arguments: dict[str, ArgumentValue] = {}
        key: str
        value: JSONValue
        for key, value in raw.items():
            if not isinstance(value, (str, int, float, bool)):
                msg = "Invalid elevation argument value."
                raise ElevationError(msg)
            arguments[key] = value
        if frozenset(arguments) != frozenset(operation.argument_names):
            msg = "Elevation argument schema mismatch."
            raise ElevationError(msg)
        return arguments

    @staticmethod
    def _window(
        *,
        issued_at: datetime,
        not_before: datetime,
        expires_at: datetime,
    ) -> None:
        """Check envelope timestamps against the current time.

        Args:
            issued_at: Issue timestamp.
            not_before: Earliest-use timestamp.
            expires_at: Expiry timestamp.

        Raises:
            ElevationError: When the envelope is not yet valid, expired, or
                issued too far in the future.

        """
        now: datetime = datetime.now(tz=UTC)
        if now < not_before:
            msg: str = "Elevation URI is not yet valid."
            raise ElevationError(msg)
        if now >= expires_at:
            msg = "Elevation URI expired."
            raise ElevationError(msg)
        if issued_at > now + Constants.clock_skew:
            msg = "Elevation URI issue time is invalid."
            raise ElevationError(msg)

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

        Raises:
            ElevationError: When the value is not valid base64.

        """
        padding: str = "=" * (-len(value) % 4)
        try:
            return urlsafe_b64decode(s=(value + padding).encode("ascii"))
        except ValueError as error:
            msg: str = "Invalid elevation token encoding."
            raise ElevationError(msg) from error

    def _token(self, *, uri: str) -> str:
        """Extract the signed token from an elevation URI.

        Args:
            uri: Signed elevation URI.

        Returns:
            The encoded token portion.

        Raises:
            ElevationError: When the URI target or version is invalid.

        """
        parsed: SplitResult = urlsplit(url=uri)
        if parsed.scheme != self.scheme:
            msg: str = "Elevation URI scheme mismatch."
            raise ElevationError(msg)
        if parsed.netloc != "elevate":
            msg = "Elevation URI authority mismatch."
            raise ElevationError(msg)
        prefix: str = "/v1/"
        if not parsed.path.startswith(prefix):
            msg = "Unsupported elevation URI version."
            raise ElevationError(msg)
        return parsed.path[len(prefix) :]

    def _payload(self, *, token: str) -> dict[str, JSONValue]:
        """Verify the signature and decode the envelope payload.

        Args:
            token: Encoded token portion.

        Returns:
            The decoded envelope payload.

        Raises:
            ElevationError: When the token is malformed or forged.

        """
        parts: list[str] = token.split(sep=".")
        if len(parts) != Constants.signed_token_parts:
            msg: str = "Malformed elevation URI."
            raise ElevationError(msg)
        body: bytes = self._decode(value=parts[0])
        supplied_signature: bytes = self._decode(value=parts[1])
        expected_signature: bytes = new(
            key=self.key,
            msg=body,
            digestmod=sha256,
        ).digest()
        if not compare_digest(supplied_signature, expected_signature):
            msg = "Invalid elevation URI signature."
            raise ElevationError(msg)
        raw: JSONValue = cast(
            typ="JSONValue",
            val=loads(s=body.decode(encoding="utf-8")),
        )
        if not isinstance(raw, dict):
            msg = "Invalid elevation envelope."
            raise ElevationError(msg)
        return raw
