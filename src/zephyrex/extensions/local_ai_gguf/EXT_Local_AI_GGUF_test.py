# SPDX-License-Identifier: AGPL-3.0-or-later
"""GGUF models, run for real on the CPU: tiny public models downloaded
once into the shared cache (xfail when they are not cached and Hugging
Face is unreachable), checked against their SHA-256 and pinned commit.

- ggml-org/models-moved ``tinyllamas/stories260K.gguf`` (1.2 MB): chat and
  text completion.
- CompendiumLabs/bge-small-en-v1.5-gguf ``bge-small-en-v1.5-q4_k_m.gguf``
  (25 MB): embeddings.
"""

import json
import math

import httpx
import pytest
from fastapi import HTTPException

from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.extensions.local_ai.LocalModels import (
    LOADED_MODELS,
    model_spec,
    models_root,
)
from zephyrex.extensions.local_ai_gguf.EXT_Local_AI_GGUF import EXT_Local_AI_GGUF
from zephyrex.extensions.local_ai_gguf.GGUF import DeadlineStop
from zephyrex.extensions.local_ai_gguf.PRV_GGUF_Chat import PRV_GGUF_Chat
from zephyrex.extensions.local_ai_gguf.PRV_GGUF_Embedding import PRV_GGUF_Embedding
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import ProviderInstanceUsageManager

STORIES = {
    "repo": "ggml-org/models-moved",
    "revision": "499bc8821c6b12b4e53c5bffcb21ec206f212d81",
    "files": {
        "tinyllamas/stories260K.gguf": (
            "270cba1bd5109f42d03350f60406024560464db173c0e387d91f0426d3bd256d"
        )
    },
}
BGE = {
    "repo": "CompendiumLabs/bge-small-en-v1.5-gguf",
    "revision": "d32f8c040ea3b516330eeb75b72bcc2d3a780ab7",
    "files": {
        "bge-small-en-v1.5-q4_k_m.gguf": (
            "363a0a4855dff6c653e06efe3209157debcf7f74e52d0d7c71e2747cd523043e"
        )
    },
}
HELLO = [{"role": "user", "content": "Once upon a time"}]


def settings(model, **extra):
    return {
        "repo": model["repo"],
        "revision": model["revision"],
        "files": json.dumps(model["files"]),
        **extra,
    }


def cached(model) -> bool:
    spec = model_spec(
        None, model["repo"], model["revision"], json.dumps(model["files"]), "test"
    )
    root = models_root()
    return all(spec.local_path(root, f, "test").is_file() for f in spec.files)


def online() -> bool:
    try:
        httpx.head("https://huggingface.co", timeout=10)
        return True
    except httpx.HTTPError:
        return False


needs_models = pytest.mark.xfail(
    not (cached(STORIES) and cached(BGE)) and not online(),
    reason="the test models are not cached and huggingface.co is unreachable",
)


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b)) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    )


@pytest.fixture
def models(provider_instance, rotation_over, monkeypatch):
    chat = provider_instance(PRV_GGUF_Chat, settings=settings(STORIES, max_tokens="64"))
    embedder = provider_instance(PRV_GGUF_Embedding, settings=settings(BGE))
    monkeypatch.setattr(
        EXT_Local_AI_GGUF, "_root_rotation_cache", rotation_over(chat, embedder)
    )
    yield chat, embedder
    for instance in (chat, embedder):
        LOADED_MODELS.unload(str(instance.id))


@needs_models
class TestModels:
    async def test_download_and_list(self, models):
        chat, embedder = models
        downloaded = await EXT_Local_AI_GGUF.download_model(chat.name)
        assert downloaded["downloaded"] and not downloaded["loaded"]
        assert downloaded["files"] == ["tinyllamas/stories260K.gguf"]
        listed = {m["id"]: m for m in await EXT_Local_AI_GGUF.list_local_models()}
        assert listed[str(chat.id)]["provider"] == "gguf_chat"
        assert listed[str(embedder.id)]["abilities"] == ["embeddings"]
        seeded = [m for m in listed.values() if m["name"] == "Root_GgufChat"]
        assert seeded and seeded[0]["error"] == "model not configured"

    async def test_the_unconfigured_seeded_instance_is_passed_over(
        self, models, provider_instance, rotation_over, monkeypatch
    ):
        chat, _ = models
        unconfigured = provider_instance(PRV_GGUF_Chat)
        monkeypatch.setattr(
            EXT_Local_AI_GGUF, "_root_rotation_cache", rotation_over(unconfigured, chat)
        )
        answer = await EXT_Local_AI_GGUF.generate_text("Hi", max_tokens=4)
        assert answer["model"] == "ggml-org/models-moved"

    async def test_chat_in_the_neutral_shape(self, models, extension_app):
        chat, _ = models
        answer = await EXT_Local_AI_GGUF.chat(HELLO, max_tokens=16, temperature=0)
        assert answer["message"]["role"] == "assistant"
        assert isinstance(answer["message"]["content"], str)
        assert answer["message"]["tool_calls"] is None
        assert answer["model"] == "ggml-org/models-moved"
        assert answer["finish_reason"] in ("stop", "length")
        assert 0 < answer["usage"]["output_tokens"] <= 16
        rows = ProviderInstanceUsageManager(
            model_registry=extension_app.state.model_registry,
            requester_id=env("ROOT_ID"),
        ).list(provider_instance_id=chat.id)
        assert {r.key for r in rows} == {"input_tokens", "output_tokens"}

    async def test_greedy_chat_is_repeatable(self, models):
        first = await EXT_Local_AI_GGUF.generate_text(
            "Lily", max_tokens=12, temperature=0
        )
        second = await EXT_Local_AI_GGUF.generate_text(
            "Lily", max_tokens=12, temperature=0
        )
        assert first["text"] == second["text"]

    async def test_a_request_is_cut_to_the_instance_cap(self, models):
        answer = await EXT_Local_AI_GGUF.complete_text(
            "Once upon a time", max_tokens=5000, temperature=0
        )
        assert answer["usage"]["output_tokens"] <= 64

    async def test_raw_completion(self, models):
        answer = await EXT_Local_AI_GGUF.complete_text(
            "Once upon a time", max_tokens=8, temperature=0
        )
        assert answer["text"] and answer["model"] == "ggml-org/models-moved"
        assert answer["usage"]["input_tokens"] > 0

    async def test_generation_stops_at_the_deadline(self, provider_instance):
        slow = provider_instance(
            PRV_GGUF_Chat,
            settings=settings(STORIES, max_tokens="400", timeout_seconds="0.000001"),
        )
        try:
            answer = await PRV_GGUF_Chat.continue_text(
                slow, "Once upon a time", None, 0
            )
        finally:
            LOADED_MODELS.unload(str(slow.id))
        assert answer["finish_reason"] == "length"
        assert answer["usage"]["output_tokens"] <= 2

    async def test_embeddings(self, models):
        found = await EXT_Local_AI_GGUF.embed(
            [
                "The cat sat on the mat.",
                "A kitten is sitting on a rug.",
                "Quarterly revenue rose by four percent.",
            ]
        )
        assert found["dimensions"] == 384 and len(found["embeddings"]) == 3
        assert found["model"] == "CompendiumLabs/bge-small-en-v1.5-gguf"
        cat, kitten, revenue = found["embeddings"]
        assert cosine(cat, kitten) > cosine(cat, revenue)

    async def test_a_chat_model_does_not_embed(self, models):
        chat, _ = models
        with pytest.raises(PermanentExternalError, match="embeddings"):
            await EXT_Local_AI_GGUF.embed(["x"], model=chat.name)

    async def test_unload_and_load_again(self, models):
        chat, _ = models
        await EXT_Local_AI_GGUF.load_model(chat.name)
        assert LOADED_MODELS.get(str(chat.id)) is not None
        unloaded = await EXT_Local_AI_GGUF.unload_model(str(chat.id))
        assert not unloaded["loaded"] and LOADED_MODELS.get(str(chat.id)) is None
        answer = await EXT_Local_AI_GGUF.generate_text("Hi", max_tokens=4)
        assert answer["model"] == "ggml-org/models-moved"

    async def test_a_prompt_longer_than_the_context_is_the_callers(
        self, provider_instance
    ):
        small = provider_instance(
            PRV_GGUF_Chat, settings=settings(STORIES, context_length="32")
        )
        try:
            with pytest.raises(InvalidInputExternalError):
                await PRV_GGUF_Chat.continue_text(small, "Once upon a time " * 50, 4, 0)
        finally:
            LOADED_MODELS.unload(str(small.id))

    async def test_a_file_that_does_not_match_its_digest_is_never_loaded(
        self, models, provider_instance
    ):
        await EXT_Local_AI_GGUF.download_model(models[0].name)
        forged = dict(STORIES, files={"tinyllamas/stories260K.gguf": "0" * 64})
        instance = provider_instance(PRV_GGUF_Chat, settings=settings(forged))
        with pytest.raises(PermanentExternalError, match="SHA-256"):
            await PRV_GGUF_Chat.chat(instance, HELLO)
        assert LOADED_MODELS.get(str(instance.id)) is None
        assert cached(STORIES)


class TestRefusals:
    @pytest.fixture
    def chat(self, provider_instance):
        return provider_instance(PRV_GGUF_Chat, settings=settings(STORIES))

    async def test_tools(self, chat):
        tool = {"type": "function", "function": {"name": "f", "parameters": {}}}
        with pytest.raises(PermanentExternalError, match="tool calls"):
            await PRV_GGUF_Chat.chat(chat, HELLO, tools=[tool])

    async def test_images(self, chat):
        with pytest.raises(PermanentExternalError, match="images"):
            await PRV_GGUF_Chat.chat(
                chat,
                [{"role": "user", "content": "x", "images": ["https://x.test/a.png"]}],
            )

    async def test_temperature(self, chat):
        with pytest.raises(InvalidInputExternalError):
            await PRV_GGUF_Chat.chat(chat, HELLO, temperature=7)

    async def test_a_path_out_of_the_cache(self, provider_instance):
        instance = provider_instance(
            PRV_GGUF_Chat,
            settings=settings(dict(STORIES, files={"../../../etc/passwd": "0" * 64})),
        )
        with pytest.raises(PermanentExternalError, match="plain relative"):
            await PRV_GGUF_Chat.download(instance)

    async def test_a_source_not_allowed(self, provider_instance, local_http_server):
        server = local_http_server({})
        instance = provider_instance(
            PRV_GGUF_Chat, settings=settings(STORIES, source=server.base_url)
        )
        with pytest.raises(PermanentExternalError, match="LOCAL_AI_MODEL_SOURCES"):
            await PRV_GGUF_Chat.download(instance)
        assert server.requests == []

    async def test_an_unknown_model(self, models):
        with pytest.raises(HTTPException) as raised:
            await EXT_Local_AI_GGUF.unload_model("no-such-model")
        assert raised.value.status_code == 404


def test_the_deadline_leaves_only_the_end_of_text():
    import numpy as np

    stop = DeadlineStop(0.0, 2)
    scores = stop(np.zeros(1, dtype=np.intc), np.ones(4, dtype=np.single))
    assert stop.fired and scores[2] == 0.0 and np.isneginf(scores[[0, 1, 3]]).all()
    patient = DeadlineStop(float("inf"), 2)
    same = np.ones(4, dtype=np.single)
    assert patient(np.zeros(1, dtype=np.intc), same) is same and not patient.fired
