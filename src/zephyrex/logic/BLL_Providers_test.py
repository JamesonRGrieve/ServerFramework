from faker import Faker

from zephyrex.AbstractTest import ParentEntity
from zephyrex.logic.AbstractBLLTest import AbstractBLLTest, CategoryOfTest, ClassOfTestsConfig
from zephyrex.logic.BLL_Providers import (
    ProviderExtensionAbilityManager,
    ProviderExtensionManager,
    ProviderInstanceExtensionAbilityManager,
    ProviderInstanceManager,
    ProviderInstanceSettingManager,
    ProviderInstanceUsageManager,
    ProviderManager,
    RotationManager,
    RotationProviderInstanceManager,
)

# Set default test configuration for all test classes
AbstractBLLTest.test_config = ClassOfTestsConfig(categories=[CategoryOfTest.LOGIC])

# Initialize faker for generating test data once
faker = Faker()


class TestProviderManager(AbstractBLLTest):
    class_under_test = ProviderManager
    create_fields = {
        "name": f"Test Provider {faker.word()}",
        "agent_settings_json": '{"test_setting": "test_value"}',
        "system": False,
    }
    update_fields = {
        "name": f"Updated Provider {faker.word()}",
        "agent_settings_json": '{"updated_setting": "updated_value"}',
    }
    unique_fields = ["name"]


class TestProviderExtensionManager(AbstractBLLTest):
    class_under_test = ProviderExtensionManager
    create_fields = {
        "provider_id": None,  # Will be set by parent entity
        "extension_id": None,  # Will be set by parent entity
    }
    update_fields: dict[str, str] = {}  # No meaningful updates for this relationship  # type: ignore[var-annotated]
    unique_fields: list[str] = []  # No unique fields for this junction table  # type: ignore[var-annotated]
    parent_entities = [
        ParentEntity(
            name="provider",
            foreign_key="provider_id",
            test_class=TestProviderManager,
        ),
        ParentEntity(
            name="extension",
            foreign_key="extension_id",
            test_class=lambda: __import__(
                "zephyrex.logic.BLL_Extensions_test", fromlist=["TestExtensionManager"]
            ).TestExtensionManager(),
        ),
    ]


class TestProviderExtensionAbilityManager(AbstractBLLTest):
    class_under_test = ProviderExtensionAbilityManager
    create_fields = {
        "provider_extension_id": None,  # Will be set by parent entity
        "ability_id": None,  # Will be set by parent entity
    }
    update_fields: dict[str, str] = {}  # No meaningful updates for this relationship  # type: ignore[var-annotated]
    unique_fields: list[str] = []  # No unique fields for this junction table  # type: ignore[var-annotated]
    parent_entities = [
        ParentEntity(
            name="provider_extension",
            foreign_key="provider_extension_id",
            test_class=TestProviderExtensionManager,
        ),
        ParentEntity(
            name="ability",
            foreign_key="ability_id",
            test_class=lambda: __import__(
                "zephyrex.logic.BLL_Extensions_test", fromlist=["TestAbilityManager"]
            ).TestAbilityManager(),
        ),
    ]


class TestProviderInstanceManager(AbstractBLLTest):
    class_under_test = ProviderInstanceManager
    create_fields = {
        "name": f"Test Provider Instance {faker.word()}",
        "provider_id": None,  # Will be set by parent entity
        "model_name": "test-model",
        "api_key": faker.uuid4(),
    }
    update_fields = {
        "name": f"Updated Provider Instance {faker.word()}",
        "model_name": "updated-model",
        "api_key": faker.uuid4(),
    }
    unique_fields = ["name"]
    parent_entities = [
        ParentEntity(
            name="provider",
            foreign_key="provider_id",
            test_class=TestProviderManager,
        ),
    ]


class TestProviderInstanceUsageManager(AbstractBLLTest):
    class_under_test = ProviderInstanceUsageManager
    create_fields = {
        "provider_instance_id": None,  # Will be set by parent entity
        "key": "input_tokens",
        "value": 100,
    }
    update_fields = {
        "key": "output_tokens",
        "value": 200,
    }
    unique_fields = ["key"]
    parent_entities = [
        ParentEntity(
            name="provider_instance",
            foreign_key="provider_instance_id",
            test_class=TestProviderInstanceManager,
        ),
    ]


class TestProviderInstanceSettingManager(AbstractBLLTest):
    class_under_test = ProviderInstanceSettingManager
    create_fields = {
        "provider_instance_id": None,  # Will be set by parent entity
        "key": f"test_key_{faker.word()}",
        "value": faker.sentence(),
    }
    update_fields = {
        "value": f"Updated {faker.sentence()}",
    }
    unique_fields = ["key"]
    parent_entities = [
        ParentEntity(
            name="provider_instance",
            foreign_key="provider_instance_id",
            test_class=TestProviderInstanceManager,
        ),
    ]


class TestProviderInstanceExtensionAbilityManager(AbstractBLLTest):
    class_under_test = ProviderInstanceExtensionAbilityManager
    create_fields = {
        "provider_instance_id": None,  # Will be set by parent entity
        "provider_extension_ability_id": None,  # Will be set by parent entity
        "state": True,
        "forced": False,
    }
    update_fields = {
        "state": False,
        "forced": True,
    }
    unique_fields: list[str] = []  # No unique fields for this junction table  # type: ignore[var-annotated]
    parent_entities = [
        ParentEntity(
            name="provider_instance",
            foreign_key="provider_instance_id",
            test_class=TestProviderInstanceManager,
        ),
        ParentEntity(
            name="provider_extension_ability",
            foreign_key="provider_extension_ability_id",
            test_class=TestProviderExtensionAbilityManager,
        ),
    ]


class TestRotationManager(AbstractBLLTest):
    class_under_test = RotationManager
    create_fields = {
        "name": f"Test Rotation {faker.word()}",
        "description": faker.paragraph(),
    }
    update_fields = {
        "name": f"Updated Rotation {faker.word()}",
        "description": f"Updated {faker.paragraph()}",
    }
    unique_fields = ["name"]


class TestRotationProviderInstanceManager(AbstractBLLTest):
    class_under_test = RotationProviderInstanceManager
    create_fields = {
        "rotation_id": None,  # Will be set by parent entity
        "provider_instance_id": None,  # Will be set by parent entity
        "parent_id": None,  # Optional, will be set in _update if needed
    }
    update_fields = {
        "parent_id": None,  # Will be set in _update
    }
    parent_entities = [
        ParentEntity(
            name="rotation",
            foreign_key="rotation_id",
            test_class=TestRotationManager,
        ),
        ParentEntity(
            name="provider_instance",
            foreign_key="provider_instance_id",
            test_class=TestProviderInstanceManager,
        ),
    ]
    unique_fields = ["rotation_id"]

    def _update(self, user_id=None, team_id=None, server=None, model_registry=None):
        """Override to create a parent rotation instance first."""
        # Create a parent using the standard entity creation
        manager = self.class_under_test(
            requester_id=user_id or self.root_id,
            target_team_id=team_id,
            model_registry=model_registry or self.model_registry,
        )
        parent_data = self.build_entities(
            server or self.server, user_id=user_id, team_id=team_id
        )[0]
        parent = manager.create(**parent_data)
        self.parent_id = parent.id
        self.tracked_entities["parent"] = parent

        # Update original update_fields with parent ID
        self.update_fields["parent_id"] = self.parent_id

        # Call the original update method from AbstractBLLTest
        return super()._update(user_id, team_id, model_registry=model_registry)
