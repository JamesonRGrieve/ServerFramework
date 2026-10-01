import psutil
import torch
from typing import Dict, List, Any


def detect_gpu_devices() -> List[Dict[str, Any]]:
    """Detect available GPU devices.

    Returns:
        List of dictionaries containing GPU device information.
    """
    devices = []

    # Check for CUDA devices
    if torch and torch.cuda.is_available():
        for i in range(torch.cuda.device_count()):
            try:
                prop = torch.cuda.get_device_properties(i)
                devices.append(
                    {
                        "type": "cuda",
                        "id": i,
                        "name": prop.name,
                        "total_memory": prop.total_memory,
                        "used_memory": torch.cuda.memory_allocated(i),
                        "available": True,
                    }
                )
            except Exception as e:
                print(f"Failed to get CUDA device {i} properties: {e}")

    # Check for Metal (Apple Silicon)
    if torch and hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        devices.append(
            {
                "type": "metal",
                "id": "mps",
                "name": "Apple MPS",
                "total_memory": psutil.virtual_memory().total,
                "used_memory": 0,  # Not directly queryable
                "available": True,
            }
        )

    return devices


def get_system_memory() -> Dict[str, int]:
    """Get system memory information.

    Returns:
        Dictionary with total and available memory in bytes.
    """
    mem = psutil.virtual_memory()
    return {
        "total": mem.total,
        "available": mem.available,
        "used": mem.used,
        "percent": mem.percent,
    }
