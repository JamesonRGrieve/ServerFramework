# SPDX-License-Identifier: AGPL-3.0-or-later
"""A GGUF embedding model: a vector per text, pooled as the model file
says (mean, CLS, last token)."""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.ai.EXT_AI import EMBEDDINGS
from zephyrex.extensions.local_ai_gguf.GGUF import (
    AbstractGGUFProvider,
    embedding_rows,
    library_failure,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class PRV_GGUF_Embedding(AbstractGGUFProvider):
    name: ClassVar[str] = "gguf_embedding"
    friendly_name: ClassVar[str] = "GGUF embedding model"
    description: ClassVar[str] = "Text embeddings with a GGUF model"
    _abilities: ClassVar[Set[str]] = {EMBEDDINGS}
    embedding: ClassVar[bool] = True

    @classmethod
    async def embed(
        cls, instance: ProviderInstanceModel, texts: List[str]
    ) -> Dict[str, Any]:
        model = cls.model(instance)

        def work(value: Any) -> Dict[str, Any]:
            try:
                answer = cls.llama(value).create_embedding(texts)
            except ValueError as exc:
                raise library_failure(exc) from exc
            vectors = embedding_rows(list(answer["data"]), len(texts), cls.name)
            return {
                "embeddings": vectors,
                "model": model,
                "dimensions": len(vectors[0]) if vectors else 0,
            }

        return await cls.run(instance, work)
