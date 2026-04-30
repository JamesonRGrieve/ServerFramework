import uuid
from typing import Any, Dict, List, Optional

import pytest
from faker import Faker

from serverframework.AbstractTest import CategoryOfTest, ClassOfTestsConfig, ParentEntity, SkipThisTest
from serverframework.endpoints.AbstractEPTest import AbstractEPTest
from serverframework.logic.BLL_Providers import (
    ProviderExtensionAbilityModel,
    ProviderExtensionModel,
    ProviderInstanceModel,
    ProviderInstanceSettingModel,
    ProviderModel,
    RotationModel,
    RotationProviderInstanceModel,
)

# Initialize faker
faker = Faker()


@pytest.mark.ep
@pytest.mark.providers
class TestProviderEndpoints(AbstractEPTest):
    """Tests for the Provider Management endpoints."""

    base_endpoint = "provider"
    entity_name = "provider"
    required_fields = ["id", "name", "created_at"]
    string_field_to_update = "name"
    system_entity = True
    class_under_test = ProviderModel

    supports_search = True
    searchable_fields = ["name"]
    search_example_value = "Test Provider"

    create_fields = {
        "name": lambda: f"test_provider_{faker.uuid4()}",
        "friendly_name": "Test Provider",
        "agent_settings_json": '{"test": "value"}',
    }
    update_fields = {
        "friendly_name": "Updated Provider",
        "agent_settings_json": '{"updated": "value"}',
    }
    unique_fields = ["name"]

    def create_payload(
        self,
        name: Optional[str] = None,
        parent_ids: Optional[Dict[str, str]] = None,
        team_id: Optional[str] = None,
        minimal: bool = False,
        invalid_data: bool = False,
    ) -> Dict[str, Any]:
        """Create a payload for provider creation."""
        name = name or self.faker.catch_phrase()

        if invalid_data:
            # Create invalid data for testing validation
            payload = {"name": 12345, "agent_settings_json": None}  # Invalid types
        else:
            # Include all fields
            payload = {
                "name": name,
                "friendly_name": "Test Provider",
                "agent_settings_json": '{"api_base": "https://api.example.com"}',
            }

        return payload


@pytest.mark.ep
@pytest.mark.extensions
class TestProviderExtensionEndpoints(AbstractEPTest):
    """Test class for Provider Extension endpoints."""

    # Test configuration
    test_config = ClassOfTestsConfig(
        categories=[CategoryOfTest.ENDPOINT, CategoryOfTest.REST],
        timeout=60,
        cleanup=True,
    )

    # Base endpoint configuration
    base_endpoint = "provider/extension"
    entity_name = "provider_extension"
    string_field_to_update = "provider_id"
    required_fields = [
        "provider_id",
        "extension_id",
    ]
    system_entity = True
    class_under_test = ProviderExtensionModel

    # Parent entity configurations
    parent_entities = [
        ParentEntity(
            name="provider",
            foreign_key="provider_id",
            nullable=False,
            system=True,
            path_level=1,
            test_class=lambda: TestProviderEndpoints,
        ),
        ParentEntity(
            name="extension",
            foreign_key="extension_id",
            nullable=False,
            system=True,
            path_level=1,
            test_class=lambda: __import__(
                "serverframework.endpoints.EP_Extensions_test", fromlist=["TestExtensionEndpoints"]
            ).TestExtensionEndpoints,
        ),
    ]

    # Search configuration
    supports_search = True
    searchable_fields = ["provider_id", "extension_id"]
    search_example_value = "Test Extension"

    # Test data generation
    create_fields = {
        "provider_id": None,  # Will be populated from parent entities
        "extension_id": lambda: str(uuid.uuid4()),  # Mock extension ID
    }
    update_fields = {
        "extension_id": lambda: str(uuid.uuid4()),  # Different extension ID
    }
    unique_fields = []

    # Tests to skip (if any)
    _skip_tests = [
        SkipThisTest(
            name="test_GET_404_nonexistent_parent",
            details="not implemeneted",
        ),
        SkipThisTest(
            name="test_GET_200_provider_extensions",
            details="not implemented",
        ),
        SkipThisTest(
            name="test_POST_200_install_extension",
            details="not implemented",
        ),
        SkipThisTest(
            name="test_POST_204_uninstall_extension",
            details="not implemented",
        ),
        SkipThisTest(
            name="test_POST_404_install_extension_nonexistent",
            details="not implemented",
        ),
        SkipThisTest(
            name="test_POST_404_install_extension_nonexistent_provider",
            details="not implemented",
        ),
        SkipThisTest(
            name="test_POST_404_uninstall_extension_nonexistent_provider",
            details="not implemented",
        ),
        SkipThisTest(
            name="test_POST_401_install_extension_unauthorized",
            details="not implemented",
        ),
        SkipThisTest(
            name="test_POST_422_install_extension_invalid_options",
            details="not implemented",
        ),
        SkipThisTest(
            name="test_PUT_200_update_extension",
            details="not implemented",
        ),
        SkipThisTest(name="test_DELETE_204_extension", details="not implemented"),
    ]

    def create_parent_entities(
        self, server: Any, admin_a, team_a: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create parent entities required for testing this resource.

        Args:
            server: Test client instance
            admin_a: Admin user object with jwt property
            team_a: Team context

        Returns:
            Dict containing created parent entities
        """
        from serverframework.lib.Environment import env

        provider_name = f"Test Provider {uuid.uuid4()}"
        provider_payload = {
            "provider": {
                "name": provider_name,
                "team_id": team_a.id if team_a else None,
            }
        }

        endpoint = "/v1/provider"
        # Since providers are system entities, we need to include the API key
        headers = self._get_appropriate_headers(
            admin_a.jwt, api_key=env("ROOT_API_KEY")
        )
        response = server.post(
            endpoint,
            json=provider_payload,
            headers=headers,
        )

        self._assert_response_status(
            response, 201, "POST provider", endpoint, provider_payload
        )

        return {"provider": self._assert_entity_in_response(response)}

    def create_payload(
        self,
        name: Optional[str] = None,
        parent_ids: Optional[Dict[str, str]] = None,
        team_id: Optional[str] = None,
        minimal: bool = False,
        invalid_data: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a payload for provider extension creation.

        Args:
            name: Optional name (not used for ProviderExtension)
            parent_ids: Optional parent IDs
            team_id: Optional team ID
            minimal: Whether to create a minimal payload
            invalid_data: Whether to create invalid data

        Returns:
            Dict containing provider extension creation payload
        """

        name = name or f"Test Provider Extension {uuid.uuid4()}"
        payload = {
            "provider_id": parent_ids.get("provider_id") if parent_ids else None,
            "extension_id": (parent_ids.get("extension_id") if parent_ids else None),
        }

        if invalid_data:
            # Create invalid data for testing validation
            payload = {"provider_id": 12345, "extension_id": None}  # Invalid types
        elif minimal:
            # Only include required fields
            payload = {
                "provider_id": parent_ids.get("provider_id") if parent_ids else None,
                "extension_id": (
                    parent_ids.get("extension_id") if parent_ids else None
                ),
            }

        return payload

    def test_GET_200_available_extensions(
        self, server: Any, admin_a, team_a: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Test listing available extensions.

        Args:
            server: Test client instance
            admin_a.jwt: Admin JWT token
            team_a: Team context

        Returns:
            List of available extensions
        """

        endpoint = "/v1/provider/extension"
        response = server.get(
            endpoint, headers=self._get_appropriate_headers(admin_a.jwt)
        )

        self._assert_response_status(
            response, 200, "GET available extensions", endpoint
        )

        self._assert_entities_in_response(response)

    def test_GET_200_provider_extensions(
        self, server: Any, admin_a, team_a: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Test getting extensions for a specific provider.

        Args:
            server: Test client instance
            admin_a.jwt: Admin JWT token
            team_a: Team context

        Returns:
            List of provider extensions
        """

        parent_entities = self.create_parent_entities(server, admin_a, team_a)
        provider = parent_entities["provider"]

        base_endpoint = f"/v1/provider/extension/provider/{provider['id']}"
        response = server.get(
            base_endpoint, headers=self._get_appropriate_headers(admin_a.jwt)
        )

        self._assert_response_status(
            response, 200, "GET provider extensions", base_endpoint
        )

        return self._assert_entities_in_response(response)

    def test_POST_200_install_extension(
        self, server: Any, admin_a, team_a: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Test installing an extension.

        Args:
            server: Test client instance
            admin_a.jwt: Admin JWT token
            team_a: Team context

        Returns:
            Installation result
        """

        parent_entities = self.create_parent_entities(server, admin_a, team_a)
        provider = parent_entities["provider"]
        extension = self.test_POST_201(server, admin_a.jwt, team_a)

        options = {"option1": "value1", "option2": "value2"}
        endpoint = f"/v1/provider/extension/{extension['id']}/install?provider_id={provider['id']}"
        response = server.post(
            endpoint, json=options, headers=self._get_appropriate_headers(admin_a.jwt)
        )

        self._assert_response_status(
            response, 200, "POST install extension", endpoint, options
        )

        json_response = response.json()
        if "result" not in json_response:
            raise AssertionError("Response missing 'result' key")
        if json_response["result"] != "success":
            raise AssertionError(
                f"Installation failed with result: {json_response['result']}"
            )

        installed_extension = json_response.get("extension")
        if not installed_extension:
            raise AssertionError("Response missing 'extension' data")
        if installed_extension["id"] != extension["id"]:
            raise AssertionError(
                f"Installed extension ID mismatch: expected {extension['id']}, got {installed_extension['id']}"
            )
        if installed_extension["provider_id"] != provider["id"]:
            raise AssertionError(
                f"Provider ID mismatch: expected {provider['id']}, got {installed_extension['provider_id']}"
            )

        # Verify installation
        verify_endpoint = f"/v1/provider/extension/provider/{provider['id']}"
        verify_response = server.get(
            verify_endpoint, headers=self._get_appropriate_headers(admin_a.jwt)
        )

        self._assert_response_status(
            verify_response, 200, "GET verify installation", verify_endpoint
        )

        verify_json = verify_response.json()
        if not any(ext["id"] == extension["id"] for ext in verify_json["extensions"]):
            raise AssertionError(
                "Installed extension not found in provider's extensions list"
            )

        return json_response

    def test_POST_204_uninstall_extension(
        self, server: Any, admin_a, team_a: Dict[str, Any]
    ) -> None:
        """
        Test uninstalling an extension.

        Args:
            server: Test client instance
            admin_a.jwt: Admin JWT token
            team_a: Team context
        """

        parent_entities = self.create_parent_entities(server, admin_a, team_a)
        provider = parent_entities["provider"]
        extension = self.test_POST_201(server, admin_a.jwt, team_a)

        # Install the extension first
        install_endpoint = f"/v1/provider/extension/{extension['id']}/install?provider_id={provider['id']}"
        install_options = {"option1": "value1"}
        install_response = server.post(
            install_endpoint,
            json=install_options,
            headers=self._get_appropriate_headers(admin_a.jwt),
        )

        self._assert_response_status(
            install_response,
            200,
            "POST install extension",
            install_endpoint,
            install_options,
        )

        # Uninstall the extension
        uninstall_endpoint = f"/v1/provider/extension/{extension['id']}/uninstall?provider_id={provider['id']}"
        uninstall_response = server.post(
            uninstall_endpoint, headers=self._get_appropriate_headers(admin_a.jwt)
        )

        self._assert_response_status(
            uninstall_response, 204, "POST uninstall extension", uninstall_endpoint
        )

        # Verify uninstallation
        verify_endpoint = f"/v1/provider/extension/provider/{provider['id']}"
        verify_response = server.get(
            verify_endpoint, headers=self._get_appropriate_headers(admin_a.jwt)
        )

        self._assert_response_status(
            verify_response, 200, "GET verify uninstallation", verify_endpoint
        )

        verify_json = verify_response.json()
        if any(ext["id"] == extension["id"] for ext in verify_json["extensions"]):
            raise AssertionError(
                "Uninstalled extension still present in provider's extensions list"
            )

    def test_GET_404_provider_extensions_nonexistent(
        self, server: Any, admin_a, team_a: Dict[str, Any]
    ) -> None:
        """
        Test getting extensions for a nonexistent provider fails.

        Args:
            server: Test client instance
            admin_a.jwt: Admin JWT token
            team_a: Team context
        """

        nonexistent_id = str(uuid.uuid4())
        endpoint = f"/v1/provider/extension/provider/{nonexistent_id}"
        response = server.get(
            endpoint, headers=self._get_appropriate_headers(admin_a.jwt)
        )

        self._assert_response_status(
            response, 404, "GET nonexistent provider extensions", endpoint
        )

    def test_POST_404_install_extension_nonexistent(
        self, server: Any, admin_a, team_a: Dict[str, Any]
    ) -> None:
        """
        Test installing a nonexistent extension fails.

        Args:
            server: Test client instance
            admin_a.jwt: Admin JWT token
            team_a: Team context
        """

        parent_entities = self.create_parent_entities(server, admin_a, team_a)
        provider = parent_entities["provider"]

        nonexistent_id = str(uuid.uuid4())
        endpoint = f"/v1/provider/extension/{nonexistent_id}/install?provider_id={provider['id']}"
        response = server.post(
            endpoint,
            json={"option1": "value1"},
            headers=self._get_appropriate_headers(admin_a.jwt),
        )

        self._assert_response_status(
            response, 404, "POST install nonexistent extension", endpoint
        )

    def test_POST_404_install_extension_nonexistent_provider(
        self, server: Any, admin_a, team_a: Dict[str, Any]
    ) -> None:
        """
        Test installing an extension to a nonexistent provider fails.

        Args:
            server: Test client instance
            admin_a.jwt: Admin JWT token
            team_a: Team context
        """

        extension = self.test_POST_201(server, admin_a.jwt, team_a)
        nonexistent_id = str(uuid.uuid4())
        endpoint = f"/v1/provider/extension/{extension['id']}/install?provider_id={nonexistent_id}"
        response = server.post(
            endpoint,
            json={"option1": "value1"},
            headers=self._get_appropriate_headers(admin_a.jwt),
        )

        self._assert_response_status(
            response, 404, "POST install to nonexistent provider", endpoint
        )

    def test_POST_404_uninstall_extension_nonexistent_provider(
        self, server: Any, admin_a, team_a: Dict[str, Any]
    ) -> None:
        """
        Test uninstalling an extension from a nonexistent provider fails.

        Args:
            server: Test client instance
            admin_a.jwt: Admin JWT token
            team_a: Team context
        """
        extension = self.test_POST_201(server, admin_a.jwt, team_a)
        nonexistent_id = str(uuid.uuid4())
        endpoint = f"/v1/provider/extension/{extension['id']}/uninstall?provider_id={nonexistent_id}"
        response = server.post(
            endpoint, headers=self._get_appropriate_headers(admin_a.jwt)
        )

        self._assert_response_status(
            response, 404, "POST uninstall from nonexistent provider", endpoint
        )

    def test_POST_401_install_extension_unauthorized(self, server: Any) -> None:
        """
        Test installing an extension without authorization fails.

        Args:
            server: Test client instance
        """

        extension_id = str(uuid.uuid4())
        provider_id = str(uuid.uuid4())
        endpoint = (
            f"/v1/provider/extension/{extension_id}/install?provider_id={provider_id}"
        )
        response = server.post(endpoint, json={"option1": "value1"})

        self._assert_response_status(
            response, 401, "POST install without auth", endpoint
        )

    def test_POST_422_install_extension_invalid_options(
        self, server: Any, admin_a, team_a: Dict[str, Any]
    ) -> None:
        """
        Test installing an extension with invalid options fails.

        Args:
            server: Test client instance
            admin_a.jwt: Admin JWT token
            team_a: Team context
        """

        parent_entities = self.create_parent_entities(server, admin_a, team_a)
        provider = parent_entities["provider"]
        extension = self.test_POST_201(server, admin_a.jwt, team_a)

        invalid_options = {"invalid_option": {"nested": "not allowed"}}
        endpoint = f"/v1/provider/extension/{extension['id']}/install?provider_id={provider['id']}"
        response = server.post(
            endpoint,
            json=invalid_options,
            headers=self._get_appropriate_headers(admin_a.jwt),
        )

        self._assert_response_status(
            response,
            422,
            "POST install with invalid options",
            endpoint,
            invalid_options,
        )

    def test_PUT_200_update_extension(self, server, admin_a, team_a):
        extension = self.test_POST_201(server, admin_a.jwt, team_a)
        updated_name = f"Updated Extension {uuid.uuid4()}"
        payload = {
            "extension": {"name": updated_name, "description": "Updated description"}
        }
        response = server.put(
            f"/v1/provider/extension/{extension['id']}",
            json=payload,
            headers=self._get_appropriate_headers(admin_a.jwt),
        )
        self._assert_response_status(
            response, 200, "PUT extension", f"/v1/provider/extension/{extension['id']}"
        )
        assert response.json()["extension"]["name"] == updated_name

    def test_DELETE_204_extension(self, server, admin_a, team_a):
        extension = self.test_POST_201(server, admin_a, team_a)
        response = server.delete(
            f"/v1/provider/extension/{extension['id']}",
            headers=self._get_appropriate_headers(admin_a.jwt),
        )
        self._assert_response_status(
            response,
            204,
            "DELETE extension",
            f"/v1/provider/extension/{extension['id']}",
        )


@pytest.mark.ep
@pytest.mark.providers
class TestProviderInstanceEndpoints(AbstractEPTest):
    """Tests for the Provider Instance Management endpoints."""

    base_endpoint = "provider/instance"
    entity_name = "provider_instance"
    required_fields = ["id", "name", "provider_id", "created_at"]
    string_field_to_update = "name"
    system_entity = False
    class_under_test = ProviderInstanceModel

    parent_entities = [
        ParentEntity(
            name="provider",
            foreign_key="provider_id",
            nullable=False,
            system=True,
            path_level=None,  # Provider instance uses top-level path, not nested
            test_class=lambda: TestProviderEndpoints,
        ),
    ]

    supports_search = True
    searchable_fields = ["name", "model_name"]
    search_example_value = "Test Instance"

    create_fields = {
        "name": lambda: f"test_provider_instance_{faker.uuid4()}",
        "model_name": "test_model",
        "api_key": "test_api_key",
    }
    update_fields = {
        "name": "updated_provider_instance",
        "model_name": "updated_model",
        "api_key": "updated_api_key",
    }
    unique_fields = ["name"]

    def create_payload(
        self,
        name: Optional[str] = None,
        parent_ids: Optional[Dict[str, str]] = None,
        team_id: Optional[str] = None,
        minimal: bool = False,
        invalid_data: bool = False,
    ) -> Dict[str, Any]:
        """Create a payload for provider instance creation."""
        name = name or f"Test Instance {self.faker.company()}"

        if invalid_data:
            # Invalid data for validation tests
            return {
                "name": 12345,  # Number instead of string
                "provider_id": None,  # Missing required field
            }
        elif minimal:
            # Only required fields
            return {
                "name": name,
                "provider_id": parent_ids.get("provider_id") if parent_ids else None,
            }
        else:
            # Full payload
            return {
                "name": name,
                "provider_id": parent_ids.get("provider_id") if parent_ids else None,
                "model_name": "gpt-4",
                "api_key": "fake-api-key-for-testing",
                "team_id": team_id,
            }

    def test_GET_200_list(self, server: Any, admin_a: Any, team_a: Any):
        """Test listing entities."""
        # Create three test entities individually and verify creation
        self._create(server, admin_a.jwt, admin_a.id, key="list_1")
        self._create(server, admin_a.jwt, admin_a.id, key="list_2")
        self._create(server, admin_a.jwt, admin_a.id, key="list_3")

        # List entities and assert they are present
        self._list(server, admin_a.jwt, admin_a.id, team_a.id)
        self._list_assert("list_result")

    def test_GET_200_list_by_team(
        self, server: Any, admin_a, team_a: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Test retrieving provider instances filtered by team."""

        instance = self.test_POST_201(server, admin_a, team_a)

        endpoint = f"{self.get_list_endpoint()}?team_id={team_a.id}"
        response = server.get(
            endpoint, headers=self._get_appropriate_headers(admin_a.jwt)
        )

        self._assert_response_status(response, 200, "GET list by team", endpoint)
        entities = self._assert_entities_in_response(response)

        instance_ids = [i["id"] for i in entities]
        if instance and instance.id not in instance_ids:
            raise AssertionError(
                f"[{self.entity_name}] Created instance not found in team-filtered results\n"
                f"Expected ID: {instance.id}\n"
                f"Found IDs: {instance_ids}"
            )


@pytest.mark.ep
@pytest.mark.providers
class TestRotationEndpoints(AbstractEPTest):
    """Tests for the Rotation Management endpoints."""

    base_endpoint = "rotation"
    entity_name = "rotation"
    required_fields = ["id", "name", "created_at"]
    string_field_to_update = "name"

    supports_search = True
    searchable_fields = ["name", "description"]
    search_example_value = "Test Rotation"
    system_entity = False
    class_under_test = RotationModel

    parent_entities: List[ParentEntity] = []  # Rotation has no parent entities

    create_fields = {
        "name": lambda: f"test_rotation_{faker.uuid4()}",
        "description": "Test rotation description",
    }
    update_fields = {
        "name": "updated_rotation",
        "description": "Updated rotation description",
    }
    unique_fields = ["name"]

    def create_payload(
        self,
        name: Optional[str] = None,
        parent_ids: Optional[Dict[str, str]] = None,
        team_id: Optional[str] = None,
        minimal: bool = False,
        invalid_data: bool = False,
    ) -> Dict[str, Any]:
        """Create a payload for rotation creation."""
        name = name or self.faker.catch_phrase()

        if invalid_data:
            # Create invalid data for testing validation
            return {"name": 12345, "description": None}
        else:
            return {
                "name": name or f"Test Rotation {faker.uuid4()}",
                "description": f"A test rotation for {name or faker.uuid4()}",
            }

    def test_GET_200_list_by_team(
        self, server: Any, admin_a, team_a: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Test retrieving rotations filtered by team."""

        rotation = self.test_POST_201(server, admin_a, team_a)

        endpoint = f"{self.get_list_endpoint()}?team_id={team_a.id}"
        response = server.get(
            endpoint, headers=self._get_appropriate_headers(admin_a.jwt)
        )

        self._assert_response_status(response, 200, "GET list by team", endpoint)
        entities = self._assert_entities_in_response(response)

        rotation_ids = [r["id"] for r in entities]
        if rotation and rotation.id not in rotation_ids:
            raise AssertionError(
                f"[{self.entity_name}] Created rotation not found in team-filtered results\n"
                f"Expected ID: {rotation.id}\n"
                f"Found IDs: {rotation_ids}"
            )


@pytest.mark.ep
@pytest.mark.providers
class TestRotationProviderInstanceEndpoints(AbstractEPTest):
    """Tests for the Rotation Provider Management endpoints."""

    base_endpoint = "rotation/provider/instance"
    entity_name = "rotation_provider_instance"
    required_fields = ["id", "rotation_id", "provider_instance_id"]
    string_field_to_update = None
    system_entity = False
    class_under_test = RotationProviderInstanceModel

    parent_entities = [
        ParentEntity(
            name="rotation",
            foreign_key="rotation_id",
            nullable=False,
            path_level=None,  # Top-level endpoint, not nested
            test_class=lambda: TestRotationEndpoints,
        ),
        ParentEntity(
            name="provider_instance",
            enabled=False,
            foreign_key="provider_instance_id",
            nullable=False,
            path_level=None,  # Top-level endpoint, not nested
            test_class=lambda: TestProviderInstanceEndpoints,
        ),
    ]

    create_fields = {
        "rotation_id": None,  # Will be populated in setup
        "provider_instance_id": None,  # Will be populated in setup
    }
    update_fields = {}  # No updateable fields besides system
    unique_fields = []

    _skip_tests = [
        SkipThisTest(
            name="test_GET_404_nonexistent_parent",
            details="not implemented",
        ),
    ]

    def nest_payload_in_entity(self, payload):
        """Wrap payload in entity envelope."""
        return {self.entity_name: payload}

    def create_payload(
        self,
        name: Optional[str] = None,
        parent_ids: Optional[Dict[str, str]] = None,
        team_id: Optional[str] = None,
        minimal: bool = False,
        invalid_data: bool = False,
    ) -> Dict[str, Any]:
        """Create a payload for rotation provider instance creation."""
        if invalid_data:
            # Create invalid data for testing validation
            payload = {"rotation_id": 12345, "provider_instance_id": None}
        elif minimal:
            # Only include required fields
            payload = {
                "rotation_id": parent_ids.get("rotation_id") if parent_ids else None,
                "provider_instance_id": (
                    parent_ids.get("provider_instance_id") if parent_ids else None
                ),
            }
        else:
            payload = {
                "rotation_id": parent_ids.get("rotation_id") if parent_ids else None,
                "provider_instance_id": (
                    parent_ids.get("provider_instance_id") if parent_ids else None
                ),
            }

        if self.entity_name == "rotation_provider_instance":
            # Special case for rotation provider instance
            return payload

        return self.nest_payload_in_entity(payload)

    def create_parent_entities(
        self, server: Any, admin_a, team_a: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create parent entities for rotation provider instance testing."""
        rotation_test = TestRotationEndpoints()
        rotation = rotation_test.test_POST_201(server, admin_a, team_a)

        provider_test = TestProviderEndpoints()
        provider = provider_test.test_POST_201(server, admin_a, team_a)

        provider_instance_test = TestProviderInstanceEndpoints()
        provider_instance = provider_instance_test.test_POST_201(
            server, admin_a, team_a
        )

        return {
            "rotation": rotation,
            "provider": provider,
            "provider_instance": provider_instance,
        }


@pytest.mark.ep
@pytest.mark.providers
class TestProviderExtensionAbilityEndpoints(AbstractEPTest):
    """Tests for the Provider Extension Ability Management endpoints."""

    base_endpoint = "extension/ability/provider"
    entity_name = "provider_extension_ability"
    required_fields = [
        "provider_extension_id",
        "ability_id",
    ]
    string_field_to_update = (
        None  # This is a relationship entity with no string fields to update
    )
    system_entity = True
    class_under_test = ProviderExtensionAbilityModel

    parent_entities = [
        ParentEntity(
            name="provider_extension",
            foreign_key="provider_extension_id",
            nullable=False,
            path_level=None,  # Top-level endpoint
            test_class=lambda: TestProviderExtensionEndpoints,
        ),
        ParentEntity(
            name="ability",
            foreign_key="ability_id",
            nullable=False,
            path_level=None,  # Top-level endpoint
            test_class=lambda: __import__(
                "serverframework.endpoints.EP_Extensions_test", fromlist=["TestAbilityEndpoints"]
            ).TestAbilityEndpoints,
        ),
    ]

    # Tests to skip (if any)
    _skip_tests = [
        SkipThisTest(
            name="test_GET_404_nonexistent_parent",
            details="not implemeneted",
        ),
        SkipThisTest(
            name="test_GQL_mutation_update",
            details="This is a relationship entity with no updateable fields",
        ),
    ]

    create_fields = {
        "provider_extension_id": None,  # Will be populated in setup
        "ability_id": lambda: str(uuid.uuid4()),
    }
    update_fields = {}  # No updateable fields besides system fields
    unique_fields = []

    def create_payload(
        self,
        name: Optional[str] = None,
        parent_ids: Optional[Dict[str, str]] = None,
        team_id: Optional[str] = None,
        minimal: bool = False,
        invalid_data: bool = False,
    ) -> Dict[str, Any]:
        """Create a payload for provider extension ability creation."""
        payload = {
            "provider_extension_id": (
                parent_ids.get("provider_extension_id") if parent_ids else None
            ),
            "ability_id": parent_ids.get("ability_id") if parent_ids else None,
        }

        if invalid_data:
            payload = {
                "provider_extension_id": 12345,
                "ability_id": 6789,
            }
        elif minimal:
            payload = {
                "provider_extension_id": (
                    parent_ids.get("provider_extension_id") if parent_ids else None
                ),
                "ability_id": parent_ids.get("ability_id") if parent_ids else None,
            }

        return payload


@pytest.mark.ep
@pytest.mark.providers
class TestProviderInstanceSettingsEndpoints(AbstractEPTest):
    base_endpoint = "provider/instance/setting"
    entity_name = "provider_instance_setting"
    string_field_to_update = "value"
    required_fields = ["id", "provider_instance_id", "key", "value"]
    system_entity = False
    class_under_test = ProviderInstanceSettingModel

    _skip_tests = [
        SkipThisTest(
            name="test_GET_404_nonexistent_parent",
            details="not implemented",
        ),
        SkipThisTest(
            name="test_GET_200_list",
            details="Provider instance settings require filtering by provider instance",
        ),
    ]

    create_fields = {
        "provider_instance_id": None,  # Will be populated from parent
        "key": "test_setting_key",
        "value": "test-setting-value",
    }
    update_fields = {
        "value": "updated-setting-value",
    }
    unique_fields = []  # No unique fields for settings

    parent_entities = [
        ParentEntity(
            name="provider_instance",
            foreign_key="provider_instance_id",
            nullable=False,
            path_level=None,  # Provider instance settings uses top-level path
            test_class=lambda: TestProviderInstanceEndpoints,
        ),
    ]

    def create_payload(
        self,
        name=None,
        parent_ids=None,
        team_id=None,
        minimal: bool = False,
        invalid_data: bool = False,
    ):

        payload = {
            "provider_instance_id": (
                parent_ids.get("provider_instance_id") if parent_ids else None
            ),
            "key": "setting_key",
            "value": "setting_value",
        }

        if invalid_data:
            payload = {"provider_instance_id": 12345, "key": None}
        elif minimal:
            payload = {
                "provider_instance_id": (
                    parent_ids.get("provider_instance_id") if parent_ids else None
                ),
                "key": "setting_key",
                "value": "setting_value",
            }

        return payload
