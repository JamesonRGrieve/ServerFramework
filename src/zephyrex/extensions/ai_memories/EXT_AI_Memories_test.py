# SPDX-License-Identifier: AGPL-3.0-or-later
"""Long-term memories: ranked by meaning when an embedding model answers
(a local OpenAI-compatible server here, with fixed vectors) and by words
when none is configured; kept for the requester and seen by no one else;
and read, searched and deleted, but never written, through the generic
routes. The old extension kept memories in process memory and lost them."""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai.EXT_AI import EXT_AI
from zephyrex.extensions.ai.PRV_OpenAICompatible import PRV_OpenAICompatible_AI
from zephyrex.extensions.ai_memories.BLL_AI_Memories import cosine, rank, words
from zephyrex.extensions.ai_memories.EXT_AI_Memories import EXT_AI_Memories
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.pydantic2.registry import ModelRegistry


def memory(content, embedding=None, model=None, created="2026-10-01"):
    return SimpleNamespace(
        content=content,
        key=None,
        embedding=json.dumps(embedding) if embedding else None,
        embedding_model=model,
        created_at=created,
    )


class TestRanking:
    def test_cosine(self):
        assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
        assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
        assert cosine([1, 0], [1, 0, 0]) == 0.0

    def test_words(self):
        assert words("Ada's cat, Bert!") == ["ada", "s", "cat", "bert"]

    def test_meaning_outranks_words(self):
        near = memory("the user likes tea", [1.0, 0.0], "m")
        far = memory("tea is mentioned here", [0.0, 1.0], "m")
        found = rank([far, near], "tea", [0.9, 0.1], "m", 5)
        assert found[0] is near

    def test_another_models_embedding_is_not_compared(self):
        other = memory("unrelated", [1.0, 0.0], "other-model")
        assert rank([other], "query", [1.0, 0.0], "m", 5) == []

    def test_words_alone(self):
        hit = memory("the deploy key rotates monthly", created="2026-09-01")
        newer = memory("deploy notes", created="2026-10-02")
        miss = memory("lunch plans")
        found = rank([hit, newer, miss], "deploy key", None, None, 5)
        assert found == [hit, newer]


EMBEDDINGS = {
    "the user prefers tea": [1.0, 0.0, 0.0],
    "the server room is cold": [0.0, 1.0, 0.0],
    "what does the user drink?": [0.9, 0.1, 0.0],
}


def embeddings_server(request):
    """An OpenAI-compatible /embeddings answer for the text sent."""
    text = json.loads(request.body)["input"][0]
    return (
        200,
        {"Content-Type": "application/json"},
        json.dumps(
            {
                "model": "embed-test",
                "data": [{"index": 0, "embedding": EMBEDDINGS[text]}],
            }
        ).encode(),
    )


class TestMemories(ExtensionServerMixin):
    extension_class = EXT_AI_Memories

    @pytest.fixture(scope="module")
    def extension_app(self, server):
        """Provider instances and rotations go in this test server's app,
        which loads the ai extension (an optional dependency)."""
        return server.app

    @pytest.fixture(autouse=True)
    def attached(self, server, monkeypatch, rotation_over) -> None:
        registry = server.app.state.model_registry
        monkeypatch.setattr(
            ModelRegistry, "attached", classmethod(lambda cls: registry)
        )
        # No embedding model unless a test configures one: an empty rotation,
        # so instances seeded from the environment are never called.
        monkeypatch.setattr(EXT_AI, "_root_rotation_cache", rotation_over())

    async def test_recall_by_words_without_an_embedding_model(self, admin_a):
        agent = "agent-words"
        kept = await EXT_AI_Memories.keep_memory(
            admin_a.id, agent, "The deploy key rotates monthly", key="ops"
        )
        await EXT_AI_Memories.keep_memory(admin_a.id, agent, "Lunch is at noon")
        found = await EXT_AI_Memories.recall_memories(admin_a.id, agent, "deploy key")
        assert [m["id"] for m in found] == [kept["id"]]
        recent = await EXT_AI_Memories.recent_memories(admin_a.id, agent)
        assert recent[0]["content"] == "Lunch is at noon"

    async def test_recall_by_meaning(
        self, admin_a, local_http_server, provider_instance, rotation_over, monkeypatch
    ):
        model_server = local_http_server({"/embeddings": embeddings_server})
        instance = provider_instance(
            PRV_OpenAICompatible_AI,
            settings={
                "base_url": model_server.base_url,
                "embedding_model": "embed-test",
            },
        )
        monkeypatch.setattr(EXT_AI, "_root_rotation_cache", rotation_over(instance))
        agent = "agent-meaning"
        tea = await EXT_AI_Memories.keep_memory(
            admin_a.id, agent, "the user prefers tea"
        )
        await EXT_AI_Memories.keep_memory(admin_a.id, agent, "the server room is cold")
        found = await EXT_AI_Memories.recall_memories(
            admin_a.id, agent, "what does the user drink?", limit=1
        )
        assert [m["id"] for m in found] == [tea["id"]]
        assert len(model_server.requests) == 3  # two memories and the query

    async def test_memories_are_their_users(self, admin_a, admin_b):
        agent = "agent-private"
        kept = await EXT_AI_Memories.keep_memory(admin_a.id, agent, "a private note")
        assert await EXT_AI_Memories.recent_memories(admin_b.id, agent) == []
        with pytest.raises(HTTPException):
            await EXT_AI_Memories.forget_memory(admin_b.id, kept["id"])
        await EXT_AI_Memories.forget_memory(admin_a.id, kept["id"])
        assert await EXT_AI_Memories.recent_memories(admin_a.id, agent) == []

    async def test_checks(self, admin_a):
        with pytest.raises(InvalidInputExternalError):
            await EXT_AI_Memories.keep_memory(admin_a.id, "a", " ")
        with pytest.raises(InvalidInputExternalError):
            await EXT_AI_Memories.recall_memories(admin_a.id, "a", "q", limit=0)

    def test_routes(self, server, admin_a, admin_b):
        headers = {"Authorization": f"Bearer {admin_a.jwt}"}
        kept = server.post(
            "/v1/memory/remember",
            json={"agent_id": "agent-routes", "content": "the badge colour is green"},
            headers=headers,
        )
        assert kept.status_code == 200, kept.text
        memory_id = kept.json()["id"]
        assert kept.json()["user_id"] == admin_a.id
        recalled = server.post(
            "/v1/memory/recall",
            json={"agent_id": "agent-routes", "query": "badge colour"},
            headers=headers,
        )
        assert [m["id"] for m in recalled.json()["memories"]] == [memory_id]
        # Memories are kept only through remember: no generic create or update.
        created = server.post(
            "/v1/memory",
            json={"memory": {"agent_id": "x", "content": "y", "embedding": "[1]"}},
            headers=headers,
        )
        assert created.status_code in (404, 405), created.text
        other = server.get(
            f"/v1/memory/{memory_id}",
            headers={"Authorization": f"Bearer {admin_b.jwt}"},
        )
        assert other.status_code == 404
        gone = server.delete(f"/v1/memory/{memory_id}", headers=headers)
        assert gone.status_code == 204, gone.text
