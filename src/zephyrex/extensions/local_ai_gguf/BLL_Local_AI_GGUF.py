from datetime import datetime
from enum import Enum

from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractLogicManager import HookContext, HookTiming

try:
    import psutil
    from llama_cpp import Llama

    DEPS_AVAILABLE = True
except ImportError as e:
    logger.warning(f"GGUF dependencies not available in BLL: {e}")
    DEPS_AVAILABLE = False


class GGUFQuantizationType(str, Enum):
    Q4_K_M = "Q4_K_M"
    Q4_K_S = "Q4_K_S"
    Q5_K_M = "Q5_K_M"
    Q5_K_S = "Q5_K_S"
    Q8_0 = "Q8_0"
    F16 = "F16"
    F32 = "F32"


class GGUFContextType(str, Enum):
    STANDARD = "standard"
    EXTENDED = "extended"
    MAXIMUM = "maximum"


class GGUFCacheQuantType(str, Enum):
    Q4_0 = "q4_0"
    Q8_0 = "q8_0"
    F16 = "f16"
    F32 = "f32"


def gguf_validation_hook(context: HookContext) -> None:
    """Validation hook for GGUF model configurations."""
    kwargs = context.kwargs

    # Validate context window limits
    if "context_window" in kwargs:
        context_window = kwargs["context_window"]
        if context_window > 32768:
            logger.warning(
                f"Large context window {context_window}, performance may suffer"
            )
        if context_window < 512:
            logger.warning(f"Small context window {context_window}, forcing to 2048")
            context.kwargs["context_window"] = 2048

    # Validate GPU layers
    if "n_gpu_layers" in kwargs:
        n_gpu_layers = kwargs["n_gpu_layers"]
        if n_gpu_layers > 100:  # Reasonable upper bound
            logger.warning(f"Too many GPU layers {n_gpu_layers}, forcing to -1 (auto)")
            context.kwargs["n_gpu_layers"] = -1

    # Validate tensor split
    if "tensor_split" in kwargs and kwargs["tensor_split"]:
        tensor_split = kwargs["tensor_split"]
        if isinstance(tensor_split, list) and sum(tensor_split) != 1.0:
            logger.warning(f"Tensor split doesn't sum to 1.0: {tensor_split}")

    # Log GGUF configuration for audit
    logger.info(f"GGUF model configuration: {kwargs.get('model_name', 'unknown')}")


def gguf_performance_hook(context: HookContext) -> None:
    """Performance monitoring hook for GGUF operations."""
    if context.timing == HookTiming.BEFORE:
        context.condition_data["start_time"] = datetime.now()
        context.condition_data["method"] = context.method_name
    elif context.timing == HookTiming.AFTER:
        duration = datetime.now() - context.condition_data["start_time"]
        method_name = context.condition_data["method"]
        logger.info(f"GGUF {method_name} completed in {duration.total_seconds():.3f}s")


# class GGUFModelConfigurationModel(
#     BaseMixinModel,
#     UpdateMixinModel,
#     NetworkMixin,
#     metaclass=ModelMeta,
# ):
#     """Configuration model for GGUF model instances."""

#     model_name: str = Field(..., description="Name/path of the GGUF model file")
#     quantization_type: GGUFQuantizationType = Field(..., description="GGUF quantization type")
#     context_window: int = Field(4096, description="Context window size")
#     context_type: GGUFContextType = Field(GGUFContextType.STANDARD, description="Context type configuration")

#     # GPU/Hardware settings
#     n_gpu_layers: int = Field(-1, description="Number of layers to offload to GPU (-1 for auto)")
#     main_gpu: int = Field(0, description="Main GPU to use")
#     tensor_split: Optional[List[float]] = Field(None, description="Tensor split across GPUs")
#     use_mmap: bool = Field(True, description="Use memory mapping")
#     use_mlock: bool = Field(False, description="Use memory locking")

#     # Cache settings
#     kv_cache_quant_type: GGUFCacheQuantType = Field(GGUFCacheQuantType.F16, description="KV cache quantization")
#     cache_offload: bool = Field(False, description="Offload cache to disk")
#     compress_cache: bool = Field(False, description="Compress cache")

#     # Generation settings
#     max_tokens: int = Field(512, description="Maximum tokens to generate")
#     temperature: float = Field(0.7, description="Generation temperature")
#     top_p: float = Field(0.9, description="Top-p sampling parameter")
#     top_k: int = Field(40, description="Top-k sampling parameter")
#     repeat_penalty: float = Field(1.1, description="Repetition penalty")
#     stop_sequences: List[str] = Field(default_factory=list, description="Stop sequences")

#     # Advanced GGUF settings
#     use_beam_search: bool = Field(False, description="Use beam search")
#     beam_count: int = Field(4, description="Number of beams for beam search")
#     rope_scaling_type: Optional[str] = Field(None, description="RoPE scaling type")
#     rope_scaling_factor: Optional[float] = Field(None, description="RoPE scaling factor")

#     # Threading and batch settings
#     n_threads: Optional[int] = Field(None, description="Number of threads (-1 for auto)")
#     n_batch: int = Field(512, description="Batch size for prompt processing")

#     model_config = {"extra": "ignore"}

#     class ReferenceID:
#         gguf_config_id: str = Field(..., description="GGUF configuration ID")

#         class Optional:
#             gguf_config_id: Optional[str] = None

#         class Search:
#             gguf_config_id: Optional[StringSearchModel] = None

#     class Create(BaseModel):
#         model_name: str = Field(..., description="Name/path of the GGUF model file")
#         quantization_type: GGUFQuantizationType = Field(..., description="GGUF quantization type")
#         context_window: int = Field(4096, description="Context window size")
#         context_type: GGUFContextType = Field(GGUFContextType.STANDARD, description="Context type configuration")
#         n_gpu_layers: int = Field(-1, description="Number of layers to offload to GPU")
#         main_gpu: int = Field(0, description="Main GPU to use")
#         tensor_split: Optional[List[float]] = Field(None, description="Tensor split across GPUs")
#         use_mmap: bool = Field(True, description="Use memory mapping")
#         use_mlock: bool = Field(False, description="Use memory locking")
#         kv_cache_quant_type: GGUFCacheQuantType = Field(GGUFCacheQuantType.F16, description="KV cache quantization")
#         cache_offload: bool = Field(False, description="Offload cache to disk")
#         compress_cache: bool = Field(False, description="Compress cache")
#         max_tokens: int = Field(512, description="Maximum tokens to generate")
#         temperature: float = Field(0.7, description="Generation temperature")
#         top_p: float = Field(0.9, description="Top-p sampling parameter")
#         top_k: int = Field(40, description="Top-k sampling parameter")
#         repeat_penalty: float = Field(1.1, description="Repetition penalty")
#         stop_sequences: List[str] = Field(default_factory=list, description="Stop sequences")
#         use_beam_search: bool = Field(False, description="Use beam search")
#         beam_count: int = Field(4, description="Number of beams for beam search")
#         rope_scaling_type: Optional[str] = Field(None, description="RoPE scaling type")
#         rope_scaling_factor: Optional[float] = Field(None, description="RoPE scaling factor")
#         n_threads: Optional[int] = Field(None, description="Number of threads")
#         n_batch: int = Field(512, description="Batch size for prompt processing")

#         @model_validator(mode="after")
#         def validate_rope_scaling(self):
#             if self.rope_scaling_type and not self.rope_scaling_factor:
#                 raise ValueError("RoPE scaling factor required when RoPE scaling type is set")
#             if self.rope_scaling_factor and not self.rope_scaling_type:
#                 raise ValueError("RoPE scaling type required when RoPE scaling factor is set")
#             return self

#     class Update(BaseModel):
#         model_name: Optional[str] = Field(None, description="Name/path of the GGUF model file")
#         quantization_type: Optional[GGUFQuantizationType] = Field(None, description="GGUF quantization type")
#         context_window: Optional[int] = Field(None, description="Context window size")
#         context_type: Optional[GGUFContextType] = Field(None, description="Context type configuration")
#         n_gpu_layers: Optional[int] = Field(None, description="Number of layers to offload to GPU")
#         main_gpu: Optional[int] = Field(None, description="Main GPU to use")
#         tensor_split: Optional[List[float]] = Field(None, description="Tensor split across GPUs")
#         use_mmap: Optional[bool] = Field(None, description="Use memory mapping")
#         use_mlock: Optional[bool] = Field(None, description="Use memory locking")
#         kv_cache_quant_type: Optional[GGUFCacheQuantType] = Field(None, description="KV cache quantization")
#         cache_offload: Optional[bool] = Field(None, description="Offload cache to disk")
#         compress_cache: Optional[bool] = Field(None, description="Compress cache")
#         max_tokens: Optional[int] = Field(None, description="Maximum tokens to generate")
#         temperature: Optional[float] = Field(None, description="Generation temperature")
#         top_p: Optional[float] = Field(None, description="Top-p sampling parameter")
#         top_k: Optional[int] = Field(None, description="Top-k sampling parameter")
#         repeat_penalty: Optional[float] = Field(None, description="Repetition penalty")
#         stop_sequences: Optional[List[str]] = Field(None, description="Stop sequences")
#         use_beam_search: Optional[bool] = Field(None, description="Use beam search")
#         beam_count: Optional[int] = Field(None, description="Number of beams for beam search")
#         rope_scaling_type: Optional[str] = Field(None, description="RoPE scaling type")
#         rope_scaling_factor: Optional[float] = Field(None, description="RoPE scaling factor")
#         n_threads: Optional[int] = Field(None, description="Number of threads")
#         n_batch: Optional[int] = Field(None, description="Batch size for prompt processing")

#     class Search(BaseMixinModel.Search, UpdateMixinModel.Search):
#         model_name: Optional[StringSearchModel] = None
#         quantization_type: Optional[GGUFQuantizationType] = None
#         context_type: Optional[GGUFContextType] = None
#         kv_cache_quant_type: Optional[GGUFCacheQuantType] = None


# class GGUFModelConfigurationReferenceModel(GGUFModelConfigurationModel.Reference.ID):
#     gguf_config: Optional[GGUFModelConfigurationModel] = None

#     class Optional(GGUFModelConfigurationModel.Reference.ID.Optional):
#         gguf_config: Optional[GGUFModelConfigurationModel] = None


# class GGUFModelConfigurationManager(AbstractBLLManager, RouterMixin):
#     """Manager for GGUF model configurations."""

#     Model = GGUFModelConfigurationModel

#     # RouterMixin configuration for automatic API generation
#     prefix: ClassVar[Optional[str]] = "/v1/gguf/configs"
#     tags: ClassVar[Optional[List[str]]] = ["GGUF Configuration"]
#     auth_type: ClassVar[AuthType] = AuthType.JWT

#     def create_validation(self, entity):
#         """Validate GGUF configuration on creation."""
#         if not DEPS_AVAILABLE:
#             raise HTTPException(
#                 status_code=503,
#                 detail="GGUF dependencies not available"
#             )

#         # Validate model file exists
#         if not entity.model_name.endswith('.gguf'):
#             logger.warning(f"Model name doesn't end with .gguf: {entity.model_name}")

#         # Validate context window based on quantization
#         if entity.context_window > 16384 and entity.quantization_type in [GGUFQuantizationType.Q4_K_S, GGUFQuantizationType.Q4_K_M]:
#             logger.warning("Large context with Q4 quantization may cause performance issues")

#         # Auto-set threads if not specified
#         if entity.n_threads is None:
#             entity.n_threads = min(psutil.cpu_count() or 4, 8)  # Reasonable default

#     def get_hardware_optimized_config(self, base_config_id: str) -> Dict[str, Any]:
#         """Get hardware-optimized configuration for a base config."""
#         try:
#             from extensions.local_ai.EXT_Local_AI import EXT_Local_AI

#             base_config = self.get(id=base_config_id)
#             hardware_info = EXT_Local_AI.hardware_detection()

#             # Create optimized configuration based on hardware
#             optimized_config = {
#                 "model_name": base_config.model_name,
#                 "quantization_type": base_config.quantization_type,
#                 "context_window": base_config.context_window,
#                 "context_type": base_config.context_type,
#             }

#             # Apply hardware-specific optimizations
#             if hardware_info.get("has_cuda"):
#                 optimized_config.update({
#                     "n_gpu_layers": -1,  # Use all available
#                     "main_gpu": 0,
#                     "use_mmap": True,
#                     "use_mlock": False,
#                 })

#                 # Multi-GPU optimization
#                 gpu_count = hardware_info.get("gpu_count", 1)
#                 if gpu_count > 1:
#                     # Equal split across GPUs
#                     split = [1.0 / gpu_count] * gpu_count
#                     optimized_config["tensor_split"] = split

#                 # VRAM-based optimizations
#                 available_vram = hardware_info.get("available_vram_gb", 0)
#                 if available_vram < 8:
#                     optimized_config.update({
#                         "cache_offload": True,
#                         "compress_cache": True,
#                         "kv_cache_quant_type": GGUFCacheQuantType.Q4_0,
#                     })

#             elif hardware_info.get("has_mps"):
#                 optimized_config.update({
#                     "n_gpu_layers": 1,  # Limited MPS support
#                     "use_mmap": True,
#                     "use_mlock": False,
#                     "kv_cache_quant_type": GGUFCacheQuantType.F16,
#                 })
#             else:
#                 optimized_config.update({
#                     "n_gpu_layers": 0,  # CPU only
#                     "use_mmap": True,
#                     "use_mlock": False,
#                     "kv_cache_quant_type": GGUFCacheQuantType.Q8_0,
#                 })

#                 # CPU optimization
#                 cpu_count = hardware_info.get("cpu_count", 4)
#                 optimized_config["n_threads"] = min(cpu_count, 8)

#             return optimized_config

#         except Exception as e:
#             logger.error(f"Error getting hardware-optimized config: {e}")
#             raise HTTPException(
#                 status_code=500,
#                 detail="Failed to generate hardware-optimized configuration"
#             )

#     def validate_model_compatibility(self, config_id: str) -> Dict[str, Any]:
#         """Validate that a GGUF configuration is compatible with current hardware."""
#         try:
#             config = self.get(id=config_id)

#             if not DEPS_AVAILABLE:
#                 return {
#                     "compatible": False,
#                     "reason": "GGUF dependencies not available",
#                     "recommendations": ["Install llama-cpp-python"]
#                 }

#             from extensions.local_ai.EXT_Local_AI import EXT_Local_AI
#             hardware_info = EXT_Local_AI.hardware_detection()

#             compatibility_issues = []
#             recommendations = []

#             # Check GPU availability
#             if config.n_gpu_layers > 0 and not hardware_info.get("has_cuda") and not hardware_info.get("has_mps"):
#                 compatibility_issues.append("GPU layers requested but no GPU available")
#                 recommendations.append("Set n_gpu_layers to 0 for CPU-only execution")

#             # Check VRAM requirements
#             if config.n_gpu_layers > 0:
#                 available_vram = hardware_info.get("available_vram_gb", 0)
#                 estimated_vram = self._estimate_vram_usage(config)

#                 if estimated_vram > available_vram:
#                     compatibility_issues.append(f"Estimated VRAM usage ({estimated_vram:.1f}GB) exceeds available ({available_vram:.1f}GB)")
#                     recommendations.append("Enable cache offload or reduce context window")

#             # Check RAM requirements
#             available_ram = hardware_info.get("available_ram_gb", 0)
#             estimated_ram = self._estimate_ram_usage(config)

#             if estimated_ram > available_ram:
#                 compatibility_issues.append(f"Estimated RAM usage ({estimated_ram:.1f}GB) exceeds available ({available_ram:.1f}GB)")
#                 recommendations.append("Use smaller quantization or reduce context window")

#             # Check tensor split validity
#             if config.tensor_split:
#                 gpu_count = hardware_info.get("gpu_count", 1)
#                 if len(config.tensor_split) != gpu_count:
#                     compatibility_issues.append("Tensor split length doesn't match GPU count")
#                     recommendations.append(f"Adjust tensor split for {gpu_count} GPUs")

#             return {
#                 "compatible": len(compatibility_issues) == 0,
#                 "issues": compatibility_issues,
#                 "recommendations": recommendations,
#                 "estimated_vram_gb": self._estimate_vram_usage(config),
#                 "estimated_ram_gb": self._estimate_ram_usage(config),
#                 "hardware_info": hardware_info
#             }

#         except Exception as e:
#             logger.error(f"Error validating model compatibility: {e}")
#             raise HTTPException(
#                 status_code=500,
#                 detail="Failed to validate model compatibility"
#             )

#     def _estimate_vram_usage(self, config: GGUFModelConfigurationModel) -> float:
#         """Estimate VRAM usage for a GGUF configuration."""
#         # Base model size estimation
#         base_size = 4.0  # Default 7B model size in GB

#         # Adjust for quantization
#         quant_multipliers = {
#             GGUFQuantizationType.Q4_K_M: 0.6,
#             GGUFQuantizationType.Q4_K_S: 0.55,
#             GGUFQuantizationType.Q5_K_M: 0.7,
#             GGUFQuantizationType.Q5_K_S: 0.65,
#             GGUFQuantizationType.Q8_0: 0.9,
#             GGUFQuantizationType.F16: 1.0,
#             GGUFQuantizationType.F32: 2.0,
#         }

#         model_size = base_size * quant_multipliers.get(config.quantization_type, 0.6)

#         # Add context cache size
#         context_size = config.context_window * 0.001  # Rough estimate

#         # Add overhead
#         overhead = model_size * 0.2

#         return model_size + context_size + overhead

#     def _estimate_ram_usage(self, config: GGUFModelConfigurationModel) -> float:
#         """Estimate RAM usage for a GGUF configuration."""
#         # If using GPU, RAM usage is lower
#         if config.n_gpu_layers > 0:
#             return 2.0  # Base system overhead
#         else:
#             # Full model in RAM for CPU execution
#             return self._estimate_vram_usage(config) + 1.0

#     def load_model_instance(self, config_id: str) -> Dict[str, Any]:
#         """Load a GGUF model instance based on configuration."""
#         if not DEPS_AVAILABLE:
#             raise HTTPException(
#                 status_code=503,
#                 detail="GGUF dependencies not available"
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

#             # Build llama.cpp configuration
#             llama_config = {
#                 "model_path": config.model_name,
#                 "n_ctx": config.context_window,
#                 "n_gpu_layers": config.n_gpu_layers,
#                 "main_gpu": config.main_gpu,
#                 "use_mmap": config.use_mmap,
#                 "use_mlock": config.use_mlock,
#                 "n_threads": config.n_threads,
#                 "n_batch": config.n_batch,
#             }

#             # Add tensor split if specified
#             if config.tensor_split:
#                 llama_config["tensor_split"] = config.tensor_split

#             # Add RoPE scaling if specified
#             if config.rope_scaling_type and config.rope_scaling_factor:
#                 llama_config["rope_scaling_type"] = config.rope_scaling_type
#                 llama_config["rope_freq_scale"] = config.rope_scaling_factor

#             # Load the model instance (in real implementation)
#             logger.info(f"Loading GGUF model instance for {config.model_name}")

#             return {
#                 "success": True,
#                 "config_id": config_id,
#                 "llama_config": llama_config,
#                 "message": f"Model instance loaded for {config.model_name}"
#             }

#         except HTTPException:
#             raise
#         except Exception as e:
#             logger.error(f"Error loading model instance: {e}")
#             raise HTTPException(
#                 status_code=500,
#                 detail="Failed to load model instance"
#             )


# # Apply hooks to the manager
# hook_bll(GGUFModelConfigurationManager, timing=HookTiming.BEFORE, priority=5)(
#     gguf_validation_hook
# )
# hook_bll(GGUFModelConfigurationManager, timing=HookTiming.BEFORE, priority=10)(
#     gguf_performance_hook
# )
# hook_bll(GGUFModelConfigurationManager, timing=HookTiming.AFTER, priority=90)(
#     gguf_performance_hook
# )
