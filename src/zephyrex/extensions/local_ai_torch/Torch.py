# SPDX-License-Identifier: AGPL-3.0-or-later
"""PyTorch models through transformers: the device and precision a model
runs at, and how its files are loaded.

Files are only ever read from the model's verified directory
(``local_files_only``), weights only from safetensors (a pickled
``.bin`` can run code as it loads), and never with code a repository
ships (``trust_remote_code`` off)."""

import re
from pathlib import Path
from typing import Any, ClassVar, Dict, Tuple

import torch

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.local_ai.LocalAI import (
    LOCAL_MODEL_SETTINGS,
    AbstractLocalAIProvider,
)
from zephyrex.extensions.local_ai.LocalModels import misconfigured
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

TORCH_DEPENDENCIES = Dependencies(
    [
        PIP_Dependency(
            name="torch",
            friendly_name="PyTorch",
            semver=">=2.13.0",
            reason="Runs the models",
        ),
        PIP_Dependency(
            name="transformers",
            friendly_name="Hugging Face Transformers",
            semver=">=5.15.1",
            reason="Loads models, tokenizers and processors",
        ),
    ]
)

_DEVICE = re.compile(r"^(cpu|mps|cuda(:[0-9]{1,2})?)$")
DTYPES: Dict[str, torch.dtype] = {
    "float32": torch.float32,
    "float16": torch.float16,
    "bfloat16": torch.bfloat16,
}
# What every from_pretrained call is given: the verified files only, and
# none of a repository's own code.
PRETRAINED: Dict[str, Any] = {"local_files_only": True, "trust_remote_code": False}
TORCH_SETTINGS: Tuple[InstanceSetting, ...] = (
    *LOCAL_MODEL_SETTINGS,
    InstanceSetting("device", "cpu, cuda, cuda:N or mps", default="cpu"),
    InstanceSetting("dtype", "float32, float16 or bfloat16", default="float32"),
)


class AbstractTorchProvider(AbstractLocalAIProvider):
    """A PyTorch model; each instance is one model directory."""

    dependencies: ClassVar[Dependencies] = TORCH_DEPENDENCIES
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = TORCH_SETTINGS

    @classmethod
    def device(cls, instance: ProviderInstanceModel) -> torch.device:
        chosen = cls.setting(instance, "device") or "cpu"
        if not _DEVICE.match(chosen):
            raise misconfigured("device is cpu, cuda, cuda:N or mps", cls.name)
        return torch.device(chosen)

    @classmethod
    def dtype(cls, instance: ProviderInstanceModel) -> torch.dtype:
        chosen = cls.setting(instance, "dtype") or "float32"
        if chosen not in DTYPES:
            raise misconfigured(f"dtype is one of {', '.join(DTYPES)}", cls.name)
        return DTYPES[chosen]

    @classmethod
    def pretrained(
        cls, auto_class: Any, instance: ProviderInstanceModel, directory: Path
    ) -> Any:
        """The weights in ``directory`` loaded by the transformers
        ``auto_class``, from safetensors only, on the instance's device
        for inference. Loaded before the tokenizer or processor, so a
        model refused here reads nothing else."""
        device, dtype = cls.device(instance), cls.dtype(instance)
        model = auto_class.from_pretrained(
            directory, dtype=dtype, use_safetensors=True, **PRETRAINED
        )
        if not isinstance(model, torch.nn.Module):
            raise TypeError(f"{cls.name} loaded a {type(model).__name__}")
        return model.to(device).eval()

    @classmethod
    def close_model(cls, value: Any) -> None:
        super().close_model(value)
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
