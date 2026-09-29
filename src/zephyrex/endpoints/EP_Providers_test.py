import uuid
from typing import Any, Dict, List, Optional

import pytest
from faker import Faker

from zephyrex.AbstractTest import (
    CategoryOfTest,
    ClassOfTestsConfig,
    ParentEntity,
    SkipThisTest,
)
from zephyrex.endpoints.AbstractEPTest import AbstractEPTest
from zephyrex.logic.BLL_Providers import (
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
                "name": name,  # type: ignore[dict-item]
                "friendly_name": "Test Provider",  # type: ignore[dict-item]
                "agent_settings_json": '{"api_base": "https://api.example.com"}',  # type: ignore[dict-item]
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
    # ProviderExtension has only foreign-key columns (provider_id,
    # extension_id) and no free-form string field — leaving
    # `string_field_to_update` set to a FK caused the inherited
    # multi-field GQL test to mint random UUIDs and fail FK lookup.
    string_field_to_update = None
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
            path_level=None,  # Body foreign key; the route is top-level
            test_class=lambda: TestProviderEndpoints,
        ),
        ParentEntity(
            name="extension",
            foreign_key="extension_id",
            nullable=False,
            system=True,
            path_level=None,  # Body foreign key; the route is top-level
            test_class=lambda: __import__(
                "zephyrex.endpoints.EP_Extensions_test",
                fromlist=["TestExtensionEndpoints"],
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
    unique_fields: list[str] = []

    # Tests to skip (if any)
    _skip_tests: list[SkipThisTest] = []

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
        from zephyrex.lib.Environment import env

        provider_name = f"Test Provider {uuid.uuid4()}"
        provider_payload = {
            "provider": {
                "name": provider_name,
                "team_id": team_a.id if team_a else None,  # type: ignore[attr-defined]
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
            payload = {"provider_id": 12345, "extension_id": None}  # type: ignore[dict-item]
        elif minimal:
            # Only include required fields
            payload = {
                "provider_id": parent_ids.get("provider_id") if parent_ids else None,
                "extension_id": (
                    parent_ids.get("extension_id") if parent_ids else None
                ),
            }

        return payload

    def test_GET_200_available_extensions(  # type: ignore[return]
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

    def test_GET_200_list_by_team(  # type: ignore[return]
        self, server: Any, admin_a, team_a: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Test retrieving provider instances filtered by team."""

        instance = self.test_POST_201(server, admin_a, team_a)

        endpoint = f"{self.get_list_endpoint()}?team_id={team_a.id}"  # type: ignore[attr-defined]
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

    def test_GET_200_list_by_team(  # type: ignore[return]
        self, server: Any, admin_a, team_a: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Test retrieving rotations filtered by team."""

        rotation = self.test_POST_201(server, admin_a, team_a)

        endpoint = f"{self.get_list_endpoint()}?team_id={team_a.id}"  # type: ignore[attr-defined]
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
    update_fields: dict[str, str] = (
        {}
    )  # No updateable fields besides system  # type: ignore[var-annotated]
    unique_fields: list[str] = []

    _skip_tests: list[SkipThisTest] = []

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
                "rotation_id": parent_ids.get("rotation_id") if parent_ids else None,  # type: ignore[dict-item]
                "provider_instance_id": (  # type: ignore[dict-item]
                    parent_ids.get("provider_instance_id") if parent_ids else None
                ),
            }
        else:
            payload = {
                "rotation_id": parent_ids.get("rotation_id") if parent_ids else None,  # type: ignore[dict-item]
                "provider_instance_id": (  # type: ignore[dict-item]
                    parent_ids.get("provider_instance_id") if parent_ids else None
                ),
            }

        if self.entity_name == "rotation_provider_instance":
            # Special case for rotation provider instance
            return payload

        return self.nest_payload_in_entity(payload)  # type: ignore[no-any-return]

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
                "zephyrex.endpoints.EP_Extensions_test",
                fromlist=["TestAbilityEndpoints"],
            ).TestAbilityEndpoints,
        ),
    ]

    # Tests to skip (if any)
    _skip_tests = [
        SkipThisTest(
            name="test_GQL_mutation_update",
            details="This is a relationship entity with no updateable fields",
        ),
    ]

    create_fields = {
        "provider_extension_id": None,  # Will be populated in setup
        "ability_id": lambda: str(uuid.uuid4()),
    }
    update_fields: dict[str, str] = (
        {}
    )  # No updateable fields besides system fields  # type: ignore[var-annotated]
    unique_fields: list[str] = []

    # ProviderExtensionAbility is a high-volume system-seeded join table.
    # Generic timestamp-based searches (created_at eq/on, updated_at eq/on)
    # match every row created within the same second; without an additional
    # narrow filter the result set hits the test's limit (1000) before the
    # newly-created test entity is reached. Including the parent
    # ``provider_extension_id`` and ``ability_id`` foreign keys narrows the
    # search to the row under test.
    search_default_filters = {
        "provider_extension_id": None,
        "ability_id": None,
    }

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
                "provider_extension_id": 12345,  # type: ignore[dict-item]
                "ability_id": 6789,  # type: ignore[dict-item]
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
    unique_fields: list[str] = (
        []
    )  # No unique fields for settings  # type: ignore[var-annotated]

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
