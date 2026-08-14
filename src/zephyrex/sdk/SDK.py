"""
Main SDK module providing unified access to all API functionality.

This module serves as the primary entry point for the SDK, providing
a single interface to access all authentication, extension, and provider
functionality.
"""

import logging
from typing import Any, Dict, Optional

from zephyrex.sdk.SDK_Auth import AuthSDK
from zephyrex.sdk.SDK_Extensions import ExtensionsSDK
from zephyrex.sdk.SDK_Providers import ProvidersSDK


class SDK:
    """Main SDK class providing unified access to all API functionality.

    This class serves as the primary entry point for the SDK, providing
    a single interface to access authentication, extensions, providers,
    and other API functionality.

    Example usage:
        # Initialize with token authentication
        sdk = SDK(
            base_url="https://api.example.com",
            token="your_jwt_token"
        )

        # Use authentication features
        user = sdk.auth.get_current_user()
        teams = sdk.auth.list_teams()

        # Use extension features
        extensions = sdk.extensions.list_extensions()

        # Use provider features
        providers = sdk.providers.list_providers()

        # Or initialize with API key
        sdk = SDK(
            base_url="https://api.example.com",
            api_key="your_api_key"
        )
    """

    def __init__(
        self,
        base_url: str,
        token: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: int = 30,
        verify_ssl: bool = True,
        logger: Optional[logging.Logger] = None,
        **kwargs,
    ):
        """Initialize the SDK with configuration.

        Args:
            base_url: Base URL of the API (e.g., "https://api.example.com")
            token: JWT token for authentication
            api_key: API key for authentication
            timeout: Request timeout in seconds
            verify_ssl: Whether to verify SSL certificates
            logger: Optional custom logger instance
            **kwargs: Additional configuration options
        """
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.api_key = api_key
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.logger = logger or logging.getLogger("sdk")

        # Validate configuration
        self._validate_config()

        # Initialize SDK modules with shared configuration
        sdk_config = {
            "base_url": self.base_url,
            "token": self.token,
            "api_key": self.api_key,
            "timeout": self.timeout,
            "verify_ssl": self.verify_ssl,
            "logger": self.logger,
            **kwargs,
        }

        # Initialize individual SDK modules
        self.auth = AuthSDK(**sdk_config)
        self.extensions = ExtensionsSDK(**sdk_config)
        self.providers = ProvidersSDK(**sdk_config)

        self.logger.info(f"SDK initialized with base URL: {self.base_url}")

    def _validate_config(self):
        """Validate SDK configuration."""
        if not self.base_url:
            raise ValueError("base_url is required")

        if not self.token and not self.api_key:
            self.logger.warning(
                "No authentication credentials provided. "
                "Some API endpoints may not be accessible."
            )

        if self.token and self.api_key:
            self.logger.warning(
                "Both token and API key provided. Token will take precedence."
            )

    def set_token(self, token: str):
        """Update the authentication token for all SDK modules.

        Args:
            token: New JWT token
        """
        self.token = token
        self.auth.token = token
        self.extensions.token = token
        self.providers.token = token
        self.logger.info("Authentication token updated")

    def set_api_key(self, api_key: str):
        """Update the API key for all SDK modules.

        Args:
            api_key: New API key
        """
        self.api_key = api_key
        self.auth.api_key = api_key
        self.extensions.api_key = api_key
        self.providers.api_key = api_key
        self.logger.info("API key updated")

    def login(self, email: str, password: str) -> Dict[str, Any]:
        """Convenience method for user login.

        This method performs login and automatically updates the token
        for all SDK modules.

        Args:
            email: User email
            password: User password

        Returns:
            Login response with user information and token
        """
        response = self.auth.login(email, password)

        # Update token if login was successful
        if "token" in response:
            self.set_token(response["token"])

        return response

    def logout(self):
        """Convenience method for user logout.

        This method terminates the current session and clears
        authentication credentials.
        """
        try:
            self.auth.terminate_current_session()
        except Exception as e:
            self.logger.warning(f"Error terminating session: {e}")
        finally:
            # Clear authentication credentials
            self.token = None
            self.api_key = None
            self.auth.token = None
            self.auth.api_key = None
            self.extensions.token = None
            self.extensions.api_key = None
            self.providers.token = None
            self.providers.api_key = None
            self.logger.info("Logged out and cleared authentication credentials")

    def verify_authentication(self) -> bool:
        """Verify if current authentication credentials are valid.

        Returns:
            True if authentication is valid, False otherwise
        """
        return self.auth.verify_token()

    def get_current_user(self) -> Dict[str, Any]:
        """Get the current authenticated user.

        Returns:
            Current user information
        """
        return self.auth.get_current_user()

    def health_check(self) -> Dict[str, Any]:
        """Perform a health check on the API.

        Returns:
            Health check response
        """
        try:
            # Use a simple endpoint to check API health
            response = self.auth.get("/v1", resource_name="health")
            return {
                "status": "healthy",
                "api_accessible": True,
                "authenticated": self.verify_authentication(),
                "response": response,
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "api_accessible": False,
                "authenticated": False,
                "error": str(e),
            }

    def get_api_info(self) -> Dict[str, Any]:
        """Get API information and abilities.

        Returns:
            API information including version, features, etc.
        """
        try:
            return self.auth.get("/v1/info", resource_name="api_info")
        except Exception as e:
            self.logger.error(f"Failed to get API info: {e}")
            return {"error": str(e), "base_url": self.base_url, "sdk_version": "1.0.0"}

    def __repr__(self) -> str:
        """String representation of the SDK instance."""
        auth_type = "token" if self.token else "api_key" if self.api_key else "none"
        return f"SDK(base_url='{self.base_url}', auth='{auth_type}')"

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        # Perform any cleanup if needed
        pass


class SDKFactory:
    """Factory class for creating SDK instances with common configurations."""

    @staticmethod
    def create_with_token(base_url: str, token: str, **kwargs) -> SDK:
        """Create SDK instance with token authentication.

        Args:
            base_url: API base URL
            token: JWT token
            **kwargs: Additional configuration options

        Returns:
            Configured SDK instance
        """
        return SDK(base_url=base_url, token=token, **kwargs)

    @staticmethod
    def create_with_api_key(base_url: str, api_key: str, **kwargs) -> SDK:
        """Create SDK instance with API key authentication.

        Args:
            base_url: API base URL
            api_key: API key
            **kwargs: Additional configuration options

        Returns:
            Configured SDK instance
        """
        return SDK(base_url=base_url, api_key=api_key, **kwargs)

    @staticmethod
    def create_with_login(base_url: str, email: str, password: str, **kwargs) -> SDK:
        """Create SDK instance and perform login.

        Args:
            base_url: API base URL
            email: User email
            password: User password
            **kwargs: Additional configuration options

        Returns:
            Configured and authenticated SDK instance
        """
        sdk = SDK(base_url=base_url, **kwargs)
        sdk.login(email, password)
        return sdk

    @staticmethod
    def create_development(base_url: str = "http://localhost:8000", **kwargs) -> SDK:
        """Create SDK instance for development environment.

        Args:
            base_url: Development API base URL
            **kwargs: Additional configuration options

        Returns:
            SDK instance configured for development
        """
        dev_config = {"verify_ssl": False, "timeout": 60, **kwargs}
        return SDK(base_url=base_url, **dev_config)

    @staticmethod
    def create_production(
        base_url: str,
        token: Optional[str] = None,
        api_key: Optional[str] = None,
        **kwargs,
    ) -> SDK:
        """Create SDK instance for production environment.

        Args:
            base_url: Production API base URL
            token: JWT token
            api_key: API key
            **kwargs: Additional configuration options

        Returns:
            SDK instance configured for production
        """
        prod_config = {"verify_ssl": True, "timeout": 30, **kwargs}
        return SDK(base_url=base_url, token=token, api_key=api_key, **prod_config)


# Convenience functions for quick SDK creation
def create_sdk(
    base_url: str, token: Optional[str] = None, api_key: Optional[str] = None, **kwargs
) -> SDK:
    """Create SDK instance with the provided configuration.

    Args:
        base_url: API base URL
        token: JWT token
        api_key: API key
        **kwargs: Additional configuration options

    Returns:
        Configured SDK instance
    """
    return SDK(base_url=base_url, token=token, api_key=api_key, **kwargs)


def create_sdk_with_login(base_url: str, email: str, password: str, **kwargs) -> SDK:
    """Create SDK instance and perform login.

    Args:
        base_url: API base URL
        email: User email
        password: User password
        **kwargs: Additional configuration options

    Returns:
        Configured and authenticated SDK instance
    """
    return SDKFactory.create_with_login(base_url, email, password, **kwargs)
