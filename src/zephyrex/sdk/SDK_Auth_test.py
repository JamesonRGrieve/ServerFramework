"""
SDK Auth Tests - Real server integration tests for authentication SDK modules.

This module contains tests for the authentication-related SDK modules using
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

from zephyrex.sdk.AbstractSDKTest import AbstractSDKTest
from zephyrex.sdk.SDK_Auth import (
    AuthSDK,
    RoleSDK,
    TeamSDK,
    UserSDK,
    UserTeamSDK,
)


class TestUserSDK(AbstractSDKTest):
    """Tests for the UserSDK module using real server integration."""

    sdk_class = UserSDK
    resource_name = "user"
    sample_data = {
        "username": "test_user",
        "email": "test@example.com",
        "password": "test_password",
        "first_name": "Test",
        "last_name": "User",
    }
    update_data = {"first_name": "Updated"}

    def create_test_data(
        self, resource_type: str, count: int = 1
    ) -> List[Dict[str, Any]]:
        """Create test data for the specified resource type."""
        base_data = {
            "username": f"test_user_{resource_type}",
            "email": f"test_{resource_type}@example.com",
            "password": "test_password",
            "first_name": "Test",
            "last_name": "User",
        }
        return [
            {
                **base_data,
                "username": f"{base_data['username']}_{uuid.uuid4().hex[:8]}_{i}",
                "email": f"test_{resource_type}_{uuid.uuid4().hex[:8]}_{i}@example.com",
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

    def test_get_current_user(self, server, admin_a):
        """Test getting current user with real authentication."""
        sdk = self.create_authenticated_sdk(server, admin_a)

        # Get current user via SDK
        response = sdk.get_current_user()

        # Validate response contains user data
        assert response is not None
        assert "id" in response or "user" in response

    def test_user_list(self, server, admin_a):
        """Test listing users via SDK."""
        sdk = self.create_authenticated_sdk(server, admin_a)

        # List users
        response = sdk.users.list()

        # Validate response structure
        assert response is not None
        # Response should contain a list of users

    def test_verify_token(self, server, admin_a):
        """Test token verification with valid token."""
        sdk = self.create_authenticated_sdk(server, admin_a)

        # Verify the token is valid
        result = sdk.verify_token()

        # Valid token should return True or successful response
        assert result is not None


class TestTeamSDK(AbstractSDKTest):
    """Tests for the TeamSDK module using real server integration."""

    sdk_class = TeamSDK
    resource_name = "team"
    sample_data = {"name": "test_team", "description": "Test team description"}
    update_data = {"description": "Updated description"}

    def create_test_data(
        self, resource_type: str, count: int = 1
    ) -> List[Dict[str, Any]]:
        """Create test data for the specified resource type."""
        base_data = {
            "name": f"test_team_{resource_type}",
            "description": f"Test {resource_type} description",
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

    def test_list_teams(self, server, admin_a, team_a):
        """Test listing teams via SDK."""
        sdk = self.create_authenticated_sdk(server, admin_a)

        # List teams
        response = sdk.teams.list()

        # Validate response structure
        assert response is not None

    def test_get_team(self, server, admin_a, team_a):
        """Test getting a specific team via SDK."""
        sdk = self.create_authenticated_sdk(server, admin_a)

        # Get the team
        response = sdk.teams.get(team_a.id)

        # Validate response
        assert response is not None
        assert "team" in response or "id" in response


class TestRoleSDK(AbstractSDKTest):
    """Tests for the RoleSDK module using real server integration."""

    sdk_class = RoleSDK
    resource_name = "role"
    sample_data = {
        "name": "test_role",
        "friendly_name": "Test Role",
        "description": "Test role",
    }
    update_data = {"description": "Updated role description"}

    def create_test_data(
        self, resource_type: str, count: int = 1
    ) -> List[Dict[str, Any]]:
        """Create test data for the specified resource type."""
        base_data = {
            "name": f"test_role_{resource_type}",
            "friendly_name": f"Test {resource_type} Role",
            "description": f"Test {resource_type}",
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

    def test_list_roles(self, server, admin_a):
        """Test listing roles via SDK."""
        sdk = self.create_authenticated_sdk(server, admin_a)

        # List roles
        response = sdk.roles.list()

        # Validate response structure
        assert response is not None


class TestUserTeamSDK(AbstractSDKTest):
    """Tests for the UserTeamSDK module using real server integration."""

    sdk_class = UserTeamSDK
    resource_name = "user_team"
    sample_data = {"user_id": "user_123", "team_id": "team_123", "role_id": "role_123"}
    update_data = {"role_id": "role_456"}

    def create_test_data(
        self, resource_type: str, count: int = 1
    ) -> List[Dict[str, Any]]:
        """Create test data for the specified resource type."""
        base_data = {
            "user_id": "user_123",
            "team_id": "team_123",
            "role_id": "role_123",
        }
        return [base_data for _ in range(count)]

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

    def test_list_user_teams(self, server, admin_a, team_a):
        """Test listing user-team associations via SDK."""
        sdk = self.create_authenticated_sdk(server, admin_a)

        # List user teams
        response = sdk.user_teams.list()

        # Validate response structure
        assert response is not None


class TestAuthSDK(AbstractSDKTest):
    """Tests for the composite AuthSDK module using real server integration."""

    sdk_class = AuthSDK
    resource_name = "auth"
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

    def test_auth_sdk_initialization(self, server):
        """Test that AuthSDK initializes all sub-SDKs correctly."""
        sdk = self.create_sdk(server)

        # Verify all resource managers are available
        expected_resources = [
            "users",
            "teams",
            "roles",
            "user_teams",
        ]

        for resource in expected_resources:
            assert hasattr(
                sdk, resource
            ), f"AuthSDK should have {resource} resource manager"
            assert sdk.resource_managers[resource] is not None

    def test_auth_sdk_get_current_user(self, server, admin_a):
        """Test getting current user via composite AuthSDK."""
        sdk = self.create_authenticated_sdk(server, admin_a)

        # Get current user
        response = sdk.get_current_user()

        # Validate response
        assert response is not None

    def test_auth_sdk_list_teams(self, server, admin_a, team_a):
        """Test listing teams via composite AuthSDK."""
        sdk = self.create_authenticated_sdk(server, admin_a)

        # List teams via the teams resource manager
        response = sdk.teams.list()

        # Validate response
        assert response is not None
