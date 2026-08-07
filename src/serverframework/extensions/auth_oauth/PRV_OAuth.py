"""Abstract base for OAuth providers in the auth_oauth extension.

Concrete subclasses (Google, GitHub, Microsoft, Amazon) implement the
synchronous OAuth2 dance using ``requests``. This is the *legacy*
auth_oauth provider interface; the newer ``oauth_consumer`` extension
uses :class:`AbstractIdPProvider` with ``httpx`` async.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class AbstractOAuthProvider(ABC):
    """Base class every auth_oauth provider extends.

    Subclasses must override:
    - ``get_new_token()`` -> refreshed access-token string
    - ``get_user_info()`` -> normalized user-profile dict
    - ``sso_handler(code, redirect_uri)`` -> classmethod returning a configured instance
    """

    name: str = ""

    def __init__(
        self,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        scopes: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes
        self.email_address: Optional[str] = None

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def get_new_token(self) -> str:
        """Exchange the refresh token for a new access token."""

    @abstractmethod
    def get_user_info(self) -> Dict[str, Any]:
        """Fetch the upstream user profile using the current access token."""

    @classmethod
    @abstractmethod
    def sso_handler(cls, code: str, redirect_uri: Optional[str] = None) -> Optional["AbstractOAuthProvider"]:
        """Handle the SSO callback: exchange *code* for tokens, return a configured instance."""

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def services() -> List[str]:
        """Return the list of service capabilities this provider exposes."""
        return ["auth", "user_info"]

    @staticmethod
    def sanitize_code(code: str) -> str:
        """Decode common URL-encoded characters that some upstreams return."""
        return str(code).replace("%2F", "/").replace("%3D", "=").replace("%3F", "?")

    def get_email(self) -> Optional[str]:
        """Convenience: fetch the user's email via :meth:`get_user_info`."""
        try:
            info = self.get_user_info()
            return info.get("email") if info else None
        except Exception:
            return None

    def handle_auth_error(self, error: Exception, context: str) -> None:
        """Log and optionally re-raise an authentication error."""
        logging.error(f"OAuth error during {context}: {error}")
        raise error
