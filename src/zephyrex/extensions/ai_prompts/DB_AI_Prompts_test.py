from faker import Faker

from AbstractTest import CategoryOfTest, ClassOfTestsConfig, ParentEntity
from zephyrex.database.AbstractDBTest import AbstractDBTest
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_prompts.BLL_AI_Prompts import PromptArgumentModel, PromptModel
from zephyrex.extensions.ai_prompts.EXT_AI_Prompts import EXT_AI_Prompts

# Set default test configuration for all test classes
AbstractDBTest.test_config = ClassOfTestsConfig(
    categories=[CategoryOfTest.DATABASE, CategoryOfTest.EXTENSION]
)

faker = Faker()


class TestPrompt(AbstractDBTest, ExtensionServerMixin):
    """Test PromptModel database operations."""

    class_under_test = PromptModel
    extension_class = EXT_AI_Prompts

    create_fields = {
        "name": lambda: f"Test Prompt {faker.unique.uuid4()}",
        "content": faker.text,
        "description": faker.sentence,
        "favourite": False,
    }
    update_fields = {
        "name": "Updated Prompt",
        "content": "Updated content with {VARIABLE}",
        "favourite": True,
    }


class TestPromptArgument(AbstractDBTest, ExtensionServerMixin):
    """Test PromptArgumentModel database operations."""

    class_under_test = PromptArgumentModel
    extension_class = EXT_AI_Prompts

    create_fields = {
        "prompt_id": None,  # Will be populated by parent_entities
        "name": lambda: f"arg_{faker.word()}",
        "default_value": faker.word,
    }
    update_fields = {
        "name": "updated_arg",
        "default_value": "new_default",
    }
    parent_entities = [
        ParentEntity(
            name="prompt",
            foreign_key="prompt_id",
            test_class=TestPrompt,
        )
    ]
