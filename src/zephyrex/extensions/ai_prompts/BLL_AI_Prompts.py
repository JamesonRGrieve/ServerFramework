"""
AI Prompts extension business logic layer.

This module provides comprehensive prompt management functionality including:
- Prompt storage and retrieval with variable injection support
- Prompt argument management for dynamic variable substitution
- Integration with conversations and AI chains
- Seed data loading from markdown files
"""

import logging
import os
import re
from os import path
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field

from zephyrex.lib.Environment import env
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DescriptionMixinModel,
    ModelMeta,
    NameMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import TeamModel, UserModel

# Get system ID from environment
SYSTEM_ID = env("SYSTEM_ID")

logger = logging.getLogger(__name__)


class PromptModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    NameMixinModel.Optional,
    DescriptionMixinModel.Optional,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    metaclass=ModelMeta,
):
    """
    Model representing a stored prompt template.

    Prompts support variable injection using the {VARIABLE_NAME} format
    and can be organized by user/team ownership.
    """

    favourite: bool = Field(
        False, description="Whether this prompt is marked as a favorite"
    )
    content: str = Field(
        ..., description="The content of the prompt with variable placeholders"
    )

    # Database metadata
    table_comment: ClassVar[str] = (
        "A Prompt represents a stored prompt that can be used in Conversations and elsewhere. "
        "Variable injection for prompts is expected in the form {VARIABLE_NAME}."
    )
    is_system_entity: ClassVar[bool] = False
    seed_creator_id: ClassVar[str] = SYSTEM_ID

    # Seed configuration
    seed_dir: ClassVar[str] = "../../../prompt"

    class Create(
        BaseModel,
        NameMixinModel,
        DescriptionMixinModel,
        UserModel.Reference.ID.Optional,
        TeamModel.Reference.ID.Optional,
    ):
        """Fields required to create a new prompt."""

        favourite: bool = Field(
            False, description="Whether this prompt is marked as a favorite"
        )
        content: str = Field(..., description="The content of the prompt")

    class Update(
        BaseModel,
        NameMixinModel.Optional,
        DescriptionMixinModel.Optional,
        UserModel.Reference.ID.Optional,
        TeamModel.Reference.ID.Optional,
    ):
        """Fields that can be updated on an existing prompt."""

        favourite: Optional[bool] = Field(
            None, description="Whether this prompt is marked as a favorite"
        )
        content: Optional[str] = Field(None, description="The content of the prompt")

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        NameMixinModel.Search,
        DescriptionMixinModel.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
    ):
        """Search criteria for prompts."""

        favourite: Optional[bool] = Field(None, description="Filter by favorite status")
        content: Optional[StringSearchModel] = None

    @staticmethod
    def _parse_markdown_file(file_path: str) -> Optional[Dict[str, Any]]:
        """Parse a markdown file to extract prompt data."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Extract the first markdown heading as the title
            title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
            title = title_match.group(1) if title_match else path.basename(file_path)

            # Create a description from the first paragraph after the title
            description = ""
            content_after_title = content.split("\n", 1)[1] if "\n" in content else ""
            first_para = re.search(r"\n\n(.*?)\n\n", content_after_title + "\n\n")
            if first_para:
                description = first_para.group(1).strip()

            return {
                "name": title,
                "content": content,
                "description": description or "Imported prompt",
                "favourite": False,
                "user_id": SYSTEM_ID,
            }
        except Exception as e:
            logger.error(f"Error parsing markdown file {file_path}: {str(e)}")
            return None

    @classmethod
    def seed_data(cls, model_registry=None) -> List[Dict[str, Any]]:
        """Load seed data from markdown files in the prompt directory."""
        seed_items = []

        try:
            # Get the absolute path relative to this file
            current_dir = os.path.dirname(os.path.abspath(__file__))
            absolute_seed_dir = os.path.abspath(os.path.join(current_dir, cls.seed_dir))

            logger.info(f"Looking for prompts in: {absolute_seed_dir}")

            if not os.path.exists(absolute_seed_dir):
                logger.warning(f"Prompt directory does not exist: {absolute_seed_dir}")
            else:
                for root, dirs, files in os.walk(absolute_seed_dir):
                    for file in files:
                        if file.endswith(".md"):
                            file_path = path.join(root, file)
                            prompt_data = cls._parse_markdown_file(file_path)
                            if prompt_data:
                                logger.info(f"Found prompt: {prompt_data['name']}")
                                seed_items.append(prompt_data)

                logger.info(f"Found {len(seed_items)} prompts to seed")
        except (FileNotFoundError, OSError) as e:
            logger.error(f"Error loading prompts: {str(e)}")

        return seed_items


class PromptManager(AbstractBLLManager, RouterMixin):
    """Manager for prompt operations."""

    _model = PromptModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/prompt"
    tags: ClassVar[Optional[List[str]]] = ["Prompt Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    auth_dependency: ClassVar[Optional[str]] = "get_current_user"

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry=None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )
        self._arguments = None

    @property
    def arguments(self) -> "PromptArgumentManager":
        """Get the prompt argument manager."""
        if self._arguments is None:
            self._arguments = PromptArgumentManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._arguments

    def build(self, prompt_id: str, **kwargs) -> str:
        """
        Build a prompt by substituting variables with provided values.

        Args:
            prompt_id: The ID of the prompt to build
            **kwargs: Variables to substitute in the prompt

        Returns:
            The built prompt with variables substituted
        """
        # Get the prompt
        prompt = self.get(id=prompt_id)

        # Get prompt arguments to understand what variables are expected
        arguments = self.arguments.list(prompt_id=prompt_id)

        # Build a dictionary of variables to substitute
        substitution_dict = {}

        # Add provided kwargs
        substitution_dict.update(kwargs)

        # Add default values for any missing arguments
        for arg in arguments:
            arg_name = arg.get("name") if isinstance(arg, dict) else arg.name
            if arg_name not in substitution_dict:
                default_value = (
                    arg.get("default_value")
                    if isinstance(arg, dict)
                    else arg.default_value
                )
                if default_value is not None:
                    substitution_dict[arg_name] = default_value

        # Substitute variables in the prompt content
        content = prompt.content if hasattr(prompt, "content") else prompt["content"]

        # Replace {VARIABLE_NAME} with actual values
        def replace_variable(match):
            var_name = match.group(1)
            return str(substitution_dict.get(var_name, match.group(0)))

        # Find all {VARIABLE_NAME} patterns and replace them
        result = re.sub(r"\{([A-Z_][A-Z0-9_]*)\}", replace_variable, content)

        return result

    def validate_prompt_variables(self, prompt_id: str, **kwargs) -> Dict[str, str]:
        """
        Validate that all required variables for a prompt are provided.

        Args:
            prompt_id: The ID of the prompt to validate
            **kwargs: Variables provided for the prompt

        Returns:
            Dictionary of validation errors (empty if valid)
        """
        errors = {}

        # Get prompt arguments
        arguments = self.arguments.list(prompt_id=prompt_id)

        # Check each argument
        for arg in arguments:
            arg_name = arg.get("name") if isinstance(arg, dict) else arg.name
            default_value = (
                arg.get("default_value") if isinstance(arg, dict) else arg.default_value
            )

            # If no default value and not provided in kwargs, it's an error
            if default_value is None and arg_name not in kwargs:
                errors[arg_name] = f"Required variable '{arg_name}' not provided"

        return errors

    def extract_variables_from_content(self, content: str) -> List[str]:
        """
        Extract variable names from prompt content.

        Args:
            content: The prompt content to analyze

        Returns:
            List of variable names found in the content
        """
        # Find all {VARIABLE_NAME} patterns
        matches = re.findall(r"\{([A-Z_][A-Z0-9_]*)\}", content)
        return list(set(matches))  # Remove duplicates

    def sync_arguments_from_content(self, prompt_id: str) -> List[str]:
        """
        Synchronize prompt arguments based on variables found in content.

        Args:
            prompt_id: The ID of the prompt to sync

        Returns:
            List of variable names that were added
        """
        # Get the prompt
        prompt = self.get(id=prompt_id)
        content = prompt.content if hasattr(prompt, "content") else prompt["content"]

        # Extract variables from content
        variables_in_content = self.extract_variables_from_content(content)

        # Get existing arguments
        existing_arguments = self.arguments.list(prompt_id=prompt_id)
        existing_names = set()
        for arg in existing_arguments:
            arg_name = arg.get("name") if isinstance(arg, dict) else arg.name
            existing_names.add(arg_name)

        # Add missing arguments
        added_variables = []
        for var_name in variables_in_content:
            if var_name not in existing_names:
                self.arguments.create(
                    prompt_id=prompt_id,
                    name=var_name,
                    default_value=None,
                )
                added_variables.append(var_name)

        return added_variables


class PromptArgumentModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    NameMixinModel.Optional,
    PromptModel.Reference.ID,
    metaclass=ModelMeta,
):
    """
    Model representing a variable argument for a prompt.

    Arguments define the variables that can be injected into a prompt
    template, along with optional default values.
    """

    default_value: Optional[str] = Field(
        None, description="Default value for this argument if not provided"
    )

    # Database metadata
    table_comment: ClassVar[str] = (
        "A PromptArgument represents a variable injection for a Prompt."
    )
    is_system_entity: ClassVar[bool] = False

    class Create(
        BaseModel,
        NameMixinModel,
        PromptModel.Reference.ID,
    ):
        """Fields required to create a new prompt argument."""

        default_value: Optional[str] = Field(
            None, description="Default value for this argument"
        )

    class Update(
        BaseModel,
        NameMixinModel.Optional,
        PromptModel.Reference.ID.Optional,
    ):
        """Fields that can be updated on an existing prompt argument."""

        default_value: Optional[str] = Field(
            None, description="Default value for this argument"
        )

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        NameMixinModel.Search,
        PromptModel.Reference.ID.Search,
    ):
        """Search criteria for prompt arguments."""

        default_value: Optional[StringSearchModel] = None


class PromptArgumentManager(AbstractBLLManager, RouterMixin):
    """Manager for prompt argument operations."""

    _model = PromptArgumentModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/prompt-argument"
    tags: ClassVar[Optional[List[str]]] = ["Prompt Argument Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    auth_dependency: ClassVar[Optional[str]] = "get_current_user"

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry=None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )

    def create_validation(self, entity) -> None:
        """Validate that the prompt exists before creating an argument."""
        # Check that the prompt exists
        prompt_manager = PromptManager(
            requester_id=self.requester.id,
            target_id=self.target_id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        )

        try:
            prompt_manager.get(id=entity.prompt_id)
        except Exception:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=404, detail=f"Prompt with ID {entity.prompt_id} not found"
            )


# Extension hooks for integrating prompts with other models
try:
    from zephyrex.extensions.conversations.BLL_Conversations import MessageModel
    from zephyrex.pydantic2.sqlalchemy import extension_model

    # Extend Message model with prompt reference
    @extension_model(MessageModel)
    class MessagePromptExtension(BaseModel):
        """Add prompt reference to conversation messages."""

        prompt_id: Optional[str] = Field(
            None, description="ID of the prompt used to generate this message"
        )

except ImportError:
    # Conversations extension not available
    pass

# Extension for AI Chains if available
# try:
#     from extensions.ai_chains.BLL_AI_Chains import ChainLinkModel
#     from lib.Pydantic2SQLAlchemy import extension_model

#     # Extend ChainLink model with prompt reference
#     @extension_model(ChainLinkModel)
#     class ChainLinkPromptExtension(BaseModel):
#         """Add prompt reference to chain links."""
#         prompt_id: Optional[str] = Field(None, description="ID of the related prompt")

# except ImportError:
#     # AI Chains extension not available
#     pass
