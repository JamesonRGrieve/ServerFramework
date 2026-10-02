# SPDX-License-Identifier: AGPL-3.0-or-later
"""AI providers: what each sends (as a local server receives it) and how
it reads the documented answers; refused keys typed as auth failures,
including the servers that refuse one with a 400; real calls refusing
bogus keys (xfail when the API is unreachable); and one short live call
per provider with a real key."""

import base64
import json

import httpx
import pytest

from zephyrex.extensions.ai.PRV_AGInYourPC import PRV_AGInYourPC_AI
from zephyrex.extensions.ai.PRV_Anthropic import PRV_Anthropic_AI
from zephyrex.extensions.ai.PRV_Azure import PRV_Azure_AI
from zephyrex.extensions.ai.PRV_DeepSeek import PRV_DeepSeek_AI
from zephyrex.extensions.ai.PRV_ElevenLabs import PRV_ElevenLabs_AI
from zephyrex.extensions.ai.PRV_Google import PRV_Google_AI
from zephyrex.extensions.ai.PRV_HuggingFace import PRV_HuggingFace_AI
from zephyrex.extensions.ai.PRV_OpenAI import PRV_OpenAI_AI
from zephyrex.extensions.ai.PRV_OpenAICompatible import PRV_OpenAICompatible_AI
from zephyrex.extensions.ai.PRV_XAI import PRV_XAI_AI
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    TransientExternalError,
)

JSON = {"Content-Type": "application/json"}
HELLO = [{"role": "user", "content": "Say hello."}]
CHAT_REPLY = json.dumps(
    {
        "model": "gpt-5-mini",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "Hello."},
            }
        ],
        "usage": {"prompt_tokens": 9, "completion_tokens": 2},
    }
).encode()


def _online(url: str) -> bool:
    try:
        httpx.head(url, timeout=5)
        return True
    except httpx.HTTPError:
        return False


def reachable(url: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(not _online(url), reason=f"{url} is unreachable")


def body_of(server, index: int = 0):
    return json.loads(server.requests[index].body)


class TestOpenAIWire:
    @pytest.fixture
    def openai(self, local_http_server, provider_instance):
        server = local_http_server(
            {
                "/chat/completions": (200, JSON, CHAT_REPLY),
                "/embeddings": (
                    200,
                    JSON,
                    json.dumps(
                        {
                            "model": "text-embedding-3-small",
                            "data": [
                                {"index": 1, "embedding": [0.3, 0.4]},
                                {"index": 0, "embedding": [0.1, 0.2]},
                            ],
                        }
                    ).encode(),
                ),
                "/images/generations": (
                    200,
                    JSON,
                    json.dumps({"data": [{"b64_json": "aW1n"}]}).encode(),
                ),
                "/audio/transcriptions": (200, JSON, b'{"text": "hello world"}'),
                "/audio/speech": (200, {"Content-Type": "audio/mpeg"}, b"ID3mp3"),
            }
        )
        instance = provider_instance(
            PRV_OpenAI_AI, api_key="sk-test", settings={"base_url": server.base_url}
        )
        return server, instance

    async def test_chat(self, openai):
        server, instance = openai
        answer = await PRV_OpenAI_AI.chat(
            instance, HELLO, max_tokens=50, temperature=0.2
        )
        assert answer["message"]["content"] == "Hello."
        sent = body_of(server)
        assert sent["model"] == "gpt-5-mini"
        assert sent["max_completion_tokens"] == 50 and "max_tokens" not in sent
        assert sent["temperature"] == 0.2
        assert server.requests[0].headers["authorization"] == "Bearer sk-test"

    async def test_a_chat_leaves_out_what_was_not_asked(self, openai):
        server, instance = openai
        await PRV_OpenAI_AI.chat(instance, HELLO)
        assert set(body_of(server)) == {"model", "messages"}

    async def test_embeddings_in_input_order(self, openai):
        _, instance = openai
        found = await PRV_OpenAI_AI.embed(instance, ["a", "b"])
        assert found["embeddings"] == [[0.1, 0.2], [0.3, 0.4]]
        assert found["dimensions"] == 2

    async def test_image(self, openai):
        server, instance = openai
        found = await PRV_OpenAI_AI.image(instance, "a red cube", "1024x1024")
        assert found == {"images": [{"base64": "aW1n"}], "model": "gpt-image-1"}
        assert body_of(server)["size"] == "1024x1024"

    async def test_transcription_sends_the_file(self, openai):
        server, instance = openai
        found = await PRV_OpenAI_AI.transcribe(instance, b"RIFFwav", "note.wav", "en")
        assert found["text"] == "hello world"
        sent = server.requests[0]
        assert sent.headers["content-type"].startswith("multipart/form-data")
        assert b'filename="note.wav"' in sent.body and b"RIFFwav" in sent.body
        assert b"gpt-4o-mini-transcribe" in sent.body

    async def test_speech(self, openai):
        server, instance = openai
        found = await PRV_OpenAI_AI.speak(instance, "Hello", None)
        assert base64.b64decode(found["audio_base64"]) == b"ID3mp3"
        assert body_of(server)["voice"] == "alloy"


class TestOtherWires:
    async def test_a_compatible_server_uses_max_tokens(
        self, local_http_server, provider_instance
    ):
        server = local_http_server({"/v1/chat/completions": (200, JSON, CHAT_REPLY)})
        instance = provider_instance(
            PRV_OpenAICompatible_AI,
            settings={"base_url": f"{server.base_url}/v1", "model": "llama3.2"},
        )
        await PRV_OpenAICompatible_AI.chat(instance, HELLO, max_tokens=20)
        sent = body_of(server)
        assert sent["max_tokens"] == 20 and sent["model"] == "llama3.2"
        assert "authorization" not in server.requests[0].headers

    async def test_a_compatible_server_needs_a_model(self, provider_instance):
        instance = provider_instance(
            PRV_AGInYourPC_AI, settings={"base_url": "https://aginyourpc.test/v1"}
        )
        with pytest.raises(TransientExternalError, match="model not configured"):
            await PRV_AGInYourPC_AI.chat(instance, HELLO)

    async def test_azure(self, local_http_server, provider_instance):
        server = local_http_server(
            {"/openai/v1/chat/completions": (200, JSON, CHAT_REPLY)}
        )
        instance = provider_instance(
            PRV_Azure_AI,
            api_key="azure-key",
            settings={"endpoint": server.base_url, "model": "my-deployment"},
        )
        await PRV_Azure_AI.chat(instance, HELLO)
        assert server.requests[0].headers["api-key"] == "azure-key"
        assert body_of(server)["model"] == "my-deployment"

    async def test_anthropic(self, local_http_server, provider_instance):
        server = local_http_server(
            {
                "/messages": (
                    200,
                    JSON,
                    json.dumps(
                        {
                            "stop_reason": "end_turn",
                            "content": [{"type": "text", "text": "Hi."}],
                            "usage": {"input_tokens": 4, "output_tokens": 2},
                        }
                    ).encode(),
                )
            }
        )
        instance = provider_instance(
            PRV_Anthropic_AI, api_key="ak", settings={"base_url": server.base_url}
        )
        answer = await PRV_Anthropic_AI.chat(instance, HELLO)
        assert answer["message"]["content"] == "Hi."
        headers = server.requests[0].headers
        assert headers["x-api-key"] == "ak" and headers["anthropic-version"]
        assert body_of(server)["max_tokens"] == 4096

    async def test_elevenlabs_transcription(self, local_http_server, provider_instance):
        server = local_http_server(
            {"/speech-to-text": (200, JSON, b'{"text": "hi there"}')}
        )
        instance = provider_instance(
            PRV_ElevenLabs_AI, api_key="xi", settings={"base_url": server.base_url}
        )
        found = await PRV_ElevenLabs_AI.transcribe(instance, b"ID3", "a.mp3", None)
        assert found == {"text": "hi there", "model": "scribe_v1"}
        assert server.requests[0].headers["xi-api-key"] == "xi"

    async def test_an_elevenlabs_voice_is_one_path_segment(self, provider_instance):
        instance = provider_instance(PRV_ElevenLabs_AI, api_key="xi")
        with pytest.raises(InvalidInputExternalError):
            await PRV_ElevenLabs_AI.speak(instance, "Hello", "../v1/user")


class TestRefusals:
    @pytest.fixture
    def answering(self, local_http_server, provider_instance):
        def _make(provider, status, body):
            server = local_http_server({"/chat/completions": (status, JSON, body)})
            return provider_instance(
                provider,
                api_key="bad",
                settings={"base_url": server.base_url, "model": "m"},
            )

        return _make

    async def test_a_401_is_a_refused_key(self, answering):
        instance = answering(PRV_DeepSeek_AI, 401, b'{"error": {"message": "nope"}}')
        with pytest.raises(AuthExternalError):
            await PRV_DeepSeek_AI.chat(instance, HELLO)

    async def test_xai_refuses_a_key_with_a_400(self, answering):
        instance = answering(
            PRV_XAI_AI,
            400,
            b'{"code":"invalid-argument","error":"Incorrect API key provided."}',
        )
        with pytest.raises(AuthExternalError):
            await PRV_XAI_AI.chat(instance, HELLO)

    async def test_gemini_refuses_a_key_with_a_400(self, answering):
        instance = answering(
            PRV_Google_AI,
            400,
            b'[{"error": {"code": 400, "message": "Please pass a valid API key"}}]',
        )
        with pytest.raises(AuthExternalError):
            await PRV_Google_AI.chat(instance, HELLO)

    async def test_any_other_400_is_the_callers(self, answering):
        instance = answering(PRV_XAI_AI, 400, b'{"error": "Model not found: grok-99"}')
        with pytest.raises(InvalidInputExternalError):
            await PRV_XAI_AI.chat(instance, HELLO)

    async def test_a_lan_server_needs_an_egress_allowance(self, provider_instance):
        instance = provider_instance(
            PRV_OpenAICompatible_AI,
            settings={"base_url": "http://192.168.1.20:11434/v1", "model": "llama3.2"},
        )
        with pytest.raises(InvalidInputExternalError, match="SSRF"):
            await PRV_OpenAICompatible_AI.chat(instance, HELLO)


BOGUS = "sk-bogus-0000000000000000000000000000"


class TestRealRefusals:
    """Each API, given a bogus key, refuses it as a credential failure."""

    @pytest.mark.parametrize(
        "provider, url",
        [
            (PRV_OpenAI_AI, "https://api.openai.com"),
            (PRV_Anthropic_AI, "https://api.anthropic.com"),
            (PRV_Google_AI, "https://generativelanguage.googleapis.com"),
            (PRV_DeepSeek_AI, "https://api.deepseek.com"),
            (PRV_XAI_AI, "https://api.x.ai"),
            (PRV_HuggingFace_AI, "https://router.huggingface.co"),
        ],
        ids=lambda p: getattr(p, "name", ""),
    )
    async def test_chat(self, provider, url, provider_instance):
        if not _online(url):
            pytest.xfail(f"{url} is unreachable")
        instance = provider_instance(provider, api_key=BOGUS)
        with pytest.raises(AuthExternalError):
            await provider.chat(instance, HELLO, max_tokens=5)

    @reachable("https://api.elevenlabs.io")
    async def test_elevenlabs(self, provider_instance):
        instance = provider_instance(PRV_ElevenLabs_AI, api_key=BOGUS)
        with pytest.raises(AuthExternalError):
            await PRV_ElevenLabs_AI.speak(instance, "Hello", None)


class TestLive:
    """One short call per provider with a real key."""

    @pytest.mark.external_api(provider="openai_key")
    async def test_openai(self, provider_instance, sandbox_credentials_for):
        key = sandbox_credentials_for("openai_key")["OPENAI_API_KEY"]
        instance = provider_instance(PRV_OpenAI_AI, api_key=key)
        answer = await PRV_OpenAI_AI.chat(instance, HELLO, max_tokens=200)
        assert answer["message"]["content"]
        found = await PRV_OpenAI_AI.embed(instance, ["hello"])
        assert found["dimensions"] > 0

    @pytest.mark.external_api(provider="anthropic_key")
    async def test_anthropic(self, provider_instance, sandbox_credentials_for):
        key = sandbox_credentials_for("anthropic_key")["ANTHROPIC_API_KEY"]
        instance = provider_instance(PRV_Anthropic_AI, api_key=key)
        answer = await PRV_Anthropic_AI.chat(instance, HELLO, max_tokens=20)
        assert answer["message"]["content"]

    @pytest.mark.external_api(provider="gemini_key")
    async def test_gemini(self, provider_instance, sandbox_credentials_for):
        key = sandbox_credentials_for("gemini_key")["GEMINI_API_KEY"]
        instance = provider_instance(PRV_Google_AI, api_key=key)
        answer = await PRV_Google_AI.chat(instance, HELLO, max_tokens=200)
        assert answer["message"]["content"]

    @pytest.mark.external_api(provider="elevenlabs_key")
    async def test_elevenlabs(self, provider_instance, sandbox_credentials_for):
        key = sandbox_credentials_for("elevenlabs_key")["ELEVENLABS_API_KEY"]
        instance = provider_instance(PRV_ElevenLabs_AI, api_key=key)
        spoken = await PRV_ElevenLabs_AI.speak(instance, "Hi.", None)
        assert base64.b64decode(spoken["audio_base64"])
