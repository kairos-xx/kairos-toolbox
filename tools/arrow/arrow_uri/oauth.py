"""OAuth 2.x and OpenID Connect local-client primitives."""

from __future__ import annotations

from base64 import urlsafe_b64encode
from dataclasses import dataclass
from hashlib import sha256
from secrets import token_urlsafe
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlencode

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .json_types import JSONValue


@dataclass(frozen=True, slots=True, kw_only=True)
class PKCEPair:
    """RFC 7636 verifier/challenge pair.

    Attributes:
        verifier: Secret code verifier.
        challenge: Derived code challenge.
        method: Challenge derivation method.

    """

    verifier: str
    challenge: str
    method: str = "S256"

    @classmethod
    def create(cls) -> PKCEPair:
        """Create a high-entropy S256 PKCE pair.

        Returns:
            A fresh verifier/challenge pair.

        """
        verifier: str = token_urlsafe(nbytes=64)
        digest: bytes = sha256(data=verifier.encode(encoding="ascii")).digest()
        challenge: str = (
            urlsafe_b64encode(s=digest).rstrip(b"=").decode("ascii")
        )
        return cls(verifier=verifier, challenge=challenge)


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthorizationRequest:
    """OAuth authorization-code request with PKCE.

    Attributes:
        authorization_endpoint: Provider authorization endpoint.
        client_id: Registered client identifier.
        redirect_uri: Registered redirect URI.
        scopes: Requested scopes.
        state: Anti-CSRF state value.
        pkce: PKCE verifier/challenge pair.
        audience: Optional token audience.

    """

    authorization_endpoint: str
    client_id: str
    redirect_uri: str
    scopes: frozenset[str]
    state: str
    pkce: PKCEPair
    audience: str | None = None

    def url(self) -> str:
        """Build the authorization URL.

        Returns:
            The full authorization request URL.

        """
        values: dict[str, str] = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(sorted(self.scopes)),
            "state": self.state,
            "code_challenge": self.pkce.challenge,
            "code_challenge_method": self.pkce.method,
        }
        if self.audience is not None:
            values["audience"] = self.audience
        return self.authorization_endpoint + "?" + urlencode(query=values)


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceAuthorization:
    """OAuth device authorization response.

    Attributes:
        device_code: Device code used for polling.
        user_code: Human-readable verification code.
        verification_uri: URI where the user enters the code.
        verification_uri_complete: Optional pre-filled verification URI.
        expires_in: Seconds until the device code expires.
        interval: Minimum polling interval in seconds.

    """

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    expires_in: int
    interval: int


@dataclass(frozen=True, slots=True, kw_only=True)
class TokenSet:
    """OAuth token endpoint result.

    Attributes:
        access_token: Bearer access token.
        token_type: Token type, usually `Bearer`.
        expires_in: Access-token lifetime in seconds.
        scope: Granted scopes.
        refresh_token: Optional refresh token.
        id_token: Optional OIDC identity token.

    """

    access_token: str
    token_type: str
    expires_in: int
    scope: frozenset[str]
    refresh_token: str | None = None
    id_token: str | None = None


class OAuthTransport(Protocol):
    """Provider-neutral OAuth network boundary."""

    def post_form(
        self,
        *,
        url: str,
        form: Mapping[str, str],
    ) -> Mapping[str, JSONValue]:
        """POST form data and return decoded JSON.

        Args:
            url: Endpoint URL.
            form: Form fields to submit.

        Returns:
            Decoded JSON response payload.

        """
        ...


@dataclass(slots=True, kw_only=True)
class OAuthClient:
    """OAuth Authorization Code + PKCE and Device Authorization client.

    Attributes:
        client_id: Registered client identifier.
        token_endpoint: Provider token endpoint.
        transport: OAuth network boundary.

    """

    client_id: str
    token_endpoint: str
    transport: OAuthTransport

    @staticmethod
    def authorization_request(
        *,
        authorization_endpoint: str,
        client_id: str,
        redirect_uri: str,
        scopes: frozenset[str],
        audience: str | None = None,
    ) -> AuthorizationRequest:
        """Create an authorization-code request with state and PKCE.

        Args:
            authorization_endpoint: Provider authorization endpoint.
            client_id: Registered client identifier.
            redirect_uri: Registered redirect URI.
            scopes: Requested scopes.
            audience: Optional token audience.

        Returns:
            The prepared authorization request.

        """
        return AuthorizationRequest(
            authorization_endpoint=authorization_endpoint,
            client_id=client_id,
            redirect_uri=redirect_uri,
            scopes=scopes,
            state=token_urlsafe(nbytes=32),
            pkce=PKCEPair.create(),
            audience=audience,
        )

    def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        verifier: str,
    ) -> TokenSet:
        """Exchange an authorization code using its PKCE verifier.

        Args:
            code: Authorization code from the provider.
            redirect_uri: Redirect URI used in the request.
            verifier: PKCE code verifier.

        Returns:
            The resulting token set.

        """
        payload: Mapping[str, JSONValue] = self.transport.post_form(
            url=self.token_endpoint,
            form={
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )
        return self._token_set(payload=payload)

    def refresh(self, *, refresh_token: str) -> TokenSet:
        """Rotate/refresh an OAuth token set.

        Args:
            refresh_token: Previously issued refresh token.

        Returns:
            The rotated token set.

        """
        payload: Mapping[str, JSONValue] = self.transport.post_form(
            url=self.token_endpoint,
            form={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "refresh_token": refresh_token,
            },
        )
        return self._token_set(payload=payload)

    @staticmethod
    def _token_set(*, payload: Mapping[str, JSONValue]) -> TokenSet:
        """Validate the provider-neutral token response shape.

        Args:
            payload: Decoded token-endpoint response.

        Returns:
            The validated token set.

        Raises:
            TypeError: When a response field has the wrong type.
            ValueError: When the access token is empty.

        """
        access_token: JSONValue = payload.get("access_token")
        token_type: JSONValue = payload.get("token_type", "Bearer")
        expires_in: JSONValue = payload.get("expires_in", 0)
        scope: JSONValue = payload.get("scope", "")
        if not isinstance(access_token, str):
            msg: str = "OAuth access_token must be a string."
            raise TypeError(msg)
        if not access_token:
            msg = "OAuth response has no access_token."
            raise ValueError(msg)
        if not isinstance(token_type, str):
            msg = "OAuth token_type must be a string."
            raise TypeError(msg)
        if not isinstance(expires_in, int):
            msg = "OAuth expires_in must be an integer."
            raise TypeError(msg)
        if not isinstance(scope, str):
            msg = "OAuth scope must be a string."
            raise TypeError(msg)
        refresh: JSONValue = payload.get("refresh_token")
        identity: JSONValue = payload.get("id_token")
        return TokenSet(
            access_token=access_token,
            token_type=token_type,
            expires_in=expires_in,
            scope=frozenset(scope.split()),
            refresh_token=refresh if isinstance(refresh, str) else None,
            id_token=identity if isinstance(identity, str) else None,
        )
