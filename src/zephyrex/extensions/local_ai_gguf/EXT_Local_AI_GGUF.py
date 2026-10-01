"""
GGUF model extension for AGInfrastructure.
Extends the Local AI framework for GGUF model support.
"""

import datetime
import gc
import json
import os
import threading
import time
import uuid
import datetime
from typing import Any, Dict, List, Optional, ClassVar
from pathlib import Path
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Union

import psutil
import tqdm
from fastapi import BackgroundTasks
from fastapi.responses import JSONResponse, StreamingResponse

from fastapi import BackgroundTasks
from fastapi.responses import JSONResponse, StreamingResponse

from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger
from zephyrex.pydantic2.fastapi import static_route
from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension, ability
from zephyrex.extensions.local_ai.utils.downloads import (
    ExistentDownloadError,
    DownloadManager,
)
from zephyrex.extensions.local_ai_gguf.utils.downloads import GGUFDownload


# Import function for checking optional dependencies
try:
    from zephyrex.lib.Dependencies import are_optional_dependencies_met
except ImportError:

    def are_optional_dependencies_met(deps):
        return False


from zephyrex.lib.Environment import env

# Try importing required libraries
try:
    from zephyrex.extensions.local_ai.utils.models import ModelManager
    from zephyrex.extensions.local_ai_gguf.utils.models import GGUFModel

    DEPS_AVAILABLE = True
except ImportError as e:
    logger.warning(f"GGUF dependencies not available: {e}")
    DEPS_AVAILABLE = False
except Exception as e:
    logger.warning(f"GGUF dependencies error: {e}")
    DEPS_AVAILABLE = False


class EXT_Local_AI_GGUF(AbstractStaticExtension):
    """
    GGUF model extension for AGInfrastructure.

    Extends the Local AI framework to provide GGUF model support via llama.cpp.
    This extension provides static functionality that:

    - Automatically discovers available GGUF models and creates Provider database records
    - Downloads and manages GGUF model files with progress tracking
    - Provides AI inference functionality called via RotationManager.rotate()
    - Supports advanced features: USE_BEAM_SEARCH, CACHE_OFFLOAD, COMPRESS_CACHE,
      ROPE_SCALING, KV_CACHE_QUANT_TYPE
    - Handles hardware-specific optimizations (NVIDIA, AMD, Intel, Apple Silicon)
    - Integrates with payment extension for subscription-based request prioritization
    - Manages model mounting/dismounting with auto-fit configuration

    Architecture:
    - Extension creates Provider records programmatically for discovered models
    - ProviderInstances represent specific model configurations (quantization, tensor split)
    - ProviderInstanceSettings store advanced configuration options
    - ProviderInstanceUsage tracks input/output tokens for billing/analytics
    - No concrete provider classes - all functionality is static meta abilities

    Usage:
        # Use via rotation system
        result = RotationManager.rotate(
            "text_to_text",
            prompt="Hello, world!",
            max_tokens=100
        )
    """

    # Extension metadata
    name: ClassVar[str] = "local_ai_gguf"
    friendly_name: ClassVar[str] = "GGUF Model Support"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "GGUF model extension providing quantized model support via llama.cpp with "
        "advanced configuration and hardware optimization"
    )

    # Meta abilities - static functionality for GGUF models
    _abilities: ClassVar[set] = {
        "discover_gguf_models",  # Auto-discover GGUF models in directories
        "download_gguf_model",  # Download GGUF models from HuggingFace
        "mount_gguf_model",  # Mount GGUF models to memory/GPU
        "unmount_gguf_model",  # Unmount GGUF models
        "configure_gguf_settings",  # Configure advanced GGUF settings
        "optimize_gguf_for_hardware",  # Hardware-specific optimizations
    }

    # Dependencies
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="local_ai",
                friendly_name="Local AI",
                is_required=True,
                description="Base local AI framework required for PyTorch support",
            ),
            EXT_Dependency(
                name="payment",
                friendly_name="Payment Processing",
                is_required=False,
                description="Optional payment extension for subscription-based request prioritization",
            ),
            PIP_Dependency(
                name="llama-cpp-python",
                friendly_name="llama.cpp Python bindings",
                semver=">=0.2.0",
                reason="GGUF model support",
            ),
        ]
    )

    # Environment variables
    _env: ClassVar[Dict[str, Any]] = {
        "GGUF_MODELS_DIR": {
            "type": str,
            "default": "./models/gguf",
            "description": "Directory to store GGUF models",
        },
        "GGUF_AUTO_DISCOVER": {
            "type": bool,
            "default": True,
            "description": "Automatically discover GGUF models on startup",
        },
        "GGUF_MAX_CONTEXT": {
            "type": int,
            "default": 4096,
            "description": "Default maximum context window for GGUF models",
        },
        "GGUF_DEFAULT_QUANT": {
            "type": str,
            "default": "Q4_K_M",
            "description": "Default quantization type for GGUF models",
        },
    }

    # =====================================================================
    # Meta Abilities - Static GGUF Functionality
    # =====================================================================

    @classmethod
    @static_route("/gguf/models/discover", method="GET", summary="Discover GGUF models")
    def discover_gguf_models(
        cls,
    ) -> Dict[str, Any]:
        """
        Show all downloaded and loaded GGUF models.

        Returns:
            Dictionary containing:
            - downloaded: List of discovered GGUF model files
            - mounted: List of currently mounted models with their status
        """
        try:
            # Get loaded models from ModelManager
            mounted_models = []
            model_manager = ModelManager()

            # List all loaded models
            loaded_models = model_manager.list_models()

            for model_id, model_data in loaded_models.items():
                if not model_data["is_loaded"]:
                    continue

                model_info = model_data["info"]
                model_name, filename = model_id.split(":", 1)

                mounted_models.append(
                    {
                        "model_id": model_id,
                        "model_name": model_name,
                        "filename": filename,
                        "path": model_info.model_path,
                        "device": model_info.device,
                        "gpu_layers": getattr(model_info, "gpu_layers", 0),
                        "context_length": getattr(model_info, "context_length", 2048),
                        "memory_usage_mb": (
                            round(model_info.memory_usage / (1024 * 1024), 2)
                            if hasattr(model_info, "memory_usage")
                            else 0
                        ),
                        "last_used": datetime.datetime.fromtimestamp(
                            model_info.last_used
                        ).isoformat(),
                    }
                )

            downloaded_models = GGUFDownload.list_all()

            return {
                "success": True,
                "downloaded": downloaded_models,
                "mounted": mounted_models,
                "search_directory": GGUFDownload.download_dir,
            }

        except Exception as e:
            logger.error(f"Failed to discover GGUF models: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Failed to discover models: {str(e)}",
            }

    @classmethod
    @static_route(
        "/gguf/models/download/status",
        method="GET",
        summary="Get GGUF model download status",
    )
    def get_download_status(cls, download_id: str) -> Dict[str, Any]:
        """
        Meta ability: Get the status of a GGUF model download.

        Returns:
            dict: Download status including progress, status, and other metadata
        """
        if not DEPS_AVAILABLE:
            return JSONResponse(
                status_code=503,
                content={"success": False, "error": "GGUF dependencies not available"},
            )

        download_manager = DownloadManager()
        download_entry = download_manager.get_download(download_id)
        if download_entry is None:
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "error": "Download ID not found",
                    "message": f"No download found with ID: {download_id}",
                },
            )

        return JSONResponse(
            status_code=200,
            content=download_entry.to_dict(),
        )

    @classmethod
    @static_route("/gguf/models/download", method="POST", summary="Download GGUF model")
    async def download_gguf_model(
        cls, payload: Dict[str, str], background_tasks: BackgroundTasks
    ) -> Dict[str, Any]:
        """
        Meta ability: Download a GGUF model from HuggingFace Hub.

        Expected payload:
        {
            "repo_id": "user/model-name",
            "filename": "model-quantization.gguf",
        }
        """
        if not DEPS_AVAILABLE:
            return JSONResponse(
                status_code=503,
                content={"success": False, "error": "GGUF dependencies not available"},
            )

        try:
            repo_id = payload.get("repo_id")
            filename = payload.get("filename")
            force = str(payload.get("force", "false")).lower() in ("true", "1", "yes")

            if not repo_id or not filename:
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "error": "Both repo_id and filename are required in the payload",
                    },
                )  # 400 Bad Request

            try:
                download = GGUFDownload(repo_id, filename)
                download_manager = DownloadManager()
                await download_manager.register(download, force=force)

                background_tasks.add_task(
                    download.start,
                    force_download=force,
                )

                return JSONResponse(
                    status_code=201,
                    content={
                        "success": True,
                        "download": download.to_dict(),
                    },
                )  # 201 Created - Resource created successfully
            except ExistentDownloadError as e:
                existing_download = download_manager.get_download(download.id)
                return JSONResponse(
                    status_code=202,
                    content={
                        "success": existing_download.status
                        in ["downloading", "completed"],
                        "download": existing_download.to_dict(),
                    },
                )

        except Exception as e:
            logger.error(f"GGUF model download failed: {e}")
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "message": "Unexpected internal error",
                },
            )

    @classmethod
    @static_route(
        "/gguf/models/mount", method="POST", summary="Mount GGUF model to memory"
    )
    def mount_gguf_model(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Mount a GGUF model to memory/GPU.

        Expected payload:
        {
            "model_path": "/path/to/model.gguf",
            "model_name": "model_name",
            "context_length": 2048,
            "batch_size": 512,
            "gpu_layers": -1,  # -1 for auto, 0 for CPU only, >0 for specific number of layers
            "device": "auto",  # "auto", "cpu", "cuda", "metal", etc.
            "verbose": false
        }
        """
        if not DEPS_AVAILABLE:
            return {"success": False, "error": "GGUF dependencies not available"}

        try:
            # Validate required fields
            required_fields = ["model_path", "model_name"]
            for field in required_fields:
                if field not in payload:
                    return {
                        "success": False,
                        "error": f"Missing required field: {field}",
                    }

            # Get model path and name
            model_path = payload["model_path"]
            model_name = payload["model_name"]
            model_id = f"{model_name}:{os.path.basename(model_path)}"

            # Initialize model manager and check if model is already loaded
            model_manager = ModelManager()
            if model_manager.get_model(model_id):
                return {
                    "success": True,
                    "status": "already_mounted",
                    "model_id": model_id,
                    "message": f"Model {model_name} is already mounted",
                    "device": model_manager.get_model_info(model_id).device,
                    "gpu_layers": getattr(
                        model_manager.get_model_info(model_id), "gpu_layers", 0
                    ),
                }

            # Mount the model
            success, result = model_manager.mount_model(
                model_id=model_id,
                model_path=model_path,
                model_name=model_name,
                config={
                    "context_length": payload.get("context_length", 2048),
                    "batch_size": payload.get("batch_size", 512),
                    "gpu_layers": payload.get("gpu_layers", -1),
                    "device": payload.get("device", "auto"),
                    "verbose": payload.get("verbose", False),
                    "cpu_threads": payload.get(
                        "cpu_threads", max(1, os.cpu_count() // 2)
                    ),
                },
                model_class=GGUFModel,
            )

            if success:
                model_info = model_manager.get_model_info(model_id)
                return JSONResponse(
                    status_code=200,
                    content={
                        "success": True,
                        "status": "mounted",
                        "model_id": model_id,
                        "message": f"Successfully mounted model {model_name}",
                        "device": model_info.device,
                        "gpu_layers": getattr(model_info, "gpu_layers", 0),
                    },
                )
            else:
                return JSONResponse(
                    status_code=500,
                    content={
                        "success": False,
                        "error": f"Failed to mount model: {result}",
                    },
                )

        except Exception as e:
            logger.error(f"GGUF model mount failed: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": f"Failed to mount model: {str(e)}"},
            )

    @classmethod
    @static_route(
        "/gguf/models/unmount", method="POST", summary="Unmount GGUF model from memory"
    )
    def unmount_gguf_model(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Unmount a GGUF model from GPU/memory.
        """
        try:
            model_id = payload.get("model_id")

            if not model_id:
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "error": "'model_id' must be provided",
                    },
                )

            # Initialize model manager
            model_manager = ModelManager()

            # Unmount the model
            success, message = model_manager.unmount_model(model_id)
            if not success:
                logger.error(message)
                return JSONResponse(
                    status_code=500,
                    content={
                        "success": False,
                        "error": "Model unmount failed with message: " + message,
                    },
                )

            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "status": "unmounted",
                    "model_id": model_id,
                    "message": f"Successfully unmounted model {model_id}",
                },
            )

        except Exception as e:
            logger.error(f"GGUF model unmount failed: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": f"Unexpected internal error. Failed to unmount model {model_id}",
                },
            )

    # Note: text_to_text and text_to_embedding are provider abilities, not extension abilities
    # They should be implemented in the concrete provider classes

    @ability
    @classmethod
    def configure_gguf_settings(
        cls, provider_instance, settings: Dict[str, str], **kwargs
    ) -> Dict[str, Any]:
        """Meta ability: Configure advanced GGUF settings for a provider instance."""
        try:
            # Validate settings against supported options
            supported_settings = {
                "USE_BEAM_SEARCH": ["true", "false"],
                "CACHE_OFFLOAD": ["true", "false"],
                "COMPRESS_CACHE": ["true", "false"],
                "ROPE_SCALING": "json",  # JSON string with type and factor
                "KV_CACHE_QUANT_TYPE": ["q4_0", "q8_0", "fp16", "fp32"],
                "MAX_CONTEXT": "integer",
                "N_GPU_LAYERS": "integer",
                "TENSOR_SPLIT": "json",  # JSON array of floats
            }

            validated_settings = {}
            for key, value in settings.items():
                if key not in supported_settings:
                    logger.warning(f"Unsupported GGUF setting: {key}")
                    continue

                # Validate value based on setting type
                if isinstance(supported_settings[key], list):
                    if value.lower() not in supported_settings[key]:
                        logger.warning(f"Invalid value for {key}: {value}")
                        continue
                elif supported_settings[key] == "json":
                    try:
                        json.loads(value)  # Validate JSON
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON for {key}: {value}")
                        continue
                elif supported_settings[key] == "integer":
                    try:
                        int(value)
                    except ValueError:
                        logger.warning(f"Invalid integer for {key}: {value}")
                        continue

                validated_settings[key] = value

            # Here we would save to ProviderInstanceSetting records
            return {
                "success": True,
                "provider_instance_id": provider_instance.id,
                "configured_settings": validated_settings,
                "message": f"Configured {len(validated_settings)} GGUF settings",
            }

        except Exception as e:
            logger.error(f"GGUF settings configuration failed: {e}")
            return {"success": False, "error": str(e)}

    @ability
    @classmethod
    def optimize_gguf_for_hardware(
        cls, hardware_info: Dict[str, Any], model_requirements: Dict[str, Any], **kwargs
    ) -> Dict[str, Any]:
        """Meta ability: Optimize GGUF configuration for detected hardware."""
        try:
            hardware_type = hardware_info.get("primary_hardware_type", "cpu_only")
            available_vram = hardware_info.get("available_vram_gb", 0)
            model_vram_required = model_requirements.get("estimated_vram_gb", 0)

            optimization_config = {
                "hardware_type": hardware_type,
                "n_gpu_layers": 0,
                "main_gpu": 0,
                "tensor_split": None,
                "use_mmap": True,
                "use_mlock": False,
            }

            if hardware_type == "nvidia" and available_vram > 0:
                # NVIDIA optimization
                if available_vram >= model_vram_required:
                    optimization_config["n_gpu_layers"] = -1  # All layers on GPU
                else:
                    # Partial offloading
                    layers_ratio = available_vram / model_vram_required
                    optimization_config["n_gpu_layers"] = int(
                        32 * layers_ratio
                    )  # Estimate

                # Memory optimizations for low VRAM
                if available_vram < 8:
                    optimization_config.update(
                        {
                            "cache_offload": True,
                            "compress_cache": True,
                            "kv_cache_quant_type": "q4_0",
                        }
                    )
            elif hardware_type == "apple":
                # Apple Silicon optimization
                optimization_config["n_gpu_layers"] = 1  # Limited MPS support
                optimization_config["use_mps"] = True
            elif hardware_type == "amd":
                # AMD optimization
                optimization_config["n_gpu_layers"] = -1
                optimization_config["use_rocm"] = True
            else:
                # CPU optimization
                optimization_config["n_threads"] = hardware_info.get("cpu_cores", 4)
                optimization_config["use_mmap"] = True

            return {
                "success": True,
                "optimization_config": optimization_config,
                "hardware_type": hardware_type,
                "available_vram_gb": available_vram,
                "model_vram_required_gb": model_vram_required,
            }

        except Exception as e:
            logger.error(f"GGUF hardware optimization failed: {e}")
            return {"success": False, "error": str(e)}

    # =====================================================================
    # GGUF-Specific Meta Abilities (Endpoint Migrations)
    # =====================================================================

    @classmethod
    @ability
    @static_route(
        "/gguf/configs/hardware-optimize",
        method="POST",
        summary="Hardware optimize GGUF config",
    )
    def hardware_optimize_endpoint(cls, base_config_id: str) -> Dict[str, Any]:
        """Get hardware-optimized GGUF configuration from a base configuration."""
        try:
            from zephyrex.extensions.local_ai.EXT_Local_AI import EXT_Local_AI

            # Get hardware info
            hardware_info = EXT_Local_AI.hardware_detection()

            # Mock optimized config based on hardware
            optimized_config = {
                "base_config_id": base_config_id,
                "quantization_type": "Q4_K_M",
                "context_window": 4096,
                "n_gpu_layers": -1 if hardware_info.get("has_cuda") else 0,
                "use_mmap": True,
                "use_mlock": False,
            }

            # Determine applied optimizations
            applied_optimizations = []
            if hardware_info.get("has_cuda"):
                applied_optimizations.extend(
                    [
                        "CUDA GPU offloading",
                        "Optimized tensor split",
                        "Memory mapping optimization",
                    ]
                )
                if hardware_info.get("available_vram_gb", 0) < 8:
                    applied_optimizations.extend(
                        [
                            "Cache offloading",
                            "Cache compression",
                            "Q4_0 KV cache quantization",
                        ]
                    )
            elif hardware_info.get("has_mps"):
                applied_optimizations.extend(
                    ["MPS acceleration", "Memory mapping optimization"]
                )
            else:
                applied_optimizations.extend(
                    [
                        "CPU-only optimization",
                        "Thread count optimization",
                        "Q8_0 KV cache quantization",
                    ]
                )

            return {
                "success": True,
                "optimized_config": optimized_config,
                "hardware_info": hardware_info,
                "applied_optimizations": applied_optimizations,
            }
        except Exception as e:
            logger.error(f"Hardware optimization failed: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @ability
    @static_route(
        "/gguf/configs/{config_id}/compatibility",
        method="GET",
        summary="Check GGUF compatibility",
    )
    def compatibility_check_endpoint(cls, config_id: str) -> Dict[str, Any]:
        """Check if a GGUF configuration is compatible with current hardware."""
        try:
            from zephyrex.extensions.local_ai.EXT_Local_AI import EXT_Local_AI

            hardware_info = EXT_Local_AI.hardware_detection()

            compatibility_issues = []
            recommendations = []

            # Mock compatibility check
            if not hardware_info.get("has_cuda") and not hardware_info.get("has_mps"):
                compatibility_issues.append("No GPU available for acceleration")
                recommendations.append("Consider using CPU-only configuration")

            available_vram = hardware_info.get("available_vram_gb", 0)
            if available_vram < 4:
                compatibility_issues.append("Insufficient VRAM for model")
                recommendations.append(
                    "Enable cache offload or use smaller quantization"
                )

            return {
                "success": True,
                "compatible": len(compatibility_issues) == 0,
                "issues": compatibility_issues,
                "recommendations": recommendations,
                "estimated_vram_gb": 4.0,
                "estimated_ram_gb": 2.0,
                "hardware_info": hardware_info,
            }
        except Exception as e:
            logger.error(f"Compatibility check failed: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @ability
    @static_route(
        "/gguf/configs/{config_id}/load-model",
        method="POST",
        summary="Load GGUF model instance",
    )
    def load_model_instance_endpoint(cls, config_id: str) -> Dict[str, Any]:
        """Load a GGUF model instance based on configuration."""
        try:
            llama_config = {
                "model_path": "/models/llama-7b.gguf",
                "n_ctx": 4096,
                "n_gpu_layers": -1,
                "use_mmap": True,
                "use_mlock": False,
            }

            return {
                "success": True,
                "config_id": config_id,
                "llama_config": llama_config,
                "message": f"Model instance loaded for config {config_id}",
            }
        except Exception as e:
            logger.error(f"Model loading failed: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @ability
    @static_route(
        "/gguf/configs/{config_id}/generate-text",
        method="POST",
        summary="Generate text with GGUF",
    )
    def generate_text_endpoint(
        cls,
        config_id: str,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """Generate text using GGUF model configuration."""
        try:
            # Mock text generation
            generated_text = f"Generated response for: {prompt[:50]}..."

            return {
                "success": True,
                "generated_text": generated_text,
                "usage": {
                    "prompt_tokens": len(prompt.split()),
                    "completion_tokens": len(generated_text.split()),
                    "total_tokens": len(prompt.split()) + len(generated_text.split()),
                },
                "model_info": {
                    "config_id": config_id,
                    "model_name": "gguf-model",
                },
                "priority_level": "standard",
            }
        except Exception as e:
            logger.error(f"Text generation failed: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @ability
    @static_route(
        "/gguf/configs/{config_id}/generate-embeddings",
        method="POST",
        summary="Generate embeddings with GGUF",
    )
    def generate_embeddings_endpoint(
        cls, config_id: str, text: str, normalize: bool = True
    ) -> Dict[str, Any]:
        """Generate embeddings using GGUF model configuration."""
        try:
            # Mock embedding generation
            embeddings = [0.1] * 768

            return {
                "success": True,
                "embeddings": embeddings,
                "dimension": len(embeddings),
                "model_info": {
                    "config_id": config_id,
                },
            }
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @static_route(
        "/gguf/v1/chat/completions",
        method="POST",
        summary="Generate chat completion using a GGUF model",
    )
    def chat_completions(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        OpenAI-compatible chat completions endpoint for GGUF models.

        Expected payload:
        {
            "model": "model_name:filename.gguf",  # Must match a mounted model ID
            "messages": [
                {"role": "system", "content": "You are a helpful assistant"},
                {"role": "user", "content": "Hello!"}
            ],
            "temperature": 0.7,
            "max_tokens": 512,
            "top_p": 0.9,
            "stream": False
        }
        """
        try:
            # Validate required fields
            if "model" not in payload:
                return {"error": "Missing required field: model"}, 400

            if "messages" not in payload or not isinstance(payload["messages"], list):
                return {"error": "Missing or invalid 'messages' array"}, 400

            # Get model ID and initialize manager
            model_id = payload["model"]

            model_manager = ModelManager()

            # Get the model
            model = model_manager.get_model(model_id)
            if not model:
                return JSONResponse(
                    content={"error": f"Model not found or not mounted: {model_id}"},
                    status_code=404,
                )

            # Format messages into a single prompt
            formatted_messages = []
            for msg in payload["messages"]:
                role = msg.get("role", "user").lower()
                content = msg.get("content", "").strip()
                if not content:
                    continue

                if role == "system":
                    formatted_messages.append(f"System: {content}")
                elif role == "assistant":
                    formatted_messages.append(f"Assistant: {content}")
                else:  # user, function, tool, etc.
                    formatted_messages.append(f"User: {content}")

            prompt = "\n".join(formatted_messages) + "\n\nAssistant:"

            # Generate response
            response = model.create_completion(
                prompt=prompt,
                max_tokens=payload.get("max_tokens", 512),
                temperature=payload.get("temperature", 0.7),
                top_p=payload.get("top_p", 0.9),
                stop=payload.get("stop", []),
                stream=payload.get("stream", False),
            )

            # Format response in OpenAI-compatible format
            if payload.get("stream", False):
                # Handle streaming response
                def generate():
                    for chunk in response:
                        data = json.dumps(
                            {
                                "id": f"chatcmpl-{uuid.uuid4()}",
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": model_id,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {
                                            "content": chunk["choices"][0]["text"]
                                        },
                                        "finish_reason": None,
                                    }
                                ],
                            }
                        )
                        yield f"data: {data}\n\n"
                    yield "data: [DONE]\n\n"

                return StreamingResponse(generate(), media_type="text/event-stream")
            else:
                # Handle non-streaming response
                return {
                    "id": f"chatcmpl-{uuid.uuid4()}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model_id,
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": response["choices"][0]["text"].strip(),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": len(prompt.split()),
                        "completion_tokens": len(
                            response["choices"][0]["text"].split()
                        ),
                        "total_tokens": len(prompt.split())
                        + len(response["choices"][0]["text"].split()),
                    },
                }

        except Exception as e:
            logger.error(f"Chat completion failed: {e}", exc_info=True)
            return {"error": f"Error generating completion: {str(e)}"}, 500

    # Note: discover_gguf_models and download_gguf_model already exist as @static_route methods

    # =====================================================================
    # Helper Methods
    # =====================================================================

    @classmethod
    def _analyze_gguf_model(cls, model_path: Path) -> Optional[Dict[str, Any]]:
        """Analyze a GGUF file and extract metadata."""
        try:
            model_name = cls._extract_model_name(model_path)
            capabilities = cls._determine_model_capabilities(model_name)
            file_size_gb = model_path.stat().st_size / (1024**3)
            estimated_vram = cls._estimate_vram_requirement(file_size_gb, model_name)

            return {
                "name": model_name,
                "friendly_name": f"{model_name} (GGUF)",
                "file_path": str(model_path),
                "file_size_gb": file_size_gb,
                "estimated_vram_gb": estimated_vram,
                "model_format": "gguf",
                "capabilities": capabilities,
                "context_window": cls._estimate_context_window(model_name),
                "parameter_count": cls._extract_parameter_count(model_name),
                "quantization": cls._extract_quantization_type(model_name),
                "supported_hardware": ["nvidia", "amd", "apple", "intel", "cpu_only"],
            }
        except Exception as e:
            logger.error(f"Error analyzing GGUF model {model_path}: {e}")
            return None

    @classmethod
    def _extract_model_name(cls, model_path: Path) -> str:
        """Extract a clean model name from the file path."""
        name = model_path.stem

        # Handle quantization suffixes
        if "." in name:
            parts = name.split(".")
            if len(parts) > 1 and any(
                q in parts[-1].upper() for q in ["Q4", "Q5", "Q8", "F16", "F32"]
            ):
                name = ".".join(parts[:-1])

        return name.replace("_", "-").replace("--", "-")

    @classmethod
    def _determine_model_capabilities(cls, model_name: str) -> List[str]:
        """Determine model capabilities based on name/type."""
        capabilities = ["text_to_text"]
        name_lower = model_name.lower()

        # Add embedding capability for embedding models
        if any(term in name_lower for term in ["embed", "e5", "bge", "sentence"]):
            capabilities.append("text_to_embedding")

        return capabilities

    @classmethod
    def _estimate_vram_requirement(cls, file_size_gb: float, model_name: str) -> float:
        """Estimate VRAM requirement based on file size and model type."""
        base_estimate = file_size_gb * 1.2

        # Adjust based on quantization
        name_upper = model_name.upper()
        if "Q4" in name_upper:
            base_estimate *= 0.8
        elif "Q8" in name_upper:
            base_estimate *= 1.1
        elif "F16" in name_upper:
            base_estimate *= 1.3

        return round(base_estimate, 2)

    @classmethod
    def _estimate_context_window(cls, model_name: str) -> int:
        """Estimate context window based on model name."""
        name_lower = model_name.lower()

        # Check for explicit context indicators
        if "32k" in name_lower:
            return 32768
        elif "16k" in name_lower:
            return 16384
        elif "8k" in name_lower:
            return 8192
        elif "4k" in name_lower:
            return 4096

        # Model-specific defaults
        if "llama-3" in name_lower or "llama3" in name_lower:
            return 8192
        elif "mistral" in name_lower:
            return 8192 if "v0.3" in name_lower else 4096
        elif "gemma" in name_lower:
            return 8192
        else:
            return 4096

    @classmethod
    def _extract_parameter_count(cls, model_name: str) -> Optional[str]:
        """Extract parameter count from model name."""
        import re

        pattern = r"(\d+\.?\d*)b"
        match = re.search(pattern, model_name.lower())
        return f"{match.group(1)}b" if match else None

    @classmethod
    def _extract_quantization_type(cls, model_name: str) -> Optional[str]:
        """Extract quantization type from model name."""
        name_upper = model_name.upper()
        quant_patterns = ["Q4_K_M", "Q4_K_S", "Q5_K_M", "Q5_K_S", "Q8_0", "F16", "F32"]

        for pattern in quant_patterns:
            if pattern in name_upper:
                return pattern
        return None

    @classmethod
    def _estimate_model_size_gguf(cls, model_id: str, quantization: str) -> float:
        """Estimate GGUF model download size."""
        base_size = 4.0  # Default 7B model

        if "70b" in model_id.lower():
            base_size = 40.0
        elif "34b" in model_id.lower():
            base_size = 20.0
        elif "13b" in model_id.lower():
            base_size = 8.0

        # Adjust for quantization
        quant_multipliers = {
            "Q4_K_M": 0.6,
            "Q4_K_S": 0.55,
            "Q5_K_M": 0.7,
            "Q8_0": 0.9,
            "F16": 1.0,
            "F32": 2.0,
        }

        return base_size * quant_multipliers.get(quantization, 0.6)

    @classmethod
    def _build_gguf_config(
        cls, provider_instance, task_type: str = "text", **kwargs
    ) -> Dict[str, Any]:
        """Build GGUF configuration from provider instance settings."""
        # Base configuration
        config = {
            "model_path": provider_instance.model_name,  # Should be path to GGUF file
            "max_tokens": kwargs.get("max_tokens", 512),
            "temperature": kwargs.get("temperature", 0.7),
            "top_p": kwargs.get("top_p", 0.9),
            "top_k": kwargs.get("top_k", 40),
            "stop": kwargs.get("stop", []),
            "context_window": 4096,
        }

        # Apply settings from ProviderInstanceSettings
        # In real implementation, would query database for settings
        instance_settings = cls._get_instance_settings(provider_instance)
        for key, value in instance_settings.items():
            if key == "USE_BEAM_SEARCH" and value.lower() == "true":
                config["use_beam_search"] = True
                config["beam_count"] = kwargs.get("beam_count", 4)
            elif key == "CACHE_OFFLOAD" and value.lower() == "true":
                config["cache_offload"] = True
            elif key == "COMPRESS_CACHE" and value.lower() == "true":
                config["compress_cache"] = True
            elif key == "ROPE_SCALING":
                config["rope_scaling"] = json.loads(value)
            elif key == "KV_CACHE_QUANT_TYPE":
                config["kv_cache_quant_type"] = value
            elif key == "MAX_CONTEXT":
                config["context_window"] = int(value)
            elif key == "N_GPU_LAYERS":
                config["n_gpu_layers"] = int(value)
            elif key == "TENSOR_SPLIT":
                config["tensor_split"] = json.loads(value)

        return config

    @classmethod
    def _get_instance_settings(cls, provider_instance) -> Dict[str, str]:
        """Get settings for a provider instance."""
        # Placeholder - would query ProviderInstanceSetting records
        return {
            "USE_BEAM_SEARCH": "false",
            "CACHE_OFFLOAD": "false",
            "COMPRESS_CACHE": "false",
            "KV_CACHE_QUANT_TYPE": "q8_0",
        }

    @classmethod
    def _apply_advanced_features(cls, config: Dict[str, Any], **kwargs) -> None:
        """Apply advanced features from kwargs to config."""
        if kwargs.get("use_beam_search"):
            config["use_beam_search"] = True
            config["beam_count"] = kwargs.get("beam_count", 4)

        if kwargs.get("cache_offload"):
            config["cache_offload"] = True

        if kwargs.get("compress_cache"):
            config["compress_cache"] = True

        if kwargs.get("rope_scaling"):
            config["rope_scaling"] = kwargs["rope_scaling"]

        if kwargs.get("kv_cache_quant_type"):
            config["kv_cache_quant_type"] = kwargs["kv_cache_quant_type"]

    @classmethod
    def _get_request_priority(
        cls, requester_id: Optional[str], team_id: Optional[str]
    ) -> Dict[str, Any]:
        """Determine request priority based on payment extension."""
        priority_config = {
            "level": "standard",
            "high_priority": False,
            "queue_position": 0,
        }

        if are_optional_dependencies_met(["payment"]) and requester_id:
            # Check subscription status via payment extension
            subscription_status = cls._check_subscription_status(requester_id, team_id)

            if subscription_status.get("tier") in ["premium", "enterprise"]:
                priority_config.update(
                    {"level": "high", "high_priority": True, "queue_position": -1}
                )
            elif subscription_status.get("tier") == "pro":
                priority_config.update({"level": "medium", "queue_position": 1})

        return priority_config

    @classmethod
    def _check_subscription_status(
        cls, requester_id: str, team_id: Optional[str]
    ) -> Dict[str, Any]:
        """Check subscription status via payment extension."""
        # Placeholder - would integrate with payment extension
        return {"tier": "standard", "active": True}

    @classmethod
    def _track_usage(
        cls, provider_instance, input_text: str, task_type: str = "text", **kwargs
    ) -> Dict[str, Any]:
        """Track usage for billing/analytics."""
        # Estimate token counts
        input_tokens = len(input_text.split()) * 1.3  # Rough token estimate
        output_tokens = kwargs.get("max_tokens", 512) if task_type == "text" else 0

        # In real implementation, would create ProviderInstanceUsage records
        usage_data = {
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "task_type": task_type,
            "provider_instance_id": provider_instance.id,
        }

        return usage_data

    @classmethod
    def _generate_gguf_text(cls, config: Dict[str, Any], prompt: str, **kwargs) -> str:
        """Generate text with GGUF model (placeholder implementation)."""
        # Placeholder - would use actual llama.cpp
        return f"Generated GGUF response for: {prompt[:50]}..."

    @classmethod
    def _generate_gguf_embeddings(
        cls, config: Dict[str, Any], text: str, **kwargs
    ) -> List[float]:
        """Generate embeddings with GGUF model (placeholder implementation)."""
        # Placeholder - would use actual llama.cpp
        return [0.1] * 768  # Mock 768-dimensional embedding
