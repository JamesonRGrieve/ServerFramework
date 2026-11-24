# SPDX-License-Identifier: AGPL-3.0-or-later
"""The AI Horde provider against a local stand-in for its API: a chat is
one instruct-template prompt submitted as a job and polled until done; a
faulted job, or an instance with no model, is transient (the rotation moves
on); the Horde's own params pass through and the rest are dropped."""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from zephyrex.extensions.ai.PRV_Horde import (
    PRV_Horde_AI,
    horde_params,
    instruct_prompt,
)
from zephyrex.extensions.ExternalErrors import TransientExternalError

HI = [
    {"role": "system", "content": "Be brief."},
    {"role": "user", "content": "Hi"},
]
JSON = {"Content-Type": "application/json"}


def test_a_chat_becomes_one_open_ended_prompt():
    assert instruct_prompt(HI, "chatml") == (
        "<|im_start|>system\nBe brief.<|im_end|>\n"
        "<|im_start|>user\nHi<|im_end|>\n"
        "<|im_start|>assistant\n"
    )
    assert instruct_prompt(HI, "plain").endswith("User: Hi\nAssistant: ")
    assert instruct_prompt(HI, "no-such") == instruct_prompt(HI, "chatml")


def test_its_own_params_pass_and_the_rest_are_dropped():
    assert horde_params(
        200, 0.7, {"top_k": 40, "rep_pen": 1.1, "stop": "END", "seed": 3}
    ) == {
        "top_k": 40,
        "rep_pen": 1.1,
        "stop_sequence": ["END"],
        "max_length": 200,
        "temperature": 0.7,
    }
    assert "max_length" not in horde_params(None, None, {})


class _HordeStandIn:
    @pytest.fixture
    def horde(self, local_http_server):
        polls: List[int] = []

        def status(_request: Any) -> Any:
            polls.append(1)
            done = len(polls) > 1
            body: Dict[str, Any] = {"done": done, "is_possible": True, "faulted": False}
            if done:
                body["generations"] = [{"text": "Hello.", "model": "koboldcpp/x"}]
            return 200, JSON, json.dumps(body).encode()

        server = local_http_server(
            {
                "/v2/generate/text/async": (202, JSON, b'{"id": "job1"}'),
                "/v2/generate/text/status/job1": status,
                "/v2/generate/async": (202, JSON, b'{"id": "img1"}'),
                "/v2/generate/check/img1": (200, JSON, b'{"done": true}'),
                "/v2/generate/status/img1": (
                    200,
                    JSON,
                    json.dumps(
                        {
                            "done": True,
                            "generations": [
                                {"img": "https://r2.test/x.webp", "model": "sdxl"}
                            ],
                        }
                    ).encode(),
                ),
                "/faulty/v2/generate/text/async": (202, JSON, b'{"id": "bad"}'),
                "/faulty/v2/generate/text/status/bad": (
                    200,
                    JSON,
                    b'{"done": false, "faulted": true}',
                ),
            }
        )
        return server, polls

    def instance(self, provider_instance, server, path="", **settings: str):
        return provider_instance(
            PRV_Horde_AI,
            model_name=settings.pop("model", "koboldcpp/x"),
            settings={
                "base_url": f"{server.base_url}{path}",
                "poll_interval_seconds": "0.01",
                **settings,
            },
        )


class TestJobs(_HordeStandIn):
    async def test_a_job_is_polled_until_done(self, horde, provider_instance):
        server, polls = horde
        answer = await PRV_Horde_AI.chat(
            self.instance(provider_instance, server), HI, max_tokens=50
        )
        assert answer["message"]["content"] == "Hello."
        assert answer["model"] == "koboldcpp/x"
        assert len(polls) == 2
        submitted = json.loads(server.requests[0].body)
        assert submitted["models"] == ["koboldcpp/x"]
        assert submitted["params"]["max_length"] == 50
        assert submitted["prompt"].endswith("<|im_start|>assistant\n")
        assert (submitted["trusted_workers"], submitted["slow_workers"]) == (
            False,
            True,
        )
        assert server.requests[0].headers["apikey"] == "0000000000"

    async def test_a_faulted_job_moves_the_rotation_on(self, horde, provider_instance):
        server, _ = horde
        with pytest.raises(TransientExternalError):
            await PRV_Horde_AI.chat(
                self.instance(provider_instance, server, "/faulty"), HI
            )

    async def test_an_instance_with_no_model_is_passed_over(
        self, horde, provider_instance
    ):
        server, _ = horde
        with pytest.raises(TransientExternalError, match="model not configured"):
            await PRV_Horde_AI.chat(
                self.instance(provider_instance, server, model=""), HI
            )
        assert server.requests == []

    async def test_its_stream_is_the_whole_answer(self, horde, provider_instance):
        server, _ = horde
        events = [
            e
            async for e in PRV_Horde_AI.stream(
                self.instance(provider_instance, server), HI
            )
        ]
        assert events[0] == {"content": "Hello."}
        assert events[-1]["finish_reason"] == "stop"


class TestImages(_HordeStandIn):
    async def test_an_image_job_is_checked_then_collected(
        self, horde, provider_instance
    ):
        server, _ = horde
        instance = self.instance(provider_instance, server, image_model="sdxl")
        drawn = await PRV_Horde_AI.image(instance, "a lighthouse", "1024x1536")
        assert drawn == {"images": [{"url": "https://r2.test/x.webp"}], "model": "sdxl"}
        submitted = json.loads(server.requests[0].body)
        assert submitted["models"] == ["sdxl"]
        assert (submitted["params"]["width"], submitted["params"]["height"]) == (
            1024,
            1536,
        )
        assert (submitted["nsfw"], submitted["censor_nsfw"]) == (False, True)
        assert [r.path for r in server.requests][1:] == [
            "/v2/generate/check/img1",
            "/v2/generate/status/img1",
        ]

    async def test_an_instance_with_no_image_model_is_passed_over(
        self, horde, provider_instance
    ):
        server, _ = horde
        with pytest.raises(TransientExternalError, match="image model"):
            await PRV_Horde_AI.image(
                self.instance(provider_instance, server), "x", "auto"
            )
        assert server.requests == []
