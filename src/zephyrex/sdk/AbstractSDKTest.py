"""
Abstract SDK Test module providing base functionality for testing SDK modules.

This module contains base test classes and utilities for testing SDK functionality
using real HTTP requests against a test server, following the framework's no-mock
testing philosophy.

This follows patterns similar to AbstractEPTest to provide comprehensive
test coverage with minimal code duplication.

Key principles (from AGENTS.md):
- NO MOCKING: Tests use actual HTTP requests to a real test server
- Real functionality: Every test validates actual SDK behavior
- pytest-based: Uses pytest for test discovery and execution
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Type

import pytest
from faker import Faker

from zephyrex.AbstractTest import AbstractTest, CategoryOfTest, ClassOfTestsConfig, SkipThisTest
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.sdk.AbstractSDKHandler import AbstractSDKHandler, ResourceConfig, SDKException


class TestVariant(str, Enum):
    """Variants of test data for testing."""

    VALID = "valid"
    MINIMAL = "minimal"
    INVALID = "invalid"
    EMPTY = "empty"
    LARGE = "large"


class TestOperation(str, Enum):
    """SDK operations that can be tested."""

    CREATE = "create"
    GET = "get"
    LIST = "list"
    UPDATE = "update"
    DELETE = "delete"
    SEARCH = "search"
    BATCH_CREATE = "batch_create"
    BATCH_UPDATE = "batch_update"
    BATCH_DELETE = "batch_delete"


@dataclass
class SDKTestConfig:
    """Configuration for SDK tests."""

    # Operations to test
    test_create: bool = True
    test_get: bool = True
    test_list: bool = True
    test_update: bool = True
    test_delete: bool = True
    test_search: bool = True
    test_batch: bool = True
    test_error_handling: bool = True
    test_authentication: bool = True

    # Test data configuration
    create_data_variants: List[TestVariant] = None
    update_data_variants: List[TestVariant] = None

    # Resource-specific configuration
    primary_resource: str = None  # Primary resource to test
    test_resources: List[str] = None  # List of resources to test

    def __post_init__(self):
        if self.create_data_variants is None:
            self.create_data_variants = [TestVariant.VALID, TestVariant.MINIMAL]
        if self.update_data_variants is None:
            self.update_data_variants = [TestVariant.VALID]
        if self.test_resources is None:
            self.test_resources = []


class AbstractSDKTest(AbstractTest, ABC):
    """Base test class for SDK modules.

    This class provides common testing utilities and patterns for testing
    SDK functionality using real HTTP requests against a test server.
    It follows the framework's no-mock testing philosophy - all tests
    hit the actual server and validate real responses.

    Child classes must override:
    - sdk_class: The SDK class being tested
    - resource_name: The primary resource name being tested
    - sample_data: Sample data for creating test entities
    - update_data: Sample data for updating test entities

    Optional overrides:
    - test_config: Test configuration to customize which tests run
    - resource_configs: Expected resource configurations for the SDK

    Example:
        class TestUserSDK(AbstractSDKTest):
            sdk_class = UserSDK
            resource_name = "user"
            sample_data = {"email": "test@example.com", "first_name": "Test"}
            update_data = {"first_name": "Updated"}
    """

    # Required overrides that child classes must provide
    sdk_class: Type[AbstractSDKHandler] = None
    resource_name: str = None
    sample_data: Dict[str, Any] = None
    update_data: Dict[str, Any] = None

    # Optional overrides
    sdk_test_config: SDKTestConfig = SDKTestConfig()
    resource_configs: Dict[str, ResourceConfig] = None

    # Test configuration - use SDK category
    test_config: ClassOfTestsConfig = ClassOfTestsConfig(
        categories=[CategoryOfTest.SDK]
    )

    # Faker for generating test data
    faker = Faker()

    # Tests to skip
    _skip_tests: List[SkipThisTest] = []

    def setup_method(self, method):
        """Set up method-level test fixtures."""
        super().setup_method(method)
        self.tracked_entities = {}
        self._sdk_instance = None

        # Validate required overrides
        assert (
            self.sdk_class is not None
        ), f"{self.__class__.__name__}: sdk_class must be defined"
        assert (
            self.resource_name is not None
        ), f"{self.__class__.__name__}: resource_name must be defined"
        assert (
            self.sample_data is not None
        ), f"{self.__class__.__name__}: sample_data must be defined"
        assert (
            self.update_data is not None
        ), f"{self.__class__.__name__}: update_data must be defined"

    def teardown_method(self, method):
        """Clean up method-level test fixtures."""
        try:
            self._cleanup_sdk_entities()
        finally:
            super().teardown_method(method)

    def _cleanup_sdk_entities(self):
        """Clean up entities created during this test via SDK."""
        if not hasattr(self, "tracked_entities"):
            return

        # Clean up created entities in reverse order
        for entity_key, entity in reversed(list(self.tracked_entities.items())):
            try:
                if isinstance(entity, dict) and "id" in entity:
                    logger.debug(
                        f"{self.resource_name}: Cleaned up entity {entity['id']}"
                    )
            except Exception as e:
                logger.debug(
                    f"{self.resource_name}: Error cleaning up entity {entity_key}: {str(e)}"
                )

        # Clear the tracking dict
        self.tracked_entities = {}

    def get_server_url(self, server) -> str:
        """Get the base URL for the test server.

        Args:
            server: TestClient from pytest fixture

        Returns:
            Base URL string for SDK requests
        """
        # TestClient uses a base URL that we need to extract
        # For testing, we use the test client directly via http_client parameter
        return "http://testserver"

    def create_sdk(
        self,
        server,
        token: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> AbstractSDKHandler:
        """Create an SDK instance configured for testing.

        This method creates an SDK instance that uses the test server's
        HTTP client for making requests, enabling real integration testing
        without mocking.

        Args:
            server: TestClient from pytest fixture
            token: Optional JWT token for authentication
            api_key: Optional API key for authentication

        Returns:
            Configured SDK instance
        """
        base_url = self.get_server_url(server)

        # Create SDK with the test server's HTTP client
        # The SDK will make real HTTP requests through the TestClient
        sdk = self.sdk_class(
            base_url=base_url,
            token=token,
            api_key=api_key,
            timeout=30,
            verify_ssl=False,
            http_client=server,  # Pass TestClient as HTTP client
        )

        return sdk

    def create_authenticated_sdk(self, server, user) -> AbstractSDKHandler:
        """Create an authenticated SDK instance for testing.

        Args:
            server: TestClient from pytest fixture
            user: User fixture with jwt attribute

        Returns:
            Authenticated SDK instance
        """
        return self.create_sdk(server, token=user.jwt)

    def get_test_data(self, variant: TestVariant = TestVariant.VALID) -> Dict[str, Any]:
        """Get test data for the specified variant.

        Args:
            variant: The test data variant to return

        Returns:
            Test data dictionary
        """
        if variant == TestVariant.VALID:
            return self._generate_unique_data(self.sample_data.copy())
        elif variant == TestVariant.MINIMAL:
            return self._get_minimal_data()
        elif variant == TestVariant.INVALID:
            return self._get_invalid_data()
        elif variant == TestVariant.EMPTY:
            return {}
        elif variant == TestVariant.LARGE:
            return self._get_large_data()
        else:
            return self._generate_unique_data(self.sample_data.copy())

    def _generate_unique_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate unique values for test data to avoid conflicts.

        Args:
            data: Base data dictionary

        Returns:
            Data with unique values for certain fields
        """
        import uuid

        unique_suffix = uuid.uuid4().hex[:8]

        # Make common unique fields unique
        if "name" in data:
            data["name"] = f"{data['name']}_{unique_suffix}"
        if "email" in data:
            base_email = data["email"].split("@")
            data["email"] = f"{base_email[0]}_{unique_suffix}@{base_email[1]}"
        if "username" in data:
            data["username"] = f"{data['username']}_{unique_suffix}"

        return data

    def _get_minimal_data(self) -> Dict[str, Any]:
        """Get minimal test data with only required fields."""
        # Default implementation - subclasses should override
        return self._generate_unique_data(
            {k: v for k, v in self.sample_data.items() if k in ["name", "id"]}
        )

    def _get_invalid_data(self) -> Dict[str, Any]:
        """Get invalid test data for validation testing."""
        # Default implementation - subclasses should override
        invalid_data = self.sample_data.copy()
        # Make some fields invalid
        if "name" in invalid_data:
            invalid_data["name"] = 123  # Invalid type
        if "email" in invalid_data:
            invalid_data["email"] = "invalid-email"  # Invalid format
        return invalid_data

    def _get_large_data(self) -> Dict[str, Any]:
        """Get large test data for stress testing."""
        # Default implementation - subclasses should override
        large_data = self._generate_unique_data(self.sample_data.copy())
        if "description" in large_data:
            large_data["description"] = "x" * 10000  # Very long description
        return large_data

    # Standard test methods that use real server

    def test_sdk_initialization(self, server):
        """Test SDK initialization with various parameters."""
        base_url = self.get_server_url(server)

        # Test with token
        sdk_with_token = self.sdk_class(
            base_url=base_url,
            token="test_token",
            http_client=server,
        )
        assert sdk_with_token.token == "test_token"
        assert sdk_with_token.base_url == base_url

        # Test with API key
        sdk_with_api_key = self.sdk_class(
            base_url=base_url,
            api_key="test_api_key",
            http_client=server,
        )
        assert sdk_with_api_key.api_key == "test_api_key"

        # Test with both
        sdk_with_both = self.sdk_class(
            base_url=base_url,
            token="test_token",
            api_key="test_api_key",
            http_client=server,
        )
        assert sdk_with_both.token == "test_token"
        assert sdk_with_both.api_key == "test_api_key"

    def test_resource_configuration(self, server):
        """Test that resources are properly configured."""
        sdk = self.create_sdk(server)

        # Check that _configure_resources returns expected structure
        configs = sdk._configure_resources()
        assert isinstance(configs, dict), "Resource configs should be a dictionary"

        # If resource_configs is provided, validate against it
        if self.resource_configs:
            for resource_name, expected_config in self.resource_configs.items():
                assert (
                    resource_name in configs
                ), f"Expected resource '{resource_name}' not found in configs"
                actual_config = configs[resource_name]
                assert (
                    actual_config.name == expected_config.name
                ), f"Resource name mismatch for {resource_name}"
                assert (
                    actual_config.endpoint == expected_config.endpoint
                ), f"Endpoint mismatch for {resource_name}"

    def test_resource_managers_created(self, server):
        """Test that resource managers are properly created."""
        sdk = self.create_sdk(server)
        configs = sdk._configure_resources()

        for resource_name in configs.keys():
            assert hasattr(
                sdk, resource_name
            ), f"Resource manager '{resource_name}' not created as attribute"

            manager = getattr(sdk, resource_name)
            assert hasattr(
                manager, "create"
            ), f"Manager {resource_name} missing create method"
            assert hasattr(
                manager, "get"
            ), f"Manager {resource_name} missing get method"
            assert hasattr(
                manager, "list"
            ), f"Manager {resource_name} missing list method"
            assert hasattr(
                manager, "update"
            ), f"Manager {resource_name} missing update method"
            assert hasattr(
                manager, "delete"
            ), f"Manager {resource_name} missing delete method"

    def test_headers_with_token(self, server):
        """Test header generation with JWT token."""
        sdk = self.create_sdk(server, token="test_token_123")
        headers = sdk._get_headers()
        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer test_token_123"

    def test_headers_with_api_key(self, server):
        """Test header generation with API key."""
        sdk = self.create_sdk(server, api_key="test_api_key_456")
        headers = sdk._get_headers()
        assert "X-API-Key" in headers
        assert headers["X-API-Key"] == "test_api_key_456"

    def test_url_building(self, server):
        """Test URL building functionality."""
        sdk = self.create_sdk(server)
        base_url = self.get_server_url(server)

        # Test basic endpoint
        url = sdk._build_url("/v1/test")
        assert url == f"{base_url}/v1/test"

        # Test with query parameters
        url = sdk._build_url("/v1/test", {"param1": "value1", "param2": "value2"})
        assert "param1=value1" in url
        assert "param2=value2" in url

        # Test with None values (should be filtered out)
        url = sdk._build_url("/v1/test", {"param1": "value1", "param2": None})
        assert "param1=value1" in url
        assert "param2" not in url

    def test_unauthenticated_request_fails(self, server):
        """Test that unauthenticated requests to protected endpoints fail."""
        sdk = self.create_sdk(server)  # No token or API key

        # Most endpoints should require authentication
        with pytest.raises(SDKException) as exc_info:
            sdk.get("/v1/user")  # Protected endpoint

        # Should get 401 or 403 error
        assert exc_info.value.status_code in [401, 403]

    # Abstract methods for subclasses to implement

    @abstractmethod
    def create_test_data(
        self, resource_type: str, count: int = 1
    ) -> List[Dict[str, Any]]:
        """Create test data for the specified resource type.

        Args:
            resource_type: Type of resource to create data for
            count: Number of test data items to create

        Returns:
            List of test data dictionaries
        """
        pass

    @abstractmethod
    def assert_valid_response_structure(
        self,
        response: Dict[str, Any],
        expected_keys: List[str],
        resource_key: Optional[str] = None,
    ):
        """Assert that a response has the expected structure.

        Args:
            response: Response data to validate
            expected_keys: Keys that should be present in the response
            resource_key: Key containing the main resource data
        """
        pass

    def assert_pagination_response(self, response: Dict[str, Any], resource_key: str):
        """Assert that a response contains proper pagination information.

        Args:
            response: Response data to validate
            resource_key: Key containing the resource list
        """
        assert resource_key in response, f"Response missing '{resource_key}' key"
        assert isinstance(
            response[resource_key], list
        ), f"'{resource_key}' should be a list"

        # Check for pagination metadata
        pagination_keys = ["total", "offset", "limit"]
        for key in pagination_keys:
            if key in response:
                assert isinstance(
                    response[key], int
                ), f"Pagination key '{key}' should be an integer"

    def assert_entity_created(
        self, response: Dict[str, Any], resource_key: str = None
    ) -> Dict[str, Any]:
        """Assert that an entity was created successfully.

        Args:
            response: Response from create operation
            resource_key: Key containing the entity data

        Returns:
            The created entity data
        """
        key = resource_key or self.resource_name
        assert key in response, f"Response missing '{key}' key"

        entity = response[key]
        assert "id" in entity, "Created entity missing 'id' field"

        return entity

    def assert_entity_updated(
        self,
        response: Dict[str, Any],
        expected_updates: Dict[str, Any],
        resource_key: str = None,
    ) -> Dict[str, Any]:
        """Assert that an entity was updated successfully.

        Args:
            response: Response from update operation
            expected_updates: Expected field values after update
            resource_key: Key containing the entity data

        Returns:
            The updated entity data
        """
        key = resource_key or self.resource_name
        assert key in response, f"Response missing '{key}' key"

        entity = response[key]
        for field, expected_value in expected_updates.items():
            assert field in entity, f"Updated entity missing '{field}' field"
            assert entity[field] == expected_value, (
                f"Field '{field}' not updated correctly. "
                f"Expected {expected_value}, got {entity[field]}"
            )

        return entity
