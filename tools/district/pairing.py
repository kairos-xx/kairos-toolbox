# Copyright (c) 2026 Project contributors.
"""Device pairing for the remote web console.

A phone gets a console by scanning a QR code that carries a *pairing token*.
Tokens are HMAC-signed with a key that lives in the user's state directory,
so the ``district`` command that prints the QR code and the server that redeems
it need no channel between them beyond that file.

Four kinds of credential exist:

* ``lan`` tokens are printed in the QR code and are redeemable only by a
  device on the same local network as the host. Traffic that arrives through
  a charter reaches the server from the loopback interface, so it can never
  redeem one, however the token leaked.
* ``local`` tokens open the dashboard in a browser on the host itself.
* ``handoff`` tokens are minted by the server after a LAN device proved its
  proximity; they carry that device on to the public (charter) origin, where
  cookies cannot be shared.
* ``session`` values are what the browser keeps in its cookie afterwards.

Pairing and handoff tokens are single use and expire in minutes; a session
lasts :attr:`PairingConstants.session_seconds`. Deleting the key file revokes
everything at once.
"""

from __future__ import annotations

from base64 import urlsafe_b64encode
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from hmac import compare_digest
from hmac import new as new_mac
from ipaddress import IPv4Address, IPv4Network, IPv6Address, ip_address
from os import O_CREAT, O_EXCL, O_WRONLY, fdopen
from os import open as open_fd
from secrets import token_bytes, token_urlsafe
from socket import AF_INET
from threading import Lock
from time import time
from typing import TYPE_CHECKING, Final, final

from psutil import net_if_addrs, net_if_stats

from .product import state_directory

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@final
class PairingConstants:
    """Group pairing constants.

    Attributes:
        key_file: Name of the signing-key file in the state directory.
        key_bytes: Length of the signing key.
        mac_bytes: Length of the truncated HMAC carried by a token.
        nonce_bytes: Entropy of a token's unique part.
        pairing_seconds: How long a QR code or local link stays redeemable.
        handoff_seconds: How long a redirect to the public origin works.
        session_seconds: How long a paired browser stays signed in.
        cookie: Name of the session cookie.
        base: Radix of the encoded expiry time.
        parts: Dot-separated fields of a token.
        burned_limit: Most single-use nonces remembered at once.
        private_networks: The RFC 1918 ranges a home or office LAN uses.
            Python's ``is_private`` is wider (documentation and benchmark
            ranges count), which is wrong for deciding what a phone can
            reach.

    """

    key_file: Final[str] = "web-auth.key"
    key_bytes: Final[int] = 32
    mac_bytes: Final[int] = 16
    nonce_bytes: Final[int] = 9
    pairing_seconds: Final[float] = 300.0
    handoff_seconds: Final[float] = 60.0
    session_seconds: Final[float] = 12 * 3600.0
    cookie: Final[str] = "district_session"
    base: Final[int] = 36
    parts: Final[int] = 4
    burned_limit: Final[int] = 4096
    private_networks: Final[tuple[IPv4Network, ...]] = (
        IPv4Network(address="10.0.0.0/8"),
        IPv4Network(address="172.16.0.0/12"),
        IPv4Network(address="192.168.0.0/16"),
    )


class PairingUnavailableError(RuntimeError):
    """Report that a device cannot be paired right now."""


class PairingKind(StrEnum):
    """Name the credential kinds a token can carry.

    Attributes:
        local: Opens the dashboard in a browser on the host itself.
        lan: Printed in the QR code; redeemable only on the host's network.
        handoff: Carries a proven-nearby device on to the public origin.
        session: The signed-in browser's cookie value.

    """

    local = "l"
    lan = "n"
    handoff = "h"
    session = "s"


class PeerKind(StrEnum):
    """Classify where a connection comes from.

    Attributes:
        loopback: The host itself, or a charter agent running on it.
        lan: A device on one of the host's private networks.
        other: Anything else.

    """

    loopback = "loopback"
    lan = "lan"
    other = "other"


@dataclass(frozen=True, slots=True, kw_only=True)
class LanAddress:
    """Describe one private IPv4 address of the host.

    Attributes:
        interface: Network interface name.
        address: The host's address on that network.
        network: The network the address belongs to.

    """

    interface: str
    address: str
    network: IPv4Network


@final
class PairingKey:
    """Load, or create, the signing key in the state directory."""

    @classmethod
    def load(cls, *, home: Path) -> bytes:
        """Return the signing key, creating it on first use.

        The file is created private to the user; if two processes race to
        create it, the loser reads the winner's key.

        Args:
            home: User home directory.

        Returns:
            The key bytes.

        """
        path: Path = state_directory(home=home) / PairingConstants.key_file
        try:
            descriptor: int = open_fd(
                path=path,
                flags=O_WRONLY | O_CREAT | O_EXCL,
                mode=0o600,
            )
        except FileExistsError:
            existing: bytes = path.read_bytes()
            if len(existing) == PairingConstants.key_bytes:
                return existing
            return cls._replace(path=path)
        key: bytes = token_bytes(nbytes=PairingConstants.key_bytes)
        with fdopen(fd=descriptor, mode="wb") as stream:
            _ = stream.write(key)
        return key

    @staticmethod
    def _replace(*, path: Path) -> bytes:
        """Overwrite a damaged key file with a fresh key.

        Args:
            path: Key file.

        Returns:
            The new key.

        """
        key: bytes = token_bytes(nbytes=PairingConstants.key_bytes)
        _ = path.write_bytes(data=key)
        path.chmod(mode=0o600)
        return key


class PairingAuth:
    """Mint and verify pairing tokens and browser sessions."""

    def __init__(
        self,
        *,
        key: bytes,
        clock: Callable[[], float] = time,
    ) -> None:
        """Initialize the authority.

        Args:
            key: Signing key shared with every process that mints tokens.
            clock: Source of the current time in seconds.

        """
        self._key: Final[bytes] = key
        self._clock: Final[Callable[[], float]] = clock
        self._lock: Final[Lock] = Lock()
        self._burned: dict[str, float] = {}

    @classmethod
    def for_home(cls, *, home: Path) -> PairingAuth:
        """Create an authority that uses the home's persistent key.

        Args:
            home: User home directory.

        Returns:
            The authority.

        """
        return cls(key=PairingKey.load(home=home))

    def mint(self, *, kind: PairingKind, ttl: float | None = None) -> str:
        """Create a signed token.

        Args:
            kind: What the token grants.
            ttl: Lifetime in seconds; the kind's default when omitted.

        Returns:
            The token text, safe to put in a URL or a cookie.

        """
        seconds: float = self._lifetime(kind=kind) if ttl is None else ttl
        expiry: str = self._encode_time(value=int(self._clock() + seconds))
        nonce: str = token_urlsafe(nbytes=PairingConstants.nonce_bytes)
        body: str = f"{kind.value}.{expiry}.{nonce}"
        return f"{body}.{self._mac(body=body)}"

    def redeem(
        self,
        *,
        token: str,
        kinds: frozenset[PairingKind],
    ) -> PairingKind | None:
        """Verify a single-use token and spend it.

        Args:
            token: Token text from a URL.
            kinds: Kinds the caller is willing to accept.

        Returns:
            The token's kind, or ``None`` when it is forged, expired, of
            another kind, or already used.

        """
        verified: tuple[PairingKind, str, float] | None = self._verify(
            token=token,
        )
        if verified is None:
            return None
        kind, nonce, expiry = verified
        if kind not in kinds or kind is PairingKind.session:
            return None
        with self._lock:
            self._forget_expired()
            if nonce in self._burned:
                return None
            if len(self._burned) >= PairingConstants.burned_limit:
                return None
            self._burned[nonce] = expiry
        return kind

    def valid_session(self, *, cookie: str | None) -> bool:
        """Check a session cookie.

        Args:
            cookie: The cookie value, if the browser sent one.

        Returns:
            ``True`` for an authentic, unexpired session.

        """
        if not cookie:
            return False
        verified: tuple[PairingKind, str, float] | None = self._verify(
            token=cookie,
        )
        return verified is not None and verified[0] is PairingKind.session

    @staticmethod
    def _lifetime(*, kind: PairingKind) -> float:
        """Return a kind's default lifetime.

        Args:
            kind: Token kind.

        Returns:
            Lifetime in seconds.

        """
        if kind is PairingKind.session:
            return PairingConstants.session_seconds
        if kind is PairingKind.handoff:
            return PairingConstants.handoff_seconds
        return PairingConstants.pairing_seconds

    @staticmethod
    def _encode_time(*, value: int) -> str:
        """Write a timestamp in the token radix.

        Args:
            value: Whole seconds since the epoch.

        Returns:
            The base-36 text.

        """
        digits: str = "0123456789abcdefghijklmnopqrstuvwxyz"
        text: str = ""
        remaining: int = max(value, 0)
        while True:
            remaining, digit = divmod(remaining, PairingConstants.base)
            text = digits[digit] + text
            if remaining == 0:
                return text

    def _verify(self, *, token: str) -> tuple[PairingKind, str, float] | None:
        """Check a token's signature and expiry.

        Args:
            token: Token text.

        Returns:
            ``(kind, nonce, expiry)``, or ``None`` when it is not valid.

        """
        fields: list[str] = token.split(sep=".")
        if len(fields) != PairingConstants.parts:
            return None
        letter, expiry_text, nonce, mac = fields
        body: str = f"{letter}.{expiry_text}.{nonce}"
        if not compare_digest(self._mac(body=body), mac):
            return None
        try:
            kind: PairingKind = PairingKind(letter)
            expiry: float = float(
                int(expiry_text, base=PairingConstants.base),
            )
        except ValueError:
            return None
        if expiry < self._clock():
            return None
        return kind, nonce, expiry

    def _mac(self, *, body: str) -> str:
        """Sign a token body.

        Args:
            body: Text to sign.

        Returns:
            The truncated, URL-safe HMAC.

        """
        digest: bytes = new_mac(
            key=self._key,
            msg=body.encode(encoding="ascii", errors="replace"),
            digestmod=sha256,
        ).digest()[: PairingConstants.mac_bytes]
        return urlsafe_b64encode(s=digest).decode(encoding="ascii").rstrip("=")

    def _forget_expired(self) -> None:
        """Drop spent nonces whose tokens could no longer be redeemed."""
        now: float = self._clock()
        self._burned = {
            nonce: expiry
            for nonce, expiry in self._burned.items()
            if expiry >= now
        }


@final
class LanNetwork:
    """Find the host's local networks and place peers on them."""

    @classmethod
    def primary(cls) -> LanAddress | None:
        """Pick the address a phone on the same network would reach.

        Returns:
            The first usable address, or ``None`` without a LAN.

        """
        addresses: tuple[LanAddress, ...] = cls.addresses()
        return addresses[0] if addresses else None

    @classmethod
    def classify(cls, *, peer: str | None) -> PeerKind:
        """Place a connection's source address.

        Args:
            peer: Source address of the TCP connection, as the server saw it.

        Returns:
            Loopback, a device on one of the host's networks, or other.

        """
        if not peer:
            return PeerKind.other
        try:
            address: IPv4Address | IPv6Address = ip_address(address=peer)
        except ValueError:
            return PeerKind.other
        if (
            isinstance(address, IPv6Address)
            and address.ipv4_mapped is not None
        ):
            address = address.ipv4_mapped
        if address.is_loopback:
            return PeerKind.loopback
        if isinstance(address, IPv4Address) and any(
            address in item.network for item in cls.addresses()
        ):
            return PeerKind.lan
        return PeerKind.other

    @staticmethod
    def addresses() -> tuple[LanAddress, ...]:
        """List the host's private IPv4 addresses on active interfaces.

        Loopback and link-local addresses are left out, as are interfaces
        that are down.

        Returns:
            One entry per usable address, in interface order.

        """
        up_by_name: dict[str, bool] = {
            name: item.isup for name, item in net_if_stats().items()
        }
        found: list[LanAddress] = []
        for interface, entries in net_if_addrs().items():
            if not up_by_name.get(interface, True):
                continue
            for entry in entries:
                if entry.family != AF_INET or not entry.netmask:
                    continue
                address: IPv4Address = IPv4Address(address=entry.address)
                if not any(
                    address in network
                    for network in PairingConstants.private_networks
                ):
                    continue
                found.append(
                    LanAddress(
                        interface=interface,
                        address=str(object=address),
                        network=IPv4Network(
                            address=f"{entry.address}/{entry.netmask}",
                            strict=False,
                        ),
                    ),
                )
        return tuple(found)
