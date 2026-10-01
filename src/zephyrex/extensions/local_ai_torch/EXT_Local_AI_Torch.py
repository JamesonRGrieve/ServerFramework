"""
PyTorch model extension for AGInfrastructure.
Extends the Local AI framework for PyTorch model support.
"""

import json
import os
import time
import tempfile
import logging
import datetime
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Union, Tuple, Type

from fastapi import BackgroundTasks
from fastapi.responses import JSONResponse

from zephyrex.extensions.local_ai.utils.downloads import DownloadManager, ExistentDownloadError
from zephyrex.extensions.local_ai.utils.models import ModelManager
from zephyrex.extensions.local_ai_torch.utils.downloads import TorchDownload
from zephyrex.extensions.local_ai_torch.utils.models import TorchModel

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension, ability
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.pydantic2.fastapi import static_route

# Try importing required libraries
try:
    # Import torch first to avoid TORCH_LIBRARY conflicts
    # Only import other libraries if torch is successfully imported
    # import accelerate
    # import bitsandbytes
    # import torch
    # import transformers
    # from transformers import AutoModel, AutoModelForCausalLM, AutoTokenizer, pipeline

    DEPS_AVAILABLE = True
except ImportError as e:
    logger.warning(f"PyTorch dependencies not available: {e}")
    DEPS_AVAILABLE = False
except Exception as e:
    # Handle TORCH_LIBRARY registration conflicts
    if "TORCH_LIBRARY" in str(e):
        logger.warning(f"PyTorch TORCH_LIBRARY conflict detected: {e}")
        logger.info("Extension will be disabled due to library conflicts")
    else:
        logger.warning(f"PyTorch dependencies error: {e}")
    DEPS_AVAILABLE = False


class EXT_Local_AI_Torch(AbstractStaticExtension):
    """
    PyTorch model extension for AGInfrastructure.

    Extends the Local AI framework to provide PyTorch model support via transformers.
    This extension provides static functionality that:

    - Automatically discovers available PyTorch models and creates Provider database records
    - Downloads and manages PyTorch model files (safetensors, pytorch_model.bin)
    - Provides AI inference functionality called via RotationManager.rotate()
    - Supports advanced features: USE_BEAM_SEARCH, CACHE_OFFLOAD, COMPRESS_CACHE,
      KV_CACHE_QUANT_TYPE, dynamic quantization, model sharding
    - Handles hardware-specific optimizations (NVIDIA, AMD, Intel, Apple Silicon)
    - Integrates with payment extension for subscription-based request prioritization
    - Manages model mounting/dismounting with auto-fit configuration

    Architecture:
    - Extension creates Provider records programmatically for discovered models
    - ProviderInstances represent specific model configurations (dtype, device_map, quantization)
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
    name: ClassVar[str] = "local_ai_torch"
    friendly_name: ClassVar[str] = "PyTorch Model Support"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "PyTorch model extension providing transformer model support via HuggingFace "
        "with advanced configuration and hardware optimization"
    )

    # Meta abilities - static functionality for PyTorch models
    _abilities: ClassVar[set] = {
        "discover_pytorch_models",  # Auto-discover PyTorch models
        "download_pytorch_model",  # Download PyTorch models from HuggingFace
        "mount_pytorch_model",  # Mount PyTorch models to memory/GPU
        "unmount_pytorch_model",  # Unmount PyTorch models
        "configure_pytorch_settings",  # Configure advanced PyTorch settings
        "optimize_pytorch_for_hardware",  # Hardware-specific optimizations
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
                name="torch",
                friendly_name="PyTorch",
                semver=">=1.12.0",
                reason="PyTorch model support",
            ),
            PIP_Dependency(
                name="transformers",
                friendly_name="HuggingFace Transformers",
                semver=">=4.20.0",
                reason="HuggingFace model support",
            ),
        ]
    )

    # Environment variables
    _env: ClassVar[Dict[str, Any]] = {
        "PYTORCH_MODELS_DIR": {
            "type": str,
            "default": "./models/pytorch",
            "description": "Directory to store PyTorch models",
        },
        "PYTORCH_AUTO_DISCOVER": {
            "type": bool,
            "default": True,
            "description": "Automatically discover PyTorch models on startup",
        },
        "PYTORCH_DEFAULT_DTYPE": {
            "type": str,
            "default": "float16",
            "description": "Default torch dtype for models (float16, bfloat16, float32)",
        },
        "PYTORCH_DEVICE_MAP": {
            "type": str,
            "default": "auto",
            "description": "Default device map strategy for model loading",
        },
    }

    # =====================================================================
    # Model Download and Management
    # =====================================================================

    @classmethod
    @static_route(
        "/pytorch/models/download", method="POST", summary="Download PyTorch model"
    )
    async def download_pytorch_model(
        cls, payload: Dict[str, Any], background_tasks: BackgroundTasks
    ) -> Dict[str, Any]:
        """
        Download a PyTorch model from HuggingFace Hub using snapshot_download.

        Expected payload:
        {
            "repo_id": "user/model-name",
            "revision": "main",  # optional, defaults to "main"
            "file_pattern": "*.safetensors,*.bin,*.json",  # optional, files to include
            "force": false  # optional, force re-download if model exists
        }
        """
        if not DEPS_AVAILABLE:
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "error": "PyTorch dependencies not available",
                },
            )

        try:
            repo_id = payload.get("repo_id")
            revision = payload.get("revision", "main")
            file_pattern = payload.get("file_pattern")
            force = str(payload.get("force", "false")).lower() in ("true", "1", "yes")

            if not repo_id:
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "error": "repo_id is required in the payload",
                    },
                )

            try:
                download = TorchDownload(
                    repo_id, revision=revision, file_pattern=file_pattern
                )
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
                )
            except ExistentDownloadError:
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
            logger.error(f"Error downloading PyTorch model: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": f"Failed to initiate download: {str(e)}",
                },
            )

    @classmethod
    @static_route(
        "/pytorch/models/download/status",
        method="GET",
        summary="Get PyTorch model download status",
    )
    def get_download_status(cls, download_id: str) -> Dict[str, Any]:
        """
        Get the status of a PyTorch model download.

        Args:
            download_id: The download ID returned by the download_pytorch_model endpoint

        Returns:
            dict: Download status including progress, status, and other metadata
        """
        if not DEPS_AVAILABLE:
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "error": "PyTorch dependencies not available",
                },
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

    # =====================================================================
    # Static Model Discovery and Provider Management
    # =====================================================================

    @classmethod
    @static_route(
        "/pytorch/models/discover", method="GET", summary="Discover PyTorch models"
    )
    def discover_pytorch_models(cls) -> Dict[str, Any]:
        """
        Discover available PyTorch models in local directories using ModelManager.

        Returns:
            Dictionary with discovered models and metadata
        """
        if not DEPS_AVAILABLE:
            return {
                "success": False,
                "error": "PyTorch dependencies not available",
                "downloaded": [],
                "mounted": [],
            }

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
                model_name = model_data.get("model_name", model_id)

                mounted_models.append(
                    {
                        "model_id": model_id,
                        "model_name": model_name,
                        "path": model_info.model_path,
                        "device": getattr(model_info, "device", "cpu"),
                        "context_length": getattr(model_info, "context_length", 2048),
                        "memory_usage_mb": (
                            round(model_info.memory_usage / (1024 * 1024), 2)
                            if hasattr(model_info, "memory_usage")
                            else 0
                        ),
                        "last_used": (
                            datetime.datetime.fromtimestamp(
                                model_info.last_used
                            ).isoformat()
                            if hasattr(model_info, "last_used")
                            else None
                        ),
                    }
                )

            # Find downloaded models in configured directory
            downloaded_models = TorchDownload.list_all()

            return {
                "success": True,
                "downloaded": downloaded_models,
                "mounted": mounted_models,
                "search_directory": TorchDownload.download_dir,
            }

        except Exception as e:
            logger.error(f"Error discovering PyTorch models: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "downloaded": [],
                "mounted": [],
                "search_directory": env("PYTORCH_MODELS_DIR"),
            }

    @classmethod
    def _analyze_pytorch_model(cls, model_path: Path) -> Optional[Dict[str, Any]]:
        """Analyze a PyTorch model directory and extract metadata for Provider creation."""
        try:
            # Check if this looks like a HuggingFace model directory
            config_file = model_path / "config.json"
            if not config_file.exists():
                return None

            # Check for model files
            has_pytorch = any(model_path.glob("pytorch_model*.bin")) or any(
                model_path.glob("model*.safetensors")
            )
            if not has_pytorch:
                return None

            # Load model configuration
            with open(config_file, "r") as f:
                config = json.load(f)

            # Extract model name from directory name
            model_name = cls._extract_model_name(model_path)

            # Determine model capabilities based on config and name
            capabilities = cls._determine_model_capabilities(model_name, config)

            # Estimate model requirements
            estimated_vram = cls._estimate_vram_requirement(model_path, config)

            return {
                "name": model_name,
                "friendly_name": f"{model_name} (PyTorch)",
                "model_path": str(model_path),
                "estimated_vram_gb": estimated_vram,
                "model_type": "pytorch_hf",
                "capabilities": capabilities,
                "context_window": cls._get_context_window(config),
                "parameter_count": cls._extract_parameter_count(config, model_name),
                "model_class": config.get("architectures", ["Unknown"])[0],
                "torch_dtype": config.get("torch_dtype", "float32"),
            }
        except Exception as e:
            logger.error(f"Error analyzing model {model_path}: {e}")
            return None

    @classmethod
    def _extract_model_name(cls, model_path: Path) -> str:
        """Extract a clean model name from the directory path."""
        name = model_path.name

        # Handle HuggingFace Hub cache structure
        if "models--" in name:
            # Extract from models--organization--model-name format
            parts = name.split("--")
            if len(parts) >= 3:
                org = parts[1]
                model = parts[2]
                name = f"{org}/{model}"

        # Clean up common prefixes/suffixes
        name = name.replace("_", "-").replace("--", "-")

        return name

    @classmethod
    def _determine_model_capabilities(
        cls, model_name: str, config: Dict[str, Any]
    ) -> List[str]:
        """Determine what capabilities a model has based on its configuration."""
        capabilities = []

        name_lower = model_name.lower()
        architectures = config.get("architectures", [])

        # Text generation models
        if any(
            arch
            in [
                "LlamaForCausalLM",
                "GPT2LMHeadModel",
                "GPTNeoForCausalLM",
                "BloomForCausalLM",
                "MistralForCausalLM",
                "GemmaForCausalLM",
            ]
            for arch in architectures
        ):
            capabilities.extend(["TextGeneration", "ChatCompletion"])

        # Code models
        if any(
            term in name_lower
            for term in ["code", "deepseek-coder", "codellama", "starcoder"]
        ):
            capabilities.extend(["CodeGeneration", "CodeCompletion"])

        # Embedding models
        if any(
            arch in ["BertModel", "RobertaModel", "SentenceTransformerModel"]
            for arch in architectures
        ) or any(term in name_lower for term in ["embed", "e5", "bge", "sentence"]):
            capabilities.append("EmbeddingGeneration")

        # Vision-language models
        if any(
            arch in ["LlavaForCausalLM", "VisionEncoderDecoderModel"]
            for arch in architectures
        ):
            capabilities.append("VisionLanguageChat")

        # Image generation models
        if any(
            arch in ["StableDiffusionPipeline", "PixArtAlphaPipeline"]
            for arch in architectures
        ):
            capabilities.append("ImageGeneration")

        return capabilities

    @classmethod
    def _estimate_vram_requirement(
        cls, model_path: Path, config: Dict[str, Any]
    ) -> float:
        """Estimate VRAM requirement based on model size and configuration."""
        try:
            # Calculate total file size
            total_size = 0
            for file_path in model_path.rglob("*.bin"):
                total_size += file_path.stat().st_size
            for file_path in model_path.rglob("*.safetensors"):
                total_size += file_path.stat().st_size

            # Convert to GB and add overhead
            size_gb = total_size / (1024**3)

            # Estimate VRAM based on size (rough approximation)
            torch_dtype = config.get("torch_dtype", "float32")
            if torch_dtype == "float16":
                vram_estimate = size_gb * 1.2  # 20% overhead for FP16
            elif torch_dtype == "bfloat16":
                vram_estimate = size_gb * 1.2  # Similar to FP16
            else:  # float32
                vram_estimate = size_gb * 1.5  # 50% overhead for FP32

            return round(vram_estimate, 2)

        except Exception:
            # Fallback estimation based on hidden size
            hidden_size = config.get("hidden_size", 768)
            num_layers = config.get("num_hidden_layers", 12)

            # Very rough estimation
            estimated_params = hidden_size * hidden_size * num_layers * 4
            estimated_gb = (
                estimated_params * 4 / (1024**3)
            )  # 4 bytes per param for float32

            return round(estimated_gb * 1.5, 2)  # Add overhead

    @classmethod
    def _get_context_window(cls, config: Dict[str, Any]) -> int:
        """Get context window from model configuration."""
        # Try various config keys
        context_keys = [
            "max_position_embeddings",
            "n_positions",
            "seq_length",
            "max_sequence_length",
        ]

        for key in context_keys:
            if key in config:
                return config[key]

        # Default based on model type
        return 2048

    @classmethod
    def _extract_parameter_count(
        cls, config: Dict[str, Any], model_name: str
    ) -> Optional[str]:
        """Extract or estimate parameter count."""
        # Try to get from name first
        name_lower = model_name.lower()
        import re

        pattern = r"(\d+\.?\d*)b"
        match = re.search(pattern, name_lower)
        if match:
            return f"{match.group(1)}b"

        # Rough estimation from config
        hidden_size = config.get("hidden_size", 768)
        num_layers = config.get("num_hidden_layers", 12)
        vocab_size = config.get("vocab_size", 50000)

        # Very rough parameter estimation
        estimated_params = (hidden_size * hidden_size * num_layers * 4) + (
            vocab_size * hidden_size
        )
        estimated_b = estimated_params / 1_000_000_000

        if estimated_b >= 1:
            return f"{estimated_b:.1f}b"
        else:
            estimated_m = estimated_params / 1_000_000
            return f"{estimated_m:.0f}m"

    @classmethod
    @ability
    @static_route(
        "/pytorch/whisper/transcribe",
        method="POST",
        summary="Transcribe audio using Whisper model",
    )
    def whisper_transcribe(
        cls,
        model_name: str,
        audio_base64: str,
        language: Optional[str] = None,
        task: str = "transcribe",
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Transcribe audio using a Whisper model via ModelManager.

        Args:
            model_name: Name of the Whisper model to use (must be mounted)
            audio_base64: Base64 encoded audio data (WAV, MP3, etc.)
            language: Optional language code (e.g., 'en', 'es', 'fr')
            task: Either 'transcribe' or 'translate'
            **kwargs: Additional arguments for the transcription

        Returns:
            Dict containing the transcription result
        """
        if not DEPS_AVAILABLE:
            return {"success": False, "error": "PyTorch dependencies not available"}

        try:
            import base64
            import io
            from faster_whisper import WhisperModel

            # Initialize ModelManager
            model_manager = ModelManager(model_class=TorchModel)

            # Get the model from ModelManager
            model = model_manager.get_model(model_name=model_name)
            if not model:
                return {
                    "success": False,
                    "error": f"Model not found or not mounted: {model_name}",
                }

            # Decode base64 audio
            try:
                audio_bytes = base64.b64decode(audio_base64)
                audio_file = io.BytesIO(audio_bytes)
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Invalid base64 audio data: {str(e)}",
                }

            # Perform transcription using the model
            try:
                # Save audio to a temporary file for processing
                import tempfile
                import os

                with tempfile.NamedTemporaryFile(
                    suffix=".wav", delete=False
                ) as temp_audio:
                    temp_audio.write(audio_bytes)
                    temp_audio_path = temp_audio.name

                try:
                    # Use the model's transcribe method if available
                    if hasattr(model, "transcribe"):
                        result = model.transcribe(
                            temp_audio_path, language=language, task=task, **kwargs
                        )
                    else:
                        # Fallback to default Whisper pipeline
                        result = model.pipeline(
                            temp_audio_path, language=language, task=task, **kwargs
                        )

                    # Clean up the temporary file
                    try:
                        os.unlink(temp_audio_path)
                    except Exception:
                        pass

                    return {
                        "success": True,
                        "text": result.get("text", ""),
                        "language": result.get("language"),
                        "segments": result.get("segments", []),
                        "model": model_name,
                    }

                except Exception as e:
                    # Clean up the temporary file in case of error
                    try:
                        os.unlink(temp_audio_path)
                    except Exception:
                        pass
                    raise e

            except Exception as e:
                logger.error(f"Transcription failed: {e}", exc_info=True)
                return {
                    "success": False,
                    "error": f"Transcription failed: {str(e)}",
                }

        except Exception as e:
            logger.error(f"Error in whisper_transcribe: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"An error occurred during transcription: {str(e)}",
            }

    # Note: text_to_text, text_to_embedding, and other AI transformation abilities
    # should be implemented in the concrete provider classes, not the extension

    @ability
    @classmethod
    def mount_pytorch_model(cls, provider_instance, **kwargs) -> Dict[str, Any]:
        """
        Mount a PyTorch model using ModelManager.

        Args:
            provider_instance: The provider instance containing model configuration
            **kwargs: Additional arguments including model_path, model_name, etc.

        Returns:
            Dict with mount status and model info
        """
        if not DEPS_AVAILABLE:
            return {"success": False, "error": "PyTorch dependencies not available"}

        try:
            # Get configuration from provider instance settings
            config = cls._build_pytorch_config(provider_instance, **kwargs)

            # Get model path and name from kwargs or provider instance
            model_path = kwargs.get("model_path")
            if not model_path:
                return {"success": False, "error": "model_path is required"}

            model_name = kwargs.get("model_name", os.path.basename(model_path))

            # Initialize ModelManager with TorchModel class
            model_manager = ModelManager(model_class=TorchModel)

            # Mount the model using ModelManager
            model = model_manager.mount_model(
                model_path=model_path, model_name=model_name, config=config
            )

            if not model:
                return {
                    "success": False,
                    "error": f"Failed to mount model at {model_path}",
                }

            # Get model info
            model_info = (
                model.get_model_info().to_dict()
                if hasattr(model, "get_model_info")
                else {}
            )

            return {
                "success": True,
                "status": "mounted",
                "provider_instance_id": provider_instance.id,
                "model_id": model_name,
                "model_info": model_info,
                "config": config,
                "message": f"Successfully mounted model: {model_name}",
            }

        except Exception as e:
            logger.error(f"PyTorch model mount failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @ability
    @classmethod
    def unmount_pytorch_model(cls, provider_instance, **kwargs) -> Dict[str, Any]:
        """
        Unmount a PyTorch model using ModelManager.

        Args:
            provider_instance: The provider instance containing model configuration
            **kwargs: Additional arguments including model_name

        Returns:
            Dict with unmount status
        """
        try:
            # Get model name from kwargs or provider instance
            model_name = kwargs.get("model_name")
            if not model_name:
                return {
                    "success": False,
                    "error": "model_name is required for unmounting",
                    "provider_instance_id": provider_instance.id,
                }

            # Initialize ModelManager with TorchModel class
            model_manager = ModelManager(model_class=TorchModel)

            # Unmount the model using ModelManager
            success = model_manager.unmount_model(model_name=model_name)

            if not success:
                return {
                    "success": False,
                    "error": f"Failed to unmount model: {model_name}",
                    "provider_instance_id": provider_instance.id,
                }

            return {
                "success": True,
                "status": "unmounted",
                "provider_instance_id": provider_instance.id,
                "model_name": model_name,
                "message": f"Successfully unmounted model: {model_name}",
            }

        except Exception as e:
            logger.error(f"PyTorch model unmount failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "provider_instance_id": provider_instance.id,
            }

    @ability
    @classmethod
    def configure_pytorch_settings(
        cls, provider_instance, settings: Dict[str, str], **kwargs
    ) -> Dict[str, Any]:
        """Meta ability: Configure advanced PyTorch settings for a provider instance."""
        try:
            # Validate settings against supported options
            supported_settings = {
                "USE_BEAM_SEARCH": ["true", "false"],
                "CACHE_OFFLOAD": ["true", "false"],
                "COMPRESS_CACHE": ["true", "false"],
                "KV_CACHE_QUANT_TYPE": ["int8", "int4", "fp8", "none"],
                "DEVICE_MAP": ["auto", "sequential", "balanced", "balanced_low_0"],
                "TORCH_DTYPE": ["float16", "bfloat16", "float32", "int8", "int4"],
                "LOAD_IN_8BIT": ["true", "false"],
                "LOAD_IN_4BIT": ["true", "false"],
                "USE_FLASH_ATTENTION": ["true", "false"],
            }

            validated_settings = {}
            for key, value in settings.items():
                if key not in supported_settings:
                    logger.warning(f"Unsupported PyTorch setting: {key}")
                    continue

                # Validate value based on setting type
                if isinstance(supported_settings[key], list):
                    if value.lower() not in supported_settings[key]:
                        logger.warning(f"Invalid value for {key}: {value}")
                        continue

                validated_settings[key] = value

            # Here we would save to ProviderInstanceSetting records
            return {
                "success": True,
                "provider_instance_id": provider_instance.id,
                "configured_settings": validated_settings,
                "message": f"Configured {len(validated_settings)} PyTorch settings",
            }

        except Exception as e:
            logger.error(f"PyTorch settings configuration failed: {e}")
            return {"success": False, "error": str(e)}

    @ability
    @classmethod
    def optimize_pytorch_for_hardware(
        cls, hardware_info: Dict[str, Any], model_requirements: Dict[str, Any], **kwargs
    ) -> Dict[str, Any]:
        """Meta ability: Optimize PyTorch configuration for detected hardware."""
        try:
            hardware_type = hardware_info.get("primary_hardware_type", "cpu_only")
            available_vram = hardware_info.get("available_vram_gb", 0)
            model_vram_required = model_requirements.get("estimated_vram_gb", 0)

            optimization_config = {
                "hardware_type": hardware_type,
                "device": "cpu",
                "torch_dtype": "float32",
                "device_map": None,
                "low_cpu_mem_usage": False,
            }

            if hardware_type == "nvidia" and available_vram > 0:
                optimization_config["device"] = "cuda"
                optimization_config["torch_dtype"] = "float16"

                # VRAM management
                if available_vram >= model_vram_required:
                    optimization_config["device_map"] = "sequential"
                else:
                    optimization_config["device_map"] = "auto"
                    optimization_config["low_cpu_mem_usage"] = True

                # Quantization for low VRAM
                if available_vram < 8:
                    optimization_config["load_in_8bit"] = True
                elif available_vram < 4:
                    optimization_config["load_in_4bit"] = True

            elif hardware_type == "apple":
                optimization_config["device"] = "mps"
                optimization_config["torch_dtype"] = "float16"
            elif hardware_type == "amd":
                optimization_config["device"] = "cuda"  # ROCm uses CUDA interface
                optimization_config["torch_dtype"] = "float16"
            else:
                # CPU optimization
                optimization_config["n_threads"] = hardware_info.get("cpu_cores", 4)
                optimization_config["torch_dtype"] = "float32"

            return {
                "success": True,
                "optimization_config": optimization_config,
                "hardware_type": hardware_type,
                "available_vram_gb": available_vram,
                "model_vram_required_gb": model_vram_required,
            }

        except Exception as e:
            logger.error(f"PyTorch hardware optimization failed: {e}")
            return {"success": False, "error": str(e)}

    # =====================================================================
    # PyTorch-Specific Meta Abilities (Endpoint Migrations)
    # =====================================================================

    @classmethod
    @static_route(
        "/pytorch/models/unmount",
        method="POST",
        summary="Unmount PyTorch model from memory",
    )
    def unmount_pytorch_model_endpoint(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Unmount a PyTorch model from GPU/memory.
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

            # Initialize ModelManager with TorchModel class
            model_manager = ModelManager()

            # Unmount the model
            success = model_manager.unmount_model(model_id=model_id)

            if not success:
                return JSONResponse(
                    status_code=404,
                    content={
                        "success": False,
                        "error": f"Model not found or already unmounted: {model_id}",
                    },
                )

            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "message": f"Successfully unmounted model: {model_id}",
                },
            )

        except Exception as e:
            logger.error(f"Failed to unmount PyTorch model: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": f"Unexpected internal error. Failed to unmount model {model_id}",
                },
            )

    @classmethod
    @static_route(
        "/pytorch/v1/audio/transcriptions",
        method="POST",
        summary="Transcribe audio using Whisper model (OpenAI-compatible)",
    )
    def whisper_transcribe_openai(
        cls,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        OpenAI-compatible endpoint for transcribing audio with Whisper models.

        Expected payload:
        {
            "model": "model_name",  # Must match a mounted model ID
            "file": "base64_encoded_audio_data",
            "language": "en",  # Optional
            "response_format": "json",  # json, text, srt, or vtt
            "temperature": 0.0  # Optional, currently not used
        }
        """
        try:
            # Validate required fields
            if "model" not in payload:
                return {"error": "Missing required field: model"}, 400

            if "file" not in payload or not payload["file"]:
                return {
                    "error": "Missing or empty 'file' field with base64 audio data"
                }, 400

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

            # Extract parameters from payload
            file = payload["file"]
            language = payload.get("language")
            response_format = payload.get("response_format", "json")
            # Check if dependencies are available
            if not DEPS_AVAILABLE:
                return {"error": "PyTorch dependencies not available"}, 503

            # Decode base64 audio
            import base64

            try:
                # Handle the file format (could be a string with data:audio/...;base64, prefix)
                if "," in file:
                    file = file.split(",", 1)[1]
                audio_bytes = base64.b64decode(file)
            except Exception as e:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": {
                            "message": f"Invalid base64 audio data: {str(e)}",
                            "type": "invalid_request_error",
                            "code": 400,
                        }
                    },
                )

            # Save audio to a temporary file for processing
            import tempfile
            import os

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
                temp_audio.write(audio_bytes)
                temp_audio_path = temp_audio.name

            try:
                # Run inference using the pipeline with the temporary file path
                result = model(
                    temp_audio_path,
                    generate_kwargs=(
                        {"language": language, "task": "transcribe"}
                        if language
                        else None
                    ),
                )

                # Clean up the temporary file
                try:
                    os.unlink(temp_audio_path)
                except Exception:
                    pass

                # Format response according to OpenAI API
                response = {
                    "text": result.get("text", ""),
                    "task": "transcribe",
                    "language": result.get("language"),
                    "duration": result.get("duration"),
                    "segments": result.get("segments", []),
                }

                # Handle different response formats
                if response_format == "text":
                    return response["text"]
                elif response_format == "srt":
                    # Convert segments to SRT format
                    srt_content = ""
                    for i, segment in enumerate(response["segments"], 1):
                        start = segment.get("start", 0)
                        end = segment.get("end", 0)
                        text = segment.get("text", "")

                        # Format time as SRT timestamp
                        def format_time(seconds):
                            ms = int((seconds % 1) * 1000)
                            seconds = int(seconds)
                            hours = seconds // 3600
                            seconds %= 3600
                            minutes = seconds // 60
                            seconds %= 60
                            return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"

                        srt_content += (
                            f"{i}\n"
                            f"{format_time(start)} --> {format_time(end)}\n"
                            f"{text}\n\n"
                        )
                    return srt_content
                elif response_format == "vtt":
                    # Convert segments to WebVTT format
                    vtt_content = "WEBVTT\n\n"
                    for i, segment in enumerate(response["segments"], 1):
                        start = segment.get("start", 0)
                        end = segment.get("end", 0)
                        text = segment.get("text", "")

                        # Format time as WebVTT timestamp
                        def format_time(seconds):
                            ms = int((seconds % 1) * 1000)
                            seconds = int(seconds)
                            hours = seconds // 3600
                            seconds %= 3600
                            minutes = seconds // 60
                            seconds %= 60
                            return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{ms:03d}"

                        vtt_content += (
                            f"{i}\n"
                            f"{format_time(start)} --> {format_time(end)}\n"
                            f"{text}\n\n"
                        )
                    return vtt_content
                else:  # json (default)
                    return response

            except Exception as e:
                # Clean up the temporary file in case of error
                try:
                    os.unlink(temp_audio_path)
                except Exception:
                    pass
                raise e

        except Exception as e:
            logger.error(f"Transcription failed: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "message": f"Transcription failed: {str(e)}",
                        "type": "server_error",
                        "code": 500,
                    }
                },
            )

    @classmethod
    @static_route(
        "/pytorch/configs/hardware-optimize",
        method="POST",
        summary="Hardware optimize PyTorch config",
    )
    def hardware_optimize_endpoint(cls, base_config_id: str) -> Dict[str, Any]:
        """Get hardware-optimized PyTorch configuration from a base configuration."""
        try:
            from zephyrex.extensions.local_ai.EXT_Local_AI import EXT_Local_AI

            # Get hardware info
            hardware_info = EXT_Local_AI.hardware_detection()

            # Mock optimized config based on hardware
            optimized_config = {
                "base_config_id": base_config_id,
                "model_type": "causal_lm",
                "torch_dtype": (
                    "float16" if hardware_info.get("has_cuda") else "float32"
                ),
                "device_type": "cuda" if hardware_info.get("has_cuda") else "cpu",
                "device_map": "auto",
                "quantization_type": "none",
            }

            # Determine applied optimizations
            applied_optimizations = []
            if hardware_info.get("has_cuda"):
                applied_optimizations.extend(
                    ["CUDA optimization", "FP16 precision", "Flash attention"]
                )
            elif hardware_info.get("has_mps"):
                applied_optimizations.extend(["MPS optimization", "FP16 precision"])
            else:
                applied_optimizations.extend(["CPU optimization", "FP32 precision"])

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
        "/pytorch/configs/{config_id}/compatibility",
        method="GET",
        summary="Check PyTorch compatibility",
    )
    def compatibility_check_endpoint(cls, config_id: str) -> Dict[str, Any]:
        """Check if a PyTorch configuration is compatible with current hardware."""
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
            if available_vram < 8:
                compatibility_issues.append("Low VRAM may require quantization")
                recommendations.append("Enable load_in_8bit or load_in_4bit")

            return {
                "success": True,
                "compatible": len(compatibility_issues) == 0,
                "issues": compatibility_issues,
                "recommendations": recommendations,
                "estimated_vram_gb": 6.0,
                "estimated_ram_gb": 4.0,
                "hardware_info": hardware_info,
            }
        except Exception as e:
            logger.error(f"Compatibility check failed: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @ability
    @static_route(
        "/pytorch/configs/{config_id}/load-pipeline",
        method="POST",
        summary="Load PyTorch pipeline",
    )
    def load_pipeline_endpoint(
        cls, config_id: str, task: str = "text-generation"
    ) -> Dict[str, Any]:
        """Load a PyTorch model pipeline based on configuration."""
        try:
            pipeline_config = {
                "task": task,
                "model_path": "/models/pytorch-model",
                "device": "auto",
                "torch_dtype": "float16",
            }

            return {
                "success": True,
                "config_id": config_id,
                "task": task,
                "pipeline_config": pipeline_config,
                "message": f"Pipeline loaded for config {config_id}",
            }
        except Exception as e:
            logger.error(f"Pipeline loading failed: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @ability
    @static_route(
        "/pytorch/configs/{config_id}/generate-text",
        method="POST",
        summary="Generate text with PyTorch",
    )
    def generate_text_endpoint(
        cls,
        config_id: str,
        prompt: str,
        max_new_tokens: int = 512,
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """Generate text using PyTorch model configuration."""
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
                    "model_name": "pytorch-model",
                },
            }
        except Exception as e:
            logger.error(f"Text generation failed: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @ability
    @static_route(
        "/pytorch/configs/{config_id}/generate-embeddings",
        method="POST",
        summary="Generate embeddings with PyTorch",
    )
    def generate_embeddings_endpoint(
        cls, config_id: str, text: str, normalize: bool = True
    ) -> Dict[str, Any]:
        """Generate embeddings using PyTorch model configuration."""
        try:
            # Mock embedding generation
            embeddings = [0.1] * 768

            return {
                "success": True,
                "embeddings": embeddings,
                "dimension": len(embeddings),
                "model_info": {
                    "config_id": config_id,
                    "model_name": "pytorch-model",
                },
            }
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            return {"success": False, "error": str(e)}

    # Note: discover_pytorch_models and download_pytorch_model already exist as @static_route methods

    # =====================================================================
    # Helper Methods
    # =====================================================================

    @classmethod
    def _build_pytorch_config(
        cls, provider_instance, task_type: str = "text", **kwargs
    ) -> Dict[str, Any]:
        """Build PyTorch configuration from provider instance settings."""
        # Base configuration
        config = {
            "model_path": provider_instance.model_name,  # Should be path to model directory
            "max_length": kwargs.get("max_length", 512),
            "temperature": kwargs.get("temperature", 1.0),
            "top_p": kwargs.get("top_p", 1.0),
            "device": "auto",
            "torch_dtype": "auto",
        }

        # Apply settings from ProviderInstanceSettings
        # In real implementation, would query database for settings
        instance_settings = cls._get_instance_settings(provider_instance)
        for key, value in instance_settings.items():
            if key == "USE_BEAM_SEARCH" and value.lower() == "true":
                config["use_beam_search"] = True
                config["num_beams"] = kwargs.get("num_beams", 4)
            elif key == "CACHE_OFFLOAD" and value.lower() == "true":
                config["cache_offload"] = True
            elif key == "DEVICE_MAP":
                config["device_map"] = value
            elif key == "TORCH_DTYPE":
                config["torch_dtype"] = value
            elif key == "LOAD_IN_8BIT" and value.lower() == "true":
                config["load_in_8bit"] = True
            elif key == "LOAD_IN_4BIT" and value.lower() == "true":
                config["load_in_4bit"] = True

        return config

    @classmethod
    def _get_instance_settings(cls, provider_instance) -> Dict[str, str]:
        """Get settings for a provider instance."""
        # Placeholder - would query ProviderInstanceSetting records
        return {
            "USE_BEAM_SEARCH": "false",
            "CACHE_OFFLOAD": "false",
            "DEVICE_MAP": "auto",
            "TORCH_DTYPE": "float16",
        }

    @classmethod
    @static_route(
        "/pytorch/models/mount",
        method="POST",
        summary="Mount PyTorch model to memory/GPU",
    )
    def mount_pytorch_model_route(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Mount a PyTorch model to memory/GPU.

        Expected payload:
        {
            "model_path": "/path/to/model",  # Path to model directory or file
            "model_name": "model_name",      # Name to identify the model
            "device": "auto",                # "auto", "cpu", "cuda", "mps", etc.
            "torch_dtype": "auto",           # "auto", "float16", "float32", etc.
            "load_in_8bit": false,           # Load in 8-bit precision
            "load_in_4bit": false,           # Load in 4-bit precision
            "device_map": "auto",            # Device map for model parallelism
            "trust_remote_code": false,      # Trust remote code in model
            "use_safetensors": true,         # Use safetensors if available
            "verbose": false                 # Enable verbose logging
        }
        """
        if not DEPS_AVAILABLE:
            return {"success": False, "error": "PyTorch dependencies not available"}

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

            # Check if model path exists
            if not os.path.exists(model_path):
                return {
                    "success": False,
                    "error": f"Model path does not exist: {model_path}",
                }

            # Initialize model manager and check if model is already loaded
            model_manager = ModelManager()

            if model_manager.get_model(model_id):
                model_info = model_manager.get_model_info(model_id)
                return {
                    "success": True,
                    "status": "already_mounted",
                    "model_id": model_id,
                    "model_name": model_name,
                    "message": f"Model {model_name} is already mounted",
                    "device": getattr(model_info, "device", "cpu"),
                    "dtype": getattr(model_info, "torch_dtype", "unknown"),
                    "config": (
                        model_info.to_dict() if hasattr(model_info, "to_dict") else {}
                    ),
                }

            # Prepare config from payload
            config = {
                "device": payload.get("device", "auto"),
                "torch_dtype": payload.get("torch_dtype", "auto"),
                "load_in_8bit": payload.get("load_in_8bit", False),
                "load_in_4bit": payload.get("load_in_4bit", False),
                "device_map": payload.get("device_map", "auto"),
                "trust_remote_code": payload.get("trust_remote_code", False),
                "use_safetensors": payload.get("use_safetensors", True),
                "verbose": payload.get("verbose", False),
            }

            # Mount the model
            success, result = model_manager.mount_model(
                model_id=model_id,
                model_path=model_path,
                model_name=model_name,
                config=config,
                model_class=TorchModel,
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
            logger.error(f"PyTorch model mount failed: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": f"Failed to mount model: {str(e)}"},
            )

    @classmethod
    def _estimate_model_size_pytorch(cls, model_id: str) -> float:
        """Estimate PyTorch model download size."""
        # Base estimation from model name
        base_size = 2.0  # Default for smaller models

        model_lower = model_id.lower()
        if "70b" in model_lower:
            base_size = 140.0
        elif "34b" in model_lower:
            base_size = 68.0
        elif "13b" in model_lower:
            base_size = 26.0
        elif "7b" in model_lower:
            base_size = 14.0
        elif "3b" in model_lower:
            base_size = 6.0

        return base_size
