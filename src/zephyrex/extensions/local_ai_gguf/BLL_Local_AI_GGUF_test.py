# """
# Test suite for Local AI GGUF Business Logic Layer.
# Tests GGUF model configuration manager and related functionality.
# """

# import pytest
# from unittest.mock import MagicMock, patch

# from extensions.local_ai_gguf.BLL_Local_AI_GGUF import (
#     GGUFModelConfigurationManager,
#     GGUFModelConfigurationModel,
#     GGUFQuantizationType,
#     GGUFContextType,
#     GGUFCacheQuantType,
# )
# from AbstractTest import CategoryOfTest, ClassOfTestsConfig
# from logic.AbstractBLLTest import AbstractBLLTest
# from logic.AbstractLogicManager import AbstractBLLManager


# @pytest.mark.local_ai
# @pytest.mark.gguf
# @pytest.mark.bll
# class TestGGUFModelConfigurationManager(AbstractBLLTest):
#     """Test GGUF model configuration manager functionality."""

#     class_under_test = GGUFModelConfigurationManager
#     test_config = ClassOfTestsConfig(
#         categories=[CategoryOfTest.LOGIC]
#     )

#     # Required fields for AbstractBLLTest
#     create_fields = {
#         "model_name": "test.gguf",
#         "quantization_type": GGUFQuantizationType.Q4_K_M,
#         "context_window": 4096,
#         "context_type": GGUFContextType.STANDARD,
#     }

#     update_fields = {
#         "temperature": 0.8,
#         "max_tokens": 1024,
#     }

#     @pytest.fixture
#     def mock_model_registry(self):
#         """Mock model registry for testing."""
#         registry = MagicMock()
#         registry.get_manager.return_value = MagicMock()
#         return registry

#     @pytest.fixture
#     def gguf_config_manager(self, mock_model_registry):
#         """Create GGUF configuration manager instance for testing."""
#         return GGUFModelConfigurationManager(
#             requester_id="test-user-123",
#             target_id="test-target-123",
#             model_registry=mock_model_registry
#         )

#     @pytest.fixture
#     def sample_gguf_config(self):
#         """Sample GGUF configuration for testing."""
#         return {
#             "model_name": "llama-2-7b-chat.Q4_K_M.gguf",
#             "quantization_type": GGUFQuantizationType.Q4_K_M,
#             "context_window": 4096,
#             "context_type": GGUFContextType.STANDARD,
#             "n_gpu_layers": -1,
#             "main_gpu": 0,
#             "tensor_split": None,
#             "use_mmap": True,
#             "use_mlock": False,
#             "kv_cache_quant_type": GGUFCacheQuantType.F16,
#             "cache_offload": False,
#             "compress_cache": False,
#             "max_tokens": 512,
#             "temperature": 0.7,
#             "top_p": 0.9,
#             "top_k": 40,
#             "repeat_penalty": 1.1,
#             "stop_sequences": [],
#             "use_beam_search": False,
#             "beam_count": 4,
#             "rope_scaling_type": None,
#             "rope_scaling_factor": None,
#             "n_threads": None,
#             "n_batch": 512,
#         }

#     def test_manager_initialization(self, gguf_config_manager):
#         """Test GGUF configuration manager initialization."""
#         assert isinstance(gguf_config_manager, AbstractLogicManager)
#         assert gguf_config_manager.requester.id == "test-user-123"
#         assert gguf_config_manager.target_id == "test-target-123"
#         assert gguf_config_manager.Model == GGUFModelConfigurationModel

#     @patch('extensions.local_ai_gguf.BLL_Local_AI_GGUF.DEPS_AVAILABLE', True)
#     @patch('extensions.local_ai_gguf.BLL_Local_AI_GGUF.psutil.cpu_count')
#     def test_create_validation_success(self, mock_cpu_count, gguf_config_manager, sample_gguf_config):
#         """Test successful creation validation."""
#         mock_cpu_count.return_value = 8

#         # Create entity from sample config
#         entity = GGUFModelConfigurationModel.Create(**sample_gguf_config)

#         # Should not raise exception and auto-set threads
#         gguf_config_manager.create_validation(entity)
#         assert entity.n_threads == 8  # min(8, 8)

#     @patch('extensions.local_ai_gguf.BLL_Local_AI_GGUF.DEPS_AVAILABLE', False)
#     def test_create_validation_deps_unavailable(self, gguf_config_manager, sample_gguf_config):
#         """Test creation validation when dependencies are unavailable."""
#         entity = GGUFModelConfigurationModel.Create(**sample_gguf_config)

#         with pytest.raises(Exception) as exc_info:
#             gguf_config_manager.create_validation(entity)
#         assert "dependencies not available" in str(exc_info.value).lower()

#     @patch('extensions.local_ai_gguf.BLL_Local_AI_GGUF.DEPS_AVAILABLE', True)
#     def test_create_validation_model_name_warning(self, gguf_config_manager, sample_gguf_config):
#         """Test model name validation warnings."""
#         sample_gguf_config["model_name"] = "model.bin"  # Not .gguf
#         entity = GGUFModelConfigurationModel.Create(**sample_gguf_config)

#         # Should not raise exception but may log warning
#         gguf_config_manager.create_validation(entity)

#     @patch('extensions.local_ai_gguf.BLL_Local_AI_GGUF.DEPS_AVAILABLE', True)
#     def test_create_validation_large_context_warning(self, gguf_config_manager, sample_gguf_config):
#         """Test large context window validation warnings."""
#         sample_gguf_config["context_window"] = 32768
#         sample_gguf_config["quantization_type"] = GGUFQuantizationType.Q4_K_M
#         entity = GGUFModelConfigurationModel.Create(**sample_gguf_config)

#         # Should not raise exception but may log warning
#         gguf_config_manager.create_validation(entity)

#     @patch('extensions.local_ai.EXT_Local_AI.EXT_Local_AI.hardware_detection')
#     def test_get_hardware_optimized_config_cuda(self, mock_hardware_detection, gguf_config_manager):
#         """Test hardware optimization for CUDA."""
#         # Mock hardware detection for CUDA
#         mock_hardware_detection.return_value = {
#             "has_cuda": True,
#             "has_mps": False,
#             "available_vram_gb": 12.0,
#             "gpu_count": 1,
#             "cpu_count": 8
#         }

#         # Mock get method to return a config
#         mock_config = MagicMock()
#         mock_config.model_name = "test.gguf"
#         mock_config.quantization_type = GGUFQuantizationType.Q4_K_M
#         mock_config.context_window = 4096
#         mock_config.context_type = GGUFContextType.STANDARD

#         gguf_config_manager.get = MagicMock(return_value=mock_config)

#         result = gguf_config_manager.get_hardware_optimized_config("test-config-id")

#         assert result["n_gpu_layers"] == -1
#         assert result["main_gpu"] == 0
#         assert result["use_mmap"] is True
#         assert result["use_mlock"] is False

#     @patch('extensions.local_ai.EXT_Local_AI.EXT_Local_AI.hardware_detection')
#     def test_get_hardware_optimized_config_multi_gpu(self, mock_hardware_detection, gguf_config_manager):
#         """Test hardware optimization for multi-GPU."""
#         # Mock hardware detection for multi-GPU
#         mock_hardware_detection.return_value = {
#             "has_cuda": True,
#             "has_mps": False,
#             "available_vram_gb": 24.0,
#             "gpu_count": 2,
#             "cpu_count": 16
#         }

#         mock_config = MagicMock()
#         mock_config.model_name = "test.gguf"
#         mock_config.quantization_type = GGUFQuantizationType.Q4_K_M
#         mock_config.context_window = 4096
#         mock_config.context_type = GGUFContextType.STANDARD

#         gguf_config_manager.get = MagicMock(return_value=mock_config)

#         result = gguf_config_manager.get_hardware_optimized_config("test-config-id")

#         assert result["tensor_split"] == [0.5, 0.5]

#     @patch('extensions.local_ai.EXT_Local_AI.EXT_Local_AI.hardware_detection')
#     def test_get_hardware_optimized_config_low_vram(self, mock_hardware_detection, gguf_config_manager):
#         """Test hardware optimization for low VRAM."""
#         # Mock hardware detection for low VRAM CUDA
#         mock_hardware_detection.return_value = {
#             "has_cuda": True,
#             "has_mps": False,
#             "available_vram_gb": 4.0,
#             "gpu_count": 1,
#             "cpu_count": 8
#         }

#         mock_config = MagicMock()
#         mock_config.model_name = "test.gguf"
#         mock_config.quantization_type = GGUFQuantizationType.Q4_K_M
#         mock_config.context_window = 4096
#         mock_config.context_type = GGUFContextType.STANDARD

#         gguf_config_manager.get = MagicMock(return_value=mock_config)

#         result = gguf_config_manager.get_hardware_optimized_config("test-config-id")

#         assert result["cache_offload"] is True
#         assert result["compress_cache"] is True
#         assert result["kv_cache_quant_type"] == GGUFCacheQuantType.Q4_0

#     @patch('extensions.local_ai.EXT_Local_AI.EXT_Local_AI.hardware_detection')
#     def test_get_hardware_optimized_config_mps(self, mock_hardware_detection, gguf_config_manager):
#         """Test hardware optimization for MPS."""
#         # Mock hardware detection for MPS
#         mock_hardware_detection.return_value = {
#             "has_cuda": False,
#             "has_mps": True,
#             "available_vram_gb": 8.0,
#             "gpu_count": 0,
#             "cpu_count": 8
#         }

#         mock_config = MagicMock()
#         mock_config.model_name = "test.gguf"
#         mock_config.quantization_type = GGUFQuantizationType.Q4_K_M
#         mock_config.context_window = 4096
#         mock_config.context_type = GGUFContextType.STANDARD

#         gguf_config_manager.get = MagicMock(return_value=mock_config)

#         result = gguf_config_manager.get_hardware_optimized_config("test-config-id")

#         assert result["n_gpu_layers"] == 1
#         assert result["use_mps"] is True
#         assert result["kv_cache_quant_type"] == GGUFCacheQuantType.F16

#     @patch('extensions.local_ai.EXT_Local_AI.EXT_Local_AI.hardware_detection')
#     def test_get_hardware_optimized_config_cpu(self, mock_hardware_detection, gguf_config_manager):
#         """Test hardware optimization for CPU."""
#         # Mock hardware detection for CPU only
#         mock_hardware_detection.return_value = {
#             "has_cuda": False,
#             "has_mps": False,
#             "available_vram_gb": 0.0,
#             "gpu_count": 0,
#             "cpu_count": 8
#         }

#         mock_config = MagicMock()
#         mock_config.model_name = "test.gguf"
#         mock_config.quantization_type = GGUFQuantizationType.Q4_K_M
#         mock_config.context_window = 4096
#         mock_config.context_type = GGUFContextType.STANDARD

#         gguf_config_manager.get = MagicMock(return_value=mock_config)

#         result = gguf_config_manager.get_hardware_optimized_config("test-config-id")

#         assert result["n_gpu_layers"] == 0
#         assert result["kv_cache_quant_type"] == GGUFCacheQuantType.Q8_0
#         assert result["n_threads"] == 8

#     @patch('extensions.local_ai_gguf.BLL_Local_AI_GGUF.DEPS_AVAILABLE', False)
#     def test_validate_model_compatibility_no_deps(self, gguf_config_manager):
#         """Test model compatibility check when dependencies unavailable."""
#         result = gguf_config_manager.validate_model_compatibility("test-config-id")

#         assert result["compatible"] is False
#         assert "dependencies not available" in result["reason"].lower()
#         assert "Install llama-cpp-python" in result["recommendations"]

#     @patch('extensions.local_ai_gguf.BLL_Local_AI_GGUF.DEPS_AVAILABLE', True)
#     @patch('extensions.local_ai.EXT_Local_AI.EXT_Local_AI.hardware_detection')
#     def test_validate_model_compatibility_no_gpu_available(self, mock_hardware_detection, gguf_config_manager):
#         """Test compatibility check when GPU layers requested but no GPU available."""
#         mock_hardware_detection.return_value = {
#             "has_cuda": False,
#             "has_mps": False,
#             "available_vram_gb": 0.0,
#             "gpu_count": 0,
#             "available_ram_gb": 16.0
#         }

#         mock_config = MagicMock()
#         mock_config.n_gpu_layers = 10
#         mock_config.tensor_split = None

#         gguf_config_manager.get = MagicMock(return_value=mock_config)
#         gguf_config_manager._estimate_vram_usage = MagicMock(return_value=2.0)
#         gguf_config_manager._estimate_ram_usage = MagicMock(return_value=8.0)

#         result = gguf_config_manager.validate_model_compatibility("test-config-id")

#         assert result["compatible"] is False
#         assert "GPU layers requested but no GPU available" in result["issues"]
#         assert "Set n_gpu_layers to 0 for CPU-only execution" in result["recommendations"]

#     @patch('extensions.local_ai_gguf.BLL_Local_AI_GGUF.DEPS_AVAILABLE', True)
#     @patch('extensions.local_ai.EXT_Local_AI.EXT_Local_AI.hardware_detection')
#     def test_validate_model_compatibility_insufficient_vram(self, mock_hardware_detection, gguf_config_manager):
#         """Test compatibility check for insufficient VRAM."""
#         mock_hardware_detection.return_value = {
#             "has_cuda": True,
#             "has_mps": False,
#             "available_vram_gb": 2.0,
#             "gpu_count": 1,
#             "available_ram_gb": 16.0
#         }

#         mock_config = MagicMock()
#         mock_config.n_gpu_layers = 10
#         mock_config.tensor_split = None

#         gguf_config_manager.get = MagicMock(return_value=mock_config)
#         gguf_config_manager._estimate_vram_usage = MagicMock(return_value=8.0)
#         gguf_config_manager._estimate_ram_usage = MagicMock(return_value=4.0)

#         result = gguf_config_manager.validate_model_compatibility("test-config-id")

#         assert result["compatible"] is False
#         assert "Estimated VRAM usage (8.0GB) exceeds available (2.0GB)" in result["issues"]
#         assert "Enable cache offload or reduce context window" in result["recommendations"]

#     @patch('extensions.local_ai_gguf.BLL_Local_AI_GGUF.DEPS_AVAILABLE', True)
#     @patch('extensions.local_ai.EXT_Local_AI.EXT_Local_AI.hardware_detection')
#     def test_validate_model_compatibility_insufficient_ram(self, mock_hardware_detection, gguf_config_manager):
#         """Test compatibility check for insufficient RAM."""
#         mock_hardware_detection.return_value = {
#             "has_cuda": False,
#             "has_mps": False,
#             "available_vram_gb": 0.0,
#             "gpu_count": 0,
#             "available_ram_gb": 4.0
#         }

#         mock_config = MagicMock()
#         mock_config.n_gpu_layers = 0
#         mock_config.tensor_split = None

#         gguf_config_manager.get = MagicMock(return_value=mock_config)
#         gguf_config_manager._estimate_vram_usage = MagicMock(return_value=0.0)
#         gguf_config_manager._estimate_ram_usage = MagicMock(return_value=8.0)

#         result = gguf_config_manager.validate_model_compatibility("test-config-id")

#         assert result["compatible"] is False
#         assert "Estimated RAM usage (8.0GB) exceeds available (4.0GB)" in result["issues"]
#         assert "Use smaller quantization or reduce context window" in result["recommendations"]

#     @patch('extensions.local_ai_gguf.BLL_Local_AI_GGUF.DEPS_AVAILABLE', True)
#     @patch('extensions.local_ai.EXT_Local_AI.EXT_Local_AI.hardware_detection')
#     def test_validate_model_compatibility_tensor_split_mismatch(self, mock_hardware_detection, gguf_config_manager):
#         """Test compatibility check for tensor split GPU count mismatch."""
#         mock_hardware_detection.return_value = {
#             "has_cuda": True,
#             "has_mps": False,
#             "available_vram_gb": 16.0,
#             "gpu_count": 2,
#             "available_ram_gb": 32.0
#         }

#         mock_config = MagicMock()
#         mock_config.n_gpu_layers = 10
#         mock_config.tensor_split = [0.5, 0.3, 0.2]  # 3 splits for 2 GPUs

#         gguf_config_manager.get = MagicMock(return_value=mock_config)
#         gguf_config_manager._estimate_vram_usage = MagicMock(return_value=4.0)
#         gguf_config_manager._estimate_ram_usage = MagicMock(return_value=2.0)

#         result = gguf_config_manager.validate_model_compatibility("test-config-id")

#         assert result["compatible"] is False
#         assert "Tensor split length doesn't match GPU count" in result["issues"]
#         assert "Adjust tensor split for 2 GPUs" in result["recommendations"]

#     def test_estimate_vram_usage(self, gguf_config_manager):
#         """Test VRAM usage estimation."""
#         mock_config = MagicMock()
#         mock_config.quantization_type = GGUFQuantizationType.Q4_K_M
#         mock_config.context_window = 4096

#         vram_usage = gguf_config_manager._estimate_vram_usage(mock_config)
#         assert isinstance(vram_usage, float)
#         assert vram_usage > 0

#     def test_estimate_ram_usage(self, gguf_config_manager):
#         """Test RAM usage estimation."""
#         mock_config = MagicMock()
#         mock_config.n_gpu_layers = 0  # CPU only
#         mock_config.quantization_type = GGUFQuantizationType.Q4_K_M
#         mock_config.context_window = 4096

#         gguf_config_manager._estimate_vram_usage = MagicMock(return_value=4.0)

#         ram_usage = gguf_config_manager._estimate_ram_usage(mock_config)
#         assert isinstance(ram_usage, float)
#         assert ram_usage > 4.0  # Should include model size + overhead

#         # Test with GPU
#         mock_config.n_gpu_layers = 10
#         ram_usage_gpu = gguf_config_manager._estimate_ram_usage(mock_config)
#         assert ram_usage_gpu < ram_usage  # Should be less for GPU

#     @patch('extensions.local_ai_gguf.BLL_Local_AI_GGUF.DEPS_AVAILABLE', True)
#     def test_load_model_instance_success(self, gguf_config_manager):
#         """Test successful model instance loading."""
#         mock_config = MagicMock()
#         mock_config.model_name = "test.gguf"
#         mock_config.context_window = 4096
#         mock_config.n_gpu_layers = -1
#         mock_config.main_gpu = 0
#         mock_config.use_mmap = True
#         mock_config.use_mlock = False
#         mock_config.n_threads = 8
#         mock_config.n_batch = 512
#         mock_config.tensor_split = None
#         mock_config.rope_scaling_type = None
#         mock_config.rope_scaling_factor = None

#         gguf_config_manager.get = MagicMock(return_value=mock_config)
#         gguf_config_manager.validate_model_compatibility = MagicMock(return_value={"compatible": True})

#         result = gguf_config_manager.load_model_instance("test-config-id")

#         assert result["success"] is True
#         assert result["config_id"] == "test-config-id"
#         assert "llama_config" in result

#     @patch('extensions.local_ai_gguf.BLL_Local_AI_GGUF.DEPS_AVAILABLE', False)
#     def test_load_model_instance_no_deps(self, gguf_config_manager):
#         """Test model instance loading when dependencies unavailable."""
#         with pytest.raises(Exception) as exc_info:
#             gguf_config_manager.load_model_instance("test-config-id")
#         assert "dependencies not available" in str(exc_info.value).lower()

#     def test_load_model_instance_incompatible_config(self, gguf_config_manager):
#         """Test model instance loading with incompatible configuration."""
#         mock_config = MagicMock()
#         gguf_config_manager.get = MagicMock(return_value=mock_config)
#         gguf_config_manager.validate_model_compatibility = MagicMock(return_value={
#             "compatible": False,
#             "issues": ["Test incompatibility"]
#         })

#         with patch('extensions.local_ai_gguf.BLL_Local_AI_GGUF.DEPS_AVAILABLE', True):
#             with pytest.raises(Exception) as exc_info:
#                 gguf_config_manager.load_model_instance("test-config-id")
#             assert "not compatible" in str(exc_info.value).lower()

#     def test_model_configuration_model_structure(self):
#         """Test GGUF model configuration model structure."""
#         # Test Create model with rope scaling validation
#         create_data = {
#             "model_name": "test.gguf",
#             "quantization_type": GGUFQuantizationType.Q4_K_M,
#             "context_window": 4096,
#             "rope_scaling_type": "linear",
#             "rope_scaling_factor": 1.5,
#         }

#         create_model = GGUFModelConfigurationModel.Create(**create_data)
#         assert create_model.model_name == "test.gguf"
#         assert create_model.quantization_type == GGUFQuantizationType.Q4_K_M
#         assert create_model.rope_scaling_type == "linear"
#         assert create_model.rope_scaling_factor == 1.5

#         # Test validation error for rope scaling
#         with pytest.raises(ValueError) as exc_info:
#             GGUFModelConfigurationModel.Create(
#                 model_name="test.gguf",
#                 quantization_type=GGUFQuantizationType.Q4_K_M,
#                 rope_scaling_type="linear",
#                 # Missing rope_scaling_factor
#             )
#         assert "scaling factor required" in str(exc_info.value).lower()

#         # Test Update model
#         update_data = {
#             "temperature": 0.8,
#             "max_tokens": 1024,
#         }

#         update_model = GGUFModelConfigurationModel.Update(**update_data)
#         assert update_model.temperature == 0.8
#         assert update_model.max_tokens == 1024

#         # Test Search model
#         search_model = GGUFModelConfigurationModel.Search()
#         assert search_model is not None

#     def test_gguf_enums(self):
#         """Test GGUF-specific enums."""
#         # Test GGUFQuantizationType
#         assert GGUFQuantizationType.Q4_K_M == "Q4_K_M"
#         assert GGUFQuantizationType.Q4_K_S == "Q4_K_S"
#         assert GGUFQuantizationType.F16 == "F16"

#         # Test GGUFContextType
#         assert GGUFContextType.STANDARD == "standard"
#         assert GGUFContextType.EXTENDED == "extended"
#         assert GGUFContextType.MAXIMUM == "maximum"

#         # Test GGUFCacheQuantType
#         assert GGUFCacheQuantType.Q4_0 == "q4_0"
#         assert GGUFCacheQuantType.Q8_0 == "q8_0"
#         assert GGUFCacheQuantType.F16 == "f16"
#         assert GGUFCacheQuantType.F32 == "f32"
