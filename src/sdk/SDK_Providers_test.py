"""
SDK Providers Tests - Real server integration tests for provider SDK modules.

This module contains tests for the provider-related SDK modules using
real HTTP requests against a test server. NO MOCKING - all tests validate
actual SDK behavior against real server responses.

Following the framework's testing philosophy from AGENTS.md:
- Tests use actual HTTP requests to a real test server
- No mocks - tests validate actual SDK behavior
- pytest-based with fixtures for test data
"""

import uuid
from typing import Any, Dict, List

import pytest

from sdk.AbstractSDKTest import AbstractSDKTest
from sdk.SDK_Providers import (
    ProviderInstanceSDK,
    ProviderSDK,
    ProvidersSDK,
    RotationSDK,
)


class TestProviderSDK(AbstractSDKTest):
    """Tests for the ProviderSDK module using real server integration."""

    sdk_class = ProviderSDK
    resource_name = "provider"
    sample_data = {
        "name": "test_provider",
        "friendly_name": "Test Provider",
        "agent_settings_json": '{"api_base": "https://api.test.com"}',
    }
    update_data = {"friendly_name": "Updated Provider"}

    def create_test_data(
        self, resource_type: str, count: int = 1
    ) -> List[Dict[str, Any]]:
        """Create test data for the specified resource type."""
        base_data = {
            "name": f"test_provider_{resource_type}",
            "friendly_name": f"Test {resource_type} Provider",
            "agent_settings_json": '{"api_base": "https://api.test.com"}',
        }
        return [
            {
                **base_data,
                "name": f"{base_data['name']}_{uuid.uuid4().hex[:8]}_{i}",
            }
            for i in range(count)
        ]

    def assert_valid_response_structure(
        self,
        response: Dict[str, Any],
        expected_keys: List[str],
        resource_key: str = None,
    ):
        """Assert that a response has the expected structure."""
        if resource_key:
            assert resource_key in response, f"Response missing '{resource_key}' key"
            data = response[resource_key]
        else:
            data = response

        for key in expected_keys:
            assert key in data, f"Response missing expected key: {key}"

    def test_list_providers(self, server, admin_a):
        """Test listing providers via SDK."""
        sdk = self.create_authenticated_sdk(server, admin_a)

        # List providers
        response = sdk.providers.list()

        # Validate response structure
        assert response is not None


class TestProviderInstanceSDK(AbstractSDKTest):
    """Tests for the ProviderInstanceSDK module using real server integration."""

    sdk_class = ProviderInstanceSDK
    resource_name = "provider_instance"
    sample_data = {
        "name": "test_instance",
        "provider_id": "provider_123",
        "model_name": "gpt-4",
        "api_key": "test_api_key",
    }
    update_data = {"model_name": "gpt-3.5-turbo"}

    def create_test_data(
        self, resource_type: str, count: int = 1
    ) -> List[Dict[str, Any]]:
        """Create test data for the specified resource type."""
        base_data = {
            "name": f"test_instance_{resource_type}",
            "provider_id": "provider_123",
            "model_name": "gpt-4",
            "api_key": "test_api_key",
        }
        return [
            {
                **base_data,
                "name": f"{base_data['name']}_{uuid.uuid4().hex[:8]}_{i}",
            }
            for i in range(count)
        ]

    def assert_valid_response_structure(
        self,
        response: Dict[str, Any],
        expected_keys: List[str],
        resource_key: str = None,
    ):
        """Assert that a response has the expected structure."""
        if resource_key:
            assert resource_key in response, f"Response missing '{resource_key}' key"
            data = response[resource_key]
        else:
            data = response

        for key in expected_keys:
            assert key in data, f"Response missing expected key: {key}"

    def test_list_provider_instances(self, server, admin_a):
        """Test listing provider instances via SDK."""
        sdk = self.create_authenticated_sdk(server, admin_a)

        # List provider instances
        response = sdk.provider_instances.list()

        # Validate response structure
        assert response is not None


class TestRotationSDK(AbstractSDKTest):
    """Tests for the RotationSDK module using real server integration."""

    sdk_class = RotationSDK
    resource_name = "rotation"
    sample_data = {
        "name": "test_rotation",
        "description": "Test rotation description",
    }
    update_data = {"description": "Updated rotation description"}

    def create_test_data(
        self, resource_type: str, count: int = 1
    ) -> List[Dict[str, Any]]:
        """Create test data for the specified resource type."""
        base_data = {
            "name": f"test_rotation_{resource_type}",
            "description": f"Test {resource_type} rotation description",
        }
        return [
            {
                **base_data,
                "name": f"{base_data['name']}_{uuid.uuid4().hex[:8]}_{i}",
            }
            for i in range(count)
        ]

    def assert_valid_response_structure(
        self,
        response: Dict[str, Any],
        expected_keys: List[str],
        resource_key: str = None,
    ):
        """Assert that a response has the expected structure."""
        if resource_key:
            assert resource_key in response, f"Response missing '{resource_key}' key"
            data = response[resource_key]
        else:
            data = response

        for key in expected_keys:
            assert key in data, f"Response missing expected key: {key}"

    def test_list_rotations(self, server, admin_a):
        """Test listing rotations via SDK."""
        sdk = self.create_authenticated_sdk(server, admin_a)

        # List rotations
        response = sdk.rotations.list()

        # Validate response structure
        assert response is not None


class TestProvidersSDK(AbstractSDKTest):
    """Tests for the composite ProvidersSDK module using real server integration."""

    sdk_class = ProvidersSDK
    resource_name = "providers"
    sample_data = {}
    update_data = {}

    def create_test_data(
        self, resource_type: str, count: int = 1
    ) -> List[Dict[str, Any]]:
        """Create test data for the specified resource type."""
        return [{}]

    def assert_valid_response_structure(
        self,
        response: Dict[str, Any],
        expected_keys: List[str],
        resource_key: str = None,
    ):
        """Assert that a response has the expected structure."""
        if resource_key:
            assert resource_key in response, f"Response missing '{resource_key}' key"
            data = response[resource_key]
        else:
            data = response

        for key in expected_keys:
            assert key in data, f"Response missing expected key: {key}"

    def test_providers_sdk_initialization(self, server):
        """Test that ProvidersSDK initializes all sub-SDKs correctly."""
        sdk = self.create_sdk(server)

        # Verify all resource managers are available
        expected_resources = [
            "providers",
            "provider_instances",
            "rotations",
        ]

        for resource in expected_resources:
            assert hasattr(
                sdk, resource
            ), f"ProvidersSDK should have {resource} resource manager"
            assert sdk.resource_managers[resource] is not None

    def test_providers_sdk_list_providers(self, server, admin_a):
        """Test listing providers via composite ProvidersSDK."""
        sdk = self.create_authenticated_sdk(server, admin_a)

        # List providers via the providers resource manager
        response = sdk.providers.list()

        # Validate response
        assert response is not None

    def test_providers_sdk_list_provider_instances(self, server, admin_a):
        """Test listing provider instances via composite ProvidersSDK."""
        sdk = self.create_authenticated_sdk(server, admin_a)

        # List provider instances via the provider_instances resource manager
        response = sdk.provider_instances.list()

        # Validate response
        assert response is not None

    def test_providers_sdk_list_rotations(self, server, admin_a):
        """Test listing rotations via composite ProvidersSDK."""
        sdk = self.create_authenticated_sdk(server, admin_a)

        # List rotations via the rotations resource manager
        response = sdk.rotations.list()

        # Validate response
        assert response is not None
