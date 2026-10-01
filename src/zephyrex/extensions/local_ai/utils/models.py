from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
import gc
import threading
import time
from typing import Any, Dict, Generic, List, Optional, Tuple, TypeVar, Union

from .device_utils import detect_gpu_devices, get_system_memory


class DeviceType(Enum):
    CPU = "cpu"
    CUDA = "cuda"
    METAL = "metal"
    VULKAN = "vulkan"


@dataclass
class ModelInfo:
    model_path: str
    model_name: str
    device: str
    context_length: int
    batch_size: int
    last_used: float = field(default_factory=lambda: time.time())
    memory_usage: int = 0  # in bytes
    lock: threading.Lock = field(default_factory=threading.Lock)


ModelT = TypeVar("ModelT")


class Model(ABC, Generic[ModelT]):
    """Base interface for all model implementations."""

    @abstractmethod
    def load(
        self, model_path: str, config: Dict[str, Any]
    ) -> Tuple[bool, Union[ModelT, str]]:
        """Load the model into memory."""
        pass

    @abstractmethod
    def unload(self) -> Tuple[bool, str]:
        """Unload the model and free resources."""
        pass

    @abstractmethod
    def is_loaded(self) -> bool:
        """Check if the model is currently loaded."""
        pass

    @abstractmethod
    def get_model(self) -> Optional[ModelT]:
        """Get the underlying model instance."""
        pass

    @abstractmethod
    def get_info(self) -> ModelInfo:
        """Get model information and metadata."""
        pass


class ModelManager(Generic[ModelT]):
    """Generic model manager that can handle any model type that implements the Model interface."""

    _instance = None
    _models: Dict[str, Model[ModelT]] = {}
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ModelManager, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """Initialize the model manager."""
        self.available_devices = self._get_available_devices()
        self.used_memory = 0

    def _get_available_devices(self) -> List[Dict[str, Any]]:
        """Get all available compute devices.

        Returns:
            List of dictionaries containing device information.
        """
        devices = []

        # Add CPU device
        mem = get_system_memory()
        devices.append(
            {
                "type": DeviceType.CPU.value,
                "id": "cpu",
                "name": "CPU",
                "total_memory": mem["total"],
                "used_memory": mem["used"],
                "available": True,
            }
        )

        # Add GPUs
        devices.extend(detect_gpu_devices())

        return devices

    def _get_best_device(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Determine the best device to use for the model.

        Args:
            config: Model configuration

        Returns:
            Dictionary with device configuration
        """
        if not any(
            d["available"] for d in self.available_devices if d["type"] != "cpu"
        ):
            return {"success": True, "device": "cpu", "gpu_layers": 0}

        # Check if CPU is forced
        if config.get("device") == "cpu":
            return {"success": True, "device": "cpu", "gpu_layers": 0}

        # Get requested GPU layers
        gpu_layers = config.get("gpu_layers", -1)  # -1 means auto-detect

        # If no GPU layers requested, use CPU
        if gpu_layers == 0:
            return {"success": True, "device": "cpu", "gpu_layers": 0}

        # Find best available GPU
        best_gpu = None
        for device in self.available_devices:
            if device["type"] != "cpu" and device["available"]:
                if (
                    best_gpu is None
                    or device["total_memory"] > best_gpu["total_memory"]
                ):
                    best_gpu = device

        if best_gpu is None:
            return {"success": False, "error": "No available GPUs found"}

        # If auto-detect, use all layers
        if gpu_layers == -1:
            gpu_layers = 1000  # A large number to use all layers

        return {
            "success": True,
            "device": best_gpu["type"],
            "gpu_layers": gpu_layers,
            "main_gpu": best_gpu["id"],
            "tensor_split": None,  # Could be enhanced for multi-GPU support
        }

    def mount_model(
        self,
        model_id: str,
        model_path: str,
        model_name: str,
        config: Dict[str, Any],
        model_class: type[Model[ModelT]],
    ) -> Tuple[bool, Union[ModelT, str]]:
        """Mount a model.

        Args:
            model_id: Unique identifier for the model
            model_path: Path to the model file
            model_name: Name of the model
            config: Configuration dictionary
            model_class: The model class to instantiate

        Returns:
            Tuple of (success, model_or_error)
        """
        with self._lock:
            # Check if model is already loaded
            if model_id in self._models:
                model = self._models[model_id]
                model.get_info().last_used = time.time()
                return True, model.get_model()

            # Get device configuration
            device_config = self._get_best_device(config)
            if not device_config["success"]:
                return False, device_config["error"]

            # Update config with device info
            config.update(
                {
                    "device": device_config["device"],
                    "gpu_layers": device_config.get("gpu_layers", 0),
                    "main_gpu": device_config.get("main_gpu", 0),
                    "tensor_split": device_config.get("tensor_split"),
                }
            )

            # Create new model instance
            try:
                model = model_class()
                success, result = model.load(model_path, config)
                if not success:
                    return False, str(result)

                # Update memory tracking
                model_info = model.get_info()
                self.used_memory += model_info.memory_usage

                self._models[model_id] = model
                return True, model.get_model()

            except Exception as e:
                return False, str(e)

    def unmount_model(self, model_id: str) -> Tuple[bool, str]:
        """Unmount a model.

        Args:
            model_id: ID of the model to unmount

        Returns:
            Tuple of (success, message)
        """
        with self._lock:
            if model_id not in self._models:
                return False, "Model not found"

            model = self._models[model_id]
            model_info = model.get_info()

            # Try to get the model lock
            if not model_info.lock.acquire(blocking=False):
                return False, "Model is currently in use"

            try:
                success, message = model.unload()

                if success:
                    # Update memory tracking
                    self.used_memory = max(
                        0, self.used_memory - model_info.memory_usage
                    )
                    del self._models[model_id]

                return success, message

            finally:
                model_info.lock.release()

    def get_model(self, model_id: str) -> Optional[ModelT]:
        """Get a loaded model by ID.

        Args:
            model_id: ID of the model to get

        Returns:
            The model instance, or None if not found
        """
        with self._lock:
            if model_id in self._models:
                model = self._models[model_id]
                model_info = model.get_info()

                # Try to get the model lock
                if model_info.lock.acquire(blocking=False):
                    try:
                        model_info.last_used = time.time()
                        return model.get_model()
                    finally:
                        model_info.lock.release()

        return None

    def get_model_info(self, model_id: str) -> Optional[ModelInfo]:
        """Get model information by ID.

        Args:
            model_id: ID of the model

        Returns:
            ModelInfo if found, None otherwise
        """
        with self._lock:
            if model_id in self._models:
                return self._models[model_id].get_info()
        return None

    def list_models(self) -> Dict[str, Dict[str, Any]]:
        """Get information about all loaded models.

        Returns:
            Dictionary mapping model IDs to their information
        """
        with self._lock:
            return {
                model_id: {"info": model.get_info(), "is_loaded": model.is_loaded()}
                for model_id, model in self._models.items()
            }

    def cleanup_unused_models(self, max_age_seconds: int = 300) -> Dict[str, str]:
        """Clean up models that haven't been used for a while.

        Args:
            max_age_seconds: Maximum age in seconds before a model is considered unused

        Returns:
            Dictionary of model IDs to status messages
        """
        results = {}
        current_time = time.time()

        with self._lock:
            for model_id, model in list(self._models.items()):
                model_info = model.get_info()

                # Skip if model was used recently
                if current_time - model_info.last_used < max_age_seconds:
                    continue

                # Try to get the model lock
                if not model_info.lock.acquire(blocking=False):
                    results[model_id] = "Model is in use"
                    continue

                try:
                    success, message = model.unload()
                    if success:
                        self.used_memory = max(
                            0, self.used_memory - model_info.memory_usage
                        )
                        del self._models[model_id]
                    results[model_id] = f"Unloaded: {message}"
                except Exception as e:
                    results[model_id] = f"Error unloading: {str(e)}"
                finally:
                    model_info.lock.release()

        # Force garbage collection
        gc.collect()

        return results
