from faker import Faker

from zephyrex.AbstractTest import ParentEntity
from zephyrex.logic.AbstractBLLTest import AbstractBLLTest, CategoryOfTest, ClassOfTestsConfig
from zephyrex.logic.BLL_Extensions import AbilityManager, ExtensionManager

# Set default test configuration for all test classes
AbstractBLLTest.test_config = ClassOfTestsConfig(categories=[CategoryOfTest.LOGIC])

# Initialize faker for generating test data once
faker = Faker()


class TestExtensionManager(AbstractBLLTest):
    class_under_test = ExtensionManager
    create_fields = {
        "name": f"Test Extension {faker.word()}",
        "description": faker.paragraph(),
        "friendly_name": f"Test Extension {faker.word()}",
    }
    update_fields = {
        "name": f"Updated Extension {faker.word()}",
        "description": f"Updated {faker.paragraph()}",
        "friendly_name": f"Updated Extension {faker.word()}",
    }
    unique_fields = ["name"]


class TestAbilityManager(AbstractBLLTest):
    class_under_test = AbilityManager
    create_fields = {
        "name": f"Test Ability {faker.word()}",
        "extension_id": None,  # Will be set by parent entity
        "meta": False,
        "friendly_name": f"Test Ability {faker.word()}",
    }
    update_fields = {
        "name": f"Updated Ability {faker.word()}",
        "meta": True,
        "friendly_name": f"Updated Ability {faker.word()}",
    }
    unique_fields = ["name"]
    parent_entities = [
        ParentEntity(
            name="extension",
            foreign_key="extension_id",
            test_class=TestExtensionManager,
        ),
    ]
