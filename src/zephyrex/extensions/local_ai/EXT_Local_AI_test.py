# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local AI: the hardware this server really has, nvidia-smi's report
read, and the models held in memory listed and released."""

from zephyrex.extensions.local_ai.EXT_Local_AI import EXT_Local_AI
from zephyrex.extensions.local_ai.Hardware import parse_nvidia_smi
from zephyrex.extensions.local_ai.LocalModels import LOADED_MODELS

NVIDIA_SMI = """0, NVIDIA GeForce RTX 4090, 24564, 1024, 23540
1, NVIDIA RTX A2000 12GB, 12282, 0, 12282
"""


def test_nvidia_smi_report():
    first, second = parse_nvidia_smi(NVIDIA_SMI)
    assert first == {
        "index": 0,
        "name": "NVIDIA GeForce RTX 4090",
        "vendor": "nvidia",
        "memory_total_bytes": 24564 * 1024 * 1024,
        "memory_used_bytes": 1024 * 1024 * 1024,
        "memory_free_bytes": 23540 * 1024 * 1024,
    }
    assert second["index"] == 1 and second["memory_used_bytes"] == 0


def test_an_unreadable_report_lists_no_gpu():
    assert parse_nvidia_smi("No devices were found\n0, x, [N/A], 1, 2\n") == []


async def test_detect_hardware():
    found = await EXT_Local_AI.detect_hardware()
    assert found["cpu_count"] >= 1
    assert 0 < found["memory_available_bytes"] <= found["memory_total_bytes"]
    assert isinstance(found["gpus"], list)


async def test_loaded_models_and_unloading_them_all():
    closed = []
    LOADED_MODELS.load("x-1", "first", "p", lambda: "one", closed.append)
    try:
        listed = await EXT_Local_AI.loaded_models()
        assert {"id": "x-1", "name": "first", "provider": "p"}.items() <= next(
            m for m in listed if m["id"] == "x-1"
        ).items()
        unloaded = await EXT_Local_AI.unload_all_models()
        assert "x-1" in [m["id"] for m in unloaded]
        assert closed == ["one"]
        assert await EXT_Local_AI.loaded_models() == []
    finally:
        LOADED_MODELS.unload("x-1")
