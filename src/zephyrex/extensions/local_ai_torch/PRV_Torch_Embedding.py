# SPDX-License-Identifier: AGPL-3.0-or-later
"""A transformer encoder (transformers ``AutoModel``) as an embedding
model: its last hidden state pooled per text (mean over the tokens, or
the first token), L2-normalized unless the instance says not to."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Set, Tuple

import torch
from transformers import AutoModel, AutoTokenizer

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ai.EXT_AI import EMBEDDINGS
from zephyrex.extensions.local_ai.LocalModels import misconfigured
from zephyrex.extensions.local_ai_torch.Torch import (
    PRETRAINED,
    TORCH_SETTINGS,
    AbstractTorchProvider,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

EMBEDDING_BATCH = 32
MAX_EMBEDDING_TOKENS = 8192
POOLINGS = ("mean", "cls")


@dataclass(frozen=True)
class EncoderModel:
    model: Any
    tokenizer: Any


def pooled(
    hidden: torch.Tensor, mask: torch.Tensor, pooling: str, normalize: bool
) -> torch.Tensor:
    """One vector per sequence of ``hidden`` (batch, tokens, width)."""
    if pooling == "cls":
        vectors = hidden[:, 0]
    else:
        weights = mask.unsqueeze(-1).to(hidden.dtype)
        vectors = (hidden * weights).sum(dim=1) / weights.sum(dim=1).clamp(min=1e-9)
    return torch.nn.functional.normalize(vectors, p=2, dim=1) if normalize else vectors


def token_limit(encoder: EncoderModel) -> int:
    limits = [MAX_EMBEDDING_TOKENS]
    for found in (
        getattr(encoder.tokenizer, "model_max_length", None),
        getattr(encoder.model.config, "max_position_embeddings", None),
    ):
        if isinstance(found, int) and found > 0:
            limits.append(found)
    return min(limits)


def embed_texts(
    encoder: EncoderModel, texts: List[str], pooling: str, normalize: bool
) -> List[List[float]]:
    """A vector per text, in order; a text longer than the model reads is
    cut at its limit."""
    limit = token_limit(encoder)
    vectors: List[List[float]] = []
    for start in range(0, len(texts), EMBEDDING_BATCH):
        batch = encoder.tokenizer(
            texts[start : start + EMBEDDING_BATCH],
            padding=True,
            truncation=True,
            max_length=limit,
            return_tensors="pt",
        ).to(encoder.model.device)
        with torch.inference_mode():
            hidden = encoder.model(**batch).last_hidden_state
        found = pooled(hidden, batch["attention_mask"], pooling, normalize)
        vectors.extend(found.float().cpu().tolist())
    return vectors


class PRV_Torch_Embedding(AbstractTorchProvider):
    name: ClassVar[str] = "torch_embedding"
    friendly_name: ClassVar[str] = "PyTorch embedding model"
    description: ClassVar[str] = "Text embeddings with a transformers encoder"
    _abilities: ClassVar[Set[str]] = {EMBEDDINGS}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        *TORCH_SETTINGS,
        InstanceSetting("pooling", "mean or cls", default="mean"),
        InstanceSetting("normalize", "L2-normalize each vector", default="true"),
    )

    @classmethod
    def load_model(cls, instance: ProviderInstanceModel, directory: Path) -> Any:
        model = cls.pretrained(AutoModel, instance, directory)
        return EncoderModel(
            model, AutoTokenizer.from_pretrained(directory, **PRETRAINED)
        )

    @classmethod
    def pooling(cls, instance: ProviderInstanceModel) -> Tuple[str, bool]:
        pooling = cls.setting(instance, "pooling") or "mean"
        if pooling not in POOLINGS:
            raise misconfigured(f"pooling is one of {', '.join(POOLINGS)}", cls.name)
        normalize = (cls.setting(instance, "normalize") or "true").lower()
        if normalize not in ("true", "false"):
            raise misconfigured("normalize is true or false", cls.name)
        return pooling, normalize == "true"

    @classmethod
    async def embed(
        cls, instance: ProviderInstanceModel, texts: List[str]
    ) -> Dict[str, Any]:
        pooling, normalize = cls.pooling(instance)
        model = cls.model(instance)

        def work(value: Any) -> Dict[str, Any]:
            if not isinstance(value, EncoderModel):
                raise TypeError(f"{cls.name} holds a {type(value).__name__}")
            vectors = embed_texts(value, texts, pooling, normalize)
            return {
                "embeddings": vectors,
                "model": model,
                "dimensions": len(vectors[0]) if vectors else 0,
            }

        return await cls.run(instance, work)
