# """
# Test suite for Local AI PyTorch Business Logic Layer.
# Tests PyTorch model configuration manager and related functionality.
# """

# import pytest
# from unittest.mock import MagicMock, patch

# from extensions.local_ai_torch.BLL_Local_AI_Torch import (
#     PyTorchModelConfigurationManager,
#     PyTorchModelConfigurationModel,
#     PyTorchModelType,
#     PyTorchDeviceType,
#     PyTorchQuantizationType,
# )
# from AbstractTest import CategoryOfTest, ClassOfTestsConfig
# from logic.AbstractBLLTest import AbstractBLLTest
# from logic.AbstractLogicManager import AbstractBLLManager


# @pytest.mark.local_ai
# @pytest.mark.pytorch
# @pytest.mark.bll
# class TestPyTorchModelConfigurationManager(AbstractBLLTest):
#     """Test PyTorch model configuration manager functionality."""

#     class_under_test = PyTorchModelConfigurationManager
#     test_config = ClassOfTestsConfig(
#         categories=[CategoryOfTest.LOGIC]
#     )

#     # Required fields for AbstractBLLTest
#     create_fields = {
#         "model_name": "test/model",
#         "model_type": PyTorchModelType.CAUSAL_LM,
#         "torch_dtype": "float16",
#         "device_type": PyTorchDeviceType.AUTO,
#         "quantization_type": PyTorchQuantizationType.NONE,
#     }

#     update_fields = {
#         "temperature": 0.8,
#         "max_new_tokens": 1024,
#     }

#     @pytest.fixture
#     def mock_model_registry(self):
#         """Mock model registry for testing."""
#         registry = MagicMock()
#         registry.get_manager.return_value = MagicMock()
#         return registry

#     @pytest.fixture
#     def pytorch_config_manager(self, mock_model_registry):
#         """Create PyTorch configuration manager instance for testing."""
#         return PyTorchModelConfigurationManager(
#             requester_id="test-user-123",
#             target_id="test-target-123",
#             model_registry=mock_model_registry
#         )

#     @pytest.fixture
#     def sample_pytorch_config(self):
#         """Sample PyTorch configuration for testing."""
#         return {
#             "model_name": "microsoft/DialoGPT-medium",
#             "model_type": PyTorchModelType.CAUSAL_LM,
#             "torch_dtype": "float16",
#             "device_type": PyTorchDeviceType.AUTO,
#             "device_map": "auto",
#             "quantization_type": PyTorchQuantizationType.NONE,
#             "use_cache": True,
#             "low_cpu_mem_usage": True,
#             "trust_remote_code": False,
#             "max_new_tokens": 512,
#             "temperature": 0.7,
#             "top_p": 0.95,
#             "top_k": 50,
#             "do_sample": True,
#             "use_flash_attention": False,
#             "gradient_checkpointing": False,
#             "compile_model": False,
#         }

#     def test_manager_initialization(self, pytorch_config_manager):
#         """Test PyTorch configuration manager initialization."""
#         assert isinstance(pytorch_config_manager, AbstractLogicManager)
#         assert pytorch_config_manager.requester.id == "test-user-123"
#         assert pytorch_config_manager.target_id == "test-target-123"
#         assert pytorch_config_manager.Model == PyTorchModelConfigurationModel

#     @patch('extensions.local_ai_torch.BLL_Local_AI_Torch.DEPS_AVAILABLE', True)
#     def test_create_validation_success(self, pytorch_config_manager, sample_pytorch_config):
#         """Test successful creation validation."""
#         # Create entity from sample config
#         entity = PyTorchModelConfigurationModel.Create(**sample_pytorch_config)

#         # Should not raise exception
#         pytorch_config_manager.create_validation(entity)

#     @patch('extensions.local_ai_torch.BLL_Local_AI_Torch.DEPS_AVAILABLE', False)
#     def test_create_validation_deps_unavailable(self, pytorch_config_manager, sample_pytorch_config):
#         """Test creation validation when dependencies are unavailable."""
#         entity = PyTorchModelConfigurationModel.Create(**sample_pytorch_config)

#         with pytest.raises(Exception) as exc_info:
#             pytorch_config_manager.create_validation(entity)
#         assert "dependencies not available" in str(exc_info.value).lower()

#     def test_create_validation_dtype_device_compatibility(self, pytorch_config_manager, sample_pytorch_config):
#         """Test dtype and device compatibility validation."""
#         # Test MPS + bfloat16 compatibility issue
#         sample_pytorch_config["device_type"] = PyTorchDeviceType.MPS
#         sample_pytorch_config["torch_dtype"] = "bfloat16"

#         entity = PyTorchModelConfigurationModel.Create(**sample_pytorch_config)

#         with patch('extensions.local_ai_torch.BLL_Local_AI_Torch.DEPS_AVAILABLE', True):
#             pytorch_config_manager.create_validation(entity)
#             # Should have been corrected to float16
#             assert entity.torch_dtype == "float16"

#     def test_create_validation_quantization_cpu_compatibility(self, pytorch_config_manager, sample_pytorch_config):
#         """Test quantization and CPU compatibility validation."""
#         # Test quantization with CPU
#         sample_pytorch_config["device_type"] = PyTorchDeviceType.CPU
#         sample_pytorch_config["quantization_type"] = PyTorchQuantizationType.INT8

#         entity = PyTorchModelConfigurationModel.Create(**sample_pytorch_config)

#         with patch('extensions.local_ai_torch.BLL_Local_AI_Torch.DEPS_AVAILABLE', True):
#             pytorch_config_manager.create_validation(entity)
#             # Should have been corrected to none
#             assert entity.quantization_type == PyTorchQuantizationType.NONE

#     @patch('extensions.local_ai.EXT_Local_AI.EXT_Local_AI.hardware_detection')
#     def test_get_hardware_optimized_config_cuda(self, mock_hardware_detection, pytorch_config_manager):
#         """Test hardware optimization for CUDA."""
#         # Mock hardware detection for CUDA
#         mock_hardware_detection.return_value = {
#             "has_cuda": True,
#             "has_mps": False,
#             "available_vram_gb": 12.0,
#             "gpu_count": 1
#         }

#         # Mock get method to return a config
#         mock_config = MagicMock()
#         mock_config.model_name = "test/model"
#         mock_config.model_type = PyTorchModelType.CAUSAL_LM
#         mock_config.torch_dtype = "float32"
#         mock_config.device_type = PyTorchDeviceType.AUTO
#         mock_config.device_map = "auto"
#         mock_config.quantization_type = PyTorchQuantizationType.NONE

#         pytorch_config_manager.get = MagicMock(return_value=mock_config)

#         result = pytorch_config_manager.get_hardware_optimized_config("test-config-id")

#         assert result["device_type"] == PyTorchDeviceType.CUDA
#         assert result["torch_dtype"] == "float16"
#         assert result["use_flash_attention"] is True

#     @patch('extensions.local_ai.EXT_Local_AI.EXT_Local_AI.hardware_detection')
#     def test_get_hardware_optimized_config_low_vram(self, mock_hardware_detection, pytorch_config_manager):
#         """Test hardware optimization for low VRAM."""
#         # Mock hardware detection for low VRAM CUDA
#         mock_hardware_detection.return_value = {
#             "has_cuda": True,
#             "has_mps": False,
#             "available_vram_gb": 4.0,
#             "gpu_count": 1
#         }

#         mock_config = MagicMock()
#         mock_config.model_name = "test/model"
#         mock_config.model_type = PyTorchModelType.CAUSAL_LM
#         mock_config.torch_dtype = "float32"
#         mock_config.device_type = PyTorchDeviceType.AUTO
#         mock_config.device_map = "auto"
#         mock_config.quantization_type = PyTorchQuantizationType.NONE

#         pytorch_config_manager.get = MagicMock(return_value=mock_config)

#         result = pytorch_config_manager.get_hardware_optimized_config("test-config-id")

#         assert result["quantization_type"] == PyTorchQuantizationType.INT8
#         assert result["low_cpu_mem_usage"] is True
#         assert result["device_map"] == "auto"

#     @patch('extensions.local_ai.EXT_Local_AI.EXT_Local_AI.hardware_detection')
#     def test_get_hardware_optimized_config_mps(self, mock_hardware_detection, pytorch_config_manager):
#         """Test hardware optimization for MPS."""
#         # Mock hardware detection for MPS
#         mock_hardware_detection.return_value = {
#             "has_cuda": False,
#             "has_mps": True,
#             "available_vram_gb": 8.0,
#             "gpu_count": 0
#         }

#         mock_config = MagicMock()
#         mock_config.model_name = "test/model"
#         mock_config.model_type = PyTorchModelType.CAUSAL_LM
#         mock_config.torch_dtype = "float32"
#         mock_config.device_type = PyTorchDeviceType.AUTO
#         mock_config.device_map = "auto"
#         mock_config.quantization_type = PyTorchQuantizationType.NONE

#         pytorch_config_manager.get = MagicMock(return_value=mock_config)

#         result = pytorch_config_manager.get_hardware_optimized_config("test-config-id")

#         assert result["device_type"] == PyTorchDeviceType.MPS
#         assert result["torch_dtype"] == "float16"
#         assert result["quantization_type"] == PyTorchQuantizationType.NONE

#     @patch('extensions.local_ai.EXT_Local_AI.EXT_Local_AI.hardware_detection')
#     def test_get_hardware_optimized_config_cpu(self, mock_hardware_detection, pytorch_config_manager):
#         """Test hardware optimization for CPU."""
#         # Mock hardware detection for CPU only
#         mock_hardware_detection.return_value = {
#             "has_cuda": False,
#             "has_mps": False,
#             "available_vram_gb": 0.0,
#             "gpu_count": 0
#         }

#         mock_config = MagicMock()
#         mock_config.model_name = "test/model"
#         mock_config.model_type = PyTorchModelType.CAUSAL_LM
#         mock_config.torch_dtype = "float16"
#         mock_config.device_type = PyTorchDeviceType.AUTO
#         mock_config.device_map = "auto"
#         mock_config.quantization_type = PyTorchQuantizationType.INT8

#         pytorch_config_manager.get = MagicMock(return_value=mock_config)

#         result = pytorch_config_manager.get_hardware_optimized_config("test-config-id")

#         assert result["device_type"] == PyTorchDeviceType.CPU
#         assert result["torch_dtype"] == "float32"
#         assert result["quantization_type"] == PyTorchQuantizationType.NONE
#         assert result["use_flash_attention"] is False

#     @patch('extensions.local_ai_torch.BLL_Local_AI_Torch.DEPS_AVAILABLE', False)
#     def test_validate_model_compatibility_no_deps(self, pytorch_config_manager):
#         """Test model compatibility check when dependencies unavailable."""
#         result = pytorch_config_manager.validate_model_compatibility("test-config-id")

#         assert result["compatible"] is False
#         assert "dependencies not available" in result["reason"].lower()
#         assert "Install PyTorch dependencies" in result["recommendations"]

#     @patch('extensions.local_ai_torch.BLL_Local_AI_Torch.DEPS_AVAILABLE', True)
#     @patch('extensions.local_ai.EXT_Local_AI.EXT_Local_AI.hardware_detection')
#     def test_validate_model_compatibility_cuda_unavailable(self, mock_hardware_detection, pytorch_config_manager):
#         """Test compatibility check when CUDA requested but unavailable."""
#         mock_hardware_detection.return_value = {
#             "has_cuda": False,
#             "has_mps": False,
#             "available_vram_gb": 0.0,
#             "gpu_count": 0
#         }

#         mock_config = MagicMock()
#         mock_config.device_type = PyTorchDeviceType.CUDA
#         mock_config.torch_dtype = "float16"

#         pytorch_config_manager.get = MagicMock(return_value=mock_config)

#         result = pytorch_config_manager.validate_model_compatibility("test-config-id")

#         assert result["compatible"] is False
#         assert "CUDA requested but not available" in result["issues"]
#         assert "Switch to CPU or install CUDA" in result["recommendations"]

#     @patch('extensions.local_ai_torch.BLL_Local_AI_Torch.DEPS_AVAILABLE', True)
#     @patch('extensions.local_ai.EXT_Local_AI.EXT_Local_AI.hardware_detection')
#     def test_validate_model_compatibility_insufficient_vram(self, mock_hardware_detection, pytorch_config_manager):
#         """Test compatibility check for insufficient VRAM."""
#         mock_hardware_detection.return_value = {
#             "has_cuda": True,
#             "has_mps": False,
#             "available_vram_gb": 2.0,
#             "gpu_count": 1
#         }

#         mock_config = MagicMock()
#         mock_config.device_type = PyTorchDeviceType.CUDA
#         mock_config.torch_dtype = "float16"

#         pytorch_config_manager.get = MagicMock(return_value=mock_config)

#         result = pytorch_config_manager.validate_model_compatibility("test-config-id")

#         assert result["compatible"] is False
#         assert "Insufficient VRAM" in result["issues"]
#         assert "Enable quantization or use CPU" in result["recommendations"]

#     @patch('extensions.local_ai_torch.BLL_Local_AI_Torch.DEPS_AVAILABLE', True)
#     @patch('extensions.local_ai.EXT_Local_AI.EXT_Local_AI.hardware_detection')
#     def test_validate_model_compatibility_mps_bfloat16(self, mock_hardware_detection, pytorch_config_manager):
#         """Test compatibility check for MPS with bfloat16."""
#         mock_hardware_detection.return_value = {
#             "has_cuda": False,
#             "has_mps": True,
#             "available_vram_gb": 8.0,
#             "gpu_count": 0
#         }

#         mock_config = MagicMock()
#         mock_config.device_type = PyTorchDeviceType.MPS
#         mock_config.torch_dtype = "bfloat16"

#         pytorch_config_manager.get = MagicMock(return_value=mock_config)

#         result = pytorch_config_manager.validate_model_compatibility("test-config-id")

#         assert result["compatible"] is False
#         assert "MPS doesn't support bfloat16" in result["issues"]
#         assert "Use float16 instead" in result["recommendations"]

#     @patch('extensions.local_ai_torch.BLL_Local_AI_Torch.DEPS_AVAILABLE', True)
#     def test_load_model_pipeline_success(self, pytorch_config_manager):
#         """Test successful model pipeline loading."""
#         mock_config = MagicMock()
#         mock_config.model_name = "test/model"
#         mock_config.torch_dtype = "float16"
#         mock_config.device_map = "auto"
#         mock_config.trust_remote_code = False
#         mock_config.use_cache = True
#         mock_config.low_cpu_mem_usage = True
#         mock_config.max_memory = None
#         mock_config.device_type = PyTorchDeviceType.AUTO

#         pytorch_config_manager.get = MagicMock(return_value=mock_config)
#         pytorch_config_manager.validate_model_compatibility = MagicMock(return_value={"compatible": True})

#         result = pytorch_config_manager.load_model_pipeline("test-config-id", "text-generation")

#         assert result["success"] is True
#         assert result["config_id"] == "test-config-id"
#         assert result["task"] == "text-generation"
#         assert "pipeline_config" in result

#     @patch('extensions.local_ai_torch.BLL_Local_AI_Torch.DEPS_AVAILABLE', False)
#     def test_load_model_pipeline_no_deps(self, pytorch_config_manager):
#         """Test model pipeline loading when dependencies unavailable."""
#         with pytest.raises(Exception) as exc_info:
#             pytorch_config_manager.load_model_pipeline("test-config-id", "text-generation")
#         assert "dependencies not available" in str(exc_info.value).lower()

#     def test_load_model_pipeline_incompatible_config(self, pytorch_config_manager):
#         """Test model pipeline loading with incompatible configuration."""
#         mock_config = MagicMock()
#         pytorch_config_manager.get = MagicMock(return_value=mock_config)
#         pytorch_config_manager.validate_model_compatibility = MagicMock(return_value={
#             "compatible": False,
#             "issues": ["Test incompatibility"]
#         })

#         with patch('extensions.local_ai_torch.BLL_Local_AI_Torch.DEPS_AVAILABLE', True):
#             with pytest.raises(Exception) as exc_info:
#                 pytorch_config_manager.load_model_pipeline("test-config-id", "text-generation")
#             assert "not compatible" in str(exc_info.value).lower()

#     def test_model_configuration_model_structure(self):
#         """Test PyTorch model configuration model structure."""
#         # Test Create model
#         create_data = {
#             "model_name": "test/model",
#             "model_type": PyTorchModelType.CAUSAL_LM,
#             "torch_dtype": "float16",
#             "device_type": PyTorchDeviceType.CUDA,
#             "quantization_type": PyTorchQuantizationType.INT8,
#             "max_new_tokens": 512,
#             "temperature": 0.7,
#         }

#         create_model = PyTorchModelConfigurationModel.Create(**create_data)
#         assert create_model.model_name == "test/model"
#         assert create_model.model_type == PyTorchModelType.CAUSAL_LM
#         assert create_model.torch_dtype == "float16"
#         assert create_model.device_type == PyTorchDeviceType.CUDA
#         assert create_model.quantization_type == PyTorchQuantizationType.INT8

#         # Test Update model
#         update_data = {
#             "temperature": 0.8,
#             "max_new_tokens": 1024,
#         }

#         update_model = PyTorchModelConfigurationModel.Update(**update_data)
#         assert update_model.temperature == 0.8
#         assert update_model.max_new_tokens == 1024

#         # Test Search model
#         search_model = PyTorchModelConfigurationModel.Search()
#         assert search_model is not None

#     def test_pytorch_enums(self):
#         """Test PyTorch-specific enums."""
#         # Test PyTorchModelType
#         assert PyTorchModelType.CAUSAL_LM == "causal_lm"
#         assert PyTorchModelType.EMBEDDING == "embedding"
#         assert PyTorchModelType.VISION_LANGUAGE == "vision_language"

#         # Test PyTorchDeviceType
#         assert PyTorchDeviceType.AUTO == "auto"
#         assert PyTorchDeviceType.CUDA == "cuda"
#         assert PyTorchDeviceType.MPS == "mps"
#         assert PyTorchDeviceType.CPU == "cpu"

#         # Test PyTorchQuantizationType
#         assert PyTorchQuantizationType.NONE == "none"
#         assert PyTorchQuantizationType.INT8 == "int8"
#         assert PyTorchQuantizationType.INT4 == "int4"
