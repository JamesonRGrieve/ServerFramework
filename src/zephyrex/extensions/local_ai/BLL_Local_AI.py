"""
Business Logic Layer for Local AI Extension.

This module provides utility functions and managers that work with the
Provider Rotation System for local AI model operations. Instead of defining
custom models, it leverages the existing Provider framework.

Architecture:
- Uses ProviderInstanceModel for storing model configurations
- Uses ProviderInstanceSettingModel for advanced configuration options
- Uses standard rotation system for failover and load balancing
- Provides utilities for hardware detection and model optimization
"""

import json
import logging
import platform
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set

try:
    import psutil
except ImportError:
    psutil = None
    import warnings

    warnings.warn(
        "psutil package currently missing, but in PIP_Dependencies, will likely install on run",
        ImportWarning,
    )

from pydantic import BaseModel, Field, computed_field
from sqlalchemy.orm import Session

from zephyrex.logic.BLL_Providers import (
    ProviderInstanceModel,
    ProviderInstanceSettingModel,
    ProviderInstanceUsageModel,
)


# Enums for Local AI operations
class ModelStatus(str, Enum):
    """Status of AI models."""

    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    MOUNTING = "mounting"
    MOUNTED = "mounted"
    UNMOUNTING = "unmounting"
    ERROR = "error"


class HardwareType(str, Enum):
    """Supported hardware types."""

    NVIDIA = "nvidia"
    AMD = "amd"
    INTEL = "intel"
    APPLE = "apple"
    CPU_ONLY = "cpu_only"


class ModelTask(str, Enum):
    """AI model task types."""

    TEXT_GENERATION = "text_generation"
    EMBEDDING = "embedding"
    IMAGE_GENERATION = "image_generation"
    SPEECH_TO_TEXT = "speech_to_text"
    TEXT_TO_SPEECH = "text_to_speech"
    VISION_LANGUAGE = "vision_language"
    CODE_GENERATION = "code_generation"
    CHAT_COMPLETION = "chat_completion"


class RequestPriority(int, Enum):
    """Priority levels for inference requests."""

    LOWEST = 1
    LOW = 2
    NORMAL = 3
    HIGH = 4
    HIGHEST = 5
    PREMIUM = 6
    SYSTEM = 7


class ModelPreference(str, Enum):
    """Model selection preferences."""

    CONTEXT_SIZE = "context_size"
    MODEL_SIZE = "model_size"
    SPEED = "speed"
    QUALITY = "quality"
    BALANCED = "balanced"


# Configuration Models for Provider Settings
class LocalAIProviderConfiguration(BaseModel):
    """Configuration model for Local AI provider instances."""

    # Core model settings
    model_path: Optional[str] = Field(
        None, description="Path to model file or HuggingFace model ID"
    )
    context_size: Optional[int] = Field(
        None, description="Context window size override"
    )

    # Beam search configuration
    use_beam_search: bool = Field(False, description="Enable beam search decoding")
    beam_width: int = Field(4, description="Number of beams for beam search")
    length_penalty: float = Field(1.0, description="Length penalty for beam search")

    # Cache configuration
    cache_offload: bool = Field(False, description="Offload cache to system memory")
    compress_cache: bool = Field(False, description="Enable cache compression")
    cache_compression_ratio: float = Field(0.5, description="Cache compression ratio")

    # RoPE scaling configuration
    rope_scaling: Optional[Dict[str, Any]] = Field(
        None, description="RoPE scaling configuration"
    )
    rope_base: float = Field(10000.0, description="RoPE base frequency")
    rope_scale: float = Field(1.0, description="RoPE scale factor")

    # KV cache quantization
    kv_cache_quant_type: Optional[str] = Field(
        None, description="KV cache quantization type"
    )
    kv_cache_precision: str = Field("fp16", description="KV cache precision")

    # Hardware configuration
    tensor_split: Optional[List[float]] = Field(
        None, description="Tensor split configuration"
    )
    n_gpu_layers: int = Field(-1, description="Number of layers to offload to GPU")
    main_gpu: int = Field(0, description="Primary GPU device ID")
    split_mode: str = Field("row", description="Tensor split mode")

    # Memory optimization
    use_mmap: bool = Field(True, description="Use memory mapping for model files")
    use_mlock: bool = Field(False, description="Lock model in memory")
    numa: bool = Field(False, description="Enable NUMA optimization")
    low_vram: bool = Field(False, description="Enable low VRAM mode")

    # Generation parameters
    temperature: float = Field(0.8, description="Sampling temperature")
    top_p: float = Field(0.95, description="Nucleus sampling probability")
    top_k: int = Field(40, description="Top-k sampling")
    repeat_penalty: float = Field(1.1, description="Repetition penalty")

    # Batch configuration
    batch_size: int = Field(512, description="Batch size for processing")
    ubatch_size: int = Field(512, description="Micro-batch size")

    # Auto-configuration
    auto_fit: bool = Field(False, description="Automatically fit to available hardware")
    prefer_context_size: bool = Field(
        False, description="Prioritize context size over model size"
    )
    prefer_model_size: bool = Field(
        False, description="Prioritize model size over context size"
    )

    def to_provider_settings(self) -> Dict[str, str]:
        """Convert to ProviderInstanceSettingModel format."""
        settings = {}
        for key, value in self.model_dump().items():
            if value is not None:
                if isinstance(value, (dict, list)):
                    settings[key] = json.dumps(value)
                else:
                    settings[key] = str(value)
        return settings

    @classmethod
    def from_provider_settings(
        cls, settings: Dict[str, str]
    ) -> "LocalAIProviderConfiguration":
        """Create from ProviderInstanceSettingModel format."""
        config_data = {}

        for key, value in settings.items():
            if key in cls.model_fields:
                field_info = cls.model_fields[key]
                field_type = field_info.annotation

                try:
                    # Handle different field types
                    if field_type == bool:
                        config_data[key] = value.lower() in ("true", "1", "yes", "on")
                    elif field_type == int:
                        config_data[key] = int(value)
                    elif field_type == float:
                        config_data[key] = float(value)
                    elif field_type in (Dict[str, Any], List[float]):
                        config_data[key] = json.loads(value)
                    else:
                        config_data[key] = value
                except (ValueError, json.JSONDecodeError):
                    logging.warning(f"Failed to parse setting {key}={value}")

        return cls(**config_data)


class HardwareInfo(BaseModel):
    """Hardware information model."""

    system_id: str = Field(..., description="System identifier")
    hostname: str = Field(..., description="System hostname")

    # CPU information
    cpu_count: int = Field(..., description="Number of CPU cores")
    cpu_name: str = Field(..., description="CPU model name")
    cpu_usage_percent: float = Field(0.0, description="Current CPU usage")

    # Memory information
    total_ram_gb: float = Field(..., description="Total system RAM in GB")
    available_ram_gb: float = Field(..., description="Available system RAM in GB")
    used_ram_gb: float = Field(..., description="Used system RAM in GB")

    # GPU information
    gpu_count: int = Field(0, description="Number of GPUs detected")
    gpu_devices: List[Dict[str, Any]] = Field(
        default_factory=list, description="GPU device information"
    )
    total_vram_gb: float = Field(0.0, description="Total VRAM across all GPUs")
    available_vram_gb: float = Field(0.0, description="Available VRAM")
    used_vram_gb: float = Field(0.0, description="Used VRAM")

    # Hardware capabilities
    has_cuda: bool = Field(False, description="NVIDIA CUDA availability")
    has_mps: bool = Field(False, description="Apple Metal Performance Shaders")
    has_opencl: bool = Field(False, description="OpenCL availability")
    has_rocm: bool = Field(False, description="AMD ROCm availability")
    has_intel_gpu: bool = Field(False, description="Intel GPU availability")

    # Hardware type detection
    primary_hardware_type: HardwareType = Field(
        HardwareType.CPU_ONLY, description="Primary hardware type"
    )
    supported_hardware_types: List[HardwareType] = Field(
        default_factory=list, description="Supported hardware types"
    )

    @computed_field
    @property
    def performance_score(self) -> float:
        """Calculate overall hardware performance score."""
        score = 0.0

        # GPU contribution (70%)
        if self.has_cuda or self.has_mps or self.has_rocm:
            gpu_score = min(100, (self.total_vram_gb / 24) * 100)  # 24GB is excellent
            score += gpu_score * 0.7
        elif self.has_opencl or self.has_intel_gpu:
            score += 30 * 0.7  # Basic GPU support
        else:
            score += 10 * 0.7  # CPU only

        # RAM contribution (20%)
        ram_score = min(100, (self.total_ram_gb / 64) * 100)  # 64GB is excellent
        score += ram_score * 0.2

        # CPU contribution (10%)
        cpu_score = min(100, (self.cpu_count / 16) * 100)  # 16 cores is excellent
        score += cpu_score * 0.1

        return round(score, 1)


# Hardware Detection Utilities
class HardwareDetector:
    """Utility class for detecting and analyzing hardware capabilities."""

    @staticmethod
    def detect_system_hardware() -> HardwareInfo:
        """Detect current system hardware capabilities."""
        import subprocess

        # Basic system info
        hardware_data = {
            "system_id": platform.node(),
            "hostname": platform.node(),
            "cpu_count": psutil.cpu_count(),
            "cpu_name": platform.processor(),
            "cpu_usage_percent": psutil.cpu_percent(),
            "total_ram_gb": psutil.virtual_memory().total / (1024**3),
            "available_ram_gb": psutil.virtual_memory().available / (1024**3),
            "used_ram_gb": psutil.virtual_memory().used / (1024**3),
            "gpu_count": 0,
            "gpu_devices": [],
            "total_vram_gb": 0.0,
            "available_vram_gb": 0.0,
            "used_vram_gb": 0.0,
            "has_cuda": False,
            "has_mps": False,
            "has_opencl": False,
            "has_rocm": False,
            "has_intel_gpu": False,
            "primary_hardware_type": HardwareType.CPU_ONLY,
            "supported_hardware_types": [HardwareType.CPU_ONLY],
        }

        # Detect GPU capabilities
        try:
            # NVIDIA CUDA detection
            try:
                result = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=name,memory.total,memory.free",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    hardware_data["has_cuda"] = True
                    hardware_data["primary_hardware_type"] = HardwareType.NVIDIA

                    gpu_devices = []
                    total_vram = 0
                    available_vram = 0

                    for line in result.stdout.strip().split("\n"):
                        if line.strip():
                            parts = line.split(", ")
                            if len(parts) >= 3:
                                name = parts[0].strip()
                                total_mem = float(parts[1]) / 1024  # Convert to GB
                                free_mem = float(parts[2]) / 1024  # Convert to GB

                                gpu_devices.append(
                                    {
                                        "name": name,
                                        "vram_total_gb": total_mem,
                                        "vram_free_gb": free_mem,
                                        "vram_used_gb": total_mem - free_mem,
                                        "device_id": len(gpu_devices),
                                    }
                                )

                                total_vram += total_mem
                                available_vram += free_mem

                    hardware_data["gpu_count"] = len(gpu_devices)
                    hardware_data["gpu_devices"] = gpu_devices
                    hardware_data["total_vram_gb"] = total_vram
                    hardware_data["available_vram_gb"] = available_vram
                    hardware_data["used_vram_gb"] = total_vram - available_vram
                    hardware_data["supported_hardware_types"].append(
                        HardwareType.NVIDIA
                    )
            except Exception:
                pass

            # Apple Metal Performance Shaders detection
            if platform.system() == "Darwin":
                try:
                    import Metal

                    hardware_data["has_mps"] = True
                    hardware_data["supported_hardware_types"].append(HardwareType.APPLE)
                    if not hardware_data["has_cuda"]:
                        hardware_data["primary_hardware_type"] = HardwareType.APPLE
                except ImportError:
                    pass

            # AMD ROCm detection
            try:
                result = subprocess.run(
                    ["rocm-smi", "--showproductname"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    hardware_data["has_rocm"] = True
                    hardware_data["supported_hardware_types"].append(HardwareType.AMD)
                    if not hardware_data["has_cuda"] and not hardware_data["has_mps"]:
                        hardware_data["primary_hardware_type"] = HardwareType.AMD
            except Exception:
                pass

            # Intel GPU detection
            try:
                if platform.system() == "Linux":
                    result = subprocess.run(
                        ["lspci"], capture_output=True, text=True, timeout=5
                    )
                    if (
                        result.returncode == 0
                        and "intel" in result.stdout.lower()
                        and "vga" in result.stdout.lower()
                    ):
                        hardware_data["has_intel_gpu"] = True
                        hardware_data["supported_hardware_types"].append(
                            HardwareType.INTEL
                        )
                        if (
                            not hardware_data["has_cuda"]
                            and not hardware_data["has_mps"]
                            and not hardware_data["has_rocm"]
                        ):
                            hardware_data["primary_hardware_type"] = HardwareType.INTEL
            except Exception:
                pass

            # OpenCL detection
            try:
                import pyopencl as cl

                platforms = cl.get_platforms()
                if platforms:
                    hardware_data["has_opencl"] = True
            except ImportError:
                pass

        except Exception as e:
            logging.warning(f"Error detecting GPU capabilities: {e}")

        return HardwareInfo(**hardware_data)

    @staticmethod
    def calculate_optimal_tensor_split(
        gpu_devices: List[Dict[str, Any]],
    ) -> List[float]:
        """Calculate optimal tensor split based on GPU VRAM."""
        if len(gpu_devices) <= 1:
            return [1.0]

        # Calculate split based on VRAM ratios
        total_vram = sum(gpu.get("vram_total_gb", 0) for gpu in gpu_devices)
        if total_vram == 0:
            return [1.0 / len(gpu_devices)] * len(gpu_devices)

        splits = []
        for gpu in gpu_devices:
            vram_ratio = gpu.get("vram_total_gb", 0) / total_vram
            splits.append(round(vram_ratio, 3))

        # Normalize to ensure sum equals 1.0
        split_sum = sum(splits)
        if split_sum > 0:
            splits = [s / split_sum for s in splits]

        return splits


# Model Recommendation Engine
class ModelRecommendationEngine:
    """Engine for recommending optimal models based on hardware and preferences."""

    @staticmethod
    def get_best_model_for_hardware(
        hardware_info: HardwareInfo,
        task_type: ModelTask,
        preference: ModelPreference = ModelPreference.BALANCED,
        available_models: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get the best model for the given criteria."""
        if not available_models:
            return None

        # Filter by hardware compatibility
        compatible_models = []
        for model in available_models:
            min_vram = model.get("min_vram_gb", 0)
            min_ram = model.get("min_ram_gb", 0)

            if (
                min_vram <= hardware_info.available_vram_gb
                and min_ram <= hardware_info.available_ram_gb
                and task_type.value in model.get("capabilities", [])
            ):
                compatible_models.append(model)

        if not compatible_models:
            return None

        # Sort by preference
        if preference == ModelPreference.CONTEXT_SIZE:
            compatible_models.sort(
                key=lambda m: m.get("context_window", 0), reverse=True
            )
        elif preference == ModelPreference.MODEL_SIZE:
            compatible_models.sort(
                key=lambda m: float(m.get("parameter_size", "0b").replace("b", "")),
                reverse=True,
            )
        elif preference == ModelPreference.SPEED:
            compatible_models.sort(
                key=lambda m: m.get("avg_tokens_per_second", 0), reverse=True
            )
        elif preference == ModelPreference.QUALITY:
            compatible_models.sort(
                key=lambda m: m.get("benchmark_scores", {}).get("average", 0),
                reverse=True,
            )
        else:  # BALANCED
            # Score based on multiple factors
            for model in compatible_models:
                score = 0
                score += (
                    model.get("context_window", 0) / 32768
                ) * 25  # Context contribution
                score += (
                    float(model.get("parameter_size", "0b").replace("b", "")) / 70
                ) * 25  # Size contribution
                score += (
                    model.get("avg_tokens_per_second", 0) / 100
                ) * 25  # Speed contribution
                score += (
                    model.get("benchmark_scores", {}).get("average", 0) / 100
                ) * 25  # Quality contribution
                model["_balance_score"] = score

            compatible_models.sort(
                key=lambda m: m.get("_balance_score", 0), reverse=True
            )

        return compatible_models[0]

    @staticmethod
    def auto_configure_for_hardware(
        base_config: LocalAIProviderConfiguration, hardware_info: HardwareInfo
    ) -> LocalAIProviderConfiguration:
        """Automatically configure model settings for optimal hardware utilization."""
        config = base_config.model_copy()

        if hardware_info.available_vram_gb > 0 and hardware_info.gpu_count > 0:
            # GPU configuration
            config.n_gpu_layers = -1  # Offload all layers by default
            config.main_gpu = 0

            if hardware_info.gpu_count > 1:
                # Multi-GPU tensor split
                tensor_split = HardwareDetector.calculate_optimal_tensor_split(
                    hardware_info.gpu_devices
                )
                config.tensor_split = tensor_split
                config.split_mode = "row"

            # Memory optimization based on VRAM
            if hardware_info.available_vram_gb < 8:
                config.low_vram = True
                config.cache_offload = True
                config.compress_cache = True
                config.kv_cache_quant_type = "q4_0"
            elif hardware_info.available_vram_gb < 16:
                config.cache_offload = False
                config.compress_cache = True
                config.kv_cache_quant_type = "q8_0"
            else:
                config.cache_offload = False
                config.compress_cache = False
                config.kv_cache_precision = "fp16"

        else:
            # CPU-only configuration
            config.n_gpu_layers = 0
            config.use_mmap = True
            config.use_mlock = False
            config.numa = True

        return config


# Request Priority Management
class RequestPriorityManager:
    """Manager for handling request prioritization with optional subscription integration."""

    @staticmethod
    def calculate_request_priority(
        user_id: Optional[str] = None,
        team_id: Optional[str] = None,
        origin: Optional[str] = None,
        user_roles: Optional[List[str]] = None,
        subscription_tier: Optional[str] = None,
        model_type: Optional[str] = None,
        task_type: Optional[ModelTask] = None,
        token_count: Optional[int] = None,
    ) -> RequestPriority:
        """Calculate request priority based on multiple factors."""
        priority = RequestPriority.NORMAL

        # Check if payment extension is available for subscription-based prioritization
        try:
            # Try to import payment extension (optional dependency)
            from zephyrex.extensions.payment.BLL_Payment import PaymentExtensionManager

            if subscription_tier:
                subscription_priority_map = {
                    "enterprise": RequestPriority.PREMIUM,
                    "pro": RequestPriority.HIGH,
                    "basic": RequestPriority.NORMAL,
                    "free": RequestPriority.LOW,
                }
                priority = subscription_priority_map.get(
                    subscription_tier, RequestPriority.NORMAL
                )
        except ImportError:
            # Payment extension not available, use role-based prioritization
            if user_roles:
                if "admin" in user_roles or "system" in user_roles:
                    priority = RequestPriority.SYSTEM
                elif "premium" in user_roles or "enterprise" in user_roles:
                    priority = RequestPriority.PREMIUM
                elif "pro" in user_roles:
                    priority = RequestPriority.HIGH

        # Adjust based on task complexity
        if task_type in [ModelTask.IMAGE_GENERATION, ModelTask.VISION_LANGUAGE]:
            # Complex tasks get slightly lower priority
            priority = RequestPriority(max(1, priority.value - 1))
        elif task_type in [ModelTask.TEXT_GENERATION, ModelTask.CHAT_COMPLETION]:
            # Standard text tasks keep current priority
            pass

        # Adjust based on token count
        if token_count and token_count > 8192:
            # Very long requests get lower priority
            priority = RequestPriority(max(1, priority.value - 1))

        return priority


# Provider Instance Helper Functions
class LocalAIProviderInstanceHelper:
    """Helper functions for working with ProviderInstance models for Local AI."""

    @staticmethod
    def create_provider_instance_configuration(
        provider_id: str,
        model_name: str,
        user_id: str,
        team_id: Optional[str] = None,
        config: Optional[LocalAIProviderConfiguration] = None,
        db: Optional[Session] = None,
    ) -> ProviderInstanceModel:
        """Create a new provider instance with Local AI configuration."""
        # Use default configuration if none provided
        if config is None:
            config = LocalAIProviderConfiguration()

        # Create the provider instance
        instance_data = {
            "provider_id": provider_id,
            "name": f"{model_name}_{user_id}",
            "model_name": model_name,
            "user_id": user_id,
            "team_id": team_id,
        }

        # This would typically be done through the Provider framework
        # For now, showing the structure
        return instance_data

    @staticmethod
    def update_provider_instance_settings(
        instance_id: str,
        config: LocalAIProviderConfiguration,
        db: Optional[Session] = None,
    ) -> bool:
        """Update provider instance settings."""
        try:
            settings = config.to_provider_settings()

            # This would update ProviderInstanceSettingModel entries
            # Implementation depends on existing provider framework

            return True
        except Exception as e:
            logging.error(f"Failed to update provider instance settings: {e}")
            return False

    @staticmethod
    def get_provider_instance_configuration(
        instance_id: str, db: Optional[Session] = None
    ) -> Optional[LocalAIProviderConfiguration]:
        """Get provider instance configuration."""
        try:
            # This would query ProviderInstanceSettingModel entries
            # and convert them back to LocalAIProviderConfiguration

            # Placeholder implementation
            settings = {}  # Would come from database
            return LocalAIProviderConfiguration.from_provider_settings(settings)
        except Exception as e:
            logging.error(f"Failed to get provider instance configuration: {e}")
            return None
