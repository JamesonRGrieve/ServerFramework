import gc
import os
import psutil
import threading
import time
from typing import Any, Dict, Optional, Tuple, Union

from llama_cpp import Llama

from zephyrex.lib.Logging import logger
from zephyrex.extensions.local_ai.utils.models import Model, ModelInfo


class GGUFModel(Model[Llama]):
    """GGUF model implementation of the Model interface."""

    def __init__(self):
        self._model: Optional[Llama] = None
        self._info: Optional[ModelInfo] = None
        self._lock = threading.Lock()

    def load(
        self, model_path: str, config: Dict[str, Any]
    ) -> Tuple[bool, Union[Llama, str]]:
        """Load a GGUF model into memory."""
        if not os.path.exists(model_path):
            return False, f"Model file not found: {model_path}"

        try:
            # Prepare model parameters
            model_params = {
                "model_path": model_path,
                "n_ctx": int(config.get("context_length", 2048)),
                "n_batch": int(config.get("batch_size", 512)),
                "n_threads": int(
                    config.get("cpu_threads", max(1, os.cpu_count() // 2))
                ),
                "n_gpu_layers": int(config.get("gpu_layers", 0)),
                "main_gpu": config.get("main_gpu", 0),
                "tensor_split": config.get("tensor_split"),
                "verbose": config.get("verbose", False),
            }

            # Load the model
            self._model = Llama(**model_params)

            # Create model info
            self._info = ModelInfo(
                model_path=model_path,
                model_name=os.path.basename(model_path),
                device=config.get("device", "cpu"),
                context_length=config.get("context_length", 2048),
                batch_size=config.get("batch_size", 512),
                memory_usage=self._estimate_model_memory(
                    model_path, config.get("gpu_layers", 0)
                ),
            )

            logger.info(f"Successfully loaded GGUF model: {model_path}")
            return True, self._model

        except Exception as e:
            error_msg = f"Failed to load GGUF model {model_path}: {e}"
            logger.error(error_msg)
            return False, error_msg

    def unload(self) -> Tuple[bool, str]:
        """Unload the model and free resources."""
        if self._model is None:
            return True, "Model not loaded"

        try:
            if hasattr(self._model, "free"):
                self._model.free()
            self._model = None
            self._info = None
            gc.collect()
            return True, "Model unloaded successfully"
        except Exception as e:
            error_msg = f"Error unloading model: {e}"
            logger.error(error_msg)
            return False, error_msg

    def is_loaded(self) -> bool:
        """Check if the model is currently loaded."""
        return self._model is not None

    def get_model(self) -> Optional[Llama]:
        """Get the underlying Llama model instance."""
        return self._model

    def get_info(self) -> ModelInfo:
        """Get model information and metadata."""
        if self._info is None:
            raise ValueError("Model not loaded")
        return self._info

    def _estimate_model_memory(self, model_path: str, gpu_layers: int = 0) -> int:
        """Estimate memory usage of a GGUF model."""
        try:
            file_size = os.path.getsize(model_path)
            # Rough estimation: model size + context overhead (20% of model size)
            estimated_memory = int(file_size * 1.2)

            # If using GPU layers, add additional overhead
            if gpu_layers > 0:
                estimated_memory = int(estimated_memory * 1.3)

            return estimated_memory
        except Exception as e:
            logger.warning(f"Failed to estimate memory for {model_path}: {e}")
            return 0
