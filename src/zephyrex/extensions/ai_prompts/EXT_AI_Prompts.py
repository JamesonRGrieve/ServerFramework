"""
AI Prompts extension for AGInfrastructure.
Implements AI prompt management and optimization through the AI framework.
"""

from abc import abstractmethod
from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger
from zephyrex.pydantic2.registry import classproperty
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class AbstractAIPromptProvider(AbstractStaticProvider):
    """
    Abstract base class for AI prompt service providers.
    Defines the common interface for all AI prompt providers with static functionality.
    All AI prompt providers should be static/abstract classes with no instantiation required.
    """

    extension_type: ClassVar[str] = "ai_prompts"

    @classmethod
    @abstractmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        """Bond a provider instance for API operations."""
        pass

    @classmethod
    @abstractmethod
    @ability(name="render_template")
    def render_template(
        cls,
        bonded_instance: AbstractProviderInstance,
        template: str,
        variables: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Render a prompt template with variables."""
        pass

    @classmethod
    @abstractmethod
    @ability(name="validate_prompt")
    def validate_prompt(
        cls,
        bonded_instance: AbstractProviderInstance,
        prompt: str,
    ) -> Dict[str, Any]:
        """Validate a prompt for correctness and effectiveness."""
        pass

    @classmethod
    @abstractmethod
    @ability(name="generate_variations")
    def generate_variations(
        cls,
        bonded_instance: AbstractProviderInstance,
        base_prompt: str,
        count: int = 3,
    ) -> Dict[str, Any]:
        """Generate variations of a base prompt."""
        pass


class EXT_AI_Prompts(AbstractStaticExtension):
    """
    AI Prompts extension for AGInfrastructure.

    Provides comprehensive AI prompt management capabilities including template creation,
    prompt optimization, formatting, and AI model integration. This extension depends
    on the AI framework for core AI provider functionality and extends it with
    prompt-specific capabilities.

    The extension focuses on:
    - Prompt template creation and management
    - Dynamic prompt generation and formatting
    - Prompt optimization and testing through AI framework
    - AI model integration via provider rotation
    - Prompt history tracking and analytics
    - Template versioning and A/B testing
    - Context-aware prompt generation

    Usage:
        # Create prompt template with AI optimization
        result = EXT_AI_Prompts.root.rotate(
            EXT_AI_Prompts.create_optimized_prompt,
            template="Hello {name}, how can I help you?",
            optimization_goal="clarity",
            test_inputs=[{"name": "John"}, {"name": "Jane"}]
        )
    """

    # Extension metadata
    name: ClassVar[str] = "ai_prompts"
    friendly_name: ClassVar[str] = "AI Prompt Management"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "AI prompt management extension for template creation, optimization, and AI model integration through AI framework"
    )

    # Environment variables that this extension needs
    _env: ClassVar[Dict[str, Any]] = {
        "AI_PROMPTS_ENABLED": "true",
        "AI_PROMPTS_TEMPLATE_CACHE_SIZE": "1000",
        "AI_PROMPTS_DEFAULT_OPTIMIZATION": "clarity",
        "AI_PROMPTS_MAX_TEMPLATE_SIZE": "10000",
    }

    # Unified dependencies using the Dependencies class
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="ai",
                friendly_name="AI Framework",
                reason="Core AI provider functionality for prompt optimization and model integration",
                optional=False,
            ),
            PIP_Dependency(
                name="jinja2",
                friendly_name="Jinja2 Template Engine",
                semver=">=3.0.0",
                reason="Template rendering and dynamic prompt generation",
            ),
        ]
    )

    # Meta abilities provided by this extension for managing prompts
    _abilities: ClassVar[Set[str]] = {
        "manage_prompt_templates",
        "optimize_prompts",
        "track_prompt_analytics",
        "manage_prompt_versions",
    }

    @classproperty
    def pip_dependencies(cls):
        """Get PIP dependencies for backward compatibility."""
        return cls.dependencies.pip

    @classproperty
    def ext_dependencies(cls):
        """Get extension dependencies for backward compatibility."""
        return cls.dependencies.ext

    @classproperty
    def sys_dependencies(cls):
        """Get system dependencies for backward compatibility."""
        return cls.dependencies.sys

    @classmethod
    def has_ability(cls, ability: str) -> bool:
        """Check if this extension has a specific ability."""
        return ability in cls._abilities

    @ability
    @classmethod
    def manage_prompt_templates(cls, **kwargs) -> Dict[str, Any]:
        """
        Meta ability: Manage AI prompt templates.

        Returns:
            Dict containing prompt template management information
        """
        try:
            # In real implementation, would query PromptTemplateModel records
            return {
                "success": True,
                "total_templates": 0,
                "active_templates": 0,
                "message": "Prompt template management capability",
            }
        except Exception as e:
            logger.error(f"Error managing prompt templates: {e}")
            return {"success": False, "error": str(e)}

    @ability
    @classmethod
    def optimize_prompts(
        cls, template: str, optimization_goal: str = "clarity", **kwargs
    ) -> Dict[str, Any]:
        """
        Meta ability: Optimize AI prompts for better performance.

        Args:
            template: The prompt template to optimize
            optimization_goal: Goal for optimization (clarity, brevity, effectiveness)

        Returns:
            Optimization result with suggestions
        """
        try:
            return {
                "success": True,
                "original_template": template,
                "optimization_goal": optimization_goal,
                "suggestions": [],
                "message": f"Prompt optimization capability for {optimization_goal}",
            }
        except Exception as e:
            logger.error(f"Error optimizing prompts: {e}")
            return {"success": False, "error": str(e)}

    @ability
    @classmethod
    def track_prompt_analytics(cls, prompt_id: str = None, **kwargs) -> Dict[str, Any]:
        """
        Meta ability: Track analytics for prompt usage and performance.

        Args:
            prompt_id: Optional prompt ID for specific analytics

        Returns:
            Analytics information
        """
        try:
            # In real implementation, would query prompt usage data
            return {
                "success": True,
                "total_usage": 0,
                "average_response_time": 0.0,
                "success_rate": 0.0,
                "message": f"Analytics for {'prompt ' + prompt_id if prompt_id else 'all prompts'}",
            }
        except Exception as e:
            logger.error(f"Error tracking prompt analytics: {e}")
            return {"success": False, "error": str(e)}

    @ability
    @classmethod
    def manage_prompt_versions(
        cls, prompt_id: str, action: str = "list", **kwargs
    ) -> Dict[str, Any]:
        """
        Meta ability: Manage prompt versions and A/B testing.

        Args:
            prompt_id: ID of the prompt
            action: Action to perform (list, create, compare, rollback)

        Returns:
            Version management result
        """
        try:
            return {
                "success": True,
                "prompt_id": prompt_id,
                "action": action,
                "versions": [],
                "message": f"Version management for prompt {prompt_id}",
            }
        except Exception as e:
            logger.error(f"Error managing prompt versions: {e}")
            return {"success": False, "error": str(e)}

    AbstractProvider = AbstractAIPromptProvider
