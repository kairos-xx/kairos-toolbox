"""Core immutable Bowman models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from .json_types import JSONValue
type Metadata = Mapping[str, str]


class URIStatus(StrEnum):
    """Framework result status.

    Attributes:
        ok: Request succeeded.
        accepted: Request was accepted for processing.
        bad_request: Request data was invalid.
        unauthorized: Authentication failed or was missing.
        forbidden: Caller was not authorized.
        not_found: No route or resource matched.
        conflict: Request conflicted with current state.
        error: Request failed with an internal error.

    """

    ok = "ok"
    accepted = "accepted"
    bad_request = "bad_request"
    unauthorized = "unauthorized"
    forbidden = "forbidden"
    not_found = "not_found"
    conflict = "conflict"
    error = "error"


@dataclass(frozen=True, slots=True, kw_only=True)
class CallerInfo:
    """Transport-established caller provenance.

    Attributes:
        transport: Transport that established the caller identity.
        pid: Caller process identifier.
        parent_pid: Caller parent process identifier.
        executable: Caller executable path.
        process_name: Caller process name.
        user: Operating-system user name.
        session_id: Caller session identifier.
        source_application: Calling application name.
        metadata: Additional transport-specific metadata.

    """

    transport: str = "in_process"
    pid: int | None = None
    parent_pid: int | None = None
    executable: str | None = None
    process_name: str | None = None
    user: str | None = None
    session_id: str | None = None
    source_application: str | None = None
    metadata: Metadata = field(
        default_factory=lambda: MappingProxyType(mapping={}),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class TraceContext:
    """Distributed/local trace identifiers.

    Attributes:
        request_id: Unique request identifier.
        trace_id: Trace identifier.
        span_id: Current span identifier.
        parent_span_id: Parent span identifier.
        started_at: Trace start timestamp.

    """

    request_id: str = field(default_factory=lambda: uuid4().hex)
    trace_id: str = field(default_factory=lambda: uuid4().hex)
    span_id: str = field(default_factory=lambda: uuid4().hex[:16])
    parent_span_id: str | None = None
    started_at: datetime = field(
        default_factory=lambda: datetime.now(tz=UTC),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class SecurityContext:
    """Authenticated identity and authorization state.

    Attributes:
        authenticated: Whether the caller is authenticated.
        principal: Authenticated principal name.
        authentication_method: Method used to authenticate.
        capabilities: Granted capability names.
        scopes: Granted OAuth-style scopes.
        claims: Additional verified claims.
        expires_at: Optional expiry timestamp.
        elevated: Whether the caller holds OS elevation.

    """

    authenticated: bool = False
    principal: str | None = None
    authentication_method: str | None = None
    capabilities: frozenset[str] = frozenset()
    scopes: frozenset[str] = frozenset()
    claims: Metadata = field(
        default_factory=lambda: MappingProxyType(mapping={}),
    )
    expires_at: datetime | None = None
    elevated: bool = False

    @property
    def valid(self) -> bool:
        """Return whether this security context remains valid.

        Returns:
            Whether the context is authenticated and unexpired.

        """
        if not self.authenticated:
            return False
        return (
            self.expires_at is None or datetime.now(tz=UTC) < self.expires_at
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class URIRequest:
    """Normalized Bowman request.

    Attributes:
        uri: Raw request URI.
        scheme: URI scheme.
        authority: URI authority component.
        path: URI path component.
        query: Decoded query parameters.
        fragment: URI fragment.
        operation: Resolved operation name.
        caller: Transport-established caller provenance.
        trace: Trace identifiers.
        security: Authorization state.

    """

    uri: str
    scheme: str
    authority: str
    path: str
    query: Mapping[str, tuple[str, ...]]
    fragment: str
    operation: str
    caller: CallerInfo
    trace: TraceContext
    security: SecurityContext


@dataclass(frozen=True, slots=True, kw_only=True)
class URIResponse:
    """Structured Bowman response.

    Attributes:
        status: Result status.
        data: Optional result payload.
        error: Optional error message.
        request_id: Echoed request identifier.
        trace_id: Echoed trace identifier.

    """

    status: URIStatus
    data: JSONValue = None
    error: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
