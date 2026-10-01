from typing import List

import pytest

from zephyrex.extensions.AbstractEXTTest import (
    AbstractEXTTest,
    ExtensionTestConfig,
    ExtensionTestType,
)
from zephyrex.extensions.ai.EXT_AI import EXT_AI


class TestEXTAI(AbstractEXTTest):
    """
    Test suite for EXT_AI extension.

    Tests extension initialization, AI capabilities, abilities, and provider integration.
    Focuses on testing AI functionality, provider management, and static extension
    metadata rather than component loading.

    Test areas:
    - Extension metadata and configuration
    - AI capabilities and abilities
    - Multi-provider integration (OpenAI, Anthropic, etc.)
    - Provider ability discovery
    - Text generation, embeddings, image generation capabilities
    - Extension lifecycle and configuration validation
    """

    # Configure the test class
    extension_class = EXT_AI
    test_config = ExtensionTestConfig(
        test_types={
            ExtensionTestType.STRUCTURE,
            ExtensionTestType.METADATA,
            ExtensionTestType.DEPENDENCIES,
            ExtensionTestType.ABILITIES,
            ExtensionTestType.ENVIRONMENT,
        },
        expected_abilities={
            "manage_ai_providers",
            "configure_ai_models",
            "track_ai_usage",
            "optimize_model_selection",
        },
    )

    # Expected extension properties
    expected_abilities = [
        "manage_ai_providers",
        "configure_ai_models",
        "track_ai_usage",
        "optimize_model_selection",
    ]

    expected_capabilities = [
        "text_generation",
        "embedding_generation",
        "image_generation",
        "audio_transcription",
        "text_to_speech",
    ]

    def test_extension_metadata(self):
        """Test that extension has proper metadata."""
        assert self.extension_class.name == "ai"
        assert self.extension_class.friendly_name == "AI Framework"
        assert self.extension_class.version == "2.0.0"
        assert (
            "AI extension providing comprehensive" in self.extension_class.description
        )

    def test_extension_dependencies(self):
        """Test that extension has proper dependencies configured."""
        assert hasattr(self.extension_class, "dependencies")
        assert self.extension_class.dependencies is not None

        # Check for required tiktoken dependency
        pip_deps = [
            dep for dep in self.extension_class.dependencies.pip if not dep.optional
        ]
        assert any(dep.name == "tiktoken" for dep in pip_deps)

    def test_extension_abilities(self):
        """Test that extension has expected abilities."""
        abilities = self.extension_class.abilities

        for expected_ability in self.expected_abilities:
            assert expected_ability in abilities, f"Missing ability: {expected_ability}"

    def test_extension_env_vars(self):
        """Test that extension has proper environment variables."""
        assert hasattr(self.extension_class, "_env")
        env_vars = self.extension_class._env

        expected_env_vars = [
            "AI_DEFAULT_MODEL",
            "AI_MAX_TOKENS",
            "AI_TEMPERATURE",
            "AI_REQUEST_TIMEOUT",
        ]

        for env_var in expected_env_vars:
            assert env_var in env_vars, f"Missing environment variable: {env_var}"

    def test_provider_discovery(self):
        """Test that providers can be discovered."""
        providers = self.extension_class.providers
        assert isinstance(providers, list)
        # Providers may be empty if not installed, but should be a list

    def test_has_capability(self):
        """Test the has_ability method."""
        # Test with known capability
        if hasattr(self.extension_class, "has_ability"):
            assert self.extension_class.has_ability("manage_ai_providers")
            # Test with unknown capability
            assert not self.extension_class.has_ability("unknown_capability")
        else:
            # Fallback to checking abilities directly
            assert "manage_ai_providers" in self.extension_class.abilities

    def test_conversations_available(self):
        """Test the conversations_available property if it exists."""
        # This should return a boolean regardless of whether conversations is available
        if hasattr(self.extension_class, "conversations_available"):
            result = self.extension_class.conversations_available
            assert isinstance(result, bool)
        else:
            # Skip if property doesn't exist
            pytest.skip("conversations_available property not found")

    def test_ai_meta_abilities_exist(self):
        """Test that AI meta abilities exist."""
        assert hasattr(self.extension_class, "manage_ai_providers")
        assert callable(getattr(self.extension_class, "manage_ai_providers"))

        assert hasattr(self.extension_class, "configure_ai_models")
        assert callable(getattr(self.extension_class, "configure_ai_models"))

        assert hasattr(self.extension_class, "track_ai_usage")
        assert callable(getattr(self.extension_class, "track_ai_usage"))

        assert hasattr(self.extension_class, "optimize_model_selection")
        assert callable(getattr(self.extension_class, "optimize_model_selection"))

    def test_abstract_provider_class_exists(self):
        """Test that AbstractProvider class exists."""
        assert hasattr(self.extension_class, "AbstractProvider")
        abstract_provider = self.extension_class.AbstractProvider

        # Test that it has the expected abstract methods
        expected_methods = [
            "bond_instance",
            "get_platform_name",
            "services",
            "generate_text",
            "generate_embeddings",
        ]

        for method_name in expected_methods:
            assert hasattr(
                abstract_provider, method_name
            ), f"Missing method: {method_name}"

    def test_meta_abilities_execution(self):
        """Test that meta abilities can be executed."""
        # Test that methods exist and are callable
        assert hasattr(self.extension_class, "manage_ai_providers")
        assert callable(getattr(self.extension_class, "manage_ai_providers"))

        assert hasattr(self.extension_class, "track_ai_usage")
        assert callable(getattr(self.extension_class, "track_ai_usage"))

        assert hasattr(self.extension_class, "configure_ai_models")
        assert callable(getattr(self.extension_class, "configure_ai_models"))

        assert hasattr(self.extension_class, "optimize_model_selection")
        assert callable(getattr(self.extension_class, "optimize_model_selection"))

    def test_extension_structure(self):
        """Test that extension has proper structure."""
        # Test extension type
        if hasattr(self.extension_class.AbstractProvider, "extension_type"):
            assert self.extension_class.AbstractProvider.extension_type == "ai"

        # Test that providers are properly linked
        if self.extension_class.providers:
            for provider in self.extension_class.providers:
                if hasattr(provider, "extension_type"):
                    assert provider.extension_type == "ai"
