# Copyright (c) 2026 Project contributors.
"""Tests for device pairing: signed tokens, the key file and LAN peers."""

from __future__ import annotations

from base64 import urlsafe_b64encode
from dataclasses import dataclass
from hashlib import sha256
from hmac import new as new_mac
from ipaddress import IPv4Network
from socket import AF_INET, AF_INET6
from stat import S_IMODE
from typing import TYPE_CHECKING, Final, final

import pytest
from district import pairing
from district.pairing import (
    LanAddress,
    LanNetwork,
    PairingAuth,
    PairingConstants,
    PairingKey,
    PairingKind,
    PeerKind,
)

from .helpers import check, expect

if TYPE_CHECKING:
    from pathlib import Path


@final
class Constants:
    """Group test constants.

    Attributes:
        key: A fixed signing key.
        lan: The host's address on a fake home network.
        network: The fake home network.

    """

    key: Final[bytes] = b"k" * PairingConstants.key_bytes
    lan: Final[str] = "192.168.1.20"
    network: Final[IPv4Network] = IPv4Network(address="192.168.1.0/24")


@final
class Clock:
    """A clock the test moves by hand.

    Attributes:
        now: Current time in seconds.

    """

    def __init__(self) -> None:
        """Start at a fixed instant."""
        self.now: float = 1_800_000_000.0

    def __call__(self) -> float:
        """Read the clock.

        Returns:
            The current time.

        """
        return self.now


def _auth(*, clock: Clock | None = None) -> PairingAuth:
    """Create an authority with the fixed key.

    Args:
        clock: Clock to read, a fresh one when omitted.

    Returns:
        The authority.

    """
    return PairingAuth(key=Constants.key, clock=clock or Clock())


def _sign(*, body: str) -> str:
    """Sign a token body the way the documented format specifies.

    Used to forge tokens that are authentic except for one bad field.

    Args:
        body: The ``kind.expiry.nonce`` text.

    Returns:
        The truncated, URL-safe HMAC of the body under the fixed key.

    """
    digest: bytes = new_mac(
        key=Constants.key,
        msg=body.encode(encoding="ascii"),
        digestmod=sha256,
    ).digest()[: PairingConstants.mac_bytes]
    return urlsafe_b64encode(s=digest).decode(encoding="ascii").rstrip("=")


def test_a_token_can_be_redeemed_exactly_once() -> None:
    """Pairing tokens are single use."""
    auth: PairingAuth = _auth()
    token: str = auth.mint(kind=PairingKind.lan)
    expect(
        actual=auth.redeem(token=token, kinds=frozenset({PairingKind.lan})),
        expected=PairingKind.lan,
    )
    expect(
        actual=auth.redeem(token=token, kinds=frozenset({PairingKind.lan})),
        expected=None,
    )


def test_a_token_of_the_wrong_kind_is_refused_without_being_spent() -> None:
    """A LAN token tried at the wrong place is still good at the right one."""
    auth: PairingAuth = _auth()
    token: str = auth.mint(kind=PairingKind.lan)
    expect(
        actual=auth.redeem(token=token, kinds=frozenset({PairingKind.local})),
        expected=None,
    )
    expect(
        actual=auth.redeem(token=token, kinds=frozenset({PairingKind.lan})),
        expected=PairingKind.lan,
    )


def test_tokens_expire() -> None:
    """A pairing token is useless after its lifetime."""
    clock: Clock = Clock()
    auth: PairingAuth = _auth(clock=clock)
    token: str = auth.mint(kind=PairingKind.local)
    clock.now += PairingConstants.pairing_seconds + 1
    expect(
        actual=auth.redeem(token=token, kinds=frozenset({PairingKind.local})),
        expected=None,
    )
    handoff: str = auth.mint(kind=PairingKind.handoff)
    clock.now += PairingConstants.handoff_seconds - 1
    expect(
        actual=auth.redeem(
            token=handoff,
            kinds=frozenset({PairingKind.handoff}),
        ),
        expected=PairingKind.handoff,
    )


def test_forged_and_malformed_tokens_are_refused() -> None:
    """Only a token signed with this key is accepted."""
    auth: PairingAuth = _auth()
    other: PairingAuth = PairingAuth(key=b"x" * PairingConstants.key_bytes)
    token: str = auth.mint(kind=PairingKind.lan)
    kinds: frozenset[PairingKind] = frozenset(PairingKind)
    foreign: str = other.mint(kind=PairingKind.lan)
    letter, expiry, nonce, mac = token.split(sep=".")
    swapped: str = "B" if mac.endswith("A") else "A"
    for bad in (
        foreign,
        f"{letter}.{expiry}.{nonce}.{mac[:-1]}{swapped}",
        f"h.{expiry}.{nonce}.{mac}",
        f"{letter}.{expiry}.{nonce}",
        f"{letter}.{expiry}.{nonce}.{mac}.extra",
        "",
        "not a token",
    ):
        expect(actual=auth.redeem(token=bad, kinds=kinds), expected=None)
    for body in (f"z.{expiry}.{nonce}", f"n.!!.{nonce}"):
        signed: str = f"{body}.{_sign(body=body)}"
        expect(actual=auth.redeem(token=signed, kinds=kinds), expected=None)


def test_sessions_are_not_pairing_tokens_and_back() -> None:
    """A session cookie cannot pair, and a pairing token is no session."""
    clock: Clock = Clock()
    auth: PairingAuth = _auth(clock=clock)
    session: str = auth.mint(kind=PairingKind.session)
    expect(actual=auth.valid_session(cookie=session), expected=True)
    expect(
        actual=auth.redeem(token=session, kinds=frozenset(PairingKind)),
        expected=None,
    )
    expect(
        actual=auth.valid_session(cookie=auth.mint(kind=PairingKind.lan)),
        expected=False,
    )
    expect(actual=auth.valid_session(cookie=None), expected=False)
    expect(actual=auth.valid_session(cookie=""), expected=False)
    expect(actual=auth.valid_session(cookie=session + "x"), expected=False)
    clock.now += PairingConstants.session_seconds + 1
    expect(actual=auth.valid_session(cookie=session), expected=False)


def test_the_spent_token_memory_has_a_ceiling_and_forgets_the_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flood of redemptions is capped, and old ones stop counting.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(
        target=PairingConstants,
        name="burned_limit",
        value=2,
    )
    clock: Clock = Clock()
    auth: PairingAuth = _auth(clock=clock)
    local: frozenset[PairingKind] = frozenset({PairingKind.local})

    def spend() -> PairingKind | None:
        """Mint a fresh local token and try to redeem it.

        Returns:
            The redeemed kind, or ``None`` when it was refused.

        """
        return auth.redeem(
            token=auth.mint(kind=PairingKind.local),
            kinds=local,
        )

    expect(
        actual=[spend(), spend(), spend()],
        expected=[PairingKind.local, PairingKind.local, None],
    )
    clock.now += PairingConstants.pairing_seconds + 1
    expect(actual=spend(), expected=PairingKind.local)


def test_the_key_file_is_created_private_and_reused(tmp_path: Path) -> None:
    """Every process of one user signs with the same private key.

    Args:
        tmp_path: Pytest temporary directory used as the home.

    """
    first: bytes = PairingKey.load(home=tmp_path)
    expect(actual=len(first), expected=PairingConstants.key_bytes)
    files: list[Path] = list(tmp_path.rglob(pattern=PairingConstants.key_file))
    expect(actual=len(files), expected=1)
    expect(actual=S_IMODE(files[0].stat().st_mode), expected=0o600)
    expect(actual=PairingKey.load(home=tmp_path), expected=first)
    minted: str = PairingAuth.for_home(home=tmp_path).mint(
        kind=PairingKind.lan,
    )
    expect(
        actual=PairingAuth.for_home(home=tmp_path).redeem(
            token=minted,
            kinds=frozenset({PairingKind.lan}),
        ),
        expected=PairingKind.lan,
    )


def test_a_damaged_key_file_is_replaced(tmp_path: Path) -> None:
    """A truncated key is regenerated rather than used.

    Args:
        tmp_path: Pytest temporary directory used as the home.

    """
    _ = PairingKey.load(home=tmp_path)
    target: Path = next(tmp_path.rglob(pattern=PairingConstants.key_file))
    _ = target.write_bytes(data=b"short")
    key: bytes = PairingKey.load(home=tmp_path)
    expect(actual=len(key), expected=PairingConstants.key_bytes)
    expect(actual=target.read_bytes(), expected=key)
    expect(actual=S_IMODE(target.stat().st_mode), expected=0o600)


@dataclass(frozen=True, slots=True)
class FakeAddress:
    """Mimic psutil's ``snicaddr`` fields the module reads.

    Attributes:
        family: Address family number.
        address: Textual address.
        netmask: Textual netmask.

    """

    family: int
    address: str
    netmask: str | None


@dataclass(frozen=True, slots=True)
class FakeStats:
    """Mimic psutil's ``snicstats`` fields the module reads.

    Attributes:
        isup: Whether the interface is up.

    """

    isup: bool


def test_only_rfc1918_ipv4_addresses_are_listed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loopback, link-local, public, IPv6 and downed interfaces are skipped.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    interfaces: dict[str, list[FakeAddress]] = {
        "lo0": [
            FakeAddress(
                family=AF_INET,
                address="127.0.0.1",
                netmask="255.0.0.0",
            ),
        ],
        "en0": [
            FakeAddress(
                family=AF_INET6,
                address="fe80::1",
                netmask="ffff:ffff:ffff:ffff::",
            ),
            FakeAddress(
                family=AF_INET,
                address=Constants.lan,
                netmask="255.255.255.0",
            ),
            FakeAddress(family=AF_INET, address="192.168.1.21", netmask=None),
        ],
        "utun0": [
            FakeAddress(
                family=AF_INET,
                address="169.254.9.9",
                netmask="255.255.0.0",
            ),
        ],
        "ppp0": [
            FakeAddress(
                family=AF_INET,
                address="203.0.113.7",
                netmask="255.255.255.0",
            ),
        ],
        "en5": [
            FakeAddress(
                family=AF_INET,
                address="10.1.2.3",
                netmask="255.0.0.0",
            ),
        ],
        "bridge0": [
            FakeAddress(
                family=AF_INET,
                address="172.16.0.5",
                netmask="255.255.0.0",
            ),
        ],
    }
    monkeypatch.setattr(
        target=pairing,
        name="net_if_addrs",
        value=lambda: interfaces,
    )
    monkeypatch.setattr(
        target=pairing,
        name="net_if_stats",
        value=lambda: {
            "en5": FakeStats(isup=False),
            "en0": FakeStats(isup=True),
        },
    )
    found: tuple[LanAddress, ...] = LanNetwork.addresses()
    expect(
        actual=[(item.interface, item.address) for item in found],
        expected=[("en0", Constants.lan), ("bridge0", "172.16.0.5")],
    )
    expect(actual=found[0].network, expected=Constants.network)
    expect(actual=LanNetwork.primary(), expected=found[0])


def test_the_real_machine_lists_addresses_without_failing() -> None:
    """Whatever the host's network, listing it returns a clean tuple."""
    found: tuple[LanAddress, ...] = LanNetwork.addresses()
    for item in found:
        check(condition=item.network.is_private, reason=str(object=item))
    expect(
        actual=LanNetwork.primary(),
        expected=found[0] if found else None,
    )


def _lan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the host sits on one home network.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(
        target=LanNetwork,
        name="addresses",
        value=staticmethod(
            lambda: (
                LanAddress(
                    interface="en0",
                    address=Constants.lan,
                    network=Constants.network,
                ),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("peer", "expected"),
    [
        ("127.0.0.1", PeerKind.loopback),
        ("::1", PeerKind.loopback),
        ("::ffff:127.0.0.1", PeerKind.loopback),
        ("192.168.1.77", PeerKind.lan),
        ("::ffff:192.168.1.77", PeerKind.lan),
        ("192.168.2.5", PeerKind.other),
        ("10.0.0.1", PeerKind.other),
        ("8.8.8.8", PeerKind.other),
        ("2001:db8::1", PeerKind.other),
        ("testclient", PeerKind.other),
        ("", PeerKind.other),
        (None, PeerKind.other),
    ],
)
def test_peers_are_classified_by_where_they_connect_from(
    monkeypatch: pytest.MonkeyPatch,
    peer: str | None,
    expected: PeerKind,
) -> None:
    """Loopback, same-network and everything else are told apart.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        peer: Source address of the connection.
        expected: Expected classification.

    """
    _lan(monkeypatch=monkeypatch)
    expect(actual=LanNetwork.classify(peer=peer), expected=expected)
