# Copyright (c) 2026 Project contributors.
"""Tests for issuing, listing, revoking and redeeming district URIs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, final, override

import pytest
from district.core import (
    District,
    SessionRepository,
)
from district.district_commands import (
    CommandError,
    DistrictCommands,
)
from district.models import SessionEntry
from district.uri_registry import (
    IssuedUri,
    UriRegistry,
    UriState,
)

from .helpers import check, expect

if TYPE_CHECKING:
    from pathlib import Path

    from district.widgets import CommandResult


@final
class Constants:
    """Group test constants.

    Attributes:
        start: The fixed moment the clock starts at.
        ttl: Lifetime requested for tickets, in seconds.
        bad_request: Status for invalid input.
        conflict: Status for a ticket that cannot run.
        not_found: Status for an unknown ticket or command.
        prefix: Start of every district URI.
        payload_offset: A position inside the signed payload.

    """

    start: Final[datetime] = datetime(year=2026, month=1, day=1, tzinfo=UTC)
    ttl: Final[int] = 600
    bad_request: Final[int] = 400
    conflict: Final[int] = 409
    not_found: Final[int] = 404
    prefix: Final[str] = "district://request/v1/"
    payload_offset: Final[int] = 12


class _Clock:
    """Hold a time that only moves when a test says so.

    Attributes:
        now: The current moment.

    """

    def __init__(self) -> None:
        """Start at the fixed moment."""
        self.now: datetime = Constants.start

    def read(self) -> datetime:
        """Return the current moment.

        Returns:
            The moment.

        """
        return self.now


class _Service(District):
    """Record the district operations a ticket performs.

    Attributes:
        killed: Ids of the lakes that were stopped.

    """

    def __init__(self, *, repository: SessionRepository) -> None:
        """Create the recording service.

        Args:
            repository: Session repository.

        """
        super().__init__(repository=repository)
        self.killed: list[str] = []

    @override
    def list_entries(self) -> tuple[SessionEntry, ...]:
        """List stored sessions without consulting tmux.

        Returns:
            Every stored session.

        """
        return self.repository.read()

    @override
    def kill(self, *, session_id: str) -> None:
        """Record a kill.

        Args:
            session_id: Session id.

        """
        self.killed.append(session_id)


def _setup(*, home: Path) -> tuple[_Service, _Clock, UriRegistry]:
    """Build a registry over a recording service and a manual clock.

    Args:
        home: Scratch home directory.

    Returns:
        The service, the clock and the registry.

    """
    service: _Service = _Service(repository=SessionRepository(home=home))
    service.repository.add(
        entry=SessionEntry(
            id="a1b2c3",
            command="district a1b2c3",
            session="x",
        ),
    )
    clock: _Clock = _Clock()
    registry: UriRegistry = UriRegistry(
        commands=DistrictCommands(service=service, home=home),
        home=home,
        clock=clock.read,
    )
    return service, clock, registry


def _kill_ticket(*, registry: UriRegistry, confirmed: bool) -> IssuedUri:
    """Issue a ticket that stops district ``a1b2c3``.

    Args:
        registry: The registry.
        confirmed: Whether the person confirmed the destructive command.

    Returns:
        The issued ticket.

    """
    return registry.create(
        command="kill",
        values={"session": "a1b2c3"},
        ttl_seconds=Constants.ttl,
        confirmed=confirmed,
    )


def test_a_new_ticket_is_an_active_signed_uri(tmp_path: Path) -> None:
    """The ticket carries the product scheme and expires after its ttl.

    Args:
        tmp_path: Scratch home directory.

    """
    _, _, registry = _setup(home=tmp_path)
    issued: IssuedUri = registry.create(
        command="health",
        values={},
        ttl_seconds=Constants.ttl,
        confirmed=False,
    )
    check(
        condition=issued.uri.startswith(Constants.prefix),
        reason="the uri should use the product scheme",
    )
    expect(actual=issued.state, expected=UriState.active)
    expect(
        actual=issued.expires_at,
        expected=Constants.start + timedelta(seconds=Constants.ttl),
    )


def test_a_destructive_ticket_cannot_be_issued_without_confirmation(
    tmp_path: Path,
) -> None:
    """Consent is given when the ticket is minted, not when it is run.

    Args:
        tmp_path: Scratch home directory.

    """
    _, _, registry = _setup(home=tmp_path)
    with pytest.raises(expected_exception=CommandError) as raised:
        _ = _kill_ticket(registry=registry, confirmed=False)
    expect(actual=raised.value.status, expected=Constants.conflict)
    expect(actual=registry.items(), expected=())


def test_a_ticket_for_an_invalid_command_is_never_issued(
    tmp_path: Path,
) -> None:
    """Input is validated up front so a bad ticket cannot exist.

    Args:
        tmp_path: Scratch home directory.

    """
    _, _, registry = _setup(home=tmp_path)
    with pytest.raises(expected_exception=CommandError) as raised:
        _ = registry.create(
            command="exec",
            values={"session": "a1b2c3"},
            ttl_seconds=Constants.ttl,
            confirmed=False,
        )
    expect(actual=raised.value.status, expected=Constants.bad_request)
    with pytest.raises(expected_exception=CommandError) as unknown:
        _ = registry.create(
            command="nope",
            values={},
            ttl_seconds=Constants.ttl,
            confirmed=False,
        )
    expect(actual=unknown.value.status, expected=Constants.not_found)


def test_running_a_ticket_performs_the_command_once(tmp_path: Path) -> None:
    """A ticket works the first time and is spent afterwards.

    Args:
        tmp_path: Scratch home directory.

    """
    service, _, registry = _setup(home=tmp_path)
    issued: IssuedUri = _kill_ticket(registry=registry, confirmed=True)
    result: CommandResult = registry.run(uri_id=issued.id)
    expect(actual=result.ok, expected=True)
    expect(actual=service.killed, expected=["a1b2c3"])
    expect(actual=registry.get(uri_id=issued.id).state, expected=UriState.used)
    with pytest.raises(expected_exception=CommandError) as raised:
        _ = registry.run(uri_id=issued.id)
    expect(actual=raised.value.status, expected=Constants.conflict)
    expect(actual=service.killed, expected=["a1b2c3"])


def test_a_revoked_ticket_cannot_run(tmp_path: Path) -> None:
    """Revoking takes effect immediately.

    Args:
        tmp_path: Scratch home directory.

    """
    service, _, registry = _setup(home=tmp_path)
    issued: IssuedUri = _kill_ticket(registry=registry, confirmed=True)
    registry.revoke(uri_id=issued.id)
    expect(
        actual=registry.get(uri_id=issued.id).state,
        expected=UriState.revoked,
    )
    with pytest.raises(expected_exception=CommandError) as raised:
        _ = registry.run(uri_id=issued.id)
    expect(actual=raised.value.status, expected=Constants.conflict)
    expect(actual=service.killed, expected=[])


def test_an_expired_ticket_cannot_run(tmp_path: Path) -> None:
    """Once the lifetime passes the ticket is refused.

    Args:
        tmp_path: Scratch home directory.

    """
    service, clock, registry = _setup(home=tmp_path)
    issued: IssuedUri = _kill_ticket(registry=registry, confirmed=True)
    clock.now += timedelta(seconds=Constants.ttl + 1)
    expect(
        actual=registry.get(uri_id=issued.id).state,
        expected=UriState.expired,
    )
    with pytest.raises(expected_exception=CommandError) as raised:
        _ = registry.run(uri_id=issued.id)
    expect(actual=raised.value.status, expected=Constants.conflict)
    expect(actual=service.killed, expected=[])


def test_tickets_are_listed_newest_first(tmp_path: Path) -> None:
    """The list shows the most recently issued ticket on top.

    Args:
        tmp_path: Scratch home directory.

    """
    _, clock, registry = _setup(home=tmp_path)
    first: IssuedUri = registry.create(
        command="health",
        values={},
        ttl_seconds=Constants.ttl,
        confirmed=False,
    )
    clock.now += timedelta(seconds=5)
    second: IssuedUri = registry.create(
        command="storage",
        values={},
        ttl_seconds=Constants.ttl,
        confirmed=False,
    )
    expect(
        actual=[item.id for item in registry.items()],
        expected=[second.id, first.id],
    )


def test_a_pasted_uri_is_checked_and_redeemed_once(tmp_path: Path) -> None:
    """A URI from outside is verified by signature, and cannot be replayed.

    Args:
        tmp_path: Scratch home directory.

    """
    service, _, registry = _setup(home=tmp_path)
    issued: IssuedUri = _kill_ticket(registry=registry, confirmed=True)
    first: CommandResult = registry.redeem(uri=issued.uri)
    expect(actual=first.ok, expected=True)
    expect(actual=service.killed, expected=["a1b2c3"])
    with pytest.raises(expected_exception=CommandError):
        _ = registry.redeem(uri=issued.uri)
    expect(actual=service.killed, expected=["a1b2c3"])


def test_a_tampered_uri_is_refused(tmp_path: Path) -> None:
    """Changing any character breaks the signature.

    Args:
        tmp_path: Scratch home directory.

    """
    service, _, registry = _setup(home=tmp_path)
    issued: IssuedUri = _kill_ticket(registry=registry, confirmed=True)
    at: int = len(Constants.prefix) + Constants.payload_offset
    swapped: str = "A" if issued.uri[at] != "A" else "B"
    forged: str = issued.uri[:at] + swapped + issued.uri[at + 1 :]
    with pytest.raises(expected_exception=CommandError) as raised:
        _ = registry.redeem(uri=forged)
    expect(actual=raised.value.status, expected=Constants.bad_request)
    expect(actual=service.killed, expected=[])
