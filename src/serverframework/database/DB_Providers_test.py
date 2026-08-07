from serverframework.AbstractTest import ParentEntity
from serverframework.database.AbstractDBTest import AbstractDBTest

# Import BLL models which will be converted to SQLAlchemy models via .DB()
from serverframework.logic.BLL_Providers import (
    ProviderExtensionAbilityModel,
    ProviderExtensionModel,
    ProviderInstanceExtensionAbilityModel,
    ProviderInstanceModel,
    ProviderInstanceSettingModel,
    ProviderInstanceUsageModel,
    ProviderModel,
    RotationModel,
    RotationProviderInstanceModel,
)


class TestProvider(AbstractDBTest):
    class_under_test = ProviderModel
    create_fields = {
        "name": "test_provider",
        "friendly_name": "Test Provider",
        "agent_settings_json": '{"test": "value"}',
    }
    update_fields = {
        "friendly_name": "Updated Provider",
        "agent_settings_json": '{"updated": "value"}',
    }
    unique_field = "name"


class TestProviderExtension(AbstractDBTest):
    class_under_test = ProviderExtensionModel
    create_fields = {}  # type: ignore[var-annotated]
    update_fields: dict[str, str] = {}  # No updateable fields besides system fields  # type: ignore[var-annotated]
    parent_entities = [
        ParentEntity(
            name="extension",
            foreign_key="extension_id",
            test_class=lambda: __import__(
                "serverframework.database.DB_Extensions_test", fromlist=["TestExtension"]
            ).TestExtension(),
        ),
        ParentEntity(
            name="provider",
            foreign_key="provider_id",
            test_class=TestProvider,
        ),
    ]


class TestProviderInstance(AbstractDBTest):
    class_under_test = ProviderInstanceModel
    create_fields = {
        "name": "test_provider_instance",
        "model_name": "test_model",
        "api_key": "test_api_key",
        "enabled": True,
    }
    update_fields = {
        "name": "updated_provider_instance",
        "model_name": "updated_model",
        "api_key": "updated_api_key",
        "enabled": False,
    }
    unique_field = "name"
    parent_entities = [
        ParentEntity(
            name="provider",
            foreign_key="provider_id",
            test_class=TestProvider,
        ),
    ]


class TestProviderExtensionAbility(AbstractDBTest):
    class_under_test = ProviderExtensionAbilityModel
    create_fields = {
        "provider_extension_id": None,  # Will be populated in setup
        "ability_id": None,  # Will be populated in setup
    }
    update_fields: dict[str, str] = {}  # No updateable fields besides system fields  # type: ignore[var-annotated]
    parent_entities = [
        ParentEntity(
            name="provider_extension",
            foreign_key="provider_extension_id",
            test_class=TestProviderExtension,
        ),
        ParentEntity(
            name="ability",
            foreign_key="ability_id",
            test_class=lambda: __import__(
                "serverframework.database.DB_Extensions_test", fromlist=["TestAbility"]
            ).TestAbility(),
        ),
    ]


class TestProviderInstanceUsage(AbstractDBTest):
    class_under_test = ProviderInstanceUsageModel
    create_fields = {
        "user_id": None,  # Will be populated in setup
        "key": "input_tokens",
        "value": 100,
    }
    update_fields = {
        "key": "output_tokens",
        "value": 200,
    }
    parent_entities = [
        ParentEntity(
            name="provider_instance",
            foreign_key="provider_instance_id",
            test_class=TestProviderInstance,
        ),
    ]


class TestProviderInstanceSetting(AbstractDBTest):
    class_under_test = ProviderInstanceSettingModel
    create_fields = {
        "key": "test_setting_key",
        "value": "test_setting_value",
    }
    update_fields = {
        "key": "updated_setting_key",
        "value": "updated_setting_value",
    }
    parent_entities = [
        ParentEntity(
            name="provider_instance",
            foreign_key="provider_instance_id",
            test_class=TestProviderInstance,
        ),
    ]


class TestProviderInstanceExtensionAbility(AbstractDBTest):
    class_under_test = ProviderInstanceExtensionAbilityModel
    create_fields = {
        "state": True,
        "forced": False,
    }
    update_fields = {
        "state": False,
        "forced": True,
    }
    parent_entities = [
        ParentEntity(
            name="provider_instance",
            foreign_key="provider_instance_id",
            test_class=TestProviderInstance,
        ),
        ParentEntity(
            name="provider_extension_ability",
            foreign_key="provider_extension_ability_id",
            test_class=TestProviderExtensionAbility,
        ),
    ]


class TestRotation(AbstractDBTest):
    class_under_test = RotationModel
    create_fields = {
        "name": "test_rotation",
        "description": "Test rotation description",
    }
    update_fields = {
        "name": "updated_rotation",
        "description": "Updated rotation description",
    }
    unique_field = "name"


class TestRotationProviderInstance(AbstractDBTest):
    class_under_test = RotationProviderInstanceModel
    create_fields = {}  # type: ignore[var-annotated]
    update_fields: dict[str, str] = {}  # No updateable fields besides system fields  # type: ignore[var-annotated]
    parent_entities = [
        ParentEntity(
            name="rotation", foreign_key="rotation_id", test_class=TestRotation
        ),
        ParentEntity(
            name="provider_instance",
            foreign_key="provider_instance_id",
            test_class=TestProviderInstance,
        ),
    ]
