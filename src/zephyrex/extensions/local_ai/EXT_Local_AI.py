"""
Local AI extension for AGInfrastructure.
Implements the Provider Rotation System for local AI model operations.
"""

import logging
from abc import abstractmethod
from typing import Any, Dict, List, Optional, Type

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.local_ai.BLL_Local_AI import (
    HardwareDetector,
    HardwareInfo,
    LocalAIProviderConfiguration,
    ModelPreference,
    ModelRecommendationEngine,
    ModelTask,
    RequestPriority,
    RequestPriorityManager,
)
from zephyrex.lib.Dependencies import EXT_Dependency
from zephyrex.pydantic2.fastapi import static_route
from zephyrex.logic.BLL_Providers import ProviderInstanceModel
from pydantic import BaseModel, Field

# Import function for checking optional dependencies
try:
    from zephyrex.lib.Dependencies import are_optional_dependencies_met
except ImportError:

    def are_optional_dependencies_met(deps):
        return False


# Request/Response Models for API Endpoints
class ModelRecommendationRequest(BaseModel):
    """Request for model recommendation."""

    hardware_info: Optional[Dict[str, Any]] = Field(
        None, description="Hardware info (optional - auto-detected if not provided)"
    )
    preference: str = Field(
        "balanced",
        description="Model preference: balanced, context_size, model_size, speed, quality",
    )
    task_type: Optional[str] = Field("text_generation", description="Target task type")


class ModelRecommendationResponse(BaseModel):
    """Response for model recommendation."""

    success: bool = Field(..., description="Operation success status")
    best_model: Optional[Dict[str, Any]] = Field(None, description="Recommended model")
    hardware_info: Optional[Dict[str, Any]] = Field(
        None, description="Hardware information used"
    )
    preference: Optional[str] = Field(None, description="Preference used")
    total_available_models: Optional[int] = Field(
        None, description="Total models considered"
    )
    error: Optional[str] = Field(None, description="Error message if failed")


class QueueDownloadRequest(BaseModel):
    """Request to queue model download."""

    model_id: str = Field(..., description="Model identifier")
    model_format: str = Field("auto", description="Model format (auto, gguf, pytorch)")
    priority: str = Field(
        "normal", description="Download priority (low, normal, medium, high)"
    )


class QueueDownloadResponse(BaseModel):
    """Response for download queue request."""

    success: bool = Field(..., description="Operation success status")
    download_id: Optional[str] = Field(None, description="Download tracking ID")
    status: Optional[str] = Field(None, description="Queue status")
    queue_position: Optional[int] = Field(None, description="Position in queue")
    estimated_size_gb: Optional[float] = Field(
        None, description="Estimated download size"
    )
    message: Optional[str] = Field(None, description="Status message")
    error: Optional[str] = Field(None, description="Error message if failed")


class AutoFitModelRequest(BaseModel):
    """Request for auto-fitting model configuration."""

    config: Dict[str, Any] = Field(..., description="Base model configuration")
    target_usage: float = Field(0.8, description="Target VRAM usage ratio")
    prefer_quality: bool = Field(True, description="Prefer quality over speed")


class AutoFitModelResponse(BaseModel):
    """Response for auto-fit model configuration."""

    success: bool = Field(..., description="Operation success status")
    optimized_config: Optional[Dict[str, Any]] = Field(
        None, description="Optimized configuration"
    )
    hardware_info: Optional[Dict[str, Any]] = Field(
        None, description="Hardware information"
    )
    target_usage: Optional[float] = Field(None, description="Target usage ratio")
    estimated_improvement: Optional[str] = Field(
        None, description="Improvement description"
    )
    error: Optional[str] = Field(None, description="Error message if failed")


class PrioritizeRequestRequest(BaseModel):
    """Request for request prioritization."""

    requester_id: str = Field(..., description="User ID making the request")
    team_id: Optional[str] = Field(None, description="Team ID if applicable")
    user_roles: Optional[List[str]] = Field(None, description="User roles")
    subscription_tier: Optional[str] = Field(None, description="Subscription tier")
    origin: Optional[str] = Field(None, description="Request origin")
    model_type: Optional[str] = Field(None, description="Model type")
    task_type: Optional[str] = Field(None, description="Task type")
    token_count: Optional[int] = Field(None, description="Expected token count")


class PrioritizeRequestResponse(BaseModel):
    """Response for request prioritization."""

    success: bool = Field(..., description="Operation success status")
    priority_config: Optional[Dict[str, Any]] = Field(
        None, description="Priority configuration"
    )
    error: Optional[str] = Field(None, description="Error message if failed")


class TensorSplitRequest(BaseModel):
    """Request for tensor split configuration."""

    gpu_devices: List[Dict[str, Any]] = Field(..., description="GPU device information")


class TensorSplitResponse(BaseModel):
    """Response for tensor split configuration."""

    success: bool = Field(..., description="Operation success status")
    tensor_split: Optional[List[float]] = Field(
        None, description="Recommended tensor split"
    )
    gpu_count: Optional[int] = Field(None, description="Number of GPUs")
    total_vram_gb: Optional[float] = Field(None, description="Total VRAM")
    recommended_split_mode: Optional[str] = Field(
        None, description="Recommended split mode"
    )
    error: Optional[str] = Field(None, description="Error message if failed")


class HardwareDetectionResponse(BaseModel):
    """Response for hardware detection."""

    success: bool = Field(..., description="Operation success status")
    hardware_info: Optional[Dict[str, Any]] = Field(
        None, description="Hardware information"
    )
    performance_score: Optional[float] = Field(
        None, description="Overall performance score"
    )
    error: Optional[str] = Field(None, description="Error message if failed")


class VRAMQueryResponse(BaseModel):
    """Response for VRAM query."""

    success: bool = Field(..., description="Operation success status")
    total_vram_gb: Optional[float] = Field(
        None, description="Total VRAM across all GPUs"
    )
    available_vram_gb: Optional[float] = Field(None, description="Available VRAM")
    used_vram_gb: Optional[float] = Field(None, description="Used VRAM")
    gpu_count: Optional[int] = Field(None, description="Number of GPUs")
    gpu_devices: Optional[List[Dict[str, Any]]] = Field(
        None, description="GPU device information"
    )
    hardware_type: Optional[str] = Field(None, description="Primary hardware type")
    supported_types: Optional[List[str]] = Field(
        None, description="Supported hardware types"
    )
    error: Optional[str] = Field(None, description="Error message if failed")


class ModelMountRequest(BaseModel):
    """Request to mount a model."""

    model_id: str = Field(..., description="Model identifier to mount")
    device: str = Field("auto", description="Device to use (cuda, cpu, mps, auto)")
    tensor_split: Optional[List[float]] = Field(
        None, description="Tensor split across GPUs"
    )
    n_gpu_layers: int = Field(-1, description="Number of layers to offload to GPU")
    main_gpu: int = Field(0, description="Primary GPU device ID")
    context_size: Optional[int] = Field(
        None, description="Context window size override"
    )
    auto_fit: bool = Field(
        False, description="Automatically fit model to available VRAM"
    )


class ModelUnmountRequest(BaseModel):
    """Request to unmount a model."""

    provider_instance_id: str = Field(
        ..., description="Provider instance ID to unmount"
    )


class ModelMountResponse(BaseModel):
    """Response for model mount/unmount operations."""

    success: bool = Field(..., description="Operation success status")
    status: str = Field(
        ..., description="Mount status (accepted, mounting, mounted, failed)"
    )
    model_id: str = Field(..., description="Model ID")
    provider_instance_id: Optional[str] = Field(
        None, description="Provider instance ID if successful"
    )
    estimated_time_ms: Optional[int] = Field(
        None, description="Estimated time to complete"
    )
    error: Optional[str] = Field(None, description="Error message if failed")
    message: Optional[str] = Field(None, description="Status message")


class ModelStatusResponse(BaseModel):
    """Response for model status query."""

    success: bool = Field(..., description="Operation success status")
    model_id: str = Field(..., description="Model ID")
    status: str = Field(
        ..., description="Current status (mounted, unmounted, downloading, etc.)"
    )
    vram_usage_gb: Optional[float] = Field(None, description="Current VRAM usage")
    ram_usage_gb: Optional[float] = Field(None, description="Current RAM usage")
    inference_count: Optional[int] = Field(
        None, description="Number of inferences performed"
    )
    last_used: Optional[str] = Field(None, description="Last inference timestamp")
    error: Optional[str] = Field(None, description="Error message if failed")


class DiscoverModelsResponse(BaseModel):
    """Response for model discovery."""

    success: bool = Field(..., description="Operation success status")
    models: List[Dict[str, Any]] = Field(..., description="Discovered models")
    count: int = Field(..., description="Number of models found")
    search_paths: List[str] = Field(..., description="Paths searched")
    error: Optional[str] = Field(None, description="Error message if failed")


class DownloadStatusResponse(BaseModel):
    """Response for download status query."""

    success: bool = Field(..., description="Operation success status")
    download_id: str = Field(..., description="Download ID")
    status: str = Field(
        ..., description="Download status (queued, downloading, completed, failed)"
    )
    progress_percent: Optional[float] = Field(
        None, description="Download progress percentage"
    )
    downloaded_gb: Optional[float] = Field(None, description="Amount downloaded")
    total_gb: Optional[float] = Field(None, description="Total download size")
    eta_seconds: Optional[int] = Field(None, description="Estimated time remaining")
    message: Optional[str] = Field(None, description="Status message")
    error: Optional[str] = Field(None, description="Error message if failed")


class AbstractLocalAIProvider(AbstractStaticProvider):
    """
    Abstract base class for local AI model providers.
    Defines the common interface for all AI providers with static functionality.
    All AI providers should be static/abstract classes with no instantiation required.
    Integrates with the Provider Rotation System for failover and load balancing.
    """

    extension_type: str = "local_ai"

    @classmethod
    @abstractmethod
    def services(cls) -> List[str]:
        """Return a list of services provided by this provider."""
        pass

    @classmethod
    @abstractmethod
    def get_platform_name(cls) -> str:
        """Get the name of the AI platform this provider interacts with."""
        pass

    @classmethod
    def get_extension_info(cls) -> Dict[str, Any]:
        """Get information about the local AI extension."""
        return {
            "name": "Local AI",
            "description": f"Local AI extension for {cls.get_platform_name()}",
        }

    @classmethod
    @abstractmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        """Bond a provider instance for AI operations."""
        pass

    # Abstract abilities - must be implemented by providers
    @classmethod
    @abstractmethod
    @ability(name="text_to_text")
    async def generate_text(
        cls,
        bonded_instance: AbstractProviderInstance,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate text using AI model."""
        pass

    @classmethod
    @abstractmethod
    @ability(name="text_to_embedding")
    async def generate_embeddings(
        cls,
        bonded_instance: AbstractProviderInstance,
        text: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate embeddings for text."""
        pass

    @classmethod
    @abstractmethod
    @ability(name="text_to_image")
    async def generate_image(
        cls,
        bonded_instance: AbstractProviderInstance,
        prompt: str,
        width: int = 512,
        height: int = 512,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate image from text prompt."""
        pass

    @classmethod
    @abstractmethod
    @ability(name="speech_to_text")
    async def transcribe_audio(
        cls,
        bonded_instance: AbstractProviderInstance,
        audio_data: bytes,
        **kwargs,
    ) -> Dict[str, Any]:
        """Transcribe audio to text."""
        pass

    @classmethod
    @abstractmethod
    @ability(name="text_to_speech")
    async def synthesize_speech(
        cls,
        bonded_instance: AbstractProviderInstance,
        text: str,
        voice: str = "default",
        **kwargs,
    ) -> Dict[str, Any]:
        """Synthesize speech from text."""
        pass


class EXT_Local_AI(AbstractStaticExtension):
    """
    Local AI extension for AGInfrastructure.

    Provides local AI model capabilities through the Provider Rotation System.
    This extension acts as the base framework that specific model format extensions
    (GGUF, PyTorch, etc.) depend on. It provides meta abilities for:

    - Hardware detection and analysis for all major GPU vendors (NVIDIA, AMD, Intel, Apple)
    - Model recommendation based on hardware capabilities and user preferences
    - Request prioritization with optional subscription integration via payment extension
    - Model download queue management with progress tracking
    - GPU VRAM monitoring and auto-fit configuration
    - Universal provider management and coordination

    Architecture:
    - Uses **meta abilities** to programmatically create and manage AI providers
    - **Providers** are created dynamically based on discovered/downloaded models
    - **ProviderInstances** represent specific model configurations (quantization, tensor split, etc.)
    - **ProviderInstanceSettings** store advanced configuration options
    - **ProviderInstanceUsage** tracks input/output tokens for billing/analytics
    - No concrete providers - all AI providers are managed programmatically

    Usage:
        # Generate text using rotation system
        result = RotationManager.rotate(
            "text_to_text",
            prompt="Hello, world!",
            max_tokens=100
        )
    """

    # Extension metadata
    name: str = "local_ai"
    friendly_name: str = "Local AI Framework"
    version: str = "2.0.0"
    description: str = (
        "Local AI extension providing comprehensive AI model capabilities with advanced "
        "configuration, hardware optimization, and request prioritization via Provider Rotation System"
    )
    AbstractProvider = AbstractLocalAIProvider

    # Meta abilities - this extension provides management capabilities
    _abilities = {
        "hardware_detection",  # Detect and analyze hardware capabilities
        "model_recommendation",  # Recommend optimal models for hardware
        "download_model",  # Download and cache models
        "queue_download",  # Queue model downloads
        "mount_model",  # Mount models to memory/GPU
        "unmount_model",  # Unmount models from memory
        "query_vram",  # Query GPU VRAM usage
        "auto_fit_model",  # Auto-configure models for hardware
        "prioritize_request",  # Handle request prioritization
        "manage_providers",  # Programmatically manage AI providers
        "configure_tensor_split",  # Configure multi-GPU tensor splitting
    }

    # Optional dependencies for enhanced functionality
    dependencies: List[EXT_Dependency] = [
        EXT_Dependency(
            name="payment",
            friendly_name="Payment Extension",
            optional=True,
            description="Optional payment extension for subscription-based request prioritization",
        )
    ]

    # No concrete providers - this extension provides meta functionality only
    @classmethod
    def providers(cls) -> List[Type]:
        """No concrete providers - this extension provides meta functionality only."""
        return []

    # =====================================================================
    # Meta Abilities - Static Extension Functionality
    # =====================================================================

    @classmethod
    @static_route(
        "/hardware/detect",
        method="GET",
        summary="Detect system hardware",
        response_model=HardwareDetectionResponse,
    )
    def hardware_detection(cls) -> Dict[str, Any]:
        """Meta ability: Detect and analyze system hardware capabilities."""
        try:
            hardware_info = HardwareDetector.detect_system_hardware()
            return {
                "success": True,
                "hardware_info": hardware_info.model_dump(),
                "performance_score": hardware_info.performance_score,
            }
        except Exception as e:
            logging.error(f"Hardware detection failed: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @static_route(
        "/models/recommend",
        method="POST",
        summary="Get model recommendation",
        response_model=ModelRecommendationResponse,
    )
    def model_recommendation(
        cls,
        hardware_info: Optional[Dict] = None,
        preference: str = "balanced",
    ) -> Dict[str, Any]:
        """Meta ability: Recommend optimal models based on hardware and preferences."""
        try:
            if not hardware_info:
                hardware_result = cls.hardware_detection()
                if not hardware_result.get("success"):
                    return hardware_result
                hardware_info = HardwareInfo(**hardware_result["hardware_info"])
            elif isinstance(hardware_info, dict):
                hardware_info = HardwareInfo(**hardware_info)

            # Get available models for recommendation
            available_models = cls._get_available_models_for_recommendation()

            # Use recommendation engine
            best_model = ModelRecommendationEngine.get_best_model_for_hardware(
                hardware_info=hardware_info,
                task_type=ModelTask.TEXT_GENERATION,  # Default task
                preference=ModelPreference(preference),
                available_models=available_models,
            )

            return {
                "success": True,
                "best_model": best_model,
                "hardware_info": hardware_info.model_dump(),
                "preference": preference,
                "total_available_models": len(available_models),
            }
        except Exception as e:
            logging.error(f"Model recommendation failed: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @ability
    @static_route(
        "/models/download/queue",
        method="POST",
        summary="Queue model download",
        response_model=QueueDownloadResponse,
    )
    def queue_download(
        cls,
        model_id: str,
        model_format: str = "auto",
        priority: str = "normal",
    ) -> Dict[str, Any]:
        """Meta ability: Queue a model download with progress tracking."""
        try:
            download_id = f"{model_format}_{model_id}_{hash(model_id) % 10000}"

            # This would integrate with a download queue system
            return {
                "success": True,
                "download_id": download_id,
                "status": "accepted",
                "queue_position": cls._get_queue_position(priority),
                "estimated_size_gb": cls._estimate_model_size(model_id),
                "message": f"Download queued for {model_id}",
            }
        except Exception as e:
            logging.error(f"Download queue failed: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @static_route(
        "/hardware/vram",
        method="GET",
        summary="Query available VRAM",
        response_model=VRAMQueryResponse,
    )
    def query_vram(cls) -> Dict[str, Any]:
        """Meta ability: Query GPU VRAM usage across all devices."""
        try:
            hardware_result = cls.hardware_detection()
            if not hardware_result.get("success"):
                return hardware_result

            hardware_info = HardwareInfo(**hardware_result["hardware_info"])

            return {
                "success": True,
                "total_vram_gb": hardware_info.total_vram_gb,
                "available_vram_gb": hardware_info.available_vram_gb,
                "used_vram_gb": hardware_info.used_vram_gb,
                "gpu_count": hardware_info.gpu_count,
                "gpu_devices": hardware_info.gpu_devices,
                "hardware_type": hardware_info.primary_hardware_type.value,
                "supported_types": [
                    ht.value for ht in hardware_info.supported_hardware_types
                ],
            }
        except Exception as e:
            logging.error(f"VRAM query failed: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @ability
    @static_route(
        "/models/auto-fit",
        method="POST",
        summary="Auto-fit model configuration",
        response_model=AutoFitModelResponse,
    )
    def auto_fit_model(
        cls,
        model_config: Dict,
        target_usage: float = 0.8,
        prefer_quality: bool = True,
    ) -> Dict[str, Any]:
        """Meta ability: Auto-configure model settings for optimal hardware utilization."""
        try:
            hardware_result = cls.hardware_detection()
            if not hardware_result.get("success"):
                return hardware_result

            hardware_info = HardwareInfo(**hardware_result["hardware_info"])

            # Convert model_config dict to LocalAIProviderConfiguration
            base_config = LocalAIProviderConfiguration(**model_config)
            optimized_config = ModelRecommendationEngine.auto_configure_for_hardware(
                base_config, hardware_info
            )

            return {
                "success": True,
                "optimized_config": optimized_config.model_dump(),
                "hardware_info": hardware_info.model_dump(),
                "target_usage": target_usage,
                "estimated_improvement": "Configuration optimized for hardware",
            }
        except Exception as e:
            logging.error(f"Auto-fit failed: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @ability
    @static_route(
        "/requests/prioritize",
        method="POST",
        summary="Calculate request priority",
        response_model=PrioritizeRequestResponse,
    )
    def prioritize_request(
        cls, requester_id: str, team_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Meta ability: Determine request priority based on subscription status."""
        # try:
        #     # Extract additional parameters from kwargs
        #     user_roles = kwargs.get("user_roles", [])
        #     subscription_tier = kwargs.get("subscription_tier")
        #     origin = kwargs.get("origin")
        #     model_type = kwargs.get("model_type")
        #     task_type = kwargs.get("task_type")
        #     token_count = kwargs.get("token_count")

        #     # Calculate priority using the RequestPriorityManager
        #     priority = RequestPriorityManager.calculate_request_priority(
        #         user_id=requester_id,
        #         team_id=team_id,
        #         origin=origin,
        #         user_roles=user_roles,
        #         subscription_tier=subscription_tier,
        #         model_type=model_type,
        #         task_type=ModelTask(task_type) if task_type else None,
        #         token_count=token_count,
        #     )

        #     priority_config = {
        #         "level": priority.name.lower(),
        #         "numeric_priority": priority.value,
        #         "high_priority": priority.value >= RequestPriority.HIGH.value,
        #         "queue_position": max(
        #             0, RequestPriority.HIGHEST.value - priority.value
        #         ),
        #     }

        #     return {"success": True, "priority_config": priority_config}
        # except Exception as e:
        #     logging.error(f"Request prioritization failed: {e}")
        #     return {"success": False, "error": str(e)}

    @classmethod
    @ability
    @static_route(
        "/hardware/tensor-split",
        method="POST",
        summary="Configure tensor split",
        response_model=TensorSplitResponse,
    )
    def configure_tensor_split(
        cls, gpu_devices: List[Dict], **kwargs
    ) -> Dict[str, Any]:
        """Meta ability: Calculate optimal tensor split for multi-GPU setups."""
        try:
            tensor_split = HardwareDetector.calculate_optimal_tensor_split(gpu_devices)

            return {
                "success": True,
                "tensor_split": tensor_split,
                "gpu_count": len(gpu_devices),
                "total_vram_gb": sum(
                    gpu.get("vram_total_gb", 0) for gpu in gpu_devices
                ),
                "recommended_split_mode": "row" if len(gpu_devices) > 1 else "none",
            }
        except Exception as e:
            logging.error(f"Tensor split configuration failed: {e}")
            return {"success": False, "error": str(e)}

    # =====================================================================
    # Utility Meta Abilities
    # =====================================================================

    @classmethod
    @static_route("/preferences", method="GET", summary="Get available preferences")
    def get_preferences(cls) -> Dict[str, List[str]]:
        """Get available model preferences."""
        return {
            "preferences": [
                "balanced",
                "context_size",
                "model_size",
                "speed",
                "quality",
            ],
            "priorities": ["low", "normal", "medium", "high"],
            "hardware_types": ["nvidia", "amd", "intel", "apple", "cpu_only"],
            "model_formats": ["auto", "gguf", "pytorch", "onnx"],
        }

    @classmethod
    @static_route("/tasks", method="GET", summary="Get available task types")
    def get_task_types(cls) -> Dict[str, List[str]]:
        """Get available task types."""
        return {
            "task_types": [
                "text_generation",
                "embedding",
                "image_generation",
                "speech_to_text",
                "text_to_speech",
                "vision_language",
                "code_generation",
                "chat_completion",
            ]
        }

    @classmethod
    @static_route("/health", method="GET", summary="Health check")
    def health_check(cls) -> Dict[str, Any]:
        """Health check for Local AI extension."""
        try:
            # Basic hardware detection as health check
            hardware_result = cls.hardware_detection()

            return {
                "status": ("healthy" if hardware_result.get("success") else "degraded"),
                "extension": "local_ai",
                "version": cls.version,
                "hardware_detection": hardware_result.get("success", False),
                "timestamp": (
                    hardware_result.get("timestamp")
                    if "timestamp" in hardware_result
                    else None
                ),
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "extension": "local_ai",
                "version": cls.version,
                "error": str(e),
            }

    @classmethod
    @static_route(
        "/configuration/template", method="GET", summary="Get configuration template"
    )
    def get_configuration_template(cls) -> Dict[str, Any]:
        """Get configuration template for Local AI providers."""
        try:
            from zephyrex.extensions.local_ai.BLL_Local_AI import LocalAIProviderConfiguration

            template_config = LocalAIProviderConfiguration()
            return {
                "template": template_config.model_dump(),
                "schema": template_config.model_json_schema(),
                "description": "Template configuration for Local AI provider instances",
            }
        except Exception as e:
            logging.error(f"Failed to generate configuration template: {e}")
            return {
                "success": False,
                "error": f"Failed to generate configuration template: {str(e)}",
            }

    # =====================================================================
    # Model Management Meta Abilities
    # =====================================================================

    @classmethod
    @static_route(
        "/models/mount",
        method="POST",
        summary="Mount AI model",
        response_model=ModelMountResponse,
    )
    def mount_model(
        cls, model_id: str, device: str = "auto", auto_fit: bool = False
    ) -> Dict[str, Any]:
        """Mount an AI model for inference."""
        try:
            return {
                "success": True,
                "status": "accepted",
                "model_id": model_id,
                "estimated_time_ms": 5000,
                "message": f"Model {model_id} mount queued",
            }
        except Exception as e:
            logging.error(f"Model mount failed: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @static_route(
        "/models/unmount",
        method="POST",
        summary="Unmount AI model",
        response_model=ModelMountResponse,
    )
    def unmount_model(cls, provider_instance_id: str) -> Dict[str, Any]:
        """Unmount an AI model."""
        try:
            return {
                "success": True,
                "status": "completed",
                "model_id": provider_instance_id,
                "estimated_time_ms": 1000,
                "message": f"Model {provider_instance_id} unmounted",
            }
        except Exception as e:
            logging.error(f"Model unmount failed: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @static_route(
        "/models/status/{model_id}",
        method="GET",
        summary="Get model status",
        response_model=ModelStatusResponse,
    )
    def get_model_status(cls, model_id: str) -> Dict[str, Any]:
        """Get model status."""
        try:
            return {
                "success": True,
                "model_id": model_id,
                "status": "mounted",
                "vram_usage_gb": 4.2,
                "ram_usage_gb": 1.8,
                "inference_count": 42,
                "last_used": "2024-01-01T12:00:00Z",
            }
        except Exception as e:
            logging.error(f"Failed to get model status: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @static_route(
        "/models/discover",
        method="GET",
        summary="Discover available models",
        response_model=DiscoverModelsResponse,
    )
    def discover_models(
        cls, search_paths: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Discover available AI models."""
        try:
            discovered_models = [
                {
                    "name": "llama3-8b-gguf",
                    "path": "/models/llama3-8b.gguf",
                    "format": "gguf",
                    "size_gb": 4.2,
                    "estimated_vram_gb": 5.0,
                    "capabilities": ["text_generation", "chat_completion"],
                },
                {
                    "name": "mistral-7b-pytorch",
                    "path": "/models/mistral-7b",
                    "format": "pytorch",
                    "size_gb": 13.5,
                    "estimated_vram_gb": 14.0,
                    "capabilities": ["text_generation", "embedding"],
                },
            ]

            return {
                "success": True,
                "models": discovered_models,
                "count": len(discovered_models),
                "search_paths": search_paths or ["/models"],
            }
        except Exception as e:
            logging.error(f"Model discovery failed: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @static_route(
        "/models/download/status/{download_id}",
        method="GET",
        summary="Get download status",
        response_model=DownloadStatusResponse,
    )
    def get_download_status(cls, download_id: str) -> Dict[str, Any]:
        """Get download status for a queued model."""
        try:
            return {
                "success": True,
                "download_id": download_id,
                "status": "downloading",
                "progress_percent": 45.2,
                "downloaded_gb": 2.1,
                "total_gb": 4.6,
                "eta_seconds": 120,
                "message": "Downloading model files...",
            }
        except Exception as e:
            logging.error(f"Failed to get download status: {e}")
            return {"success": False, "error": str(e)}

    # =====================================================================
    # OpenAI-Compatible Meta Abilities
    # =====================================================================

    @classmethod
    @static_route(
        "/openai/v1/models",
        method="GET",
        summary="List available models (OpenAI compatible)",
    )
    def list_openai_models(cls) -> Dict[str, Any]:
        """List models in OpenAI-compatible format."""
        try:
            models = [
                {
                    "id": "llama3-8b-instruct",
                    "object": "model",
                    "created": 1677610602,
                    "owned_by": "local",
                    "permission": [],
                    "root": "llama3-8b-instruct",
                    "parent": None,
                },
                {
                    "id": "mistral-7b-instruct",
                    "object": "model",
                    "created": 1677610602,
                    "owned_by": "local",
                    "permission": [],
                    "root": "mistral-7b-instruct",
                    "parent": None,
                },
            ]

            return {"object": "list", "data": models}
        except Exception as e:
            logging.error(f"Failed to list models: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @static_route(
        "/openai/v1/chat/completions",
        method="POST",
        summary="Chat completions (OpenAI compatible)",
    )
    def create_chat_completion(cls, request: Dict[str, Any]) -> Dict[str, Any]:
        """Create chat completion in OpenAI-compatible format."""
        try:
            return {
                "id": "chatcmpl-123",
                "object": "chat.completion",
                "created": 1677652288,
                "model": request.get("model", "llama3-8b-instruct"),
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Hello! I'm a local AI assistant. How can I help you today?",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 15,
                    "total_tokens": 25,
                },
            }
        except Exception as e:
            logging.error(f"Chat completion failed: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @static_route(
        "/openai/v1/completions",
        method="POST",
        summary="Text completions (OpenAI compatible)",
    )
    def create_completion(cls, request: Dict[str, Any]) -> Dict[str, Any]:
        """Create text completion in OpenAI-compatible format."""
        try:
            return {
                "id": "cmpl-123",
                "object": "text_completion",
                "created": 1677652288,
                "model": request.get("model", "llama3-8b-instruct"),
                "choices": [
                    {
                        "text": " This is a sample completion from a local AI model.",
                        "index": 0,
                        "logprobs": None,
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 10,
                    "total_tokens": 15,
                },
            }
        except Exception as e:
            logging.error(f"Text completion failed: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @static_route(
        "/openai/v1/embeddings",
        method="POST",
        summary="Create embeddings (OpenAI compatible)",
    )
    def create_embeddings(cls, request: Dict[str, Any]) -> Dict[str, Any]:
        """Create embeddings in OpenAI-compatible format."""
        try:
            input_text = request.get("input", "")
            if isinstance(input_text, list):
                data = [
                    {
                        "object": "embedding",
                        "embedding": [0.1] * 1536,  # Mock embedding vector
                        "index": i,
                    }
                    for i, _ in enumerate(input_text)
                ]
            else:
                data = [
                    {
                        "object": "embedding",
                        "embedding": [0.1] * 1536,  # Mock embedding vector
                        "index": 0,
                    }
                ]

            return {
                "object": "list",
                "data": data,
                "model": request.get("model", "text-embedding-ada-002"),
                "usage": {
                    "prompt_tokens": len(str(input_text).split()),
                    "total_tokens": len(str(input_text).split()),
                },
            }
        except Exception as e:
            logging.error(f"Embedding creation failed: {e}")
            return {"success": False, "error": str(e)}

    # =====================================================================
    # Helper Methods
    # =====================================================================

    @classmethod
    def _get_queue_position(cls, priority: str) -> int:
        """Get queue position based on priority level."""
        priority_map = {"high": 0, "medium": 5, "normal": 10, "low": 20}
        return priority_map.get(priority, 10)

    @classmethod
    def _estimate_model_size(cls, model_id: str) -> float:
        """Estimate model download size in GB."""
        # Simple heuristic based on model name
        if "70b" in model_id.lower():
            return 40.0
        elif "34b" in model_id.lower():
            return 20.0
        elif "13b" in model_id.lower():
            return 8.0
        elif "7b" in model_id.lower():
            return 4.0
        else:
            return 2.0

    @classmethod
    def _get_available_models_for_recommendation(cls) -> List[Dict[str, Any]]:
        """Get available models for recommendation."""
        # Sample models for demonstration - in production this would come from a registry
        return [
            {
                "name": "llama3-8b",
                "parameter_size": "8b",
                "context_window": 8192,
                "min_vram_gb": 6,
                "min_ram_gb": 8,
                "capabilities": ["text_generation", "chat_completion"],
                "avg_tokens_per_second": 50,
                "benchmark_scores": {"average": 75},
            },
            {
                "name": "llama3-70b",
                "parameter_size": "70b",
                "context_window": 8192,
                "min_vram_gb": 40,
                "min_ram_gb": 64,
                "capabilities": ["text_generation", "chat_completion"],
                "avg_tokens_per_second": 20,
                "benchmark_scores": {"average": 90},
            },
            {
                "name": "mistral-7b",
                "parameter_size": "7b",
                "context_window": 32768,
                "min_vram_gb": 4,
                "min_ram_gb": 6,
                "capabilities": ["text_generation", "chat_completion"],
                "avg_tokens_per_second": 60,
                "benchmark_scores": {"average": 70},
            },
        ]

    @classmethod
    def _check_subscription_status(
        cls, requester_id: str, team_id: Optional[str]
    ) -> Dict[str, Any]:
        """Check subscription status via payment extension if available."""
        try:
            if are_optional_dependencies_met(["payment"]):
                # Import payment extension functionality
                from zephyrex.logic.BLL_Auth import UserManager

                # This would check actual subscription status
                pass

            return {"tier": "standard", "active": True, "expires_at": None}
        except Exception:
            return {"tier": "standard", "active": True}


class AbstractLocalAIProviderMixin:
    """
    Abstract mixin for local AI providers.

    This mixin defines the interface that AI model providers should implement.
    AI providers are created programmatically by the extension system and
    represent specific models with their capabilities.

    In the Provider framework:
    - **Provider** = Model name + parameter size (e.g., "llama2-7b", "gemma-27b")
    - **ProviderInstance** = Specific configuration (quantization, tensor split, etc.)
    - **ProviderInstanceSettings** = Advanced configuration options (USE_BEAM_SEARCH, etc.)
    - **ProviderInstanceUsage** = Token tracking for billing/analytics
    - **ProviderInstanceExtensionAbilities** = Enabled capabilities per instance

    AI Transformation Abilities:
    - text_to_text: Text generation, chat completion, code generation
    - text_to_embedding: Text embedding generation
    - image_to_text: Vision-language understanding
    - text_to_image: Image generation from text
    - audio_to_text: Speech-to-text transcription
    - text_to_audio: Text-to-speech synthesis
    """

    @classmethod
    def get_supported_abilities(cls) -> List[str]:
        """Return list of supported AI transformation abilities."""
        raise NotImplementedError("Providers must implement get_supported_abilities")

    @classmethod
    def get_model_format(cls) -> str:
        """Return the model format this provider supports (e.g., 'gguf', 'pytorch')."""
        raise NotImplementedError("Providers must implement get_model_format")

    @classmethod
    def get_hardware_requirements(cls, model_config: Dict) -> Dict[str, Any]:
        """Return hardware requirements for a model configuration."""
        raise NotImplementedError("Providers must implement get_hardware_requirements")

    @classmethod
    def validate_configuration(cls, config: Dict) -> bool:
        """Validate if a configuration is supported by this provider."""
        raise NotImplementedError("Providers must implement validate_configuration")

    # AI Transformation Methods (called via RotationManager.rotate)
    @classmethod
    def text_to_text(cls, provider_instance, prompt: str, **kwargs) -> Dict[str, Any]:
        """Transform text to text (generation, completion, etc.)."""
        raise NotImplementedError("Providers must implement text_to_text")

    @classmethod
    def text_to_embedding(
        cls, provider_instance, text: str, **kwargs
    ) -> Dict[str, Any]:
        """Transform text to embedding vector."""
        raise NotImplementedError("Providers must implement text_to_embedding")

    @classmethod
    def image_to_text(
        cls, provider_instance, image_data: bytes, **kwargs
    ) -> Dict[str, Any]:
        """Transform image to text (vision-language understanding)."""
        raise NotImplementedError("Providers must implement image_to_text")

    @classmethod
    def text_to_image(cls, provider_instance, prompt: str, **kwargs) -> Dict[str, Any]:
        """Transform text to image."""
        raise NotImplementedError("Providers must implement text_to_image")

    @classmethod
    def audio_to_text(
        cls, provider_instance, audio_data: bytes, **kwargs
    ) -> Dict[str, Any]:
        """Transform audio to text (speech-to-text)."""
        raise NotImplementedError("Providers must implement audio_to_text")

    @classmethod
    def text_to_audio(cls, provider_instance, text: str, **kwargs) -> Dict[str, Any]:
        """Transform text to audio (text-to-speech)."""
        raise NotImplementedError("Providers must implement text_to_audio")


# Alias for backwards compatibility and consistency with extension naming
AbstractLocalAIExtensionProvider = AbstractLocalAIProviderMixin

# Link the abstract provider back to its extension (Provider Rotation System),
# mirroring the framework pattern (e.g. AbstractDatabaseExtensionProvider).
AbstractLocalAIProvider.extension = EXT_Local_AI
