# SPDX-License-Identifier: AGPL-3.0-or-later
"""PyTorch models, run for real on the CPU: tiny public models downloaded
once into the shared cache (xfail when they are not cached and Hugging
Face is unreachable), checked against their SHA-256 and pinned commit.

- trl-internal-testing/tiny-Qwen2ForCausalLM-2.5 (5 MB of random
  weights, a real tokenizer and chat template): chat and completion.
- sentence-transformers/all-MiniLM-L6-v2 (91 MB): embeddings.
- openai/whisper-tiny (151 MB): transcription, of the JFK inaugural
  sample from Xenova/transformers.js-docs.

And what a model repository could do to the server, served by a real
local server: pickled weights and a repository's own code never run.
"""

import base64
import hashlib
import json
import math
import pickle
import struct
from pathlib import Path

import httpx
import numpy as np
import pytest

from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.extensions.local_ai.LocalModels import (
    LOADED_MODELS,
    download_verified,
    holds,
    model_spec,
    models_root,
)
from zephyrex.extensions.local_ai_torch.EXT_Local_AI_Torch import EXT_Local_AI_Torch
from zephyrex.extensions.local_ai_torch.PRV_Torch_Embedding import (
    PRV_Torch_Embedding,
    pooled,
)
from zephyrex.extensions.local_ai_torch.PRV_Torch_Speech import (
    PRV_Torch_Speech,
    resampled,
    wav_samples,
)
from zephyrex.extensions.local_ai_torch.PRV_Torch_TextGeneration import (
    PRV_Torch_TextGeneration,
)
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import ProviderInstanceUsageManager

QWEN = {
    "repo": "trl-internal-testing/tiny-Qwen2ForCausalLM-2.5",
    "revision": "ce8d0bf270b28c8fab026ced69fe8aa14b0f0eda",
    "files": {
        "added_tokens.json": "58b54bbe36fc752f79a24a271ef66a0a0830054b4dfad94bde757d851968060b",
        "chat_template.jinja": "cd8e9439f0570856fd70470bf8889ebd8b5d1107207f67a5efb46e342330527f",
        "config.json": "114c0f6f53f591bb5bd28e54f6aedc534171e797581b5f10078646ea468035d9",
        "generation_config.json": "fd7be13576a65237b59df8656aeb8af61b94368650e2c13ae2f1ba95266c98e4",
        "merges.txt": "8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5",
        "model.safetensors": "653e6a0513543f691f5926b7deb442fe9a179d82cbcd59dddaf3de109c9f5f70",
        "special_tokens_map.json": "76862e765266b85aa9459767e33cbaf13970f327a0e88d1c65846c2ddd3a1ecd",
        "tokenizer.json": "9c5ae00e602b8860cbd784ba82a8aa14e8feecec692e7076590d014d7b7fdafa",
        "tokenizer_config.json": "0a04a9d7d4a62b28482bdfe726c122756de85714fb64166ace92ae75b8f57614",
        "vocab.json": "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910",
    },
}
MINILM = {
    "repo": "sentence-transformers/all-MiniLM-L6-v2",
    "revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
    "files": {
        "config.json": "953f9c0d463486b10a6871cc2fd59f223b2c70184f49815e7efbcab5d8908b41",
        "model.safetensors": "53aa51172d142c89d9012cce15ae4d6cc0ca6895895114379cacb4fab128d9db",
        "special_tokens_map.json": "303df45a03609e4ead04bc3dc1536d0ab19b5358db685b6f3da123d05ec200e3",
        "tokenizer.json": "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037",
        "tokenizer_config.json": "acb92769e8195aabd29b7b2137a9e6d6e25c476a4f15aa4355c233426c61576b",
        "vocab.txt": "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3",
    },
}
WHISPER = {
    "repo": "openai/whisper-tiny",
    "revision": "169d4a4341b33bc18d8881c4b69c2e104e1cc0af",
    "files": {
        "added_tokens.json": "9715fd2243b6f06a5858b5e32950d2853f73dd5bc201aafcf76f5082a2d8acd1",
        "config.json": "ffdccec4f3211f4c63310f2b7098f309fe70f3952cedc5e4d11e43f5b2379b98",
        "generation_config.json": "a5d5325911f16e74001a72fa13d6e208eee51548f994646de1f4b4cc8b35b512",
        "merges.txt": "2df2990a395e35e8dfbc7511e08c12d56018d8d04691e0133e5d63b21e154dc6",
        "model.safetensors": "7ebd0e69e78190ffe1438491fa05cc1f5c1aa3a4c4db3bc1723adbb551ea2395",
        "normalizer.json": "bf1c507dc8724ca9cf9903640dacfb69dae2f00edee4f21ceba106a7392f26dd",
        "preprocessor_config.json": "9b5cd03a36fbb8a627c64d98a5b5b126ead95a77720723944487311f0110b666",
        "special_tokens_map.json": "e67ae3a0aaa99abcd9f187138e12db1f65c16a14761c50ef10eef2c174a7a691",
        "tokenizer.json": "27fc476bfe7f17299480be2273fc0608e4d5a99aba2ab5dec5374b4482d1a566",
        "tokenizer_config.json": "2a4c4281cf9f51ac6ccc406fdc711a087afe6530f671fa7b80953edc498275ce",
        "vocab.json": "8f680bba319e01a653d2e8a5dbc17a9157179e0576e6ce74ce0c06356c6e24f9",
    },
}
JFK_URL = (
    "https://huggingface.co/datasets/Xenova/transformers.js-docs/resolve/"
    "fbe92bd97d48f3ec17779d8d8f2964e1c6bc7634/jfk.wav"
)
JFK_SHA256 = "aa81c2552465568567e670f3823117e633900d16bd6202346a72f3c8464c74c8"
HELLO = [{"role": "user", "content": "Say hello."}]


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
    not all(cached(m) for m in (QWEN, MINILM, WHISPER)) and not online(),
    reason="the test models are not cached and huggingface.co is unreachable",
)


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b)) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    )


def wav(samples: np.ndarray, rate: int, width: int = 2, channels: int = 1) -> bytes:
    """A PCM WAV file, written by hand so the reader is checked against
    the format rather than against itself."""
    scale = 2 ** (8 * width - 1) - 1
    kind = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
    values = np.repeat(samples, channels) * scale
    data: bytes = (values + (128 if width == 1 else 0)).astype(kind).tobytes()
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(data),
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        rate,
        rate * channels * width,
        channels * width,
        8 * width,
        b"data",
        len(data),
    )
    return header + data


@pytest.fixture
def models(provider_instance, rotation_over, monkeypatch):
    text = provider_instance(
        PRV_Torch_TextGeneration, settings=settings(QWEN, max_tokens="24")
    )
    encoder = provider_instance(PRV_Torch_Embedding, settings=settings(MINILM))
    speech = provider_instance(PRV_Torch_Speech, settings=settings(WHISPER))
    monkeypatch.setattr(
        EXT_Local_AI_Torch,
        "_root_rotation_cache",
        rotation_over(text, encoder, speech),
    )
    yield text, encoder, speech
    for instance in (text, encoder, speech):
        LOADED_MODELS.unload(str(instance.id))


@pytest.fixture
async def jfk() -> bytes:
    target = models_root() / "test-audio" / "jfk.wav"
    if not holds(target, JFK_SHA256):
        await download_verified(JFK_URL, target, JFK_SHA256, provider="test")
    return target.read_bytes()


@needs_models
class TestModels:
    async def test_chat_in_the_neutral_shape(self, models, extension_app):
        text, _, _ = models
        answer = await EXT_Local_AI_Torch.chat(HELLO, max_tokens=8, temperature=0)
        assert answer["message"]["role"] == "assistant"
        assert isinstance(answer["message"]["content"], str)
        assert answer["model"] == QWEN["repo"]
        assert answer["finish_reason"] in ("stop", "length")
        assert answer["usage"]["input_tokens"] > 5
        assert 0 < answer["usage"]["output_tokens"] <= 8
        rows = ProviderInstanceUsageManager(
            model_registry=extension_app.state.model_registry,
            requester_id=env("ROOT_ID"),
        ).list(provider_instance_id=text.id)
        assert {r.key for r in rows} == {"input_tokens", "output_tokens"}

    async def test_greedy_completion_is_repeatable_and_capped(self, models):
        first = await EXT_Local_AI_Torch.complete_text("Hello", 500, 0)
        second = await EXT_Local_AI_Torch.complete_text("Hello", 500, 0)
        assert first["text"] == second["text"]
        assert first["usage"]["output_tokens"] <= 24

    async def test_generation_stops_at_the_deadline(self, provider_instance):
        slow = provider_instance(
            PRV_Torch_TextGeneration,
            settings=settings(QWEN, max_tokens="2000", timeout_seconds="0.01"),
        )
        try:
            answer = await PRV_Torch_TextGeneration.continue_text(slow, "Hi", None, 0)
        finally:
            LOADED_MODELS.unload(str(slow.id))
        assert answer["finish_reason"] == "length"
        assert answer["usage"]["output_tokens"] < 2000

    async def test_embeddings(self, models):
        found = await EXT_Local_AI_Torch.embed(
            [
                "The cat sat on the mat.",
                "A kitten is sitting on a rug.",
                "Quarterly revenue rose by four percent.",
            ]
        )
        assert found["dimensions"] == 384 and found["model"] == MINILM["repo"]
        cat, kitten, revenue = found["embeddings"]
        assert math.isclose(sum(v * v for v in cat), 1.0, rel_tol=1e-4)
        assert cosine(cat, kitten) > cosine(cat, revenue) + 0.2

    async def test_transcription(self, models, jfk):
        found = await EXT_Local_AI_Torch.transcribe(
            base64.b64encode(jfk).decode(), "jfk.wav", "en"
        )
        assert "ask not what your country can do for you" in found["text"].lower()
        assert found["model"] == WHISPER["repo"]

    async def test_audio_longer_than_allowed(self, provider_instance, jfk):
        short = provider_instance(
            PRV_Torch_Speech, settings=settings(WHISPER, max_audio_seconds="5")
        )
        with pytest.raises(InvalidInputExternalError, match="at most 5 seconds"):
            await PRV_Torch_Speech.transcribe(short, jfk, "jfk.wav", None)
        assert LOADED_MODELS.get(str(short.id)) is None

    async def test_listed_and_unloaded(self, models):
        text, encoder, _ = models
        await EXT_Local_AI_Torch.load_model(encoder.name)
        listed = {m["id"]: m for m in await EXT_Local_AI_Torch.list_local_models()}
        assert (
            listed[str(encoder.id)]["loaded"] and listed[str(encoder.id)]["downloaded"]
        )
        assert listed[str(text.id)]["abilities"] == ["chat", "text_completion"]
        assert not (await EXT_Local_AI_Torch.unload_model(encoder.name))["loaded"]


class Planted:
    """Pickled weights that, if unpickled, leave a file behind."""

    def __init__(self, marker: Path) -> None:
        self.marker = marker

    def __reduce__(self):
        return (Path.touch, (self.marker,))


REVISION = "0123456789abcdef0123456789abcdef01234567"


class TestHostileRepositories:
    @pytest.fixture
    def serve(self, local_http_server, set_env, tmp_path):
        """Serve ``files`` as repository ``repo`` from the allowed source,
        into a scratch models directory: the settings declaring them."""
        set_env("LOCAL_AI_MODELS_DIR", str(tmp_path / "models"))

        def _serve(repo, files):
            server = local_http_server(
                {
                    f"/{repo}/resolve/{REVISION}/{path}": (200, {}, body)
                    for path, body in files.items()
                }
            )
            set_env("LOCAL_AI_MODEL_SOURCES", server.base_url)
            return {
                "source": server.base_url,
                "repo": repo,
                "revision": REVISION,
                "files": json.dumps(
                    {p: hashlib.sha256(b).hexdigest() for p, b in files.items()}
                ),
            }

        return _serve

    async def test_pickled_weights_never_load(self, serve, provider_instance, tmp_path):
        marker = tmp_path / "unpickled"
        config = {"model_type": "gpt2", "n_layer": 1, "n_head": 1, "n_embd": 4}
        declared = serve(
            "hostile/pickle",
            {
                "config.json": json.dumps(config).encode(),
                "pytorch_model.bin": pickle.dumps(Planted(marker)),
            },
        )
        instance = provider_instance(PRV_Torch_TextGeneration, settings=declared)
        with pytest.raises(PermanentExternalError, match="could not be loaded"):
            await PRV_Torch_TextGeneration.load(instance)
        assert not marker.exists()
        assert LOADED_MODELS.get(str(instance.id)) is None

    async def test_a_repositorys_own_code_never_runs(
        self, serve, provider_instance, tmp_path
    ):
        marker = tmp_path / "imported"
        code = f"import pathlib\npathlib.Path({str(marker)!r}).touch()\n".encode()
        config = {
            "model_type": "hostile",
            "architectures": ["HostileModel"],
            "auto_map": {
                "AutoConfig": "configuration_hostile.HostileConfig",
                "AutoModel": "modeling_hostile.HostileModel",
            },
        }
        declared = serve(
            "hostile/code",
            {
                "config.json": json.dumps(config).encode(),
                "configuration_hostile.py": code,
                "modeling_hostile.py": code,
            },
        )
        instance = provider_instance(PRV_Torch_Embedding, settings=declared)
        with pytest.raises(PermanentExternalError, match="could not be loaded"):
            await PRV_Torch_Embedding.load(instance)
        assert not marker.exists()


class TestAudio:
    def test_wav_samples(self):
        tone = np.sin(np.linspace(0, 20 * np.pi, 800)).astype(np.float32)
        for width in (1, 2, 4):
            samples, rate = wav_samples(wav(tone, 8000, width, channels=2))
            assert rate == 8000 and len(samples) == 800
            assert np.allclose(samples, tone, atol=0.02)

    def test_resampled(self):
        samples = np.arange(8000, dtype=np.float32) / 8000
        found = resampled(samples, 8000, 16000)
        assert len(found) == 16000 and found.dtype == np.float32
        assert np.allclose(found[::2][:-1], samples[:-1], atol=1e-3)

    @pytest.mark.parametrize("audio", [b"ID3 not wav", b"", b"RIFF\x00\x00"])
    def test_only_wav(self, audio):
        with pytest.raises(InvalidInputExternalError, match="WAV"):
            wav_samples(audio)

    async def test_a_language_is_a_code_or_name(self, provider_instance):
        speech = provider_instance(PRV_Torch_Speech, settings=settings(WHISPER))
        with pytest.raises(InvalidInputExternalError, match="language"):
            await PRV_Torch_Speech.transcribe(
                speech, wav(np.zeros(160, np.float32), 16000), "a.wav", "../en"
            )


class TestRefusals:
    @pytest.fixture
    def text(self, provider_instance):
        return provider_instance(PRV_Torch_TextGeneration, settings=settings(QWEN))

    async def test_tools_and_images(self, text):
        tool = {"type": "function", "function": {"name": "f", "parameters": {}}}
        with pytest.raises(PermanentExternalError, match="tool calls"):
            await PRV_Torch_TextGeneration.chat(text, HELLO, tools=[tool])
        with pytest.raises(PermanentExternalError, match="images"):
            await PRV_Torch_TextGeneration.chat(
                text,
                [{"role": "user", "content": "x", "images": ["https://x.test/a.png"]}],
            )

    @pytest.mark.parametrize(
        "setting", [{"device": "cuda; rm -rf /"}, {"dtype": "float8"}]
    )
    async def test_device_and_dtype(self, provider_instance, setting):
        instance = provider_instance(
            PRV_Torch_TextGeneration, settings=settings(QWEN, **setting)
        )
        with pytest.raises(PermanentExternalError, match="misconfigured"):
            PRV_Torch_TextGeneration.pretrained(None, instance, Path("."))


def test_pooling():
    import torch

    hidden = torch.tensor([[[1.0, 0.0], [3.0, 4.0], [9.0, 9.0]]])
    mask = torch.tensor([[1, 1, 0]])
    assert pooled(hidden, mask, "mean", False).tolist() == [[2.0, 2.0]]
    assert pooled(hidden, mask, "cls", False).tolist() == [[1.0, 0.0]]
    assert torch.allclose(
        pooled(hidden, mask, "mean", True), torch.tensor([[0.7071, 0.7071]]), atol=1e-4
    )
