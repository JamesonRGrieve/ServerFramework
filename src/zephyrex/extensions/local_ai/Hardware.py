# SPDX-License-Identifier: AGPL-3.0-or-later
"""What this server has to run models on: its processor, memory and
NVIDIA GPUs (as ``nvidia-smi`` reports them)."""

import os
import platform
import shutil
import subprocess
from typing import Any, Dict, List

import psutil

GPU_QUERY_TIMEOUT_SECONDS = 10
BYTES_PER_MIB = 1024 * 1024
_GPU_FIELDS = "index,name,memory.total,memory.used,memory.free"


def parse_nvidia_smi(output: str) -> List[Dict[str, Any]]:
    """``nvidia-smi --query-gpu=<_GPU_FIELDS> --format=csv,noheader,nounits``
    output as one entry per GPU, memory in bytes."""
    gpus = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 5 or not parts[0].isdigit():
            continue
        index, name, total, used, free = parts
        try:
            memory = [int(float(v) * BYTES_PER_MIB) for v in (total, used, free)]
        except ValueError:
            continue
        gpus.append(
            {
                "index": int(index),
                "name": name,
                "vendor": "nvidia",
                "memory_total_bytes": memory[0],
                "memory_used_bytes": memory[1],
                "memory_free_bytes": memory[2],
            }
        )
    return gpus


def nvidia_gpus() -> List[Dict[str, Any]]:
    tool = shutil.which("nvidia-smi")
    if tool is None:
        return []
    try:
        result = subprocess.run(
            [tool, f"--query-gpu={_GPU_FIELDS}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=GPU_QUERY_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_nvidia_smi(result.stdout) if result.returncode == 0 else []


def detect() -> Dict[str, Any]:
    memory = psutil.virtual_memory()
    return {
        "machine": platform.machine(),
        "system": platform.system(),
        "cpu_count": os.cpu_count() or 0,
        "physical_cpu_count": psutil.cpu_count(logical=False) or 0,
        "memory_total_bytes": memory.total,
        "memory_available_bytes": memory.available,
        "gpus": nvidia_gpus(),
    }
