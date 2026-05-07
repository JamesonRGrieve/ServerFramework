"""Abstract base for external OAuth2 Identity Providers consumed by this server.

An IdP provider implements the OAuth2 Authorization Code dance against an
external service (Google, GitHub, Microsoft, Amazon Cognito, ...). A concrete
subclass receives the auth code at the configured redirect URI, exchanges it
for tokens, and exposes the upstream user profile so the framework can resolve
or create a local ``UserModel``.

This is the *consumer* side of OAuth: the server is the OAuth client.
The complementary ``oauth_provider`` extension implements the server side
(third parties authenticate against us).
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class AbstractIdPProvider(ABC):
    """Base class every external IdP implementation extends.

    Subclasses must override:
    - ``get_user_info(access_token)`` -> normalized ``{email, first_name, last_name, display_name, ...}``
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
        """Fetch the upstream user profile using the bearer token."""

    @staticmethod
    def sanitize_code(code: str) -> str:
        """Decode common URL-encoded characters that some upstreams return."""
        return str(code).replace("%2F", "/").replace("%3D", "=").replace("%3F", "?")

    @staticmethod
    def services() -> list:
        return ["auth", "user_info"]
