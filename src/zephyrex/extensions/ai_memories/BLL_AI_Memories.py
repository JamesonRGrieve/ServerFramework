# SPDX-License-Identifier: AGPL-3.0-or-later
"""Long-term memories: what an agent (or a user's tooling) keeps beyond a
single turn, in the app's own database.

A memory belongs to the user it was kept for (its owner, who sees it
through the REST routes and the abilities) and names the agent it is
about. Its embedding, when an embedding model is configured in the ai
extension, lets recall rank by meaning; without one, recall matches words.
Embeddings are stored with the model that made them, and only compared
with embeddings from the same model.
"""

import json
import math
from typing import Any, ClassVar, List, Optional, Sequence, Tuple

from pydantic import BaseModel as RouteModel
from pydantic import Field

from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.AbstractLogicManager.ownership import each_created, owned_by
from zephyrex.logic.BLL_Auth import TeamModel, UserModel
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.pydantic2.fastapi.types import RouteType
from zephyrex.pydantic2.registry import BaseModel

MAX_MEMORY_CHARACTERS = 20_000
MAX_RECALL = 50
# How many of an agent's most recent memories recall ranks.
RECALL_CANDIDATES = 2_000
# Below this cosine similarity a memory is not considered related.
MIN_SIMILARITY = 0.2


async def embedding_of(text: str) -> Tuple[Optional[List[float]], Optional[str]]:
    """``text``'s embedding and the model that made it, from the ai
    extension; ``(None, None)`` when no embedding model is configured or the
    call fails (the memory is still kept, and recalled by its words)."""
    from fastapi import HTTPException

    from zephyrex.extensions.ai.EXT_AI import EXT_AI
    from zephyrex.extensions.ExternalErrors import BaseExternalError

    try:
        found = await EXT_AI.embed([text])
    except (HTTPException, BaseExternalError) as exc:
        logger.warning(f"Memory kept without an embedding: {exc}")
        return None, None
    embeddings = found.get("embeddings") or []
    return (embeddings[0] if embeddings else None), found.get("model")


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def words(text: str) -> List[str]:
    return [
        w for w in "".join(c.lower() if c.isalnum() else " " for c in text).split() if w
    ]


def rank(
    memories: List[Any],
    query: str,
    query_embedding: Optional[List[float]],
    embedding_model: Optional[str],
    limit: int,
) -> List[Any]:
    """The ``limit`` memories most related to ``query``: by embedding
    similarity where both sides have one from the same model, then by the
    share of the query's words they contain; newest first among equals."""
    terms = set(words(query))

    def score(memory: Any) -> tuple:
        similarity = 0.0
        if (
            query_embedding
            and memory.embedding
            and memory.embedding_model == embedding_model
        ):
            similarity = cosine(query_embedding, json.loads(memory.embedding))
        present = set(words(f"{memory.key or ''} {memory.content}"))
        overlap = len(terms & present) / len(terms) if terms else 0.0
        return (similarity if similarity >= MIN_SIMILARITY else 0.0, overlap)

    scored = [(score(m), m) for m in memories]
    related = [(s, m) for s, m in scored if s[0] > 0 or s[1] > 0]
    related.sort(key=lambda pair: (pair[0], str(pair[1].created_at)), reverse=True)
    return [m for _, m in related[:limit]]


class MemoryModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference.Optional,
    TeamModel.Reference.ID.Optional,
    metaclass=ModelMeta,
):
    agent_id: str = Field(..., description="The agent the memory is about")
    conversation_id: Optional[str] = Field(
        None, description="The conversation it came from, if any"
    )
    key: Optional[str] = Field(None, description="A short label for the memory")
    content: str = Field(
        ..., description="What is remembered", max_length=MAX_MEMORY_CHARACTERS
    )
    source: str = Field("agent", description="Where it came from (agent, user, import)")
    embedding: Optional[str] = Field(
        None, description="Set by the server: the embedding, as a JSON array"
    )
    embedding_model: Optional[str] = Field(
        None, description="Set by the server: the model that made the embedding"
    )

    table_comment: ClassVar[str] = (
        "Long-term memories an agent keeps for its user, with embeddings for recall"
    )

    class Create(
        BaseModel, UserModel.Reference.ID.Optional, TeamModel.Reference.ID.Optional
    ):
        agent_id: str
        conversation_id: Optional[str] = None
        key: Optional[str] = None
        content: str = Field(..., max_length=MAX_MEMORY_CHARACTERS)
        source: str = "agent"
        embedding: Optional[str] = None
        embedding_model: Optional[str] = None

    class Update(BaseModel):
        key: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
    ):
        agent_id: Optional[StringSearchModel] = None
        conversation_id: Optional[StringSearchModel] = None
        key: Optional[StringSearchModel] = None
        content: Optional[StringSearchModel] = None
        source: Optional[StringSearchModel] = None


class RememberRequest(RouteModel):
    agent_id: str
    content: str = Field(..., max_length=MAX_MEMORY_CHARACTERS)
    key: Optional[str] = None
    conversation_id: Optional[str] = None


class RecallRequest(RouteModel):
    agent_id: str
    query: str
    limit: int = Field(5, ge=1, le=MAX_RECALL)


class RecalledMemories(RouteModel):
    memories: List[MemoryModel]


class MemoryManager(AbstractBLLManager, RouterMixin):
    """Memories are kept through :meth:`keep` (which the remember route and
    the abilities use, embedding first); the generic routes read, search and
    delete them."""

    _model = MemoryModel

    prefix: ClassVar[Optional[str]] = "/v1/memory"
    tags: ClassVar[Optional[List[str]]] = ["Memories"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[List[RouteType]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
        RouteType.DELETE,
    ]

    def create(self, **kwargs: Any) -> Any:
        """Memories kept for the requester (ROOT and SYSTEM name the owner)."""
        return super().create(**each_created(kwargs, owned_by(self.requester.id)))

    def update(self, id: str, **kwargs: Any) -> Any:
        """Only a memory's label changes; what is remembered does not."""
        return super().update(id, **{k: v for k, v in kwargs.items() if k == "key"})

    async def keep(
        self,
        agent_id: str,
        content: str,
        key: Optional[str] = None,
        conversation_id: Optional[str] = None,
        source: str = "agent",
    ) -> Any:
        """Embed ``content`` (when a model is configured) and keep it."""
        vector, model = await embedding_of(content)
        return self.create(
            agent_id=agent_id,
            content=content,
            key=key,
            conversation_id=conversation_id,
            source=source,
            embedding=json.dumps(vector) if vector else None,
            embedding_model=model,
        )

    def recent(self, agent_id: str, limit: int) -> List[Any]:
        result: List[Any] = self.list(
            agent_id=agent_id, sort_by="created_at", sort_order="desc", limit=limit
        )
        return result

    async def recall(self, agent_id: str, query: str, limit: int) -> List[Any]:
        """The memories most related to ``query`` (the most recent, for an
        empty query)."""
        if not query.strip():
            return self.recent(agent_id, limit)
        vector, model = await embedding_of(query)
        return rank(
            self.recent(agent_id, RECALL_CANDIDATES), query, vector, model, limit
        )

    @custom_route(
        method="POST",
        path="/remember",
        input_model=RememberRequest,
        output_model=MemoryModel,
        authentication_type="jwt",
        openapi_tags=("Memories",),
        summary="Keep a memory, embedded when an embedding model is configured",
        expose_in=(ExposeIn.REST,),
    )
    async def remember_route(self, body: RememberRequest) -> MemoryModel:
        kept: MemoryModel = await self.keep(
            body.agent_id, body.content, body.key, body.conversation_id, source="user"
        )
        return kept

    @custom_route(
        method="POST",
        path="/recall",
        input_model=RecallRequest,
        output_model=RecalledMemories,
        authentication_type="jwt",
        openapi_tags=("Memories",),
        summary="The memories most related to a query",
        expose_in=(ExposeIn.REST,),
    )
    async def recall_route(self, body: RecallRequest) -> RecalledMemories:
        return RecalledMemories(
            memories=await self.recall(body.agent_id, body.query, body.limit)
        )
