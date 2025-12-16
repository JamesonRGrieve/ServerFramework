"""
SDK Extensions Tests - Real server integration tests for extension SDK modules.

This module contains tests for the extension-related SDK modules using
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
from sdk.SDK_Extensions import AbilitySDK, ExtensionSDK, ExtensionsSDK


class TestExtensionSDK(AbstractSDKTest):
    """Tests for the ExtensionSDK module using real server integration."""

    sdk_class = ExtensionSDK
    resource_name = "extension"
    sample_data = {
        "name": "test_extension",
        "description": "Test extension description",
        "version": "1.0.0",
    }
    update_data = {"description": "Updated extension description"}

    def create_test_data(
        self, resource_type: str, count: int = 1
    ) -> List[Dict[str, Any]]:
        """Create test data for the specified resource type."""
        base_data = {
            "name": f"test_extension_{resource_type}",
            "description": f"Test {resource_type} extension description",
            "version": "1.0.0",
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

    def test_list_extensions(self, server, admin_a):
        """Test listing extensions via SDK."""
        sdk = self.create_authenticated_sdk(server, admin_a)

        # List extensions
        response = sdk.extensions.list()

        # Validate response structure
        assert response is not None


class TestAbilitySDK(AbstractSDKTest):
    """Tests for the AbilitySDK module using real server integration."""

    sdk_class = AbilitySDK
    resource_name = "ability"
    sample_data = {
        "name": "test_ability",
        "extension_id": "extension_123",
        "description": "Test ability description",
        "ability_type": "function",
    }
    update_data = {"description": "Updated ability description"}

    def create_test_data(
        self, resource_type: str, count: int = 1
    ) -> List[Dict[str, Any]]:
        """Create test data for the specified resource type."""
        base_data = {
            "name": f"test_ability_{resource_type}",
            "extension_id": "extension_123",
            "description": f"Test {resource_type} ability description",
            "ability_type": "function",
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

    def test_list_abilities(self, server, admin_a):
        """Test listing abilities via SDK."""
        sdk = self.create_authenticated_sdk(server, admin_a)

        # List abilities
        response = sdk.abilities.list()

        # Validate response structure
        assert response is not None


class TestExtensionsSDK(AbstractSDKTest):
    """Tests for the composite ExtensionsSDK module using real server integration."""

    sdk_class = ExtensionsSDK
    resource_name = "extensions"
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

    def test_extensions_sdk_initialization(self, server):
        """Test that ExtensionsSDK initializes all sub-SDKs correctly."""
        sdk = self.create_sdk(server)

        # Verify all resource managers are available
        expected_resources = ["extensions", "abilities"]

        for resource in expected_resources:
            assert hasattr(
                sdk, resource
            ), f"ExtensionsSDK should have {resource} resource manager"
            assert sdk.resource_managers[resource] is not None

    def test_extensions_sdk_list_extensions(self, server, admin_a):
        """Test listing extensions via composite ExtensionsSDK."""
        sdk = self.create_authenticated_sdk(server, admin_a)

        # List extensions via the extensions resource manager
        response = sdk.extensions.list()

        # Validate response
        assert response is not None

    def test_extensions_sdk_list_abilities(self, server, admin_a):
        """Test listing abilities via composite ExtensionsSDK."""
        sdk = self.create_authenticated_sdk(server, admin_a)

        # List abilities via the abilities resource manager
        response = sdk.abilities.list()

        # Validate response
        assert response is not None
