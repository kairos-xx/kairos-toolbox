# Copyright (c) 2026 Project contributors.
"""End-to-end tests of device pairing in the web dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from ipaddress import IPv4Network
from os import terminal_size
from re import Match, search
from typing import TYPE_CHECKING, Final, Protocol, final
from urllib.parse import SplitResult, urlsplit

import pytest
from district import cli as cli_module
from district import web as web_module
from district.cli import Cli
from district.core import (
    District,
    SessionRepository,
)
from district.exposure import PublicEndpoint
from district.pairing import (
    LanAddress,
    LanNetwork,
    PairingAuth,
    PairingConstants,
    PairingKind,
    PairingUnavailableError,
)
from district.web import WebDashboard
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from .helpers import check, expect, present

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


@final
class Constants:
    """Group test constants.

    Attributes:
        key: Fixed signing key.
        lan_ip: The host's address on its fake home network.
        network: The fake home network.
        phone: A phone on that network.
        stranger: A device on some other network.
        public: The charter's public origin.
        probe: A protected URL that answers 404 once authentication passes.
        evil: An origin that is not the dashboard's.
        redirect: Status of the redirect that follows a pairing.
        refused: Status of a refused pairing link.
        unauthorized: Status of a request without a session.
        forbidden: Status of a cross-site request.
        policy: WebSocket close code for a refused handshake.
        port: Port the fake server "picks".

    """

    key: Final[bytes] = b"k" * PairingConstants.key_bytes
    lan_ip: Final[str] = "192.168.1.20"
    network: Final[IPv4Network] = IPv4Network(address="192.168.1.0/24")
    phone: Final[str] = "192.168.1.77"
    stranger: Final[str] = "8.8.8.8"
    public: Final[str] = "https://abc.example"
    probe: Final[str] = "/api/sessions/zzz/view"
    evil: Final[str] = "https://evil.example"
    redirect: Final[int] = 303
    refused: Final[int] = 403
    unauthorized: Final[int] = 401
    forbidden: Final[int] = 403
    policy: Final[int] = 1008
    port: Final[int] = 9000


class Reply(Protocol):
    """The parts of an HTTP response the tests read."""

    @property
    def status_code(self) -> int:
        """Return the HTTP status.

        Returns:
            The status code.

        """
        ...

    @property
    def text(self) -> str:
        """Return the body as text.

        Returns:
            The body.

        """
        ...

    @property
    def headers(self) -> Mapping[str, str]:
        """Return the response headers.

        Returns:
            Header names (lower case) mapped to values.

        """
        ...


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


def _home_addresses() -> tuple[LanAddress, ...]:
    """Describe the fake host's single private network.

    Returns:
        One LAN address.

    """
    return (
        LanAddress(
            interface="en0",
            address=Constants.lan_ip,
            network=Constants.network,
        ),
    )


def _no_addresses() -> tuple[LanAddress, ...]:
    """Describe a host that is on no private network.

    Returns:
        No addresses.

    """
    return ()


def _on_home_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the host is on one private network.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(
        target=LanNetwork,
        name="addresses",
        value=staticmethod(_home_addresses),
    )


def _dashboard(*, home: Path, clock: Clock | None = None) -> WebDashboard:
    """Create a dashboard that requires pairing.

    Args:
        home: Scratch home directory.
        clock: Clock for token lifetimes, a fresh one when omitted.

    Returns:
        The dashboard.

    """
    dashboard: WebDashboard = WebDashboard(
        service=District(repository=SessionRepository(home=home)),
        home=home,
    )
    dashboard.auth = PairingAuth(key=Constants.key, clock=clock or Clock())
    return dashboard


def _client(*, dashboard: WebDashboard, peer: str) -> TestClient:
    """Create a client whose connections come from ``peer``.

    Args:
        dashboard: Dashboard to talk to.
        peer: Source address the server will see.

    Returns:
        A client that does not follow redirects.

    """
    return TestClient(
        app=dashboard.application(),
        client=(peer, 50000),
        follow_redirects=False,
    )


def _link(*, dashboard: WebDashboard, kind: PairingKind) -> str:
    """Mint a pairing link path the way the CLI does.

    Args:
        dashboard: Dashboard whose key signs it.
        kind: What the token grants.

    Returns:
        The ``/pair?t=...`` path.

    """
    token: str = present(value=dashboard.auth).mint(kind=kind)
    return f"/pair?t={token}"


def _token_of(*, location: str) -> str:
    """Extract the ``t`` token from a link.

    Args:
        location: A URL or path carrying ``t=<token>``.

    Returns:
        The token.

    """
    found: Match[str] | None = search(
        pattern=r"[?&]t=([^&\s]+)",
        string=location,
    )
    return present(value=found).group(1)


def _path_of(*, url: str) -> str:
    """Reduce a full URL to the path and query a client requests.

    Args:
        url: Absolute URL.

    Returns:
        ``/path?query``.

    """
    parts: SplitResult = urlsplit(url=url)
    return f"{parts.path}?{parts.query}"


def test_an_unpaired_device_gets_nothing_but_a_pairing_hint(
    tmp_path: Path,
) -> None:
    """The console, the API and the live socket all refuse a stranger.

    Args:
        tmp_path: Pytest temporary directory used as the home.

    """
    dashboard: WebDashboard = _dashboard(home=tmp_path)
    with _client(dashboard=dashboard, peer=Constants.phone) as client:
        index: Reply = client.get(url="/")
        expect(actual=index.status_code, expected=Constants.unauthorized)
        check(condition="district --web --qr" in index.text, reason=index.text)
        posted: Reply = client.post(url=Constants.probe)
        expect(actual=posted.status_code, expected=Constants.unauthorized)
        expect(actual=client.get(url="/health").status_code, expected=200)
        expect(
            actual=client.get(url="/assets/js/app.js").status_code,
            expected=200,
        )
        with (
            pytest.raises(expected_exception=WebSocketDisconnect) as closed,
            client.websocket_connect(url="/ws"),
        ):
            pass
        expect(actual=closed.value.code, expected=Constants.policy)
    expect(actual=dashboard.consumers, expected=0)


def test_a_local_link_signs_in_the_host_browser(tmp_path: Path) -> None:
    """A loopback device redeems a local link and gets the console.

    Args:
        tmp_path: Pytest temporary directory used as the home.

    """
    dashboard: WebDashboard = _dashboard(home=tmp_path)
    link: str = _link(dashboard=dashboard, kind=PairingKind.local)
    with _client(dashboard=dashboard, peer="127.0.0.1") as client:
        paired: Reply = client.get(url=link)
        expect(actual=paired.status_code, expected=Constants.redirect)
        expect(actual=paired.headers["location"], expected="/")
        cookie: str = paired.headers["set-cookie"]
        check(condition=PairingConstants.cookie in cookie, reason=cookie)
        check(condition="HttpOnly" in cookie, reason=cookie)
        check(condition="samesite=lax" in cookie.lower(), reason=cookie)
        check(condition="Secure" not in cookie, reason=cookie)
        expect(actual=paired.headers["cache-control"], expected="no-store")
        expect(
            actual=paired.headers["referrer-policy"],
            expected="no-referrer",
        )
        expect(actual=client.get(url="/").status_code, expected=200)
        expect(
            actual=client.post(url=Constants.probe).status_code,
            expected=404,
        )
        reused: Reply = client.get(url=link)
        expect(actual=reused.status_code, expected=Constants.refused)
        check(condition="cannot be used" in reused.text, reason=reused.text)


def test_the_cookie_is_secure_behind_an_https_charter(tmp_path: Path) -> None:
    """A charter that terminates TLS makes the cookie ``Secure``.

    Args:
        tmp_path: Pytest temporary directory used as the home.

    """
    dashboard: WebDashboard = _dashboard(home=tmp_path)
    link: str = _link(dashboard=dashboard, kind=PairingKind.local)
    with _client(dashboard=dashboard, peer="127.0.0.1") as client:
        paired: Reply = client.get(
            url=link,
            headers={"x-forwarded-proto": "https"},
        )
        check(
            condition="Secure" in paired.headers["set-cookie"],
            reason="the cookie must carry the Secure flag",
        )


def test_a_lan_link_works_only_from_the_same_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The QR link is useless from the charter, the internet or another LAN.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory used as the home.

    """
    _on_home_network(monkeypatch=monkeypatch)
    dashboard: WebDashboard = _dashboard(home=tmp_path)
    link: str = _link(dashboard=dashboard, kind=PairingKind.lan)
    for peer in ("127.0.0.1", Constants.stranger, "192.168.2.5", "testclient"):
        with _client(dashboard=dashboard, peer=peer) as client:
            refused: Reply = client.get(url=link)
            expect(actual=refused.status_code, expected=Constants.refused)
            check(
                condition="set-cookie" not in refused.headers,
                reason=f"{peer} must not receive a session",
            )
    with _client(dashboard=dashboard, peer=Constants.phone) as phone:
        paired: Reply = phone.get(url=link)
        expect(actual=paired.status_code, expected=Constants.redirect)
        expect(actual=paired.headers["location"], expected="/")
        expect(actual=phone.get(url="/").status_code, expected=200)


def test_a_local_link_is_refused_from_the_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Links meant for the host's own browser do not work from a phone.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory used as the home.

    """
    _on_home_network(monkeypatch=monkeypatch)
    dashboard: WebDashboard = _dashboard(home=tmp_path)
    link: str = _link(dashboard=dashboard, kind=PairingKind.local)
    with _client(dashboard=dashboard, peer=Constants.phone) as client:
        expect(
            actual=client.get(url=link).status_code,
            expected=Constants.refused,
        )


def test_a_lan_device_is_handed_on_to_the_public_origin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """With a charter, proving proximity moves the phone to the public URL.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory used as the home.

    """
    _on_home_network(monkeypatch=monkeypatch)
    dashboard: WebDashboard = _dashboard(home=tmp_path)
    dashboard.public_url = Constants.public
    link: str = _link(dashboard=dashboard, kind=PairingKind.lan)
    with _client(dashboard=dashboard, peer=Constants.phone) as phone:
        moved: Reply = phone.get(url=link)
        expect(actual=moved.status_code, expected=Constants.redirect)
        location: str = moved.headers["location"]
        check(
            condition=location.startswith(f"{Constants.public}/pair?t="),
            reason=location,
        )
        check(
            condition="set-cookie" not in moved.headers,
            reason="the LAN origin keeps no session after handing on",
        )
        handoff: str = _token_of(location=location)
    # The charter agent forwards the phone's request from the loopback side.
    with _client(dashboard=dashboard, peer="127.0.0.1") as charter:
        arrived: Reply = charter.get(url=f"/pair?t={handoff}")
        expect(actual=arrived.status_code, expected=Constants.redirect)
        expect(actual=arrived.headers["location"], expected="/")
        check(
            condition=PairingConstants.cookie in arrived.headers["set-cookie"],
            reason="the public origin signs the phone in",
        )
        expect(actual=charter.get(url="/").status_code, expected=200)
        replay: Reply = charter.get(url=f"/pair?t={handoff}")
        expect(actual=replay.status_code, expected=Constants.refused)


def test_tampered_and_expired_sessions_are_rejected(tmp_path: Path) -> None:
    """A forged cookie, or one past its lifetime, signs nobody in.

    Args:
        tmp_path: Pytest temporary directory used as the home.

    """
    clock: Clock = Clock()
    dashboard: WebDashboard = _dashboard(home=tmp_path, clock=clock)
    link: str = _link(dashboard=dashboard, kind=PairingKind.local)
    with _client(dashboard=dashboard, peer="127.0.0.1") as client:
        paired: Reply = client.get(url=link)
        name, _, session = (
            paired.headers["set-cookie"]
            .split(sep=";")[0]
            .partition(
                "=",
            )
        )
        client.cookies.clear()
        client.cookies.set(name=name, value=session + "x")
        expect(
            actual=client.get(url="/").status_code,
            expected=Constants.unauthorized,
        )
        client.cookies.clear()
        client.cookies.set(name=name, value=session)
        expect(actual=client.get(url="/").status_code, expected=200)
        clock.now += PairingConstants.session_seconds + 1
        expect(
            actual=client.get(url="/").status_code,
            expected=Constants.unauthorized,
        )


def test_cross_site_requests_are_refused_even_with_a_session(
    tmp_path: Path,
) -> None:
    """A page on another site cannot drive a paired browser.

    Args:
        tmp_path: Pytest temporary directory used as the home.

    """
    dashboard: WebDashboard = _dashboard(home=tmp_path)
    link: str = _link(dashboard=dashboard, kind=PairingKind.local)
    with _client(dashboard=dashboard, peer="127.0.0.1") as client:
        _ = client.get(url=link)
        foreign: Reply = client.post(
            url=Constants.probe,
            headers={"origin": Constants.evil},
        )
        expect(actual=foreign.status_code, expected=Constants.forbidden)
        own: Reply = client.post(
            url=Constants.probe,
            headers={"origin": "http://testserver"},
        )
        expect(actual=own.status_code, expected=404)
        with (
            pytest.raises(expected_exception=WebSocketDisconnect) as closed,
            client.websocket_connect(
                url="/ws",
                headers={"origin": Constants.evil},
            ),
        ):
            pass
        expect(actual=closed.value.code, expected=Constants.policy)


def test_a_paired_browser_gets_the_live_socket(tmp_path: Path) -> None:
    """After pairing, the WebSocket streams the dashboard payload.

    Args:
        tmp_path: Pytest temporary directory used as the home.

    """
    dashboard: WebDashboard = _dashboard(home=tmp_path)
    link: str = _link(dashboard=dashboard, kind=PairingKind.local)
    with _client(dashboard=dashboard, peer="127.0.0.1") as client:
        _ = client.get(url=link)
        with client.websocket_connect(url="/ws") as live:
            check(
                condition='"sessions"' in live.receive_text(),
                reason="the live payload arrives",
            )


def test_without_pairing_the_dashboard_stays_open(tmp_path: Path) -> None:
    """Library use and the existing tests see the dashboard as before.

    Args:
        tmp_path: Pytest temporary directory used as the home.

    """
    dashboard: WebDashboard = WebDashboard(
        service=District(repository=SessionRepository(home=tmp_path)),
        home=tmp_path,
    )
    with _client(dashboard=dashboard, peer=Constants.stranger) as client:
        expect(actual=client.get(url="/").status_code, expected=200)
        expect(
            actual=client.post(url=Constants.probe).status_code,
            expected=404,
        )
        bounced: Reply = client.get(url="/pair?t=anything")
        expect(actual=bounced.status_code, expected=Constants.redirect)
        expect(actual=bounced.headers["location"], expected="/")


def test_a_pair_link_without_a_valid_token_is_refused(tmp_path: Path) -> None:
    """``/pair`` with nothing to redeem explains itself.

    Args:
        tmp_path: Pytest temporary directory used as the home.

    """
    dashboard: WebDashboard = _dashboard(home=tmp_path)
    with _client(dashboard=dashboard, peer="127.0.0.1") as client:
        expect(
            actual=client.get(url="/pair").status_code,
            expected=Constants.refused,
        )
        expect(
            actual=client.get(url="/pair?t=garbage").status_code,
            expected=Constants.refused,
        )


@final
class FakeServer:
    """A uvicorn server stand-in that returns at once.

    Attributes:
        runs: ``sockets`` argument of each ``run`` call (``None`` when absent).

    """

    runs: Final[list[list[FakeSocket] | None]] = []

    def __init__(self, *, config: object) -> None:
        """Remember nothing.

        Args:
            config: Ignored server configuration.

        """
        _ = config

    def run(self, sockets: list[FakeSocket] | None = None) -> None:
        """Record the sockets the server was given.

        Args:
            sockets: Pre-bound listening sockets, if any.

        """
        self.runs.append(sockets)


@final
class FakeSocket:
    """A listening socket that only records how it was used.

    Attributes:
        created: Every socket made since the test began.
        bound: Where this socket was bound.
        closed: Whether this socket was closed.

    """

    created: Final[list[FakeSocket]] = []

    def __init__(self, **options: int) -> None:
        """Record the creation.

        Args:
            **options: Address family and socket type, ignored.

        """
        _ = options
        self.bound: tuple[str, int] | None = None
        self.closed: bool = False
        self.created.append(self)

    @staticmethod
    def setsockopt(level: int, option: int, value: int) -> None:
        """Ignore socket options.

        Args:
            level: Option level.
            option: Option number.
            value: Option value.

        """
        _ = (level, option, value)

    def bind(self, address: tuple[str, int]) -> None:
        """Remember where the socket was bound.

        Args:
            address: Host and port.

        """
        self.bound = address

    def close(self) -> None:
        """Remember that the socket was closed."""
        self.closed = True


@final
class FakeCharter:
    """A charter handle that remembers being closed.

    Attributes:
        public_url: Origin the charter publishes.
        closed: Whether :meth:`close` ran.

    """

    def __init__(self) -> None:
        """Open the fake charter."""
        self.public_url: str = Constants.public + "/"
        self.closed: bool = False

    def close(self) -> None:
        """Close the charter."""
        self.closed = True


@dataclass(frozen=True, slots=True)
class FakeIdentity:
    """Process identity with the one field the dashboard stores.

    Attributes:
        started_token: Opaque start-time token.

    """

    started_token: str


@final
class Harness:
    """Everything a ``run`` test observes.

    Attributes:
        out: Standard output written by the dashboard.
        err: Standard error written by the dashboard.
        opened: URLs the browser was asked to open.
        charter: The fake charter, when an endpoint was configured.

    """

    def __init__(self) -> None:
        """Start with nothing observed."""
        self.out: StringIO = StringIO()
        self.err: StringIO = StringIO()
        self.opened: list[str] = []
        self.charter: FakeCharter | None = None


def _harness(
    *,
    monkeypatch: pytest.MonkeyPatch,
    charter: bool = False,
) -> Harness:
    """Replace everything ``run`` would do outside the process.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        charter: Whether a public charter endpoint is configured.

    Returns:
        The observation points.

    """
    harness: Harness = Harness()
    FakeServer.runs.clear()
    FakeSocket.created.clear()

    def fake_open(*, url: str, new: int) -> bool:
        """Record the URL the browser would open.

        Args:
            url: Address to open.
            new: Browser target, ignored.

        Returns:
            Always ``True``.

        """
        _ = new
        harness.opened.append(url)
        return True

    def fake_exposure(*, port: int, exposure: object) -> PublicEndpoint | None:
        """Return the configured fake endpoint.

        Args:
            port: Local port, ignored.
            exposure: Resolved choices, ignored.

        Returns:
            A fake public endpoint, or ``None``.

        """
        _ = (port, exposure)
        if not charter:
            return None
        harness.charter = FakeCharter()
        return PublicEndpoint(
            url="https://sho.rt/district",
            charter_url=harness.charter.public_url,
            charter=harness.charter,
        )

    def fake_port(*, host: str, requested: int) -> int:
        """Pick a fixed port.

        Args:
            host: Bind host, ignored.
            requested: Requested port, ignored.

        Returns:
            The fixed port.

        """
        _ = (host, requested)
        return Constants.port

    def fake_identity(*, pid: int) -> FakeIdentity:
        """Describe the process without inspecting it.

        Args:
            pid: Process id, ignored.

        Returns:
            A fixed identity.

        """
        _ = pid
        return FakeIdentity(started_token=str(object=Constants.port))

    def nbowman_terminal(*, fallback: tuple[int, int]) -> terminal_size:
        """Report a narrow terminal so the link wraps beside the code.

        Args:
            fallback: Ignored default size.

        Returns:
            An 80-column terminal.

        """
        _ = fallback
        return terminal_size((80, 24))

    for name, value in (
        ("stdout", harness.out),
        ("stderr", harness.err),
        ("Server", FakeServer),
        ("socket", FakeSocket),
        ("open_browser", fake_open),
        ("open_exposure", fake_exposure),
        ("get_terminal_size", nbowman_terminal),
    ):
        monkeypatch.setattr(target=web_module, name=name, value=value)
    monkeypatch.setattr(
        target=WebDashboard,
        name="port",
        value=staticmethod(fake_port),
    )
    monkeypatch.setattr(
        target=web_module.ProcessIdentity,
        name="capture",
        value=staticmethod(fake_identity),
    )
    return harness


def _printed_link(*, harness: Harness) -> str:
    """Read the pairing link the dashboard printed beside the QR code.

    Args:
        harness: Observation points of the run.

    Returns:
        The full link from the copyable ``Link:`` line.

    """
    found: Match[str] | None = search(
        pattern=r"Link: (\S+)",
        string=harness.out.getvalue(),
    )
    return present(value=found).group(1)


def test_run_prints_a_qr_link_that_pairs_a_phone(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The link beside the QR code, opened by a phone, ends in the console.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory used as the home.

    """
    _on_home_network(monkeypatch=monkeypatch)
    harness: Harness = _harness(monkeypatch=monkeypatch)
    dashboard: WebDashboard = WebDashboard(
        service=District(repository=SessionRepository(home=tmp_path)),
        home=tmp_path,
    )
    status: int = dashboard.run(
        host="127.0.0.1",
        port=0,
        browser=False,
        lan=True,
    )
    expect(actual=status, expected=0)
    expect(actual=harness.opened, expected=[])
    sockets: list[FakeSocket] | None = FakeServer.runs[0]
    expect(
        actual=[item.bound for item in present(value=sockets)],
        expected=[
            ("127.0.0.1", Constants.port),
            (Constants.lan_ip, Constants.port),
        ],
    )
    check(
        condition=all(item.closed for item in present(value=sockets)),
        reason="the listeners must be closed when the server stops",
    )
    check(
        condition="Scan with your phone" in harness.out.getvalue(),
        reason=harness.out.getvalue(),
    )
    link: str = _printed_link(harness=harness)
    check(
        condition=link.startswith(
            f"http://{Constants.lan_ip}:{Constants.port}/pair?t=n.",
        ),
        reason=link,
    )
    with _client(dashboard=dashboard, peer=Constants.phone) as phone:
        paired: Reply = phone.get(url=_path_of(url=link))
        expect(actual=paired.status_code, expected=Constants.redirect)
        expect(actual=phone.get(url="/").status_code, expected=200)


def test_run_with_a_charter_continues_the_phone_on_the_public_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The banner names the public URL and the handshake ends there.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory used as the home.

    """
    _on_home_network(monkeypatch=monkeypatch)
    harness: Harness = _harness(monkeypatch=monkeypatch, charter=True)
    dashboard: WebDashboard = WebDashboard(
        service=District(repository=SessionRepository(home=tmp_path)),
        home=tmp_path,
    )
    expect(
        actual=dashboard.run(
            host="127.0.0.1",
            port=0,
            browser=False,
            lan=True,
        ),
        expected=0,
    )
    output: str = harness.out.getvalue()
    check(
        condition="Public URL: https://sho.rt/district" in output,
        reason=output,
    )
    check(
        condition=f"Then continues at {Constants.public}" in output,
        reason=output,
    )
    check(
        condition=present(value=harness.charter).closed,
        reason="the charter must close with the server",
    )
    expect(actual=dashboard.public_url, expected=Constants.public)
    link: str = _printed_link(harness=harness)
    with _client(dashboard=dashboard, peer=Constants.phone) as phone:
        moved: Reply = phone.get(url=_path_of(url=link))
        location: str = moved.headers["location"]
        check(
            condition=location.startswith(f"{Constants.public}/pair?t=h."),
            reason=location,
        )
    with _client(dashboard=dashboard, peer="127.0.0.1") as charter:
        arrived: Reply = charter.get(url=_path_of(url=location))
        expect(actual=arrived.headers["location"], expected="/")
        expect(actual=charter.get(url="/").status_code, expected=200)


def test_run_opens_the_host_browser_with_a_local_link(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Without --qr only a loopback listener exists, and it needs pairing too.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory used as the home.

    """
    harness: Harness = _harness(monkeypatch=monkeypatch)
    dashboard: WebDashboard = WebDashboard(
        service=District(repository=SessionRepository(home=tmp_path)),
        home=tmp_path,
    )
    expect(
        actual=dashboard.run(host="127.0.0.1", port=0, browser=True),
        expected=0,
    )
    expect(actual=FakeServer.runs, expected=[None])
    expect(actual=FakeSocket.created, expected=[])
    expect(actual=len(harness.opened), expected=1)
    url: str = harness.opened[0]
    check(
        condition=url.startswith(
            f"http://127.0.0.1:{Constants.port}/pair?t=l.",
        ),
        reason=url,
    )
    with _client(dashboard=dashboard, peer="127.0.0.1") as client:
        expect(
            actual=client.get(url=_path_of(url=url)).status_code,
            expected=Constants.redirect,
        )
        expect(actual=client.get(url="/").status_code, expected=200)
    check(
        condition="Scan with your phone" not in harness.out.getvalue(),
        reason="no QR code without --qr",
    )


def test_run_refuses_lan_mode_without_a_private_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """With no LAN there is nothing for a phone to reach, so nothing starts.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory used as the home.

    """
    monkeypatch.setattr(
        target=LanNetwork,
        name="addresses",
        value=staticmethod(_no_addresses),
    )
    harness: Harness = _harness(monkeypatch=monkeypatch, charter=True)
    dashboard: WebDashboard = WebDashboard(
        service=District(repository=SessionRepository(home=tmp_path)),
        home=tmp_path,
    )
    with pytest.raises(
        expected_exception=PairingUnavailableError,
        match="private network",
    ):
        _ = dashboard.run(host="127.0.0.1", port=0, browser=False, lan=True)
    expect(actual=harness.charter, expected=None)
    expect(actual=FakeServer.runs, expected=[])


def test_run_rejects_a_non_loopback_host(tmp_path: Path) -> None:
    """Listening on the network is only possible through --qr.

    Args:
        tmp_path: Pytest temporary directory used as the home.

    """
    dashboard: WebDashboard = WebDashboard(
        service=District(repository=SessionRepository(home=tmp_path)),
        home=tmp_path,
    )
    with pytest.raises(expected_exception=ValueError, match="--qr"):
        _ = dashboard.run(host=Constants.lan_ip, port=0)


def _running(
    *,
    monkeypatch: pytest.MonkeyPatch,
    dashboard: WebDashboard,
    record: tuple[int, str, str, int, str] | None,
) -> None:
    """Make the dashboard believe a server is already running.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        dashboard: Dashboard under test.
        record: The singleton row the database returns.

    """

    def alive() -> str:
        """Report the running server's URL.

        Returns:
            A loopback URL.

        """
        return f"http://127.0.0.1:{Constants.port}/"

    def stored() -> tuple[int, str, str, int, str] | None:
        """Return the stored singleton record.

        Returns:
            The scripted record.

        """
        return record

    monkeypatch.setattr(target=dashboard, name="running_url", value=alive)
    monkeypatch.setattr(
        target=dashboard.database,
        name="web_server",
        value=stored,
    )


def test_a_running_loopback_server_cannot_serve_a_qr_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Asking for --qr against a loopback-only server says what to do.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory used as the home.

    """
    harness: Harness = _harness(monkeypatch=monkeypatch)
    dashboard: WebDashboard = WebDashboard(
        service=District(repository=SessionRepository(home=tmp_path)),
        home=tmp_path,
    )
    for record in (
        None,
        (1, "t", "127.0.0.1", Constants.port, "http://127.0.0.1:9000/"),
    ):
        _running(monkeypatch=monkeypatch, dashboard=dashboard, record=record)
        status: int = dashboard.run(
            host="127.0.0.1",
            port=0,
            browser=False,
            lan=True,
        )
        expect(actual=status, expected=1)
    check(
        condition="loopback only" in harness.err.getvalue(),
        reason=harness.err.getvalue(),
    )
    expect(actual=FakeServer.runs, expected=[])


def test_a_running_lan_server_gets_a_fresh_qr_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A second --qr prints a new code for the server already running.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory used as the home.

    """
    harness: Harness = _harness(monkeypatch=monkeypatch)
    dashboard: WebDashboard = WebDashboard(
        service=District(repository=SessionRepository(home=tmp_path)),
        home=tmp_path,
    )
    _running(
        monkeypatch=monkeypatch,
        dashboard=dashboard,
        record=(
            1,
            "t",
            Constants.lan_ip,
            Constants.port,
            "http://127.0.0.1:9000/",
        ),
    )
    status: int = dashboard.run(
        host="127.0.0.1",
        port=0,
        browser=False,
        lan=True,
    )
    expect(actual=status, expected=0)
    link: str = _printed_link(harness=harness)
    check(
        condition=link.startswith(
            f"http://{Constants.lan_ip}:{Constants.port}/pair?t=n.",
        ),
        reason=link,
    )
    expect(actual=FakeServer.runs, expected=[])


def test_a_running_server_is_reopened_with_a_local_link(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Running --web again opens the existing server, signed in.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory used as the home.

    """
    harness: Harness = _harness(monkeypatch=monkeypatch)
    dashboard: WebDashboard = WebDashboard(
        service=District(repository=SessionRepository(home=tmp_path)),
        home=tmp_path,
    )
    _running(monkeypatch=monkeypatch, dashboard=dashboard, record=None)
    expect(
        actual=dashboard.run(host="127.0.0.1", port=0, browser=False),
        expected=0,
    )
    expect(actual=harness.opened, expected=[])
    expect(
        actual=dashboard.run(host="127.0.0.1", port=0, browser=True),
        expected=0,
    )
    expect(actual=len(harness.opened), expected=1)
    check(
        condition="/pair?t=l." in harness.opened[0],
        reason=harness.opened[0],
    )


@final
class FakeDashboard:
    """Stand-in dashboard that records how the CLI calls ``run``.

    Attributes:
        kwargs: Arguments of the last ``run`` call.
        unavailable: Whether ``run`` fails like a host without a LAN.

    """

    def __init__(self) -> None:
        """Start with nothing recorded."""
        self.kwargs: dict[str, object] = {}
        self.unavailable: bool = False

    def run(self, **kwargs: object) -> int:
        """Record the call or fail.

        Args:
            **kwargs: ``host``, ``port``, ``browser``, ``exposure``, ``lan``.

        Returns:
            Exit status.

        Raises:
            PairingUnavailableError: When ``unavailable`` is set.

        """
        self.kwargs = kwargs
        if self.unavailable:
            msg: str = "not on a private network"
            raise PairingUnavailableError(msg)
        return 0


def _cli(
    *,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    dashboard: FakeDashboard,
) -> tuple[Cli, StringIO]:
    """Build a CLI whose web dashboard is the fake.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory used as the home.
        dashboard: The fake dashboard.

    Returns:
        The CLI and the buffer its standard error goes to.

    """
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    err: StringIO = StringIO()
    monkeypatch.setattr(target=cli_module, name="stderr", value=err)

    def factory(**options: object) -> FakeDashboard:
        """Return the fake dashboard whatever the options.

        Args:
            **options: ``service`` and ``home``, ignored.

        Returns:
            The fake dashboard.

        """
        _ = options
        return dashboard

    monkeypatch.setattr(target=cli_module, name="WebDashboard", value=factory)
    return (
        Cli(service=District(repository=SessionRepository(home=tmp_path))),
        err,
    )


@pytest.mark.parametrize(
    ("arguments", "lan", "browser"),
    [
        (("--web",), False, True),
        (("--web", "--no-browser"), False, False),
        (("--web", "--qr"), True, False),
        (("--web", "--qr", "--no-browser"), True, False),
        (("--web", "--no-browser", "--qr"), True, False),
    ],
)
def test_the_cli_turns_qr_into_lan_pairing(
    *,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    arguments: tuple[str, ...],
    lan: bool,
    browser: bool,
) -> None:
    """``--qr`` selects LAN pairing and leaves the host browser closed.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory used as the home.
        arguments: Command-line arguments.
        lan: Expected ``lan`` value.
        browser: Expected ``browser`` value.

    """
    dashboard: FakeDashboard = FakeDashboard()
    cli, _ = _cli(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        dashboard=dashboard,
    )
    expect(actual=cli.run(arguments=arguments), expected=0)
    expect(actual=dashboard.kwargs["lan"], expected=lan)
    expect(actual=dashboard.kwargs["browser"], expected=browser)


def test_the_cli_reports_a_host_without_a_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A missing LAN is an error message and a non-zero exit.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temporary directory used as the home.

    """
    dashboard: FakeDashboard = FakeDashboard()
    dashboard.unavailable = True
    cli, err = _cli(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        dashboard=dashboard,
    )
    expect(actual=cli.run(arguments=("--web", "--qr")), expected=1)
    check(
        condition="not on a private network" in err.getvalue(),
        reason=err.getvalue(),
    )
