from datetime import datetime
from enum import Enum

from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractLogicManager import HookContext, HookTiming

try:
    import torch
    import transformers
    from transformers import AutoModel, AutoModelForCausalLM, AutoTokenizer, pipeline

    DEPS_AVAILABLE = True  # Is available.
except ImportError as e:
    logger.warning(f"PyTorch dependencies not available in BLL: {e}")
    DEPS_AVAILABLE = False


class PyTorchModelType(str, Enum):
    CAUSAL_LM = "causal_lm"
    SEQUENCE_CLASSIFICATION = "sequence_classification"
    TOKEN_CLASSIFICATION = "token_classification"
    EMBEDDING = "embedding"
    VISION_LANGUAGE = "vision_language"
    IMAGE_GENERATION = "image_generation"


class PyTorchQuantizationType(str, Enum):
    NONE = "none"
    INT8 = "int8"
    INT4 = "int4"
    GPTQ = "gptq"
    AWQ = "awq"
    BNBQ = "bnbq"


class PyTorchDeviceType(str, Enum):
    AUTO = "auto"
    CPU = "cpu"
    CUDA = "cuda"
    MPS = "mps"
    SPECIFIC = "specific"


def pytorch_validation_hook(context: HookContext) -> None:
    """Validation hook for PyTorch model configurations."""
    kwargs = context.kwargs

    # Validate torch_dtype
    if "torch_dtype" in kwargs:
        valid_dtypes = ["float32", "float16", "bfloat16", "int8"]
        if kwargs["torch_dtype"] not in valid_dtypes:
            logger.warning(
                f"Invalid torch_dtype: {kwargs['torch_dtype']}, forcing to float16"
            )
            context.kwargs["torch_dtype"] = "float16"

    # Validate device configuration
    device_type = kwargs.get("device_type", PyTorchDeviceType.AUTO)
    if device_type == PyTorchDeviceType.CUDA and not DEPS_AVAILABLE:
        logger.warning("CUDA requested but PyTorch not available, forcing to CPU")
        context.kwargs["device_type"] = PyTorchDeviceType.CPU

    # Log PyTorch configuration for audit
    logger.info(f"PyTorch model configuration: {kwargs.get('model_name', 'unknown')}")


def pytorch_performance_hook(context: HookContext) -> None:
    """Performance monitoring hook for PyTorch operations."""
    if context.timing == HookTiming.BEFORE:
        context.condition_data["start_time"] = datetime.now()
        context.condition_data["method"] = context.method_name
    elif context.timing == HookTiming.AFTER:
        duration = datetime.now() - context.condition_data["start_time"]
        method_name = context.condition_data["method"]
        logger.info(
            f"PyTorch {method_name} completed in {duration.total_seconds():.3f}s"
        )


# class PyTorchModelConfigurationModel(
#     BaseMixinModel,
#     UpdateMixinModel,
#     NetworkMixin,
#     metaclass=ModelMeta,
# ):
#     """Configuration model for PyTorch model instances."""

#     model_name: str = Field(..., description="Name/path of the PyTorch model")
#     model_type: PyTorchModelType = Field(..., description="Type of PyTorch model")
#     torch_dtype: str = Field("float16", description="PyTorch data type (float32, float16, bfloat16)")
#     device_type: PyTorchDeviceType = Field(PyTorchDeviceType.AUTO, description="Device type for model")
#     device_map: Optional[str] = Field("auto", description="Device mapping strategy")
#     quantization_type: PyTorchQuantizationType = Field(PyTorchQuantizationType.NONE, description="Quantization method")
#     use_cache: bool = Field(True, description="Whether to use model caching")
#     low_cpu_mem_usage: bool = Field(True, description="Use low CPU memory loading")
#     trust_remote_code: bool = Field(False, description="Trust remote code execution")
#     max_memory: Optional[Dict[str, str]] = Field(None, description="Maximum memory per device")

#     # Generation specific settings
#     max_new_tokens: int = Field(512, description="Maximum new tokens to generate")
#     temperature: float = Field(0.7, description="Generation temperature")
#     top_p: float = Field(0.95, description="Top-p sampling parameter")
#     top_k: int = Field(50, description="Top-k sampling parameter")
#     do_sample: bool = Field(True, description="Whether to use sampling")

#     # Advanced PyTorch settings
#     use_flash_attention: bool = Field(False, description="Use flash attention if available")
#     gradient_checkpointing: bool = Field(False, description="Use gradient checkpointing")
#     compile_model: bool = Field(False, description="Use torch.compile for optimization")

#     model_config = {"extra": "ignore"}

#     class ReferenceID:
#         pytorch_config_id: str = Field(..., description="PyTorch configuration ID")

#         class Optional:
#             pytorch_config_id: Optional[str] = None

#         class Search:
#             pytorch_config_id: Optional[StringSearchModel] = None

#     class Create(BaseModel):
#         model_name: str = Field(..., description="Name/path of the PyTorch model")
#         model_type: PyTorchModelType = Field(..., description="Type of PyTorch model")
#         torch_dtype: str = Field("float16", description="PyTorch data type")
#         device_type: PyTorchDeviceType = Field(PyTorchDeviceType.AUTO, description="Device type")
#         device_map: Optional[str] = Field("auto", description="Device mapping strategy")
#         quantization_type: PyTorchQuantizationType = Field(PyTorchQuantizationType.NONE, description="Quantization method")
#         use_cache: bool = Field(True, description="Whether to use model caching")
#         low_cpu_mem_usage: bool = Field(True, description="Use low CPU memory loading")
#         trust_remote_code: bool = Field(False, description="Trust remote code execution")
#         max_memory: Optional[Dict[str, str]] = Field(None, description="Maximum memory per device")
#         max_new_tokens: int = Field(512, description="Maximum new tokens to generate")
#         temperature: float = Field(0.7, description="Generation temperature")
#         top_p: float = Field(0.95, description="Top-p sampling parameter")
#         top_k: int = Field(50, description="Top-k sampling parameter")
#         do_sample: bool = Field(True, description="Whether to use sampling")
#         use_flash_attention: bool = Field(False, description="Use flash attention if available")
#         gradient_checkpointing: bool = Field(False, description="Use gradient checkpointing")
#         compile_model: bool = Field(False, description="Use torch.compile for optimization")

#     class Update(BaseModel):
#         model_name: Optional[str] = Field(None, description="Name/path of the PyTorch model")
#         torch_dtype: Optional[str] = Field(None, description="PyTorch data type")
#         device_type: Optional[PyTorchDeviceType] = Field(None, description="Device type")
#         device_map: Optional[str] = Field(None, description="Device mapping strategy")
#         quantization_type: Optional[PyTorchQuantizationType] = Field(None, description="Quantization method")
#         use_cache: Optional[bool] = Field(None, description="Whether to use model caching")
#         low_cpu_mem_usage: Optional[bool] = Field(None, description="Use low CPU memory loading")
#         trust_remote_code: Optional[bool] = Field(None, description="Trust remote code execution")
#         max_memory: Optional[Dict[str, str]] = Field(None, description="Maximum memory per device")
#         max_new_tokens: Optional[int] = Field(None, description="Maximum new tokens to generate")
#         temperature: Optional[float] = Field(None, description="Generation temperature")
#         top_p: Optional[float] = Field(None, description="Top-p sampling parameter")
#         top_k: Optional[int] = Field(None, description="Top-k sampling parameter")
#         do_sample: Optional[bool] = Field(None, description="Whether to use sampling")
#         use_flash_attention: Optional[bool] = Field(None, description="Use flash attention if available")
#         gradient_checkpointing: Optional[bool] = Field(None, description="Use gradient checkpointing")
#         compile_model: Optional[bool] = Field(None, description="Use torch.compile for optimization")

#     class Search(BaseMixinModel.Search, UpdateMixinModel.Search):
#         model_name: Optional[StringSearchModel] = None
#         model_type: Optional[PyTorchModelType] = None
#         torch_dtype: Optional[str] = None
#         device_type: Optional[PyTorchDeviceType] = None
#         quantization_type: Optional[PyTorchQuantizationType] = None


# class PyTorchModelConfigurationReferenceModel(PyTorchModelConfigurationModel.Reference.ID):
#     pytorch_config: Optional[PyTorchModelConfigurationModel] = None

#     class Optional(PyTorchModelConfigurationModel.Reference.ID.Optional):
#         pytorch_config: Optional[PyTorchModelConfigurationModel] = None


# class PyTorchModelConfigurationManager(AbstractBLLManager, RouterMixin):
#     """Manager for PyTorch model configurations."""

#     Model = PyTorchModelConfigurationModel

#     # RouterMixin configuration for automatic API generation
#     prefix: ClassVar[Optional[str]] = "/v1/pytorch/configs"
#     tags: ClassVar[Optional[List[str]]] = ["PyTorch Configuration"]
#     auth_type: ClassVar[AuthType] = AuthType.JWT

#     def create_validation(self, entity):
#         """Validate PyTorch configuration on creation."""
#         if not DEPS_AVAILABLE:
#             raise HTTPException(
#                 status_code=503,
#                 detail="PyTorch dependencies not available"
#             )

#         # Validate model name/path
#         if not entity.model_name:
#             raise HTTPException(
#                 status_code=400,
#                 detail="Model name is required"
#             )

#         # Validate dtype and device compatibility
#         if entity.device_type == PyTorchDeviceType.MPS and entity.torch_dtype == "bfloat16":
#             logger.warning("MPS doesn't support bfloat16, switching to float16")
#             entity.torch_dtype = "float16"

#         # Validate quantization compatibility
#         if entity.quantization_type != PyTorchQuantizationType.NONE and entity.device_type == PyTorchDeviceType.CPU:
#             logger.warning("Quantization not recommended for CPU, disabling")
#             entity.quantization_type = PyTorchQuantizationType.NONE

#     def get_hardware_optimized_config(self, base_config_id: str) -> Dict[str, Any]:
#         """Get hardware-optimized configuration for a base config."""
#         try:
#             from extensions.local_ai.EXT_Local_AI import EXT_Local_AI

#             base_config = self.get(id=base_config_id)
#             hardware_info = EXT_Local_AI.hardware_detection()

#             # Create optimized configuration based on hardware
#             optimized_config = {
#                 "model_name": base_config.model_name,
#                 "model_type": base_config.model_type,
#                 "torch_dtype": base_config.torch_dtype,
#                 "device_type": base_config.device_type,
#                 "device_map": base_config.device_map,
#                 "quantization_type": base_config.quantization_type,
#             }

#             # Apply hardware-specific optimizations
#             if hardware_info.get("has_cuda"):
#                 optimized_config.update({
#                     "device_type": PyTorchDeviceType.CUDA,
#                     "torch_dtype": "float16",
#                     "use_flash_attention": True,
#                 })

#                 # VRAM-based optimizations
#                 available_vram = hardware_info.get("available_vram_gb", 0)
#                 if available_vram < 8:
#                     optimized_config.update({
#                         "quantization_type": PyTorchQuantizationType.INT8,
#                         "low_cpu_mem_usage": True,
#                         "device_map": "auto",
#                     })

#             elif hardware_info.get("has_mps"):
#                 optimized_config.update({
#                     "device_type": PyTorchDeviceType.MPS,
#                     "torch_dtype": "float16",
#                     "quantization_type": PyTorchQuantizationType.NONE,
#                 })
#             else:
#                 optimized_config.update({
#                     "device_type": PyTorchDeviceType.CPU,
#                     "torch_dtype": "float32",
#                     "quantization_type": PyTorchQuantizationType.NONE,
#                     "use_flash_attention": False,
#                 })

#             return optimized_config

#         except Exception as e:
#             logger.error(f"Error getting hardware-optimized config: {e}")
#             raise HTTPException(
#                 status_code=500,
#                 detail="Failed to generate hardware-optimized configuration"
#             )

#     def validate_model_compatibility(self, config_id: str) -> Dict[str, Any]:
#         """Validate that a model configuration is compatible with current hardware."""
#         try:
#             config = self.get(id=config_id)

#             if not DEPS_AVAILABLE:
#                 return {
#                     "compatible": False,
#                     "reason": "PyTorch dependencies not available",
#                     "recommendations": ["Install PyTorch dependencies"]
#                 }

#             from extensions.local_ai.EXT_Local_AI import EXT_Local_AI
#             hardware_info = EXT_Local_AI.hardware_detection()

#             compatibility_issues = []
#             recommendations = []

#             # Check device compatibility
#             if config.device_type == PyTorchDeviceType.CUDA and not hardware_info.get("has_cuda"):
#                 compatibility_issues.append("CUDA requested but not available")
#                 recommendations.append("Switch to CPU or install CUDA")

#             if config.device_type == PyTorchDeviceType.MPS and not hardware_info.get("has_mps"):
#                 compatibility_issues.append("MPS requested but not available")
#                 recommendations.append("Switch to CPU or use macOS with Apple Silicon")

#             # Check memory requirements
#             available_vram = hardware_info.get("available_vram_gb", 0)
#             if config.device_type in [PyTorchDeviceType.CUDA, PyTorchDeviceType.MPS] and available_vram < 4:
#                 compatibility_issues.append("Insufficient VRAM")
#                 recommendations.append("Enable quantization or use CPU")

#             # Check dtype compatibility
#             if config.device_type == PyTorchDeviceType.MPS and config.torch_dtype == "bfloat16":
#                 compatibility_issues.append("MPS doesn't support bfloat16")
#                 recommendations.append("Use float16 instead")

#             return {
#                 "compatible": len(compatibility_issues) == 0,
#                 "issues": compatibility_issues,
#                 "recommendations": recommendations,
#                 "hardware_info": hardware_info
#             }

#         except Exception as e:
#             logger.error(f"Error validating model compatibility: {e}")
#             raise HTTPException(
#                 status_code=500,
#                 detail="Failed to validate model compatibility"
#             )

#     def load_model_pipeline(self, config_id: str, task: str = "text-generation") -> Dict[str, Any]:
#         """Load a PyTorch model pipeline based on configuration."""
#         if not DEPS_AVAILABLE:
#             raise HTTPException(
#                 status_code=503,
#                 detail="PyTorch dependencies not available"
#             )

#         try:
#             config = self.get(id=config_id)

#             # Validate compatibility first
#             compatibility = self.validate_model_compatibility(config_id)
#             if not compatibility["compatible"]:
#                 raise HTTPException(
#                     status_code=400,
#                     detail=f"Model configuration not compatible: {compatibility['issues']}"
#                 )

#             # Build pipeline configuration
#             pipeline_config = {
#                 "model": config.model_name,
#                 "torch_dtype": getattr(torch, config.torch_dtype),
#                 "device_map": config.device_map,
#                 "trust_remote_code": config.trust_remote_code,
#                 "use_cache": config.use_cache,
#                 "low_cpu_mem_usage": config.low_cpu_mem_usage,
#             }

#             # Add device-specific settings
#             if config.device_type != PyTorchDeviceType.AUTO:
#                 pipeline_config["device"] = config.device_type.value

#             # Add memory constraints
#             if config.max_memory:
#                 pipeline_config["max_memory"] = config.max_memory

#             # Load the pipeline (in real implementation)
#             logger.info(f"Loading PyTorch pipeline for {config.model_name} with task {task}")

#             return {
#                 "success": True,
#                 "config_id": config_id,
#                 "task": task,
#                 "pipeline_config": pipeline_config,
#                 "message": f"Pipeline loaded for {config.model_name}"
#             }

#         except HTTPException:
#             raise
#         except Exception as e:
#             logger.error(f"Error loading model pipeline: {e}")
#             raise HTTPException(
#                 status_code=500,
#                 detail="Failed to load model pipeline"
#             )


# # Apply hooks to the manager
# hook_bll(PyTorchModelConfigurationManager, timing=HookTiming.BEFORE, priority=5)(
#     pytorch_validation_hook
# )
# hook_bll(PyTorchModelConfigurationManager, timing=HookTiming.BEFORE, priority=10)(
#     pytorch_performance_hook
# )
# hook_bll(PyTorchModelConfigurationManager, timing=HookTiming.AFTER, priority=90)(
#     pytorch_performance_hook
# )
