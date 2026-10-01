from faker import Faker

from AbstractTest import CategoryOfTest, ClassOfTestsConfig, ParentEntity
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_prompts.BLL_AI_Prompts import PromptArgumentManager, PromptManager
from zephyrex.extensions.ai_prompts.EXT_AI_Prompts import EXT_AI_Prompts
from zephyrex.logic.AbstractBLLTest import AbstractBLLTest
from zephyrex.logic.BLL_Auth_test import TestUserManager as CoreUserManagerTests

# Set default test configuration for all test classes
AbstractBLLTest.test_config = ClassOfTestsConfig(
    categories=[CategoryOfTest.LOGIC, CategoryOfTest.EXTENSION]
)

# Initialize faker
faker = Faker()


class TestPromptManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = PromptManager
    extension_class = EXT_AI_Prompts

    create_fields = {
        "name": lambda: f"Test Prompt {faker.word()}",
        "description": lambda: faker.sentence(),
        "content": lambda: f"Hello {{USER_NAME}}, {faker.sentence()}",
        "favourite": False,
    }
    update_fields = {
        "name": "Updated Prompt Name",
        "description": "Updated prompt description",
        "content": "Updated: {USER_NAME}, this is the updated content.",
        "favourite": True,
    }
    unique_fields = []  # No unique fields for prompts
    parent_entities = [
        ParentEntity(
            name="user",
            foreign_key="user_id",
            test_class=CoreUserManagerTests,
        ),
    ]

    def test_create_prompt_with_variables(self, admin_a, model_registry):
        """Test creating a prompt with variable placeholders"""
        with self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        ) as manager:
            prompt_content = (
                "Hello {USER_NAME}, welcome to {PLATFORM}! Please {ACTION}."
            )

            prompt = manager.create(
                user_id=admin_a.id,
                name="Welcome Prompt",
                description="A welcome message with variables",
                content=prompt_content,
                favourite=False,
            )

            assert prompt is not None
            assert prompt.name == "Welcome Prompt"
            assert prompt.content == prompt_content
            assert prompt.user_id == admin_a.id

    def test_extract_variables_from_content(self, admin_a, model_registry):
        """Test extracting variables from prompt content"""
        with self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        ) as manager:
            content = "Hello {USER_NAME}, your {ITEM_COUNT} items are ready. Visit {WEBSITE_URL}."
            variables = manager.extract_variables_from_content(content)

            expected_vars = {"USER_NAME", "ITEM_COUNT", "WEBSITE_URL"}
            assert set(variables) == expected_vars

    def test_build_prompt_with_variables(self, admin_a, model_registry):
        """Test building a prompt with variable substitution"""
        with self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        ) as manager:
            # Create a prompt with variables
            prompt = manager.create(
                user_id=admin_a.id,
                name="Test Build Prompt",
                description="Prompt for testing build functionality",
                content="Hello {USER_NAME}, you have {MESSAGE_COUNT} messages.",
                favourite=False,
            )

            # Create arguments for the prompt
            manager.arguments.create(
                prompt_id=prompt.id,
                name="USER_NAME",
                default_value="Guest",
            )
            manager.arguments.create(
                prompt_id=prompt.id,
                name="MESSAGE_COUNT",
                default_value="0",
            )

            # Test building with provided variables
            built_prompt = manager.build(
                prompt_id=prompt.id, USER_NAME="John", MESSAGE_COUNT="5"
            )

            assert built_prompt == "Hello John, you have 5 messages."

            # Test building with default values
            built_prompt_default = manager.build(prompt_id=prompt.id)
            assert built_prompt_default == "Hello Guest, you have 0 messages."

    def test_validate_prompt_variables(self, admin_a, model_registry):
        """Test validating prompt variables"""
        with self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        ) as manager:
            # Create a prompt
            prompt = manager.create(
                user_id=admin_a.id,
                name="Validation Test Prompt",
                description="Prompt for testing validation",
                content="Required: {REQUIRED_VAR}, Optional: {OPTIONAL_VAR}",
                favourite=False,
            )

            # Create one required argument (no default) and one optional (with default)
            manager.arguments.create(
                prompt_id=prompt.id,
                name="REQUIRED_VAR",
                default_value=None,  # Required
            )
            manager.arguments.create(
                prompt_id=prompt.id,
                name="OPTIONAL_VAR",
                default_value="default_value",  # Optional
            )

            # Test validation with missing required variable
            errors = manager.validate_prompt_variables(prompt_id=prompt.id)
            assert "REQUIRED_VAR" in errors
            assert "OPTIONAL_VAR" not in errors

            # Test validation with all variables provided
            errors_complete = manager.validate_prompt_variables(
                prompt_id=prompt.id, REQUIRED_VAR="provided_value"
            )
            assert len(errors_complete) == 0

    def test_sync_arguments_from_content(self, admin_a, model_registry):
        """Test syncing arguments based on content variables"""
        with self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        ) as manager:
            # Create a prompt with variables in content
            prompt = manager.create(
                user_id=admin_a.id,
                name="Sync Test Prompt",
                description="Prompt for testing argument sync",
                content="Variables: {VAR1}, {VAR2}, {VAR3}",
                favourite=False,
            )

            # Sync arguments from content
            added_vars = manager.sync_arguments_from_content(prompt_id=prompt.id)

            assert len(added_vars) == 3
            assert set(added_vars) == {"VAR1", "VAR2", "VAR3"}

            # Verify arguments were created
            arguments = manager.arguments.list(prompt_id=prompt.id)
            arg_names = {
                arg.name if hasattr(arg, "name") else arg["name"] for arg in arguments
            }
            assert arg_names == {"VAR1", "VAR2", "VAR3"}

    def test_custom_route_build_prompt(self, admin_a, model_registry):
        """Test the custom route for building prompts"""
        with self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        ) as manager:
            # Create a prompt
            prompt = manager.create(
                user_id=admin_a.id,
                name="Custom Route Test",
                description="Testing custom route",
                content="Hello {NAME}!",
                favourite=False,
            )

            # Create argument
            manager.arguments.create(
                prompt_id=prompt.id,
                name="NAME",
                default_value="World",
            )

            # Test custom route
            result = manager.build(prompt_id=prompt.id, NAME="Custom Route")

            assert result == "Hello Custom Route!"

    def test_custom_route_validate_prompt_variables(self, admin_a, model_registry):
        """Test the custom route for validating prompt variables"""
        with self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        ) as manager:
            # Create a prompt
            prompt = manager.create(
                user_id=admin_a.id,
                name="Custom Validation Test",
                description="Testing custom validation route",
                content="Required: {REQUIRED}",
                favourite=False,
            )

            # Create required argument
            manager.arguments.create(
                prompt_id=prompt.id,
                name="REQUIRED",
                default_value=None,
            )

            # Test custom route validation
            errors = manager.validate_prompt_variables(prompt_id=prompt.id)
            assert "REQUIRED" in errors


class TestPromptArgumentManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = PromptArgumentManager
    extension_class = EXT_AI_Prompts

    create_fields = {
        "name": lambda: f"ARG_{faker.word().upper()}",
        "default_value": lambda: faker.word(),
    }
    update_fields = {
        "name": "UPDATED_ARG",
        "default_value": "updated_value",
    }
    unique_fields = []  # No unique fields for arguments
    parent_entities = [
        ParentEntity(
            name="prompt",
            foreign_key="prompt_id",
            test_class=TestPromptManager,
        ),
    ]

    def test_create_argument_with_default(self, admin_a, model_registry):
        """Test creating an argument with a default value"""
        # Create a prompt first
        with PromptManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as prompt_manager:
            prompt = prompt_manager.create(
                user_id=admin_a.id,
                name="Test Prompt for Args",
                description="Testing argument creation",
                content="Hello {TEST_VAR}!",
                favourite=False,
            )

        # Create an argument
        with self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        ) as manager:
            argument = manager.create(
                prompt_id=prompt.id,
                name="TEST_VAR",
                default_value="World",
            )

            assert argument is not None
            assert argument.name == "TEST_VAR"
            assert argument.default_value == "World"
            assert argument.prompt_id == prompt.id

    def test_create_required_argument(self, admin_a, model_registry):
        """Test creating a required argument (no default value)"""
        # Create a prompt first
        with PromptManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as prompt_manager:
            prompt = prompt_manager.create(
                user_id=admin_a.id,
                name="Test Prompt for Required Args",
                description="Testing required argument creation",
                content="Hello {REQUIRED_VAR}!",
                favourite=False,
            )

        # Create a required argument
        with self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        ) as manager:
            argument = manager.create(
                prompt_id=prompt.id,
                name="REQUIRED_VAR",
                default_value=None,  # No default = required
            )

            assert argument is not None
            assert argument.name == "REQUIRED_VAR"
            assert argument.default_value is None
            assert argument.prompt_id == prompt.id
