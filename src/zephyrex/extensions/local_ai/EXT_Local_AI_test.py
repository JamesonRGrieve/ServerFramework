"""
Test suite for Local AI extension.
Tests base Local AI extension metadata and abstract provider interface.
"""

from typing import Dict, List, Set
from unittest.mock import MagicMock

import pytest

from zephyrex.extensions.local_ai.EXT_Local_AI import (
    AbstractLocalAIExtensionProvider,
    EXT_Local_AI,
)
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency


class ConcreteLocalAIProvider(AbstractLocalAIExtensionProvider):
    """Concrete implementation of AbstractLocalAIExtensionProvider for testing"""

    # Static provider metadata
    name = "test_local_ai"
    version = "1.0.0"
    description = "Test local AI provider"

    # Link to parent extension (REQUIRED for Provider Rotation System)
    extension = EXT_Local_AI

    # Add unified dependencies using the Dependencies class
    dependencies = Dependencies(
        [
            PIP_Dependency(
                name="torch",
                friendly_name="PyTorch",
                semver=">=1.12.0",
                reason="AI model support",
            ),
        ]
    )

    # Initialize static abilities for testing
    abilities = {
        "text_generation",
        "embedding_generation",
        "model_management",
        "hardware_optimization",
    }

    @classmethod
    def get_abilities(cls) -> Set[str]:
        """Get provider abilities for rotation system."""
        return cls.abilities

    @classmethod
    def load_model(cls, model_config: Dict, **kwargs) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "model_id": "test_model_123",
            "model_type": model_config.get("model_type", "test"),
            "status": "loaded",
            "device": model_config.get("device", "cpu"),
            "memory_usage_mb": 1024,
        }

    @classmethod
    def unload_model(cls, model_id: str) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "model_id": model_id,
            "status": "unloaded",
            "memory_freed_mb": 1024,
        }

    @classmethod
    def generate_text(
        cls, prompt: str, max_tokens: int = 512, temperature: float = 0.7, **kwargs
    ) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "text": f"Generated response to: {prompt[:50]}...",
            "tokens_used": min(len(prompt.split()) + 20, max_tokens),
            "temperature": temperature,
            "model_id": "test_model_123",
        }

    @classmethod
    def generate_embeddings(cls, text: str, normalize: bool = True, **kwargs) -> Dict:
        """Mock implementation for testing"""
        # Generate a simple test embedding (384 dimensions)
        embedding = [0.1] * 384
        return {
            "success": True,
            "embeddings": embedding,
            "dimensions": len(embedding),
            "normalized": normalize,
            "text_length": len(text),
        }

    @classmethod
    def get_model_status(cls, model_id: str) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "model_id": model_id,
            "status": "loaded",
            "memory_usage_mb": 1024,
            "device": "cpu",
            "load_time": "2023-01-01T12:00:00Z",
        }

    @classmethod
    def get_hardware_info(cls) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "has_cuda": False,
            "has_mps": False,
            "cpu_count": 4,
            "total_memory_gb": 16,
            "available_memory_gb": 8,
            "recommended_device": "cpu",
        }

    @classmethod
    def optimize_for_hardware(cls, model_config: Dict) -> Dict:
        """Mock implementation for testing"""
        optimized_config = model_config.copy()
        optimized_config["device"] = "cpu"
        optimized_config["optimized"] = True
        return {
            "success": True,
            "original_config": model_config,
            "optimized_config": optimized_config,
            "optimizations_applied": ["cpu_optimization", "memory_optimization"],
        }

    @classmethod
    def can_handle_model(cls, model_path_or_id: str) -> bool:
        """Mock implementation for testing"""
        # Accept any model for testing
        return True

    @classmethod
    def get_model_capabilities(cls, model_path_or_id: str) -> Set[str]:
        """Mock implementation for testing"""
        return {"text_generation", "embedding_generation"}

    @classmethod
    def bond_instance(cls, instance):
        """Bond provider instance for rotation system."""
        return cls()

    @classmethod
    def services(cls) -> List[str]:
        """Return list of services provided by this provider."""
        return ["ai", "ml", "text_generation", "embeddings"]

    @classmethod
    def has_ability(cls, ability: str) -> bool:
        """Check if provider has a specific ability."""
        return ability in cls.abilities


@pytest.mark.local_ai
class TestEXTLocalAI:
    """
    Test suite for EXT_Local_AI extension.

    Tests basic extension metadata and abstract provider interface.
    Only tests functionality that actually exists in the implementation.

    Test areas:
    - Extension metadata (name, version, description)
    - Abstract local AI provider class structure
    - Provider inheritance and linkage
    - Hardware detection functionality
    - Model management interface
    """

    def test_extension_metadata(self):
        """Test extension metadata."""
        assert EXT_Local_AI.name == "local_ai"
        assert EXT_Local_AI.friendly_name == "Local AI Framework"
        assert EXT_Local_AI.version == "2.0.0"
        assert "local ai" in EXT_Local_AI.description.lower()
        assert "provider rotation" in EXT_Local_AI.description.lower()

    def test_extension_class_structure(self):
        """Test extension class structure."""
        # Test that EXT_Local_AI is properly defined
        assert hasattr(EXT_Local_AI, "name")
        assert hasattr(EXT_Local_AI, "friendly_name")
        assert hasattr(EXT_Local_AI, "version")
        assert hasattr(EXT_Local_AI, "description")

        # Test inheritance
        from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension

        assert issubclass(EXT_Local_AI, AbstractStaticExtension)

    def test_abstract_local_ai_provider_class_exists(self):
        """The abstract local-AI provider exists and subclasses the static base."""
        from zephyrex.extensions.AbstractExtensionProvider import (
            AbstractStaticProvider,
        )
        from zephyrex.extensions.local_ai.EXT_Local_AI import AbstractLocalAIProvider

        assert AbstractLocalAIProvider is not None
        assert issubclass(AbstractLocalAIProvider, AbstractStaticProvider)
        assert AbstractLocalAIProvider.extension == EXT_Local_AI

    def test_abstract_local_ai_provider_cannot_be_instantiated(self):
        """The abstract local-AI provider cannot be instantiated directly."""
        from zephyrex.extensions.local_ai.EXT_Local_AI import AbstractLocalAIProvider

        with pytest.raises(TypeError):
            AbstractLocalAIProvider()

    def test_concrete_provider_can_inherit_from_abstract(self):
        """Test that concrete providers can inherit from AbstractLocalAIExtensionProvider."""
        # Test that ConcreteLocalAIProvider inherits properly
        assert issubclass(ConcreteLocalAIProvider, AbstractLocalAIExtensionProvider)

        # Test required attributes exist
        assert hasattr(ConcreteLocalAIProvider, "name")
        assert hasattr(ConcreteLocalAIProvider, "version")
        assert hasattr(ConcreteLocalAIProvider, "description")
        assert hasattr(ConcreteLocalAIProvider, "extension")

        # Test extension linkage works
        assert ConcreteLocalAIProvider.extension == EXT_Local_AI

    def test_concrete_provider_metadata(self):
        """Test concrete provider metadata."""
        assert ConcreteLocalAIProvider.name == "test_local_ai"
        assert ConcreteLocalAIProvider.version == "1.0.0"
        assert ConcreteLocalAIProvider.description == "Test local AI provider"

    def test_concrete_provider_methods_implementation(self):
        """Test that concrete provider implements required local AI methods."""
        # Test that all expected local AI methods exist and are callable
        local_ai_methods = [
            "load_model",
            "unload_model",
            "generate_text",
            "generate_embeddings",
            "get_model_status",
            "get_hardware_info",
            "optimize_for_hardware",
            "can_handle_model",
            "get_model_capabilities",
            "get_abilities",
        ]

        for method_name in local_ai_methods:
            assert hasattr(ConcreteLocalAIProvider, method_name)
            assert callable(getattr(ConcreteLocalAIProvider, method_name))

    def test_concrete_provider_model_management_functionality(self):
        """Test concrete provider model management functionality works."""
        # Test load_model
        result = ConcreteLocalAIProvider.load_model(
            {"model_type": "test", "model_path": "test/model", "device": "cpu"}
        )
        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["model_type"] == "test"

        # Test unload_model
        result = ConcreteLocalAIProvider.unload_model("test_model_123")
        assert isinstance(result, dict)
        assert result["success"] is True
        assert "model_id" in result

        # Test get_model_status
        result = ConcreteLocalAIProvider.get_model_status("test_model_123")
        assert isinstance(result, dict)
        assert result["success"] is True
        assert "status" in result

    def test_concrete_provider_text_generation_functionality(self):
        """Test concrete provider text generation functionality works."""
        # Test generate_text
        result = ConcreteLocalAIProvider.generate_text(
            prompt="What is artificial intelligence?",
            max_tokens=100,
            temperature=0.8,
        )
        assert isinstance(result, dict)
        assert result["success"] is True
        assert "text" in result
        assert result["temperature"] == 0.8

    def test_concrete_provider_embedding_functionality(self):
        """Test concrete provider embedding functionality works."""
        # Test generate_embeddings
        result = ConcreteLocalAIProvider.generate_embeddings(
            text="This is a test sentence",
            normalize=True,
        )
        assert isinstance(result, dict)
        assert result["success"] is True
        assert "embeddings" in result
        assert isinstance(result["embeddings"], list)
        assert result["normalized"] is True
        assert result["dimensions"] == 384

    def test_concrete_provider_hardware_functionality(self):
        """Test concrete provider hardware functionality works."""
        # Test get_hardware_info
        result = ConcreteLocalAIProvider.get_hardware_info()
        assert isinstance(result, dict)
        assert result["success"] is True
        assert "has_cuda" in result
        assert "cpu_count" in result

        # Test optimize_for_hardware
        config = {"model_type": "test", "device": "auto"}
        result = ConcreteLocalAIProvider.optimize_for_hardware(config)
        assert isinstance(result, dict)
        assert result["success"] is True
        assert "optimized_config" in result
        assert result["optimized_config"]["optimized"] is True

    def test_concrete_provider_model_compatibility_functionality(self):
        """Test concrete provider model compatibility functionality works."""
        # Test can_handle_model
        can_handle = ConcreteLocalAIProvider.can_handle_model("test/model/path")
        assert isinstance(can_handle, bool)
        assert can_handle is True

        # Test get_model_capabilities
        capabilities = ConcreteLocalAIProvider.get_model_capabilities("test/model/path")
        assert isinstance(capabilities, set)
        assert "text_generation" in capabilities

    def test_concrete_provider_utility_methods(self):
        """Test concrete provider utility methods."""
        # Test get_abilities
        abilities = ConcreteLocalAIProvider.get_abilities()
        assert isinstance(abilities, set)
        assert "text_generation" in abilities
        assert "embedding_generation" in abilities

        # Test services
        services = ConcreteLocalAIProvider.services()
        assert isinstance(services, list)
        assert "ai" in services

        # Test has_ability
        assert ConcreteLocalAIProvider.has_ability("text_generation") is True
        assert ConcreteLocalAIProvider.has_ability("nonexistent_ability") is False

    def test_concrete_provider_dependencies_structure(self):
        """Test concrete provider dependencies structure."""
        assert hasattr(ConcreteLocalAIProvider, "dependencies")
        assert isinstance(ConcreteLocalAIProvider.dependencies, Dependencies)

        # Test that it has pip dependencies
        pip_deps = ConcreteLocalAIProvider.dependencies.pip
        assert len(pip_deps) >= 1
        assert any(dep.name == "torch" for dep in pip_deps)

    def test_concrete_provider_bond_instance_method(self):
        """Test concrete provider bond_instance method."""
        mock_instance = MagicMock()
        result = ConcreteLocalAIProvider.bond_instance(mock_instance)
        assert result is not None

    def test_provider_discovery(self):
        """Test provider discovery functionality."""
        providers = EXT_Local_AI.providers()  # Call as a method since it's @classmethod
        assert isinstance(providers, list), "Providers should be a list"
        # Providers list may be empty in test environment, which is acceptable

    def test_extension_no_dependencies(self):
        """Base extension declares only optional dependencies (no hard deps)."""
        deps = getattr(EXT_Local_AI, "dependencies", [])
        assert all(getattr(d, "optional", False) for d in deps)

    def test_concrete_provider_abilities_coverage(self):
        """Test that concrete provider covers expected AI abilities."""
        abilities = ConcreteLocalAIProvider.get_abilities()

        # Core AI abilities should be present
        expected_abilities = {
            "text_generation",
            "embedding_generation",
            "model_management",
        }

        for ability in expected_abilities:
            assert ability in abilities, f"Missing expected ability: {ability}"

    def test_concrete_provider_error_handling(self):
        """Test concrete provider handles errors gracefully."""
        # Test with invalid model config
        result = ConcreteLocalAIProvider.load_model({})
        assert isinstance(result, dict)
        # Should not crash even with empty config

        # Test with None values
        result = ConcreteLocalAIProvider.generate_text("")
        assert isinstance(result, dict)
        # Should handle empty prompt gracefully

    def test_hardware_info_structure(self):
        """Test hardware info returns expected structure."""
        hardware_info = ConcreteLocalAIProvider.get_hardware_info()

        # Check required fields
        required_fields = [
            "has_cuda",
            "has_mps",
            "cpu_count",
            "total_memory_gb",
            "available_memory_gb",
            "recommended_device",
        ]

        for field in required_fields:
            assert field in hardware_info, f"Missing hardware info field: {field}"

    def test_model_config_optimization_preserves_original(self):
        """Test that hardware optimization preserves original config."""
        original_config = {
            "model_type": "test",
            "device": "auto",
            "some_custom_param": "value",
        }

        result = ConcreteLocalAIProvider.optimize_for_hardware(original_config)

        # Original config should be preserved
        assert result["original_config"] == original_config

        # Optimized config should be different
        assert result["optimized_config"] != original_config

        # But should preserve custom parameters
        assert result["optimized_config"]["some_custom_param"] == "value"
