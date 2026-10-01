from typing import List

import pytest

from zephyrex.extensions.AbstractEXTTest import (
    AbstractEXTTest,
    ExtensionTestConfig,
    ExtensionTestType,
)
from zephyrex.extensions.ai_prompts.EXT_AI_Prompts import EXT_AI_Prompts


class TestEXTAIPrompts(AbstractEXTTest):
    """
    Test suite for EXT_AI_Prompts extension.

    Tests extension initialization, AI prompt capabilities, abilities, and AI model integration.
    Focuses on testing prompt management functionality, template rendering, and static extension
    metadata rather than component loading.

    Test areas:
    - Extension metadata and configuration
    - Dependencies and provider discovery
    - Static extension functionality
    - Integration with AI framework through dependencies
    """

    # Configure the test class
    extension_class = EXT_AI_Prompts
    test_config = ExtensionTestConfig(
        test_types={
            ExtensionTestType.STRUCTURE,
            ExtensionTestType.METADATA,
            ExtensionTestType.DEPENDENCIES,
            ExtensionTestType.ABILITIES,
            ExtensionTestType.ENVIRONMENT,
        },
        expected_abilities={
            "manage_prompt_templates",
            "optimize_prompts",
            "track_prompt_analytics",
            "manage_prompt_versions",
        },
    )

    # Expected extension properties
    expected_abilities = [
        "manage_prompt_templates",
        "optimize_prompts",
        "track_prompt_analytics",
        "manage_prompt_versions",
    ]

    def test_extension_metadata(self):
        """Test extension metadata and basic attributes"""
        assert self.extension_class.name == "ai_prompts"
        assert self.extension_class.version == "1.0.0"
        assert self.extension_class.friendly_name == "AI Prompt Management"
        assert "AI prompt management extension" in self.extension_class.description

    def test_dependencies_structure(self):
        """Test that dependencies are properly structured"""
        # Check that dependencies is a Dependencies object
        assert hasattr(self.extension_class, "dependencies")
        assert self.extension_class.dependencies is not None

        # Check extension dependencies
        ext_deps = self.extension_class.dependencies.ext
        assert len(ext_deps) == 1
        ai_dep = ext_deps[0]
        assert ai_dep.name == "ai"
        assert not ai_dep.optional

        # Check pip dependencies
        pip_deps = self.extension_class.dependencies.pip
        assert len(pip_deps) >= 1

        # Check for jinja2 dependency
        jinja_deps = [dep for dep in pip_deps if dep.name == "jinja2"]
        assert len(jinja_deps) == 1
        jinja_dep = jinja_deps[0]
        assert ">=3.0.0" in jinja_dep.semver

    def test_provider_discovery(self):
        """Test that provider discovery works"""
        providers = self.extension_class.providers
        assert isinstance(providers, list)
        # AI Prompts may have providers or not, just check it's a list

    def test_extension_env_vars(self):
        """Test that extension has proper environment variables."""
        assert hasattr(self.extension_class, "_env")
        env_vars = self.extension_class._env

        expected_env_vars = [
            "AI_PROMPTS_ENABLED",
            "AI_PROMPTS_TEMPLATE_CACHE_SIZE",
            "AI_PROMPTS_DEFAULT_OPTIMIZATION",
            "AI_PROMPTS_MAX_TEMPLATE_SIZE",
        ]

        for env_var in expected_env_vars:
            assert env_var in env_vars, f"Missing environment variable: {env_var}"

    def test_meta_abilities_execution(self):
        """Test that meta abilities can be executed."""
        # Test that methods exist and are callable
        assert hasattr(self.extension_class, "manage_prompt_templates")
        assert callable(getattr(self.extension_class, "manage_prompt_templates"))

        assert hasattr(self.extension_class, "optimize_prompts")
        assert callable(getattr(self.extension_class, "optimize_prompts"))

        assert hasattr(self.extension_class, "track_prompt_analytics")
        assert callable(getattr(self.extension_class, "track_prompt_analytics"))

        assert hasattr(self.extension_class, "manage_prompt_versions")
        assert callable(getattr(self.extension_class, "manage_prompt_versions"))

    def test_abstract_provider_class(self):
        """Test that AbstractAIPromptProvider exists in EXT_AI_Prompts."""
        from zephyrex.extensions.ai_prompts.EXT_AI_Prompts import AbstractAIPromptProvider

        assert hasattr(AbstractAIPromptProvider, "extension_type")
        assert AbstractAIPromptProvider.extension_type == "ai_prompts"

        # Test that it has the expected abstract methods
        expected_methods = [
            "bond_instance",
            "render_template",
            "validate_prompt",
            "generate_variations",
        ]

        for method_name in expected_methods:
            assert hasattr(
                AbstractAIPromptProvider, method_name
            ), f"Missing method: {method_name}"

    def test_static_extension_inheritance(self):
        """Test that extension inherits from AbstractStaticExtension"""
        from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension

        assert issubclass(self.extension_class, AbstractStaticExtension)

    def test_extension_abilities(self):
        """Test that extension has expected abilities."""
        abilities = self.extension_class.abilities

        for expected_ability in self.expected_abilities:
            assert expected_ability in abilities, f"Missing ability: {expected_ability}"

    def test_has_ability(self):
        """Test the has_ability method."""
        # Test with known capability
        if hasattr(self.extension_class, "has_ability"):
            assert self.extension_class.has_ability("manage_prompt_templates")
            # Test with unknown capability
            assert not self.extension_class.has_ability("unknown_capability")
        else:
            # Fallback to checking abilities directly
            assert "manage_prompt_templates" in self.extension_class.abilities
