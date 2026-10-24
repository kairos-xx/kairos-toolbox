# Copyright (c) 2026 Project contributors.
"""Issue, track and redeem signed URIs that each run one district command."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from hashlib import sha256
from typing import TYPE_CHECKING, Final, final

from bowman.errors import AuthorizationError
from bowman.security import NonceStore
from bowman.uri import SignedURIManager, SignedURIRequest

from .district_commands import (
    CommandConstants,
    CommandError,
    CommandSpec,
    Danger,
    DistrictCommands,
)
from .identity import IdentityStore
from .ipc import IpcEndpoint
from .product import Product

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from pathlib import Path

    from .json_config import JsonValue
    from .widgets import CommandResult


class UriState(StrEnum):
    """Say whether a URI can still be used.

    Attributes:
        active: It can be run.
        used: It was run and is spent.
        expired: Its lifetime ended.
        revoked: Its owner cancelled it.

    """

    active = "active"
    used = "used"
    expired = "expired"
    revoked = "revoked"


@final
class UriConstants:
    """Group URI-registry constants.

    Attributes:
        prefix: Operation-name prefix of district-command URIs.
        subject: Subject recorded in each signature.
        domain: Label that separates this signing key from every other.
        minimum_ttl: Shortest lifetime, in seconds.
        maximum_ttl: Longest lifetime, in seconds.
        retained: Most tickets kept in memory.

    """

    prefix: Final[str] = "district."
    subject: Final[str] = "web-console"
    domain: Final[bytes] = b"\0district-web-uri-v1"
    minimum_ttl: Final[int] = 30
    maximum_ttl: Final[int] = 604_800
    retained: Final[int] = 100


@dataclass(frozen=True, slots=True, kw_only=True)
class IssuedUri:
    """A ticket as it is shown to the person who issued it.

    Attributes:
        id: Stable identifier of the ticket.
        uri: The signed URI itself.
        command: Name of the command the ticket runs.
        title: Name of that command shown to people.
        values: The fixed input the command runs with.
        destructive: Whether the command removes something.
        created_at: When the ticket was issued.
        expires_at: When the ticket stops working.
        state: Whether it can still be used.

    """

    id: str
    uri: str
    command: str
    title: str
    values: dict[str, str]
    destructive: bool
    created_at: datetime
    expires_at: datetime
    state: UriState

    def as_json(self) -> dict[str, JsonValue]:
        """Render the ticket.

        Returns:
            A JSON-compatible mapping.

        """
        values: dict[str, JsonValue] = {**self.values}
        return {
            "id": self.id,
            "uri": self.uri,
            "command": self.command,
            "title": self.title,
            "values": values,
            "destructive": self.destructive,
            "createdAt": self.created_at.isoformat(),
            "expiresAt": self.expires_at.isoformat(),
            "state": self.state.value,
        }


@dataclass(slots=True, kw_only=True)
class _Record:
    """Hold one ticket and the facts that change after it is issued.

    Attributes:
        id: Stable identifier of the ticket.
        uri: The signed URI.
        command: Command the ticket runs.
        title: Name of that command.
        values: Fixed input.
        destructive: Whether the command removes something.
        created_at: When it was issued.
        expires_at: When it stops working.
        used: Whether it was run.
        revoked: Whether it was cancelled.

    """

    id: str
    uri: str
    command: str
    title: str
    values: dict[str, str]
    destructive: bool
    created_at: datetime
    expires_at: datetime
    used: bool = field(default=False)
    revoked: bool = field(default=False)


@final
class UriRegistry:
    """Issue single-use, expiring URIs that each run one district command.

    Tickets live only in this object, in memory: a stored bearer URI would be
    a secret at rest. Each URI is signed with a key derived for this purpose
    alone, so it cannot be mistaken for any other kind of signed request.
    """

    def __init__(
        self,
        *,
        commands: DistrictCommands,
        home: Path,
        clock: Callable[[], datetime],
    ) -> None:
        """Create the registry.

        Args:
            commands: The command layer tickets run through.
            home: Home directory holding the district state.
            clock: Source of the current time, injected so expiry is
                reproducible.

        """
        self._commands: DistrictCommands = commands
        self._clock: Callable[[], datetime] = clock
        self._records: dict[str, _Record] = {}
        self._manager: SignedURIManager = SignedURIManager(
            scheme=Product.url_scheme,
            issuer="district",
            audience=IdentityStore(home=home).host_id(),
            key=sha256(
                data=IpcEndpoint.for_home(home=home).authkey
                + UriConstants.domain,
            ).digest(),
            nonces=NonceStore(),
        )

    def create(
        self,
        *,
        command: str,
        values: dict[str, str],
        ttl_seconds: int,
        confirmed: bool,
    ) -> IssuedUri:
        """Issue a ticket that runs ``command`` once with fixed input.

        Args:
            command: Name of the district command.
            values: Input the command will run with.
            ttl_seconds: Requested lifetime; kept within the allowed range.
            confirmed: Whether the person confirmed a destructive command.

        Returns:
            The issued ticket.

        Raises:
            CommandError: When the command is unknown, its input is invalid,
                or it is destructive and was not confirmed.

        """
        spec: CommandSpec
        clean: dict[str, str]
        spec, clean = self._commands.prepare(name=command, values=values)
        destructive: bool = spec.danger is Danger.destructive
        if destructive and not confirmed:
            raise CommandError(
                message=f"{spec.title} needs your confirmation.",
                status=CommandConstants.conflict,
            )
        lifetime: int = max(
            UriConstants.minimum_ttl,
            min(UriConstants.maximum_ttl, ttl_seconds),
        )
        uri: str = self._manager.create(
            operation=UriConstants.prefix + command,
            values=dict(clean),
            subject=UriConstants.subject,
            ttl=timedelta(seconds=lifetime),
        )
        request: SignedURIRequest = self._manager.validate(
            uri=uri,
            consume=False,
        )
        now: datetime = self._clock()
        self._records[request.request_id] = _Record(
            id=request.request_id,
            uri=uri,
            command=command,
            title=spec.title,
            values=clean,
            destructive=destructive,
            created_at=now,
            expires_at=now + timedelta(seconds=lifetime),
        )
        self._trim()
        return self.get(uri_id=request.request_id)

    def items(self) -> tuple[IssuedUri, ...]:
        """List every ticket, newest first.

        Returns:
            The tickets with their current state.

        """
        return tuple(
            self._view(record=record)
            for record in sorted(
                self._records.values(),
                key=lambda item: item.created_at,
                reverse=True,
            )
        )

    def get(self, *, uri_id: str) -> IssuedUri:
        """Look up one ticket.

        Args:
            uri_id: Ticket identifier.

        Returns:
            The ticket with its current state.

        Raises:
            CommandError: When there is no such ticket.

        """
        return self._view(record=self._record(uri_id=uri_id))

    def revoke(self, *, uri_id: str) -> IssuedUri:
        """Cancel a ticket so it can no longer run.

        Args:
            uri_id: Ticket identifier.

        Returns:
            The ticket, now revoked unless it was already spent.

        Raises:
            CommandError: When there is no such ticket.

        """
        record: _Record = self._record(uri_id=uri_id)
        record.revoked = True
        return self._view(record=record)

    def run(self, *, uri_id: str) -> CommandResult:
        """Run a ticket's command; the ticket is spent whatever the outcome.

        Args:
            uri_id: Ticket identifier.

        Returns:
            What the command produced.

        Raises:
            CommandError: When the ticket cannot run, its signature does not
                verify, or the command fails.

        """
        record: _Record = self._record(uri_id=uri_id)
        view: IssuedUri = self._view(record=record)
        if view.state is not UriState.active:
            raise CommandError(
                message=f"This URI is {view.state.value} and cannot run.",
                status=CommandConstants.conflict,
            )
        self._verify(uri=record.uri, consume=True)
        record.used = True
        return self._commands.run(
            name=record.command,
            values=record.values,
            confirmed=True,
        )

    def redeem(self, *, uri: str) -> CommandResult:
        """Verify and run a URI that was pasted in.

        Args:
            uri: The signed URI.

        Returns:
            What the command produced.

        Raises:
            CommandError: When the URI is forged, spent, expired or is not a
                district URI, or the command fails.

        """
        request: SignedURIRequest = self._verify(uri=uri, consume=False)
        if not request.operation.startswith(UriConstants.prefix):
            raise CommandError(
                message="That URI does not run a district command.",
                status=CommandConstants.bad_request,
            )
        if request.request_id in self._records:
            return self.run(uri_id=request.request_id)
        _ = self._verify(uri=uri, consume=True)
        return self._commands.run(
            name=request.operation[len(UriConstants.prefix) :],
            values={
                name: str(object=value)
                for name, value in request.values.items()
            },
            confirmed=True,
        )

    def _verify(self, *, uri: str, consume: bool) -> SignedURIRequest:
        """Check a URI's signature, lifetime and replay state.

        Args:
            uri: The signed URI.
            consume: Whether to spend it.

        Returns:
            The verified request.

        Raises:
            CommandError: When the URI does not verify; a replay is a 409 and
                anything else a 400.

        """
        try:
            return self._manager.validate(uri=uri, consume=consume)
        except AuthorizationError as error:
            replay: bool = "replay" in str(object=error).lower()
            raise CommandError(
                message=(
                    "This URI was already used."
                    if replay
                    else "That URI is not valid."
                ),
                status=(
                    CommandConstants.conflict
                    if replay
                    else CommandConstants.bad_request
                ),
            ) from error

    def _record(self, *, uri_id: str) -> _Record:
        """Find a ticket's record.

        Args:
            uri_id: Ticket identifier.

        Returns:
            The record.

        Raises:
            CommandError: When there is no such ticket.

        """
        record: _Record | None = self._records.get(uri_id)
        if record is None:
            raise CommandError(
                message="There is no such URI.",
                status=CommandConstants.not_found,
            )
        return record

    def _view(self, *, record: _Record) -> IssuedUri:
        """Describe a record, working out its current state.

        Args:
            record: The record.

        Returns:
            The ticket as shown to the person.

        """
        if record.revoked:
            state: UriState = UriState.revoked
        elif record.used:
            state = UriState.used
        elif self._clock() >= record.expires_at:
            state = UriState.expired
        else:
            state = UriState.active
        return IssuedUri(
            id=record.id,
            uri=record.uri,
            command=record.command,
            title=record.title,
            values=dict(record.values),
            destructive=record.destructive,
            created_at=record.created_at,
            expires_at=record.expires_at,
            state=state,
        )

    def _trim(self) -> None:
        """Forget the oldest tickets beyond the retention limit."""
        surplus: int = len(self._records) - UriConstants.retained
        if surplus <= 0:
            return
        for record in sorted(
            self._records.values(),
            key=lambda item: item.created_at,
        )[:surplus]:
            del self._records[record.id]
