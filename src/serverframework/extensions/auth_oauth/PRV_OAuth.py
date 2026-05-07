from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from fastapi import HTTPException


class AbstractOAuthProvider(ABC):
    """
    Abstract base class for OAuth providers.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: Optional[str] = None,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        redirect_uri: Optional[str] = None,
        scopes: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize the OAuth provider with configuration parameters.
        Args:
            client_id: The OAuth client ID
            client_secret: The OAuth client secret
            access_token: The OAuth access token
            refresh_token: The OAuth refresh token
            redirect_uri: The OAuth redirect URI
            scopes: Space-separated list of OAuth scopes
            **kwargs: Additional provider-specific parameters
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.redirect_uri = redirect_uri
        self.scopes = scopes
        self.user_info: Optional[Dict[str, Any]] = None
        # Store kwargs if needed, e.g., self.config = kwargs

    @abstractmethod
    async def get_new_token(self) -> Dict[str, Any]:
        """
        Refresh the OAuth access token using the refresh token.
        Returns:
            Dict containing the new token information (e.g., access_token, expires_in)
        Raises:
            HTTPException: If token refresh fails
        """
        pass

    @abstractmethod
    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        """
        Get user information from the provider using the access token.
        Args:
            access_token: The access token to use for fetching user info.
        Returns:
            Dictionary with user information
        Raises:
            HTTPException: If getting user info fails
        """
        pass

    @staticmethod
    def sanitize_code(code: str) -> str:
        """
        Sanitize the authorization code by handling URL encoding.
        Args:
            code: The authorization code
        Returns:
            Sanitized code
        """
        return str(code).replace("%2F", "/").replace("%3D", "=").replace("%3F", "?")

    @staticmethod
    def services() -> list:
        """
        List the services provided by this interface.
        Returns:
            List of service identifiers (e.g., "auth", "user_info", etc.)
        """
        return ["auth", "user_info"]

    async def get_email(self) -> Optional[str]:
        """
        Get the user's email address.
        Returns:
            User's email address if available, None otherwise
        """
        if not self.user_info and self.access_token:
            self.user_info = await self.get_user_info(self.access_token)

        if self.user_info and "email" in self.user_info:
            return self.user_info["email"]
        return None

    async def get_name(self) -> Dict[str, str]:
        """
        Get the user's name components.
        Returns:
            Dictionary with first_name and last_name
        """
        if not self.user_info and self.access_token:
            self.user_info = await self.get_user_info(self.access_token)

        result = {"first_name": "", "last_name": ""}
        if self.user_info:
            if "first_name" in self.user_info:
                result["first_name"] = self.user_info["first_name"]
            if "last_name" in self.user_info:
                result["last_name"] = self.user_info["last_name"]
            elif "name" in self.user_info:
                parts = self.user_info["name"].split(" ", 1)
                result["first_name"] = parts[0]
                if len(parts) > 1:
                    result["last_name"] = parts[1]
        return result

    def handle_auth_error(self, error: Exception, context: str) -> None:
        """
        Handle OAuth authentication errors.
        Args:
            error: The exception that occurred
            context: Description of where the error occurred
        Raises:
            HTTPException: With appropriate status code and message
        """
        error_msg = f"OAuth error during {context}: {str(error)}"
        print(error_msg)
        raise HTTPException(status_code=401, detail=error_msg)


# Example Factory (Optional but helpful)
class OAuthProviderFactory:
    def __init__(self):
        self._providers = {}

    def register_provider(self, name: str, provider_class):
        self._providers[name.lower()] = provider_class

    def get_provider(self, provider_name: str, **config) -> AbstractOAuthProvider:
        provider_class = self._providers.get(provider_name.lower())
        if not provider_class:
            raise ValueError(f"Unsupported OAuth provider: {provider_name}")
        return provider_class(**config)
