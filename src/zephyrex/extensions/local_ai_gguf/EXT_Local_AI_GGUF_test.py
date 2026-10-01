"""
Test suite for Local AI GGUF extension.
Tests GGUF extension metadata and abstract GGUF provider interface.
"""

from typing import Dict, List, Set
from unittest.mock import MagicMock

import pytest

from zephyrex.extensions.local_ai_gguf.EXT_Local_AI_GGUF import EXT_Local_AI_GGUF
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency


class ConcreteGGUFProvider:
    """Concrete implementation of AbstractGGUFExtensionProvider for testing"""

    # Static provider metadata
    name = "test_gguf"
    version = "1.0.0"
    description = "Test GGUF provider"

    # Link to parent extension (REQUIRED for Provider Rotation System)
    extension = EXT_Local_AI_GGUF

    # Add unified dependencies using the Dependencies class
    dependencies = Dependencies(
        [
            PIP_Dependency(
                name="llama-cpp-python",
                friendly_name="llama.cpp Python bindings",
                semver=">=0.2.0",
                reason="GGUF model support",
            ),
        ]
    )

    # Initialize static abilities for testing
    abilities = {
        "text_generation",
        "embedding_generation",
        "quantized_inference",
        "cpu_optimization",
        "gpu_acceleration",
        "memory_mapping",
        "context_extension",
    }

    @classmethod
    def get_abilities(cls) -> Set[str]:
        """Get GGUF-specific abilities for rotation system."""
        return cls.abilities

    @classmethod
    def load_model(
        cls, model_path: str, n_gpu_layers: int = -1, context_size: int = 4096, **kwargs
    ) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "model_id": f"gguf_model_{hash(model_path) % 1000}",
            "model_path": model_path,
            "format": "gguf",
            "n_gpu_layers": n_gpu_layers,
            "context_size": context_size,
            "status": "loaded",
            "memory_usage_mb": 2048,
            "quantization": "Q4_K_M",
        }

    @classmethod
    def unload_model(cls, model_id: str) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "model_id": model_id,
            "status": "unloaded",
            "memory_freed_mb": 2048,
        }

    @classmethod
    def generate_text(
        cls,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.9,
        top_k: int = 40,
        repeat_penalty: float = 1.1,
        stream: bool = False,
        **kwargs,
    ) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "text": f"GGUF generated response to: {prompt[:30]}...",
            "tokens_used": min(len(prompt.split()) + 25, max_tokens),
            "temperature": temperature,
            "top_p": top_p,
            "top_k": top_k,
            "repeat_penalty": repeat_penalty,
            "model_format": "gguf",
            "quantization": "Q4_K_M",
            "stream": stream,
        }

    @classmethod
    def generate_embeddings(cls, text: str, **kwargs) -> Dict:
        """Mock implementation for testing"""
        # Generate a test embedding (512 dimensions for GGUF)
        embedding = [0.05] * 512
        return {
            "success": True,
            "embeddings": embedding,
            "dimensions": len(embedding),
            "text_length": len(text),
            "model_format": "gguf",
        }

    @classmethod
    def get_model_info(cls, model_path: str) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "model_path": model_path,
            "format": "gguf",
            "version": 3,
            "tensor_count": 291,
            "quantization": "Q4_K_M",
            "context_size": 4096,
            "vocab_size": 32000,
            "architecture": "llama",
            "file_size_mb": 3800,
        }

    @classmethod
    def optimize_gpu_settings(cls, model_config: Dict) -> Dict:
        """Mock implementation for testing"""
        optimized_config = model_config.copy()
        optimized_config["n_gpu_layers"] = 32
        optimized_config["main_gpu"] = 0
        optimized_config["tensor_split"] = None
        return {
            "success": True,
            "original_config": model_config,
            "optimized_config": optimized_config,
            "gpu_optimizations": ["layer_distribution", "memory_optimization"],
            "estimated_vram_usage_mb": 3200,
        }

    @classmethod
    def optimize_cpu_settings(cls, model_config: Dict) -> Dict:
        """Mock implementation for testing"""
        optimized_config = model_config.copy()
        optimized_config["n_threads"] = 6
        optimized_config["n_batch"] = 128
        optimized_config["use_mlock"] = True
        optimized_config["use_mmap"] = True
        return {
            "success": True,
            "original_config": model_config,
            "optimized_config": optimized_config,
            "cpu_optimizations": [
                "thread_optimization",
                "memory_mapping",
                "batch_sizing",
            ],
        }

    @classmethod
    def can_handle_model(cls, model_path: str) -> bool:
        """Mock implementation for testing"""
        # Check if it's a GGUF file
        return model_path.lower().endswith((".gguf", ".ggml"))

    @classmethod
    def get_quantization_info(cls, model_path: str) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "quantization": "Q4_K_M",
            "bits_per_weight": 4.5,
            "memory_reduction": "75%",
            "quality_level": "medium",
            "recommended_use": "Balanced performance and quality",
        }

    @classmethod
    def estimate_memory_usage(cls, model_path: str, n_gpu_layers: int = -1) -> Dict:
        """Mock implementation for testing"""
        base_memory = 3800  # MB
        gpu_memory = base_memory * 0.8 if n_gpu_layers > 0 else 0
        cpu_memory = base_memory - gpu_memory

        return {
            "success": True,
            "total_memory_mb": base_memory,
            "gpu_memory_mb": gpu_memory,
            "cpu_memory_mb": cpu_memory,
            "context_memory_mb": 512,
            "overhead_mb": 256,
        }

    @classmethod
    def validate_gguf_file(cls, model_path: str) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "valid": True,
            "format": "gguf",
            "version": 3,
            "magic": "GGUF",
            "metadata_valid": True,
            "tensor_count": 291,
        }

    @classmethod
    def bond_instance(cls, instance):
        """Bond provider instance for rotation system."""
        return cls()

    @classmethod
    def services(cls) -> List[str]:
        """Return list of services provided by this provider."""
        return ["ai", "llm", "quantized_inference", "cpu_optimization"]

    @classmethod
    def has_ability(cls, ability: str) -> bool:
        """Check if provider has a specific ability."""
        return ability in cls.abilities


@pytest.mark.local_ai
@pytest.mark.gguf
class TestEXTLocalAI_GGUF:
    """
    Test suite for EXT_Local_AI_GGUF extension.

    Tests GGUF extension metadata and abstract provider interface.
    Only tests functionality that actually exists in the implementation.

    Test areas:
    - Extension metadata (name, version, description, dependencies)
    - Abstract GGUF provider class structure
    - Provider inheritance and linkage
    - GGUF-specific functionality
    - Hardware optimization for GGUF models
    """

    def test_extension_metadata(self):
        """Test GGUF extension metadata."""
        assert EXT_Local_AI_GGUF.name == "local_ai_gguf"
        assert EXT_Local_AI_GGUF.friendly_name == "GGUF Model Support"
        assert EXT_Local_AI_GGUF.version == "1.0.0"
        assert "gguf" in EXT_Local_AI_GGUF.description.lower()
        assert "quantized" in EXT_Local_AI_GGUF.description.lower()

    def test_extension_dependencies(self):
        """Test GGUF extension dependencies."""
        assert hasattr(EXT_Local_AI_GGUF, "dependencies")
        assert isinstance(EXT_Local_AI_GGUF.dependencies, list)
        assert "local_ai" in EXT_Local_AI_GGUF.dependencies

    def test_extension_class_structure(self):
        """Test GGUF extension class structure."""
        # Test that EXT_Local_AI_GGUF is properly defined
        assert hasattr(EXT_Local_AI_GGUF, "name")
        assert hasattr(EXT_Local_AI_GGUF, "friendly_name")
        assert hasattr(EXT_Local_AI_GGUF, "version")
        assert hasattr(EXT_Local_AI_GGUF, "description")
        assert hasattr(EXT_Local_AI_GGUF, "dependencies")

        # Test inheritance
        from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension

        assert issubclass(EXT_Local_AI_GGUF, AbstractStaticExtension)

    def test_concrete_provider_metadata(self):
        """Test concrete GGUF provider metadata."""
        assert ConcreteGGUFProvider.name == "test_gguf"
        assert ConcreteGGUFProvider.version == "1.0.0"
        assert ConcreteGGUFProvider.description == "Test GGUF provider"

    def test_concrete_provider_methods_implementation(self):
        """Test that concrete provider implements required GGUF methods."""
        # Test that all expected GGUF methods exist and are callable
        gguf_methods = [
            "load_model",
            "unload_model",
            "generate_text",
            "generate_embeddings",
            "get_model_info",
            "optimize_gpu_settings",
            "optimize_cpu_settings",
            "can_handle_model",
            "get_quantization_info",
            "estimate_memory_usage",
            "validate_gguf_file",
            "get_abilities",
        ]

        for method_name in gguf_methods:
            assert hasattr(ConcreteGGUFProvider, method_name)
            assert callable(getattr(ConcreteGGUFProvider, method_name))

    def test_concrete_provider_gguf_model_loading(self):
        """Test concrete provider GGUF model loading functionality."""
        # Test load_model with GGUF-specific parameters
        result = ConcreteGGUFProvider.load_model(
            model_path="/path/to/model.gguf",
            n_gpu_layers=32,
            context_size=4096,
        )
        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["format"] == "gguf"
        assert result["n_gpu_layers"] == 32
        assert result["context_size"] == 4096

        # Test unload_model
        result = ConcreteGGUFProvider.unload_model("gguf_model_123")
        assert isinstance(result, dict)
        assert result["success"] is True
        assert "memory_freed_mb" in result

    def test_concrete_provider_gguf_text_generation(self):
        """Test concrete provider GGUF text generation functionality."""
        # Test generate_text with GGUF-specific parameters
        result = ConcreteGGUFProvider.generate_text(
            prompt="What are GGUF models?",
            max_tokens=256,
            temperature=0.7,
            top_p=0.9,
            top_k=40,
            repeat_penalty=1.1,
        )
        assert isinstance(result, dict)
        assert result["success"] is True
        assert "text" in result
        assert result["model_format"] == "gguf"
        assert result["quantization"] == "Q4_K_M"
        assert result["repeat_penalty"] == 1.1

    def test_concrete_provider_gguf_embedding_generation(self):
        """Test concrete provider GGUF embedding generation functionality."""
        # Test generate_embeddings
        result = ConcreteGGUFProvider.generate_embeddings(
            text="GGUF models are quantized for efficiency",
        )
        assert isinstance(result, dict)
        assert result["success"] is True
        assert "embeddings" in result
        assert isinstance(result["embeddings"], list)
        assert result["dimensions"] == 512
        assert result["model_format"] == "gguf"

    def test_concrete_provider_gguf_model_info(self):
        """Test concrete provider GGUF model info functionality."""
        # Test get_model_info
        result = ConcreteGGUFProvider.get_model_info("/path/to/model.gguf")
        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["format"] == "gguf"
        assert "quantization" in result
        assert "tensor_count" in result
        assert "architecture" in result

        # Test get_quantization_info
        result = ConcreteGGUFProvider.get_quantization_info("/path/to/model.gguf")
        assert isinstance(result, dict)
        assert result["success"] is True
        assert "quantization" in result
        assert "memory_reduction" in result
        assert "quality_level" in result

    def test_concrete_provider_gguf_hardware_optimization(self):
        """Test concrete provider GGUF hardware optimization functionality."""
        config = {"model_path": "/path/to/model.gguf", "device": "auto"}

        # Test optimize_gpu_settings
        result = ConcreteGGUFProvider.optimize_gpu_settings(config)
        assert isinstance(result, dict)
        assert result["success"] is True
        assert "optimized_config" in result
        assert result["optimized_config"]["n_gpu_layers"] == 32
        assert "gpu_optimizations" in result

        # Test optimize_cpu_settings
        result = ConcreteGGUFProvider.optimize_cpu_settings(config)
        assert isinstance(result, dict)
        assert result["success"] is True
        assert "optimized_config" in result
        assert result["optimized_config"]["use_mlock"] is True
        assert "cpu_optimizations" in result

    def test_concrete_provider_gguf_memory_estimation(self):
        """Test concrete provider GGUF memory estimation functionality."""
        # Test estimate_memory_usage
        result = ConcreteGGUFProvider.estimate_memory_usage(
            "/path/to/model.gguf", n_gpu_layers=32
        )
        assert isinstance(result, dict)
        assert result["success"] is True
        assert "total_memory_mb" in result
        assert "gpu_memory_mb" in result
        assert "cpu_memory_mb" in result
        assert "context_memory_mb" in result

    def test_concrete_provider_gguf_file_validation(self):
        """Test concrete provider GGUF file validation functionality."""
        # Test validate_gguf_file
        result = ConcreteGGUFProvider.validate_gguf_file("/path/to/model.gguf")
        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["valid"] is True
        assert result["format"] == "gguf"
        assert result["magic"] == "GGUF"

        # Test can_handle_model
        assert ConcreteGGUFProvider.can_handle_model("model.gguf") is True
        assert ConcreteGGUFProvider.can_handle_model("model.ggml") is True
        assert ConcreteGGUFProvider.can_handle_model("model.bin") is False
        assert ConcreteGGUFProvider.can_handle_model("model.safetensors") is False

    def test_concrete_provider_gguf_abilities(self):
        """Test concrete provider GGUF-specific abilities."""
        abilities = ConcreteGGUFProvider.get_abilities()
        assert isinstance(abilities, set)

        # GGUF-specific abilities
        expected_gguf_abilities = {
            "quantized_inference",
            "cpu_optimization",
            "gpu_acceleration",
            "memory_mapping",
            "context_extension",
        }

        for ability in expected_gguf_abilities:
            assert ability in abilities, f"Missing GGUF ability: {ability}"

        # General AI abilities
        assert "text_generation" in abilities
        assert "embedding_generation" in abilities

    def test_concrete_provider_utility_methods(self):
        """Test concrete provider utility methods."""
        # Test services
        services = ConcreteGGUFProvider.services()
        assert isinstance(services, list)
        assert "quantized_inference" in services
        assert "cpu_optimization" in services

        # Test has_ability
        assert ConcreteGGUFProvider.has_ability("quantized_inference") is True
        assert ConcreteGGUFProvider.has_ability("memory_mapping") is True
        assert ConcreteGGUFProvider.has_ability("nonexistent_ability") is False

    def test_concrete_provider_dependencies_structure(self):
        """Test concrete provider dependencies structure."""
        assert hasattr(ConcreteGGUFProvider, "dependencies")
        assert isinstance(ConcreteGGUFProvider.dependencies, Dependencies)

        # Test that it has pip dependencies
        pip_deps = ConcreteGGUFProvider.dependencies.pip
        assert len(pip_deps) >= 1
        assert any(dep.name == "llama-cpp-python" for dep in pip_deps)

    def test_concrete_provider_bond_instance_method(self):
        """Test concrete provider bond_instance method."""
        mock_instance = MagicMock()
        result = ConcreteGGUFProvider.bond_instance(mock_instance)
        assert result is not None

    def test_provider_discovery(self):
        """Test provider discovery functionality."""
        providers = EXT_Local_AI_GGUF.providers()
        assert isinstance(providers, list), "Providers should be a list"
        # Providers list may be empty in test environment, which is acceptable

    def test_gguf_quantization_levels(self):
        """Test GGUF quantization level handling."""
        # Test different quantization detection
        result = ConcreteGGUFProvider.get_quantization_info("model.Q4_K_M.gguf")
        assert result["quantization"] == "Q4_K_M"
        assert "bits_per_weight" in result
        assert "memory_reduction" in result

    def test_gguf_context_extension_capability(self):
        """Test GGUF context extension capability."""
        abilities = ConcreteGGUFProvider.get_abilities()
        assert "context_extension" in abilities

        # Test loading with extended context
        result = ConcreteGGUFProvider.load_model(
            model_path="/path/to/model.gguf",
            context_size=8192,  # Extended context
        )
        assert result["success"] is True
        assert result["context_size"] == 8192

    def test_gguf_gpu_layer_distribution(self):
        """Test GGUF GPU layer distribution optimization."""
        config = {"model_path": "/path/to/model.gguf"}

        result = ConcreteGGUFProvider.optimize_gpu_settings(config)
        optimized = result["optimized_config"]

        # Should optimize GPU layer distribution
        assert "n_gpu_layers" in optimized
        assert "main_gpu" in optimized
        assert "estimated_vram_usage_mb" in result

    def test_gguf_memory_mapping_optimization(self):
        """Test GGUF memory mapping optimization."""
        config = {"model_path": "/path/to/model.gguf"}

        result = ConcreteGGUFProvider.optimize_cpu_settings(config)
        optimized = result["optimized_config"]

        # Should enable memory optimizations
        assert optimized["use_mmap"] is True
        assert optimized["use_mlock"] is True
        assert "memory_mapping" in result["cpu_optimizations"]

    def test_gguf_streaming_generation_support(self):
        """Test GGUF streaming generation support."""
        result = ConcreteGGUFProvider.generate_text(
            prompt="Stream this response",
            stream=True,
        )
        assert result["success"] is True
        assert result["stream"] is True


@pytest.mark.local_ai
@pytest.mark.gguf
@pytest.mark.integration
class TestGGUFIntegration:
    """Integration tests for GGUF extension with real API endpoints."""

    def test_full_model_lifecycle_with_haiku_generation(self):
        """
        Full integration test: delete model if exists, download from HuggingFace,
        mount, generate haiku, dismount.
        """
        import requests
        import time
        from typing import Dict, Any

        # Test configuration
        BASE_URL = "http://localhost:8000"
        MODEL_ID = "bartowski/Llama-3.2-1B-Instruct-GGUF"
        QUANTIZATION = "Q4_K_M"
        TEST_TIMEOUT = 300  # 5 minutes for download

        # Helper function for API calls
        def api_call(
            method: str, endpoint: str, json_data: Dict[str, Any] = None
        ) -> Dict[str, Any]:
            """Make API call with error handling."""
            url = f"{BASE_URL}{endpoint}"
            try:
                if method.upper() == "GET":
                    response = requests.get(url, timeout=30)
                elif method.upper() == "POST":
                    response = requests.post(url, json=json_data, timeout=30)
                elif method.upper() == "DELETE":
                    response = requests.delete(url, timeout=30)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                pytest.fail(f"API call failed: {method} {endpoint} - {str(e)}")

        # Step 1: Check if model already exists and delete if necessary
        print("Step 1: Checking for existing model...")
        try:
            # Discover existing models
            discover_result = api_call("GET", "/v1/ai/local/models/discover")
            existing_models = discover_result.get("models", [])

            # Find and delete existing model if it exists
            for model in existing_models:
                if MODEL_ID in model.get("model_id", "") and QUANTIZATION in model.get(
                    "quantization", ""
                ):
                    print(
                        f"Found existing model: {model.get('model_id')} - Deleting..."
                    )
                    # Unmount first if mounted
                    if model.get("status") == "mounted":
                        api_call(
                            "POST",
                            "/v1/ai/local/models/unmount",
                            {"model_id": model.get("model_id")},
                        )

                    # Delete model configuration if it exists
                    configs_result = api_call("GET", "/v1/gguf/configs")
                    for config in configs_result.get("configs", []):
                        if MODEL_ID in config.get("model_name", ""):
                            api_call("DELETE", f"/v1/gguf/configs/{config['id']}")
                            print(f"Deleted model configuration: {config['id']}")

            print("Step 1 completed: Existing model cleanup done")
        except Exception as e:
            print(f"Step 1 warning: Could not clean up existing models: {e}")

        # Step 2: Download model from HuggingFace
        print("Step 2: Downloading model from HuggingFace...")
        download_result = api_call(
            "POST",
            "/v1/gguf/configs/download-model",
            {"model_id": MODEL_ID, "quantization": QUANTIZATION},
        )

        assert download_result["success"] is True
        download_id = download_result["download_id"]
        print(f"Download queued with ID: {download_id}")

        # Wait for download to complete
        download_complete = False
        start_time = time.time()
        while not download_complete and (time.time() - start_time) < TEST_TIMEOUT:
            time.sleep(10)  # Check every 10 seconds
            status_result = api_call(
                "GET", f"/v1/ai/local/models/download-status/{download_id}"
            )
            status = status_result.get("status", "unknown")
            print(f"Download status: {status}")

            if status == "completed":
                download_complete = True
                break
            elif status == "failed":
                pytest.fail(
                    f"Model download failed: {status_result.get('error', 'Unknown error')}"
                )

        if not download_complete:
            pytest.fail(f"Model download timed out after {TEST_TIMEOUT} seconds")

        print("Step 2 completed: Model download successful")

        # Step 3: Create GGUF configuration and mount the model
        print("Step 3: Creating GGUF configuration and mounting model...")

        # Create GGUF configuration
        config_result = api_call(
            "POST",
            "/v1/gguf/configs",
            {
                "model_name": f"{MODEL_ID}-{QUANTIZATION}",
                "quantization_type": QUANTIZATION,
                "context_window": 2048,
                "n_gpu_layers": -1,
                "temperature": 0.7,
                "max_tokens": 100,
            },
        )

        assert config_result["success"] is True
        config_id = config_result["config"]["id"]
        print(f"Created GGUF configuration: {config_id}")

        # Load/mount the model
        mount_result = api_call("POST", f"/v1/gguf/configs/{config_id}/load-model")
        assert mount_result["success"] is True
        print("Step 3 completed: Model mounted successfully")

        # Step 4: Generate a haiku
        print("Step 4: Generating haiku...")

        haiku_prompt = """Write a haiku about artificial intelligence. Follow the traditional 5-7-5 syllable pattern. Just return the haiku, nothing else."""

        generation_result = api_call(
            "POST",
            f"/v1/gguf/configs/{config_id}/generate-text",
            {
                "prompt": haiku_prompt,
                "max_tokens": 50,
                "temperature": 0.8,
                "stop_sequences": ["\n\n", "---"],
            },
        )

        assert generation_result["success"] is True
        generated_text = generation_result["generated_text"]
        print(f"Generated haiku:\n{generated_text}")

        # Validate haiku was generated (basic check)
        assert len(generated_text.strip()) > 10, "Generated text too short"
        assert "\n" in generated_text, "Haiku should have multiple lines"

        print("Step 4 completed: Haiku generation successful")

        # Step 5: Dismount/unmount the model
        print("Step 5: Dismounting model...")

        # Find the mounted model instance
        models_status = api_call("GET", "/v1/ai/local/models/discover")
        mounted_model = None
        for model in models_status.get("models", []):
            if (
                MODEL_ID in model.get("model_id", "")
                and model.get("status") == "mounted"
            ):
                mounted_model = model
                break

        if mounted_model:
            unmount_result = api_call(
                "POST",
                "/v1/ai/local/models/unmount",
                {"model_id": mounted_model["model_id"]},
            )
            assert unmount_result["success"] is True
            print("Model unmounted successfully")

        # Clean up configuration
        api_call("DELETE", f"/v1/gguf/configs/{config_id}")
        print("Configuration cleaned up")

        print("Step 5 completed: Model dismounted and cleaned up")

        # Final verification
        print("Integration test completed successfully!")
        print(
            f"Successfully downloaded {MODEL_ID}, mounted it, generated a haiku, and cleaned up."
        )

        # Return the generated haiku for verification
        return generated_text
