"""
Abstract SDK Handler module providing base functionality for all SDK modules.

This module contains the base classes and utilities used by all SDK modules including:
- Exception hierarchy for SDK-specific errors
- Base SDK handler with common HTTP request functionality
- Resource manager for standardized CRUD operations
- Configuration-driven resource management
- Logging and error handling utilities
"""

import json
import logging
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx


class SDKException(Exception):
    """Base exception for SDK-related errors."""

    def __init__(
        self, message: str, status_code: int = None, details: Dict[str, Any] = None
    ):
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


class AuthenticationError(SDKException):
    """Exception raised when authentication fails."""

    def __init__(
        self, message: str = "Authentication failed", details: Dict[str, Any] = None
    ):
        super().__init__(message, 401, details)


class ResourceNotFoundError(SDKException):
    """Exception raised when a requested resource is not found."""

    def __init__(
        self, resource_name: str, resource_id: str, details: Dict[str, Any] = None
    ):
        message = f"{resource_name} with ID '{resource_id}' not found"
        super().__init__(message, 404, details)


class ValidationError(SDKException):
    """Exception raised when request validation fails."""

    def __init__(
        self, message: str = "Validation failed", details: Dict[str, Any] = None
    ):
        super().__init__(message, 422, details)


class ResourceConflictError(SDKException):
    """Exception raised when a resource conflict occurs."""

    def __init__(
        self, resource_name: str, conflict_type: str, details: Dict[str, Any] = None
    ):
        message = f"{resource_name} conflict: {conflict_type}"
        super().__init__(message, 409, details)


@dataclass
class ResourceConfig:
    """Configuration for a resource in the SDK."""

    name: str  # Singular resource name (e.g., "user")
    name_plural: str  # Plural resource name (e.g., "users")
    endpoint: str  # Base endpoint (e.g., "/v1/user")
    supports_search: bool = True
    supports_batch: bool = True
    required_fields: List[str] = None
    unique_fields: List[str] = None
    parent_resource: Optional[str] = None
    nested_under: Optional[str] = None

    def __post_init__(self):
        if self.required_fields is None:
            self.required_fields = []
        if self.unique_fields is None:
            self.unique_fields = []


class ResourceManager:
    """Standardized resource manager for CRUD operations."""

    def __init__(self, handler: "AbstractSDKHandler", config: ResourceConfig):
        self.handler = handler
        self.config = config
        self.logger = handler.logger

    def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new resource."""
        payload = {self.config.name: data}
        return self.handler._request(
            "POST", self.config.endpoint, data=payload, resource_name=self.config.name
        )

    def get(self, resource_id: str) -> Dict[str, Any]:
        """Get a resource by ID."""
        endpoint = f"{self.config.endpoint}/{resource_id}"
        return self.handler._request("GET", endpoint, resource_name=self.config.name)

    def list(
        self,
        offset: int = 0,
        limit: int = 100,
        sort_by: str = None,
        sort_order: str = "asc",
        **filters,
    ) -> Dict[str, Any]:
        """List resources with optional filtering and pagination."""
        params = {
            "offset": offset,
            "limit": limit,
            **filters,
        }

        if sort_by:
            params["sort_by"] = sort_by
            params["sort_order"] = sort_order

        return self.handler._request(
            "GET",
            self.config.endpoint,
            query_params=params,
            resource_name=self.config.name_plural,
        )

    def update(self, resource_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update a resource."""
        endpoint = f"{self.config.endpoint}/{resource_id}"
        payload = {self.config.name: updates}
        return self.handler._request(
            "PUT", endpoint, data=payload, resource_name=self.config.name
        )

    def delete(self, resource_id: str) -> None:
        """Delete a resource."""
        endpoint = f"{self.config.endpoint}/{resource_id}"
        self.handler._request("DELETE", endpoint, resource_name=self.config.name)

    def search(
        self,
        criteria: Dict[str, Any],
        offset: int = 0,
        limit: int = 100,
        sort_by: str = None,
        sort_order: str = "asc",
    ) -> Dict[str, Any]:
        """Search resources."""
        if not self.config.supports_search:
            raise SDKException(f"Search not supported for {self.config.name}")

        params = {
            "offset": offset,
            "limit": limit,
            **criteria,
        }

        if sort_by:
            params["sort_by"] = sort_by
            params["sort_order"] = sort_order

        endpoint = f"{self.config.endpoint}/search"
        return self.handler._request(
            "GET", endpoint, query_params=params, resource_name=self.config.name_plural
        )

    def batch_create(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Create multiple resources in a batch."""
        if not self.config.supports_batch:
            raise SDKException(f"Batch operations not supported for {self.config.name}")

        payload = {self.config.name_plural: items}
        return self.handler._request(
            "POST",
            self.config.endpoint,
            data=payload,
            resource_name=self.config.name_plural,
        )

    def batch_update(
        self, updates: Dict[str, Any], resource_ids: List[str]
    ) -> Dict[str, Any]:
        """Update multiple resources in a batch."""
        if not self.config.supports_batch:
            raise SDKException(f"Batch operations not supported for {self.config.name}")

        payload = {
            self.config.name: updates,
            "target_ids": resource_ids,
        }
        return self.handler._request(
            "PUT",
            self.config.endpoint,
            data=payload,
            resource_name=self.config.name_plural,
        )

    def batch_delete(self, resource_ids: List[str]) -> Dict[str, Any]:
        """Delete multiple resources in a batch."""
        if not self.config.supports_batch:
            raise SDKException(f"Batch operations not supported for {self.config.name}")

        payload = {"target_ids": resource_ids}
        return self.handler._request(
            "DELETE",
            self.config.endpoint,
            data=payload,
            resource_name=self.config.name_plural,
        )


class AbstractSDKHandler(ABC):
    """Base class for all SDK handlers.

    This class provides common functionality for interacting with the API,
    including authentication, request handling, and error handling.

    All SDK modules should extend this class and implement _configure_resources()
    to define their resource configurations. This follows a configuration-driven
    approach similar to the endpoint patterns.

    Example:
        class UserSDK(AbstractSDKHandler):
            def _configure_resources(self) -> Dict[str, ResourceConfig]:
                return {
                    "user": ResourceConfig(
                        name="user",
                        name_plural="users",
                        endpoint="/v1/user",
                        required_fields=["email", "first_name", "last_name"],
                        supports_search=True,
                        supports_batch=True
                    )
                }
    """

    # Default configuration
    default_timeout: int = 30
    default_verify_ssl: bool = True

    def __init__(
        self,
        base_url: str,
        token: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: int = 30,
        verify_ssl: bool = True,
        logger: Optional[logging.Logger] = None,
        http_client=None,
    ):
        """Initialize the SDK handler.

        Args:
            base_url: Base URL for the API
            token: JWT token for authentication
            api_key: API key for authentication
            timeout: Request timeout in seconds
            verify_ssl: Whether to verify SSL certificates
            logger: Logger instance (optional)
            http_client: Custom HTTP client for testing (optional)
        """
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.api_key = api_key
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.http_client = http_client

        # Set up logging
        self.logger = logger or logging.getLogger(self.__class__.__name__)

        # Configure resources and initialize managers
        self.resource_configs = self._configure_resources()
        self._initialize_resource_managers()

    def _initialize_resource_managers(self):
        """Initialize resource managers based on configuration."""
        for resource_name, config in self.resource_configs.items():
            manager = self.create_resource_manager(config)
            setattr(self, resource_name, manager)

    @property
    def resource_managers(self) -> Dict[str, ResourceManager]:
        """Get a dictionary of all resource managers."""
        managers = {}
        for resource_name in self.resource_configs.keys():
            if hasattr(self, resource_name):
                managers[resource_name] = getattr(self, resource_name)
        return managers

    def get_resource_manager(self, resource_name: str) -> ResourceManager:
        """Get a resource manager by name."""
        if hasattr(self, resource_name):
            return getattr(self, resource_name)
        raise ValueError(f"Resource manager '{resource_name}' not found")

    def create_resource_manager(self, config: ResourceConfig) -> ResourceManager:
        """Create a resource manager from configuration.

        Args:
            config: Resource configuration

        Returns:
            ResourceManager instance
        """
        return ResourceManager(self, config)

    def _get_headers(self) -> Dict[str, str]:
        """Get headers for API requests, including authentication.

        Returns:
            Dictionary of HTTP headers for API requests
        """
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        return headers

    def _build_url(self, endpoint: str, query_params: Dict[str, Any] = None) -> str:
        """Build complete URL from endpoint and query parameters.

        Args:
            endpoint: API endpoint path
            query_params: Optional query parameters

        Returns:
            Complete URL string
        """
        # Ensure endpoint starts with /
        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"

        url = f"{self.base_url}{endpoint}"

        if query_params:
            # Filter out None values and convert to strings
            filtered_params = {
                k: str(v) for k, v in query_params.items() if v is not None
            }
            if filtered_params:
                query_string = urllib.parse.urlencode(filtered_params)
                url = f"{url}?{query_string}"

        return url

    def _format_response(self, response) -> Dict[str, Any]:
        """Format API response data.

        Args:
            response: HTTP response object

        Returns:
            Formatted response data
        """
        try:
            if hasattr(response, "json"):
                return response.json()
            elif hasattr(response, "text"):
                return json.loads(response.text)
            else:
                return {}
        except (json.JSONDecodeError, ValueError):
            return {"raw_response": str(response)}

    def _handle_response_error(self, response, resource_name: str = "resource"):
        """Handle HTTP response errors and raise appropriate exceptions.

        Args:
            response: HTTP response object
            resource_name: Name of the resource for error context

        Raises:
            SDKException: For various error conditions
        """
        status_code = response.status_code

        try:
            error_data = self._format_response(response)
            error_message = (
                error_data.get("detail", f"HTTP {status_code} error")
                if isinstance(error_data, dict)
                else f"HTTP {status_code} error"
            )
        except Exception as e:
            self.logger.warning(f"Failed to parse error response: {e}", exc_info=True)
            error_data = {}
            error_message = f"HTTP {status_code} error"

        if status_code == 401:
            raise AuthenticationError(error_message, error_data)
        elif status_code == 404:
            resource_id = (
                error_data.get("resource_id", "unknown")
                if isinstance(error_data, dict)
                else "unknown"
            )
            raise ResourceNotFoundError(resource_name, resource_id, error_data)
        elif status_code == 422:
            raise ValidationError(error_message, error_data)
        elif status_code == 409:
            conflict_type = (
                error_data.get("conflict_type", "unknown")
                if isinstance(error_data, dict)
                else "unknown"
            )
            raise ResourceConflictError(resource_name, conflict_type, error_data)
        else:
            raise SDKException(error_message, status_code, error_data)

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Any = None,
        params: Dict[str, Any] = None,
        headers: Dict[str, str] = None,
        client: Any = None,
    ) -> Dict[str, Any]:
        """Make an HTTP request, optionally using a caller-supplied *client*.

        When *client* is ``None`` a fresh ``httpx.Client`` is created for
        the call (the default production path).  When a *client* is
        provided (e.g. a ``TestClient``), it is used directly without
        ``timeout`` / ``verify`` kwargs that test clients do not accept.

        Args:
            method: HTTP method
            endpoint: API endpoint
            data: Request data
            params: Query parameters
            headers: Additional headers
            client: Optional pre-built HTTP client (e.g. for testing)

        Returns:
            Response data
        """
        use_custom = client is not None
        url = self._build_url(endpoint, params)
        request_headers = self._get_headers()
        if headers:
            request_headers.update(headers)

        self.logger.debug(f"Making {method} request to {url}")

        request_kwargs: Dict[str, Any] = {
            "headers": request_headers,
        }

        # Only pass transport-level options to real httpx clients.
        if not use_custom:
            request_kwargs["timeout"] = self.timeout
            request_kwargs["verify"] = self.verify_ssl

        if data is not None:
            if isinstance(data, (dict, list)):
                request_kwargs["json"] = data
            else:
                request_kwargs["data"] = data

        # Custom clients (e.g. TestClient) expect params in request_kwargs
        # rather than pre-encoded into the URL.
        if use_custom and params:
            request_kwargs["params"] = params

        try:
            if use_custom:
                response = client.request(method, url, **request_kwargs)
            else:
                with httpx.Client() as http_client:
                    response = http_client.request(method, url, **request_kwargs)

            self.logger.debug(f"Response status: {response.status_code}")

            if not (200 <= response.status_code < 300):
                self._handle_response_error(response)

            if response.status_code == 204:  # No content
                return {}

            result = self._format_response(response)
            self.logger.debug(f"Response data: {result}")
            return result

        except Exception as e:
            if isinstance(e, SDKException):
                raise
            self.logger.error(f"Request failed: {str(e)}")
            raise SDKException(f"Request failed: {str(e)}")

    def _request(
        self,
        method: str,
        endpoint: str,
        data: Any = None,
        query_params: Dict[str, Any] = None,
        headers: Dict[str, str] = None,
        resource_name: str = "resource",
    ) -> Dict[str, Any]:
        """Make an HTTP request to the API.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            endpoint: API endpoint path
            data: Request data (for POST/PUT requests)
            query_params: Query parameters
            headers: Additional headers
            resource_name: Name of the resource for error handling

        Returns:
            Response data from the API

        Raises:
            SDKException: If the request fails
            AuthenticationError: If authentication fails
            ResourceNotFoundError: If the resource is not found
            ValidationError: If request validation fails
        """
        return self._make_request(
            method, endpoint, data, query_params, headers, client=self.http_client
        )

    # Convenience methods for common HTTP operations
    def get(
        self,
        endpoint: str,
        params: Dict[str, Any] = None,
        headers: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """Make a GET request."""
        return self._request("GET", endpoint, query_params=params, headers=headers)

    def post(
        self,
        endpoint: str,
        data: Any = None,
        params: Dict[str, Any] = None,
        headers: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """Make a POST request."""
        return self._request(
            "POST", endpoint, data=data, query_params=params, headers=headers
        )

    def put(
        self,
        endpoint: str,
        data: Any = None,
        params: Dict[str, Any] = None,
        headers: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """Make a PUT request."""
        return self._request(
            "PUT", endpoint, data=data, query_params=params, headers=headers
        )

    def patch(
        self,
        endpoint: str,
        data: Any = None,
        params: Dict[str, Any] = None,
        headers: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """Make a PATCH request."""
        return self._request(
            "PATCH", endpoint, data=data, query_params=params, headers=headers
        )

    def delete(
        self,
        endpoint: str,
        params: Dict[str, Any] = None,
        headers: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """Make a DELETE request."""
        return self._request("DELETE", endpoint, query_params=params, headers=headers)

    # Abstract methods for subclasses to implement
    @abstractmethod
    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for this SDK handler.

        This method must be implemented by subclasses to define their resource
        configurations. This follows a declarative, configuration-driven approach
        similar to the endpoint patterns.

        Returns:
            Dictionary mapping resource names to their configurations

        Example:
            return {
                "user": ResourceConfig(
                    name="user",
                    name_plural="users",
                    endpoint="/v1/user",
                    required_fields=["email", "first_name", "last_name"],
                    supports_search=True,
                    supports_batch=True
                ),
                "team": ResourceConfig(
                    name="team",
                    name_plural="teams",
                    endpoint="/v1/team",
                    required_fields=["name"],
                    supports_search=True,
                    supports_batch=False
                )
            }
        """
        pass
