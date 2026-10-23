# Copyright (c) 2026 Project contributors.
"""Arrow trust boundary for Lake Terminal URI control."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Final

from arrow_uri.context import Deadline, RequestContext, RequestIdentity
from arrow_uri.models import CallerInfo, SecurityContext
from arrow_uri.security import NonceStore
from arrow_uri.trace import Trace
from arrow_uri.trust import (
    AuthenticationStrength,
    Evidence,
    EvidenceKind,
    Principal,
    TrustLevel,
)
from arrow_uri.uri import (
    SignedURIManager,
    SignedURIRequest,
    URIValue,
    URIValues,
)
from arrow_uri.validity import ValidityCode, ValidityResult

from .identity import IdentityStore
from .ipc import EventServer, IpcEndpoint
from .product import Product

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True, kw_only=True)
class LakeArrowOperation:
    """Map one public Arrow operation to existing Lake Terminal IPC.

    Attributes:
        name: Public Arrow operation name.
        ipc_operation: Existing IPC operation name.
        destructive: Whether the operation mutates state destructively.

    """

    name: str
    ipc_operation: str
    destructive: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class LakeArrowResult:
    """Result and trusted context for one Arrow invocation.

    Attributes:
        response: Existing Lake Terminal IPC response text.
        context: Trusted Arrow request context.

    """

    response: str
    context: RequestContext


class LakeArrow:
    """Expose existing Lake Terminal control through signed Arrow URIs.

    Attributes:
        operations: Public Arrow operations mapped to existing IPC.

    """

    operations: Final[dict[str, LakeArrowOperation]] = {
        "daemon.ping": LakeArrowOperation(
            name="daemon.ping",
            ipc_operation="ping",
        ),
        "daemon.stop": LakeArrowOperation(
            name="daemon.stop",
            ipc_operation="stop",
            destructive=True,
        ),
        "buffer.read": LakeArrowOperation(
            name="buffer.read",
            ipc_operation="buffer",
        ),
        "conpty.output": LakeArrowOperation(
            name="conpty.output",
            ipc_operation="conpty-output",
        ),
        "conpty.write": LakeArrowOperation(
            name="conpty.write",
            ipc_operation="conpty-write",
        ),
        "conpty.kill": LakeArrowOperation(
            name="conpty.kill",
            ipc_operation="conpty-kill",
            destructive=True,
        ),
        "conpty.alive": LakeArrowOperation(
            name="conpty.alive",
            ipc_operation="conpty-alive",
        ),
    }

    def __init__(self, *, home: Path) -> None:
        """Create the Lake Terminal Arrow adapter.

        Args:
            home: User home directory.

        """
        self._home: Path = home
        endpoint: IpcEndpoint = IpcEndpoint.for_home(home=home)
        domain_key: bytes = sha256(
            data=endpoint.authkey + b"\0lake-terminal-arrow-v1",
        ).digest()
        self._manager: SignedURIManager = SignedURIManager(
            scheme=Product.url_scheme,
            issuer="lake-terminal",
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
        """Create a signed, expiring Lake Terminal control URI.

        Args:
            operation: Public Arrow operation.
            values: Scalar operation arguments.
            subject: Authenticated/local subject label.
            ttl: Optional validity duration.

        Returns:
            Signed Lake Terminal URI.

        Raises:
            KeyError: When the operation is not exposed through Arrow.

        """
        self._operation(name=operation)
        return self._manager.create(
            operation=operation,
            values=values,
            subject=subject,
            ttl=ttl,
        )

    def execute_uri(self, *, uri: str) -> LakeArrowResult:
        """Validate and execute one signed Lake Terminal URI.

        Args:
            uri: Signed Arrow URI.

        Returns:
            Existing Lake Terminal IPC response and trusted Arrow context.

        """
        request: SignedURIRequest = self._manager.validate(uri=uri)
        operation: LakeArrowOperation = self._operation(
            name=request.operation,
        )
        values: dict[str, str] = self._ipc_values(values=request.values)
        response: str = EventServer.request(
            home=self._home,
            operation=operation.ipc_operation,
            values=values,
        )
        return LakeArrowResult(
            response=response,
            context=self._context(
                request=request,
                operation=operation,
            ),
        )

    @classmethod
    def _operation(cls, *, name: str) -> LakeArrowOperation:
        """Resolve an exposed Arrow operation.

        Args:
            name: Public Arrow operation name.

        Returns:
            The exposed operation mapping.

        Raises:
            KeyError: When the operation is not exposed through Arrow.

        """
        try:
            return cls.operations[name]
        except KeyError as error:
            msg: str = (
                f"Lake Terminal Arrow operation {name!r} is not exposed."
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
        operation: LakeArrowOperation,
    ) -> RequestContext:
        """Build the trusted context for one validated request.

        Args:
            request: Validated signed URI request.
            operation: Resolved exposed operation.

        Returns:
            The trusted Arrow request context.

        """
        evidence: Evidence = Evidence(
            kind=EvidenceKind.SIGNATURE,
            name="lake-terminal-uri",
            value=request.request_id,
            trust=TrustLevel.CRYPTOGRAPHICALLY_VERIFIED,
            issuer="lake-terminal",
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
                transport="arrow-uri",
                session_id=request.subject,
            ),
            principal=principal,
            security=SecurityContext(
                authenticated=True,
                principal=request.subject,
                authentication_method="signed-arrow-uri",
                capabilities=frozenset({operation.name}),
                expires_at=request.expires_at,
            ),
            validity=ValidityResult(
                valid=True,
                code=ValidityCode.VALID,
                reason="Signed Arrow URI validated.",
            ),
            trace=Trace(trace_id=request.trace_id),
            deadline=Deadline(at=request.expires_at),
        )
