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


# Single shared async HTTP client. IdP traffic is low-volume; the timeout
# keeps a hung upstream from blocking the event loop.
_HTTP_CLIENT: Optional[httpx.AsyncClient] = None


def get_http_client() -> httpx.AsyncClient:
    """Return a process-shared ``httpx.AsyncClient`` for IdP HTTP calls."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        _HTTP_CLIENT = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
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
        """

    @staticmethod
    def sanitize_code(code: str) -> str:
        """Decode common URL-encoded characters that some upstreams return."""
        return str(code).replace("%2F", "/").replace("%3D", "=").replace("%3F", "?")

    @staticmethod
    def services() -> list:
        return ["auth", "user_info"]
