"""
PyTorch model implementation for the Local AI framework.

This module provides a PyTorch model implementation that works with the generic ModelManager.
"""

import gc
import os
import logging
from typing import Any, Dict, Optional, Tuple, Union
import numpy as np
import torch
import threading
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

from zephyrex.extensions.local_ai.utils.models import Model, ModelInfo

logger = logging.getLogger(__name__)


class TorchModel(Model):
    """PyTorch model implementation for the Local AI framework."""

    def __init__(self):
        self._model: Optional[Any] = None
        self._info: Optional[ModelInfo] = None
        self._lock = threading.Lock()
        self._processor = None
        self._pipeline = None
        self._device = "auto"
        self._dtype = "auto"
        self._model_type = "whisper"  # Default model type

    def load(
        self, model_path: str, config: Dict[str, Any]
    ) -> Tuple[bool, Union[Any, str]]:
        """Load a PyTorch model into memory."""
        if not os.path.exists(model_path):
            return False, f"Model path not found: {model_path}"

        try:
            # Set device and dtype from config
            self._device = config.get("device", "auto")
            self._dtype = config.get("dtype", "auto")
            self._model_type = config.get("model_type", "whisper")

            # Determine device
            if self._device == "auto":
                self._device = "cuda:0" if torch.cuda.is_available() else "cpu"

            # Determine dtype
            torch_dtype = torch.float32
            if self._dtype == "auto":
                if torch.cuda.is_available():
                    if torch.cuda.is_bf16_supported():
                        torch_dtype = torch.bfloat16
                    else:
                        torch_dtype = torch.float16
            elif self._dtype == "float16":
                torch_dtype = torch.float16
            elif self._dtype == "bfloat16":
                torch_dtype = torch.bfloat16

            if self._model_type == "whisper":
                # Load Whisper model
                model = AutoModelForSpeechSeq2Seq.from_pretrained(
                    model_path,
                    torch_dtype=torch_dtype,
                    low_cpu_mem_usage=True,
                    use_safetensors=True,
                )
                model.to(self._device)

                processor = AutoProcessor.from_pretrained(model_path)

                self._pipeline = pipeline(
                    "automatic-speech-recognition",
                    model=model,
                    tokenizer=processor.tokenizer,
                    feature_extractor=processor.feature_extractor,
                    max_new_tokens=128,
                    torch_dtype=torch_dtype,
                    device=self._device,
                )

                self._model = model
                self._processor = processor

                # Create model info
                self._info = ModelInfo(
                    model_path=model_path,
                    model_name=os.path.basename(model_path),
                    device=self._device,
                    context_length=config.get("context_length", 2048),
                    batch_size=config.get("batch_size", 1),
                    memory_usage=self._estimate_model_memory(model_path),
                )

                logger.info(
                    f"Successfully loaded PyTorch model: {model_path} on {self._device}"
                )
                return True, self._model

            # Add support for other model types here
            return False, f"Unsupported model type: {self._model_type}"

        except Exception as e:
            error_msg = f"Failed to load PyTorch model {model_path}: {e}"
            logger.error(error_msg, exc_info=True)
            return False, error_msg

    def unload(self) -> Tuple[bool, str]:
        """Unload the model and free resources."""
        if self._model is None:
            return True, "Model not loaded"

        try:
            # Clean up model
            if hasattr(self._model, "to"):
                self._model.to("cpu")
            del self._model
            self._model = None

            # Clean up processor
            if self._processor is not None:
                del self._processor
                self._processor = None

            # Clean up pipeline
            if self._pipeline is not None:
                del self._pipeline
                self._pipeline = None

            # Clear CUDA cache if available
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            # Force garbage collection
            gc.collect()

            # Clear model info
            self._info = None

            return True, "Model unloaded successfully"

        except Exception as e:
            error_msg = f"Error unloading model: {e}"
            logger.error(error_msg)
            return False, error_msg

    def is_loaded(self) -> bool:
        """Check if the model is currently loaded."""
        return self._model is not None

    def get_model(self) -> Optional[Any]:
        """Get the underlying model instance."""
        # torch models should be handled by the pipeline, not by the model itself
        # In order to preserve the abstraction's naming system, this returns the pipeline
        return self._pipeline

    def get_info(self) -> ModelInfo:
        """Get model information and metadata."""
        if self._info is None:
            raise ValueError("Model not loaded")
        return self._info

    def _estimate_model_memory(self, model_path: str) -> int:
        """Estimate memory usage of the model."""
        try:
            # Get size of the model directory
            total_size = 0
            for dirpath, _, filenames in os.walk(model_path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    total_size += os.path.getsize(fp)

            # Add some overhead for model loading
            estimated_memory = int(total_size * 1.5)

            # Add additional overhead for CUDA if using GPU
            if self._device != "cpu" and torch.cuda.is_available():
                estimated_memory = int(estimated_memory * 1.5)

            return estimated_memory

        except Exception as e:
            logger.warning(f"Failed to estimate memory for {model_path}: {e}")
            return 0
