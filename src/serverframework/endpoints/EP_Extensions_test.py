from typing import Any, Dict, Optional

import pytest
from faker import Faker

from serverframework.AbstractTest import CategoryOfTest, ClassOfTestsConfig, ParentEntity
from serverframework.endpoints.AbstractEPTest import AbstractEPTest
from serverframework.lib.Environment import env
from serverframework.logic.BLL_Extensions import AbilityModel, ExtensionModel

# Initialize faker
faker = Faker()


@pytest.mark.ep
@pytest.mark.extensions
class TestExtensionEndpoints(AbstractEPTest):
    """Test class for Extension endpoints."""

    # Test configuration
    test_config = ClassOfTestsConfig(
        categories=[CategoryOfTest.ENDPOINT, CategoryOfTest.REST],
        timeout=60,
        cleanup=True,
    )

    base_endpoint = "extension"
    entity_name = "extension"
    required_fields = ["id", "name", "description"]
    class_under_test = ExtensionModel
    string_field_to_update = "name"
    supports_search = True
    searchable_fields = ["name", "description"]
    # Mark as system entity since extensions can only be created by system users
    system_entity = True

    create_fields = {
        "name": lambda: f"test_extension_{faker.uuid4()}",
        "description": "Test extension description",
        "friendly_name": "Test Extension",
    }
    update_fields = {
        "name": "updated_extension",
        "description": "Updated extension description",
        "friendly_name": "Updated Extension",
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
        """Create a payload for extension creation."""
        if invalid_data:
            return {"name": 12345}  # Invalid: number instead of string

        return {
            "name": name or self.faker.word(),
            "description": "Test extension description",
            "friendly_name": "Test Extension",
        }

    def test_GET_200(self, server, admin_a):
        """Test that we can retrieve extensions (system entities)."""
        self._create(
            server, admin_a.jwt, admin_a.id, api_key=env("ROOT_API_KEY"), key="test_get"
        )
        self._get(
            server,
            admin_a.jwt,
            admin_a.id,
            save_key="get_test_result",
            get_key="test_get",
        )
        self._get_assert("get_test_result")

        # Test with API key only
        self._get(
            server,
            api_key=env("ROOT_API_KEY"),
            save_key="get_test_result_api_key",
            get_key="test_get",
        )
        self._get_assert("get_test_result_api_key")

    def test_GET_200_list(self, server: Any, admin_a: Any, team_a: Any):
        """Test listing extensions (system entities)."""
        # Create three test extensions individually
        self._create(
            server, admin_a.jwt, admin_a.id, api_key=env("ROOT_API_KEY"), key="list_1"
        )
        self._create(
            server, admin_a.jwt, admin_a.id, api_key=env("ROOT_API_KEY"), key="list_2"
        )
        self._create(
            server, admin_a.jwt, admin_a.id, api_key=env("ROOT_API_KEY"), key="list_3"
        )

        # List extensions using JWT auth (system entities are readable by all users)
        self._list(server, admin_a.jwt, admin_a.id, team_a.id)
        self._list_assert("list_result")

        # List extensions using API key
        self._list(server, api_key=env("ROOT_API_KEY"))
        self._list_assert("list_result")

    def test_DELETE_204(self, server, admin_a):
        """Test that we can delete extensions (system entities)."""
        self._create(
            server,
            admin_a.jwt,
            admin_a.id,
            api_key=env("ROOT_API_KEY"),
            key="test_delete",
        )
        self._delete(
            server,
            admin_a.jwt,
            admin_a.id,
            api_key=env("ROOT_API_KEY"),
            delete_key="test_delete",
        )
        self._delete_assert("test_delete", server, admin_a.jwt)

        # Test with API key only
        self._create(
            server,
            admin_a.jwt,
            admin_a.id,
            api_key=env("ROOT_API_KEY"),
            key="test_delete_api_key",
        )
        self._delete(
            server,
            api_key=env("ROOT_API_KEY"),
            delete_key="test_delete_api_key",
        )
        # Use JWT for assertion since ROOT_API_KEY can see soft-deleted entities
        self._delete_assert("test_delete_api_key", server, jwt_token=admin_a.jwt)


@pytest.mark.ep
@pytest.mark.extensions
class TestAbilityEndpoints(AbstractEPTest):
    """Test class for Ability endpoints."""

    # Test configuration
    test_config = ClassOfTestsConfig(
        categories=[CategoryOfTest.ENDPOINT, CategoryOfTest.REST],
        timeout=60,
        cleanup=True,
    )
    class_under_test = AbilityModel
    base_endpoint = "ability"
    entity_name = "ability"
    required_fields = ["id", "name", "extension_id"]
    string_field_to_update = "name"
    supports_search = True
    searchable_fields = ["name", "extension_id"]
    # Mark as system entity since abilities can only be created by system users
    system_entity = True

    # AbilityModel.Search requires 'meta' field and inherits extension_id
    # These are automatically added to search payloads when not being tested
    search_default_filters = {
        "meta": False,
        "extension_id": None,  # Will use entity value when available
    }

    # Define parent entities - abilities require an extension
    parent_entities = [
        ParentEntity(
            name="extension",
            foreign_key="extension_id",
            test_class=TestExtensionEndpoints,
        ),
    ]

    create_fields = {
        "name": lambda: f"test_ability_{faker.uuid4()}",
        "extension_id": None,  # Will be set by parent entity
        "meta": False,
        "friendly_name": "Test Ability",
    }
    update_fields = {
        "name": "updated_ability",
        "meta": True,
        "friendly_name": "Updated Ability",
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
        """Create a payload for ability creation."""
        if invalid_data:
            return {"name": 12345}  # Invalid: number instead of string

        payload = {
            "name": name or self.faker.word(),
            "meta": False,
            "friendly_name": "Test Ability",
        }

        # Add extension_id from parent_ids if provided
        if parent_ids and "extension_id" in parent_ids:
            payload["extension_id"] = parent_ids["extension_id"]
        # If no parent_ids provided, leave extension_id out - framework will create one

        return payload

    def test_GET_200(self, server, admin_a):
        """Test that we can retrieve abilities (system entities)."""
        self._create(
            server, admin_a.jwt, admin_a.id, api_key=env("ROOT_API_KEY"), key="test_get"
        )
        self._get(
            server,
            admin_a.jwt,
            admin_a.id,
            save_key="get_test_result",
            get_key="test_get",
        )
        self._get_assert("get_test_result")

        # Test with API key only
        self._get(
            server,
            api_key=env("ROOT_API_KEY"),
            save_key="get_test_result_api_key",
            get_key="test_get",
        )
        self._get_assert("get_test_result_api_key")

    def test_GET_200_list(self, server: Any, admin_a: Any, team_a: Any):
        """Test listing abilities (system entities)."""
        # Create three test abilities individually
        self._create(
            server, admin_a.jwt, admin_a.id, api_key=env("ROOT_API_KEY"), key="list_1"
        )
        self._create(
            server, admin_a.jwt, admin_a.id, api_key=env("ROOT_API_KEY"), key="list_2"
        )
        self._create(
            server, admin_a.jwt, admin_a.id, api_key=env("ROOT_API_KEY"), key="list_3"
        )

        # List abilities using JWT auth (system entities are readable by all users)
        self._list(server, admin_a.jwt, admin_a.id, team_a.id)
        self._list_assert("list_result")

        # List abilities using API key
        self._list(server, api_key=env("ROOT_API_KEY"))
        self._list_assert("list_result")

    def test_DELETE_204(self, server, admin_a):
        """Test that we can delete abilities (system entities)."""
        self._create(
            server,
            admin_a.jwt,
            admin_a.id,
            api_key=env("ROOT_API_KEY"),
            key="test_delete",
        )
        self._delete(
            server,
            admin_a.jwt,
            admin_a.id,
            api_key=env("ROOT_API_KEY"),
            delete_key="test_delete",
        )
        self._delete_assert("test_delete", server, admin_a.jwt)

        # Test with API key only
        self._create(
            server,
            admin_a.jwt,
            admin_a.id,
            api_key=env("ROOT_API_KEY"),
            key="test_delete_api_key",
        )
        self._delete(
            server,
            api_key=env("ROOT_API_KEY"),
            delete_key="test_delete_api_key",
        )
        # Use JWT for assertion since ROOT_API_KEY can see soft-deleted entities
        self._delete_assert("test_delete_api_key", server, jwt_token=admin_a.jwt)
