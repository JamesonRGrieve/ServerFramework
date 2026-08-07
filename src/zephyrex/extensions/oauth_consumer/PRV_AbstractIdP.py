"""Abstract base for external OAuth2 Identity Providers consumed by this server.

An IdP provider implements the OAuth2 Authorization Code dance against an
external service (Google, GitHub, Microsoft, Amazon Cognito, ...). A concrete
subclass receives the auth code at the configured redirect URI, exchanges it
for tokens, and exposes the upstream user profile so the framework can resolve
or create a local ``UserModel``.

This is the *consumer* side of OAuth: the server is the OAuth client.
The complementary ``oauth_provider`` extension implements the server side
(third parties authenticate against us).

The ``get_user_info`` contract requires concrete IdPs to set
``email_verified`` to True only when the upstream marks the email as
verified. ``BLL_OAuthConsumer`` refuses to auto-link an unverified IdP
identity to an existing local account because the email claim is the
matching key (CWE-287, "pre-account takeover").
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import httpx

from zephyrex.lib.ProviderHTTPClient import validate_outbound_url


# Single shared async HTTP client. IdP traffic is low-volume; the timeout
# keeps a hung upstream from blocking the event loop.
_HTTP_CLIENT: Optional[httpx.AsyncClient] = None


async def _ssrf_request_hook(request: httpx.Request) -> None:
    """H-6 — every IdP HTTP request flows through the framework's SSRF
    guard. Calling ``validate_outbound_url`` here means a future code path
    that turns a user-controlled string into a token-exchange URL or
    userinfo target inherits the same protection as
    :class:`ProviderHTTPClient`.
    """
    validate_outbound_url(str(request.url))


def get_http_client() -> httpx.AsyncClient:
    """Return a process-shared ``httpx.AsyncClient`` for IdP HTTP calls.

    Every request is run through :func:`validate_outbound_url` via an
    event hook so the IdP path cannot be used as a generic egress
    oracle.
    """
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            event_hooks={"request": [_ssrf_request_hook]},
        )
    return _HTTP_CLIENT


class AbstractIdPProvider(ABC):
    """Base class every external IdP implementation extends.

    Subclasses must override:
    - ``get_user_info(access_token)`` -> normalized
      ``{email, email_verified, first_name, last_name, display_name, provider_user_id}``
    - ``get_new_token()`` -> refreshed access token
    - ``sso_handler(code, redirect_uri)`` -> classmethod returning a configured instance
    """

    name: str = ""

    def __init__(
        self,
        client_id: str,
        client_secret: Optional[str] = None,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        redirect_uri: Optional[str] = None,
        scopes: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.redirect_uri = redirect_uri
        self.scopes = scopes
        self.user_info: Optional[Dict[str, Any]] = None

    @abstractmethod
    async def get_new_token(self) -> Dict[str, Any]:
        """Exchange the refresh token for a new access token."""

    @abstractmethod
    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        """Fetch the upstream user profile using the bearer token.

        Return value MUST include ``email_verified: bool`` reflecting the
        upstream verification status. Callers that auto-link IdP identities
        to local accounts rely on this signal.

        Trust-boundary contract (M-6) — ``email_verified`` MUST be True
        only when the upstream guarantees the user controls the mailbox
        named in ``email``. Implementer responsibilities by IdP class:

        - **OIDC providers (Google, Cognito)** — copy the ``email_verified``
          claim verbatim from the userinfo response, normalising any
          string-encoded boolean to ``bool``.
        - **GitHub** — emails endpoint returns a list; treat as verified
          only when the matched email has ``primary == True`` AND
          ``verified == True``. Do not fall back to the profile email,
          which is user-editable.
        - **Microsoft Graph** — ``mail`` is the admin-provisioned address
          and is the only attribute that may be considered verified.
          Never treat ``userPrincipalName`` or ``otherMails`` as verified.
        - **Custom / non-OIDC providers** — if the upstream does not
          expose a verification signal, set ``email_verified=False``.
          Do not infer verification from response shape.

        ``BLL_OAuthConsumer.complete_callback`` refuses to auto-link an
        IdP identity to an existing local account when
        ``email_verified`` is False — see CWE-287 (pre-account takeover).
        Lying here turns the OAuth consumer into an account-takeover
        primitive against every local user that shares an email with
        the upstream.
        """

    @staticmethod
    def sanitize_code(code: str) -> str:
        """Decode common URL-encoded characters that some upstreams return."""
        return str(code).replace("%2F", "/").replace("%3D", "=").replace("%3F", "?")

    @staticmethod
    def services() -> list:
        return ["auth", "user_info"]
