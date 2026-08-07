from zephyrex.AbstractTest import ParentEntity
from zephyrex.database.AbstractDBTest import AbstractDBTest
from zephyrex.logic.BLL_Extensions import AbilityModel, ExtensionModel


class TestExtension(AbstractDBTest):
    class_under_test = ExtensionModel
    create_fields = {
        "name": "test_extension",
        "description": "Test extension description",
        "friendly_name": "Test Extension",
    }
    update_fields = {
        "name": "updated_extension",
        "description": "Updated extension description",
        "friendly_name": "Updated Extension",
    }
    unique_field = "name"


class TestAbility(AbstractDBTest):
    class_under_test = AbilityModel
    create_fields = {
        "extension_id": None,  # Will be populated in setup
        "name": "test_ability",
        "meta": False,
        "friendly_name": "Test Ability",
    }
    update_fields = {
        "name": "updated_ability",
        "meta": True,
        "friendly_name": "Updated Ability",
    }
    parent_entities = [
        ParentEntity(
            name="extension",
            foreign_key="extension_id",
            test_class=TestExtension,
        )
    ]

    # def setup_method(self, method):
    #     super().setup_method(method)
    #     # Create an extension to reference
    #     server = getattr(self, "_server", None)
    #     model_registry = server.app.state.model_registry.database_manager if server else None
    #     extension = Extension.create(
    #         self.root_user_id,
    #         model_registry,
    #         return_type="dict",
    #         name="test_parent_extension",
    #         description="Extension for ability test",
    #     )
    #     # Update create_fields with valid extension_id
    #     self.create_fields["extension_id"] = extension["id"]
