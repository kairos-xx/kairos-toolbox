# Copyright (c) 2026 Project contributors.
"""Bowman trust boundary for District Terminal URI control."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Final

from bowman.context import Deadline, RequestContext, RequestIdentity
from bowman.models import CallerInfo, SecurityContext
from bowman.security import NonceStore
from bowman.trace import Trace
from bowman.trust import (
    AuthenticationStrength,
    Evidence,
    EvidenceKind,
    Principal,
    TrustLevel,
)
from bowman.uri import (
    SignedURIManager,
    SignedURIRequest,
    URIValue,
    URIValues,
)
from bowman.validity import ValidityCode, ValidityResult

from .identity import IdentityStore
from .ipc import EventServer, IpcEndpoint
from .product import Product

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True, kw_only=True)
class DistrictBowmanOperation:
    """Map one public Bowman operation to existing District Terminal IPC.

    Attributes:
        name: Public Bowman operation name.
        ipc_operation: Existing IPC operation name.
        destructive: Whether the operation mutates state destructively.

    """

    name: str
    ipc_operation: str
    destructive: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class DistrictBowmanResult:
    """Result and trusted context for one Bowman invocation.

    Attributes:
        response: Existing District Terminal IPC response text.
        context: Trusted Bowman request context.

    """

    response: str
    context: RequestContext


class DistrictBowman:
    """Expose existing District Terminal control through signed Bowman URIs.

    Attributes:
        operations: Public Bowman operations mapped to existing IPC.

    """

    operations: Final[dict[str, DistrictBowmanOperation]] = {
        "daemon.ping": DistrictBowmanOperation(
            name="daemon.ping",
            ipc_operation="ping",
        ),
        "daemon.stop": DistrictBowmanOperation(
            name="daemon.stop",
            ipc_operation="stop",
            destructive=True,
        ),
        "buffer.read": DistrictBowmanOperation(
            name="buffer.read",
            ipc_operation="buffer",
        ),
        "conpty.output": DistrictBowmanOperation(
            name="conpty.output",
            ipc_operation="conpty-output",
        ),
        "conpty.write": DistrictBowmanOperation(
            name="conpty.write",
            ipc_operation="conpty-write",
        ),
        "conpty.kill": DistrictBowmanOperation(
            name="conpty.kill",
            ipc_operation="conpty-kill",
            destructive=True,
        ),
        "conpty.alive": DistrictBowmanOperation(
            name="conpty.alive",
            ipc_operation="conpty-alive",
        ),
    }

    def __init__(self, *, home: Path) -> None:
        """Create the District Terminal Bowman adapter.

        Args:
            home: User home directory.

        """
        self._home: Path = home
        endpoint: IpcEndpoint = IpcEndpoint.for_home(home=home)
        domain_key: bytes = sha256(
            data=endpoint.authkey + b"\0district-bowman-v1",
        ).digest()
        self._manager: SignedURIManager = SignedURIManager(
            scheme=Product.url_scheme,
            issuer="district",
            audience=IdentityStore(home=home).host_id(),
            key=domain_key,
            nonces=NonceStore(),
            default_ttl=timedelta(minutes=2),
        )

    def create_uri(
        self,
        *,
        operation: str,
        values: URIValues | None = None,
        subject: str = "local-user",
        ttl: timedelta | None = None,
    ) -> str:
        """Create a signed, expiring District Terminal control URI.

        Args:
            operation: Public Bowman operation.
            values: Scalar operation arguments.
            subject: Authenticated/local subject label.
            ttl: Optional validity duration.

        Returns:
            Signed District Terminal URI.

        Raises:
            KeyError: When the operation is not exposed through Bowman.

        """
        self._operation(name=operation)
        return self._manager.create(
            operation=operation,
            values=values,
            subject=subject,
            ttl=ttl,
        )

    def execute_uri(self, *, uri: str) -> DistrictBowmanResult:
        """Validate and execute one signed District Terminal URI.

        Args:
            uri: Signed Bowman URI.

        Returns:
            Existing District Terminal IPC response and trusted Bowman context.

        """
        request: SignedURIRequest = self._manager.validate(uri=uri)
        operation: DistrictBowmanOperation = self._operation(
            name=request.operation,
        )
        values: dict[str, str] = self._ipc_values(values=request.values)
        response: str = EventServer.request(
            home=self._home,
            operation=operation.ipc_operation,
            values=values,
        )
        return DistrictBowmanResult(
            response=response,
            context=self._context(
                request=request,
                operation=operation,
            ),
        )

    @classmethod
    def _operation(cls, *, name: str) -> DistrictBowmanOperation:
        """Resolve an exposed Bowman operation.

        Args:
            name: Public Bowman operation name.

        Returns:
            The exposed operation mapping.

        Raises:
            KeyError: When the operation is not exposed through Bowman.

        """
        try:
            return cls.operations[name]
        except KeyError as error:
            msg: str = (
                f"District Terminal Bowman operation {name!r} is not exposed."
            )
            raise KeyError(
                msg,
            ) from error

    @staticmethod
    def _ipc_values(*, values: URIValues) -> dict[str, str]:
        """Flatten scalar URI values into IPC strings.

        Args:
            values: Scalar operation arguments.

        Returns:
            String-typed IPC values.

        """
        result: dict[str, str] = {}
        name: str
        value: URIValue
        for name, value in values.items():
            if value is None:
                result[name] = ""
            elif isinstance(value, bool):
                result[name] = "1" if value else "0"
            else:
                result[name] = str(object=value)
        return result

    @staticmethod
    def _context(
        *,
        request: SignedURIRequest,
        operation: DistrictBowmanOperation,
    ) -> RequestContext:
        """Build the trusted context for one validated request.

        Args:
            request: Validated signed URI request.
            operation: Resolved exposed operation.

        Returns:
            The trusted Bowman request context.

        """
        evidence: Evidence = Evidence(
            kind=EvidenceKind.SIGNATURE,
            name="district-uri",
            value=request.request_id,
            trust=TrustLevel.CRYPTOGRAPHICALLY_VERIFIED,
            issuer="district",
        )
        principal: Principal = Principal(
            subject=request.subject,
            strength=AuthenticationStrength.LOCAL_SESSION,
            capabilities=frozenset({operation.name}),
            evidence=(evidence,),
        )
        return RequestContext(
            request=RequestIdentity(
                request_id=request.request_id,
                operation=operation.name,
                uri=f"{Product.url_scheme}://request/v1/[REDACTED]",
            ),
            caller=CallerInfo(
                transport="bowman-uri",
                session_id=request.subject,
            ),
            principal=principal,
            security=SecurityContext(
                authenticated=True,
                principal=request.subject,
                authentication_method="signed-bowman-uri",
                capabilities=frozenset({operation.name}),
                expires_at=request.expires_at,
            ),
            validity=ValidityResult(
                valid=True,
                code=ValidityCode.VALID,
                reason="Signed Bowman URI validated.",
            ),
            trace=Trace(trace_id=request.trace_id),
            deadline=Deadline(at=request.expires_at),
        )
