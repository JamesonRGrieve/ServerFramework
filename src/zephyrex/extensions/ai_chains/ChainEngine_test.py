# SPDX-License-Identifier: AGPL-3.0-or-later
"""The chain engine, against the app database: steps run in order, as the
chain's owner, within the chain's bounds; every step executed is recorded;
a failed step fails the run; a run can be cancelled.

The old extension had no engine: ``execute_chain`` answered a canned
"Chain execution completed" (3 steps, 2.5 s) for any chain id, and every
other ability returned fabricated data."""

import asyncio
import json

import pytest

from zephyrex.extensions.ai_chains.BLL_AI_Chains import (
    ChainRunManager,
    ChainStepManager,
)
from zephyrex.extensions.ai_chains.chain_fixtures_test import (
    ChainFixtures,
    ScriptedChat,
    ability_id,
)
from zephyrex.extensions.ai_chains.ChainEngine import ChainEngine
from zephyrex.extensions.ExternalErrors import TransientExternalError
from zephyrex.pydantic2.registry import ModelRegistry


class TestSteps(ChainFixtures):
    async def test_set_and_condition_steps(self, admin_a, model_registry):
        chain = self._chain(admin_a, model_registry)
        self._step(
            admin_a,
            model_registry,
            chain,
            "double",
            "set",
            1,
            expression="n * 2",
            variable="doubled",
        )
        self._step(
            admin_a,
            model_registry,
            chain,
            "big",
            "condition",
            2,
            expression="doubled > 10",
            on_true="say_big",
        )
        self._step(
            admin_a,
            model_registry,
            chain,
            "say_small",
            "set",
            3,
            expression="'small'",
            variable="verdict",
        )
        self._step(
            admin_a,
            model_registry,
            chain,
            "stop",
            "condition",
            4,
            expression="True",
            on_true="end",
        )
        self._step(
            admin_a,
            model_registry,
            chain,
            "say_big",
            "set",
            5,
            expression="'big'",
            variable="verdict",
        )
        small = await self._run(admin_a, model_registry, chain, {"n": 3})
        assert small.status == "succeeded", small.error
        assert small.variables == {"n": 3, "doubled": 6, "verdict": "small"}
        assert json.loads(small.output) == "small"
        names = [r.step_name for r in self._results(admin_a, model_registry, small)]
        assert names == ["double", "big", "say_small", "stop"]
        big = await self._run(admin_a, model_registry, chain, {"n": 8})
        assert big.variables["verdict"] == "big"
        assert [r.step_name for r in self._results(admin_a, model_registry, big)] == [
            "double",
            "big",
            "say_big",
        ]

    async def test_each_result_records_input_output_and_timing(
        self, admin_a, model_registry
    ):
        chain = self._chain(admin_a, model_registry)
        self._step(
            admin_a,
            model_registry,
            chain,
            "greet",
            "set",
            1,
            expression="'hi ' + who",
            variable="greeting",
        )
        run = await self._run(admin_a, model_registry, chain, {"who": "Ada"})
        [result] = self._results(admin_a, model_registry, run)
        assert (result.status, result.sequence, result.kind) == ("succeeded", 1, "set")
        assert json.loads(result.input) == {"expression": "'hi ' + who"}
        assert json.loads(result.output) == "hi Ada"
        assert result.started_at and result.completed_at
        assert result.duration_ms is not None and result.duration_ms >= 0
        assert run.steps_executed == 1 and run.started_at and run.completed_at

    async def test_a_bounded_loop(self, admin_a, model_registry):
        chain = self._chain(admin_a, model_registry)
        self._step(
            admin_a,
            model_registry,
            chain,
            "count",
            "set",
            1,
            expression="i + 1",
            variable="i",
        )
        self._step(
            admin_a,
            model_registry,
            chain,
            "again",
            "condition",
            2,
            expression="i < 5",
            on_true="count",
            max_loops=10,
        )
        run = await self._run(admin_a, model_registry, chain, {"i": 0})
        assert run.status == "succeeded", run.error
        assert run.variables["i"] == 5 and run.steps_executed == 10

    async def test_a_prompt_step_is_filled_and_answered_as_the_owner(
        self, admin_a, model_registry
    ):
        prompt = self._prompt(admin_a, model_registry, "Summarise for {READER}: {TEXT}")
        chain = self._chain(admin_a, model_registry)
        self._step(
            admin_a,
            model_registry,
            chain,
            "summarise",
            "prompt",
            1,
            prompt_id=prompt.id,
            arguments={"READER": "upper(reader)"},
            variable="summary",
        )
        chat = ScriptedChat("A short summary.")
        run = await self._run(
            admin_a,
            model_registry,
            chain,
            {"TEXT": "a long text", "reader": "ops"},
            chat=chat,
        )
        assert run.status == "succeeded", run.error
        assert run.variables["summary"] == "A short summary."
        [call] = chat.calls
        assert call["requester_id"] == admin_a.id
        assert call["messages"] == [
            {"role": "user", "content": "Summarise for OPS: a long text"}
        ]
        [result] = self._results(admin_a, model_registry, run)
        assert json.loads(result.input)["text"] == "Summarise for OPS: a long text"

    async def test_a_prompt_missing_a_variable_fails_its_step(
        self, admin_a, model_registry
    ):
        prompt = self._prompt(admin_a, model_registry, "Hello {NAME}")
        chain = self._chain(admin_a, model_registry)
        self._step(
            admin_a,
            model_registry,
            chain,
            "hello",
            "prompt",
            1,
            prompt_id=prompt.id,
        )
        chat = ScriptedChat("never asked")
        run = await self._run(admin_a, model_registry, chain, chat=chat)
        assert (run.status, run.error_kind) == ("failed", "step")
        assert "NAME" in run.error and chat.calls == []

    async def test_a_model_failure_fails_the_run(self, admin_a, model_registry):
        prompt = self._prompt(admin_a, model_registry, "Say something")
        chain = self._chain(admin_a, model_registry)
        self._step(
            admin_a, model_registry, chain, "ask", "prompt", 1, prompt_id=prompt.id
        )
        self._step(
            admin_a,
            model_registry,
            chain,
            "after",
            "set",
            2,
            expression="1",
            variable="after",
        )
        run = await self._run(
            admin_a,
            model_registry,
            chain,
            chat=ScriptedChat(TransientExternalError("model unavailable")),
        )
        assert (run.status, run.error_kind) == ("failed", "step")
        assert "model unavailable" in run.error
        [result] = self._results(admin_a, model_registry, run)
        assert result.status == "failed" and "model unavailable" in result.error
        assert "after" not in (run.variables or {})

    async def test_an_expression_failure_fails_the_run(self, admin_a, model_registry):
        chain = self._chain(admin_a, model_registry)
        self._step(
            admin_a,
            model_registry,
            chain,
            "oops",
            "set",
            1,
            expression="1 / zero",
            variable="x",
        )
        run = await self._run(admin_a, model_registry, chain, {"zero": 0})
        assert (run.status, run.error_kind) == ("failed", "step")
        assert "ZeroDivisionError" in run.error


class TestAbilitySteps(ChainFixtures):
    @pytest.fixture(autouse=True)
    def attached(self, server, monkeypatch) -> None:
        """Abilities find the running app through the attached registry."""
        registry = server.app.state.model_registry
        monkeypatch.setattr(
            ModelRegistry, "attached", classmethod(lambda cls: registry)
        )

    async def test_an_ability_runs_as_the_chains_owner(
        self, admin_a, admin_b, model_registry
    ):
        mine = self._prompt(admin_a, model_registry, "mine")
        theirs = self._prompt(admin_b, model_registry, "theirs")
        chain = self._chain(admin_a, model_registry)
        self._step(
            admin_a,
            model_registry,
            chain,
            "prompts",
            "ability",
            1,
            ability_id=ability_id(model_registry, "ai_prompts", "list_prompts"),
            arguments={"favourites_only": "False", "requester_id": f"'{admin_b.id}'"},
            variable="listed",
        )
        run = await self._run(admin_a, model_registry, chain)
        assert run.status == "succeeded", run.error
        listed = {p["id"] for p in run.variables["listed"]}
        assert mine.id in listed and theirs.id not in listed

    async def test_a_refused_ability_fails_its_step(
        self, admin_a, admin_b, model_registry
    ):
        """The owner's permissions bound the ability: reading another user's
        prompt is refused, and the refusal is the step's error."""
        theirs = self._prompt(admin_b, model_registry, "secret {X}")
        chain = self._chain(admin_a, model_registry)
        self._step(
            admin_a,
            model_registry,
            chain,
            "peek",
            "ability",
            1,
            ability_id=ability_id(model_registry, "ai_prompts", "get_prompt"),
            arguments={"prompt_id": f"'{theirs.id}'"},
        )
        run = await self._run(admin_a, model_registry, chain)
        assert (run.status, run.error_kind) == ("failed", "step")
        [result] = self._results(admin_a, model_registry, run)
        assert result.status == "failed" and "refused" in result.error

    async def test_an_ability_with_wrong_arguments_fails_its_step(
        self, admin_a, model_registry
    ):
        chain = self._chain(admin_a, model_registry)
        self._step(
            admin_a,
            model_registry,
            chain,
            "wrong",
            "ability",
            1,
            ability_id=ability_id(model_registry, "ai_prompts", "list_prompts"),
            arguments={"no_such_argument": "1"},
        )
        run = await self._run(admin_a, model_registry, chain)
        assert run.status == "failed" and "called wrongly" in run.error

    async def test_an_ability_whose_extension_is_absent_fails_its_step(
        self, admin_a, model_registry
    ):
        chain = self._chain(admin_a, model_registry)
        self._step(
            admin_a,
            model_registry,
            chain,
            "absent",
            "ability",
            1,
            ability_id=ability_id(model_registry, "not_loaded", "anything"),
        )
        run = await self._run(admin_a, model_registry, chain)
        assert run.status == "failed" and "not an ability that can run" in run.error


class TestBounds(ChainFixtures):
    async def test_max_steps(self, admin_a, model_registry):
        chain = self._chain(admin_a, model_registry, max_steps=5)
        self._step(
            admin_a,
            model_registry,
            chain,
            "tick",
            "set",
            1,
            expression="i + 1",
            variable="i",
        )
        self._step(
            admin_a,
            model_registry,
            chain,
            "loop",
            "condition",
            2,
            expression="True",
            on_true="tick",
            max_loops=1000,
        )
        run = await self._run(admin_a, model_registry, chain, {"i": 0})
        assert (run.status, run.error_kind) == ("failed", "limit")
        assert run.steps_executed == 5 and "5 steps" in run.error

    async def test_a_loop_stops_at_its_bound(self, admin_a, model_registry):
        chain = self._chain(admin_a, model_registry)
        self._step(
            admin_a,
            model_registry,
            chain,
            "tick",
            "set",
            1,
            expression="i + 1",
            variable="i",
        )
        self._step(
            admin_a,
            model_registry,
            chain,
            "loop",
            "condition",
            2,
            expression="True",
            on_true="tick",
            max_loops=3,
        )
        run = await self._run(admin_a, model_registry, chain, {"i": 0})
        assert (run.status, run.error_kind) == ("failed", "limit")
        assert run.variables["i"] == 4 and "looped back 3 times" in run.error

    async def test_a_loop_without_a_bound_does_not_run(self, admin_a, model_registry):
        chain = self._chain(admin_a, model_registry)
        self._step(
            admin_a,
            model_registry,
            chain,
            "tick",
            "set",
            1,
            expression="1",
            variable="i",
        )
        self._step(
            admin_a,
            model_registry,
            chain,
            "loop",
            "condition",
            2,
            expression="True",
            on_true="tick",
        )
        run = await self._run(admin_a, model_registry, chain)
        assert (run.status, run.error_kind) == ("failed", "definition")
        assert run.steps_executed == 0
        assert self._results(admin_a, model_registry, run) == []

    async def test_a_jump_to_no_step_does_not_run(self, admin_a, model_registry):
        chain = self._chain(admin_a, model_registry)
        self._step(
            admin_a,
            model_registry,
            chain,
            "go",
            "condition",
            1,
            expression="True",
            on_true="nowhere",
        )
        run = await self._run(admin_a, model_registry, chain)
        assert (run.status, run.error_kind) == ("failed", "definition")
        assert "nowhere" in run.error

    async def test_the_output_cap(self, admin_a, model_registry):
        chain = self._chain(admin_a, model_registry, max_output_characters=50)
        self._step(
            admin_a,
            model_registry,
            chain,
            "small",
            "set",
            1,
            expression="'x' * 10",
            variable="a",
        )
        self._step(
            admin_a,
            model_registry,
            chain,
            "large",
            "set",
            2,
            expression="'x' * 100",
            variable="b",
        )
        run = await self._run(admin_a, model_registry, chain)
        assert (run.status, run.error_kind) == ("failed", "limit")
        assert run.variables == {"a": "x" * 10}
        small, large = self._results(admin_a, model_registry, run)
        assert small.status == "succeeded"
        assert large.status == "failed" and large.output is None

    async def test_the_deadline(self, admin_a, model_registry):
        prompt = self._prompt(admin_a, model_registry, "Think hard")
        chain = self._chain(admin_a, model_registry, timeout_seconds=1)
        self._step(
            admin_a, model_registry, chain, "slow", "prompt", 1, prompt_id=prompt.id
        )

        async def slow_model() -> str:
            await asyncio.sleep(30)
            return "too late"

        run = await self._run(
            admin_a, model_registry, chain, chat=ScriptedChat(slow_model)
        )
        assert (run.status, run.error_kind) == ("failed", "timeout")
        [result] = self._results(admin_a, model_registry, run)
        assert result.status == "failed" and result.duration_ms < 10_000


class TestLifecycle(ChainFixtures):
    async def test_a_run_executes_once(self, admin_a, model_registry):
        from fastapi import HTTPException

        chain = self._chain(admin_a, model_registry)
        run = await self._run(admin_a, model_registry, chain)
        assert run.status == "succeeded" and run.steps_executed == 0
        with pytest.raises(HTTPException) as again:
            await ChainEngine(
                model_registry=model_registry, requester_id=admin_a.id
            ).run(run.id)
        assert again.value.status_code == 409

    async def test_cancelled_before_it_starts(self, admin_a, model_registry):
        from fastapi import HTTPException

        chain = self._chain(admin_a, model_registry)
        runs = self._m(ChainRunManager, admin_a, model_registry)
        run = runs.create(chain_id=chain.id)
        cancelled = runs.cancel(run.id)
        assert (cancelled.status, cancelled.error_kind) == ("cancelled", "cancelled")
        with pytest.raises(HTTPException):
            await ChainEngine(
                model_registry=model_registry, requester_id=admin_a.id
            ).run(run.id)

    async def test_cancelled_while_a_step_is_awaited(self, admin_a, model_registry):
        prompt = self._prompt(admin_a, model_registry, "Think")
        chain = self._chain(admin_a, model_registry)
        self._step(
            admin_a, model_registry, chain, "think", "prompt", 1, prompt_id=prompt.id
        )
        self._step(
            admin_a,
            model_registry,
            chain,
            "after",
            "set",
            2,
            expression="1",
            variable="after",
        )
        runs = self._m(ChainRunManager, admin_a, model_registry)
        run = runs.create(chain_id=chain.id)

        async def model_asked_to_stop() -> str:
            runs.cancel(run.id, "changed my mind")
            await asyncio.sleep(30)
            return "never"

        await ChainEngine(
            model_registry=model_registry,
            requester_id=admin_a.id,
            chat=ScriptedChat(model_asked_to_stop),
        ).run(run.id)
        ended = runs.get(id=run.id)
        assert (ended.status, ended.error_kind) == ("cancelled", "cancelled")
        [result] = self._results(admin_a, model_registry, ended)
        assert result.step_name == "think" and result.status == "cancelled"

    async def test_steps_run_in_position_order(self, admin_a, model_registry):
        chain = self._chain(admin_a, model_registry)
        self._step(
            admin_a,
            model_registry,
            chain,
            "second",
            "set",
            20,
            expression="trail + 'b'",
            variable="trail",
        )
        self._step(
            admin_a,
            model_registry,
            chain,
            "first",
            "set",
            10,
            expression="trail + 'a'",
            variable="trail",
        )
        run = await self._run(admin_a, model_registry, chain, {"trail": ""})
        assert run.variables["trail"] == "ab"

    async def test_a_step_moved_after_saving_still_runs_in_order(
        self, admin_a, model_registry
    ):
        chain = self._chain(admin_a, model_registry)
        late = self._step(
            admin_a,
            model_registry,
            chain,
            "late",
            "set",
            1,
            expression="trail + 'b'",
            variable="trail",
        )
        self._step(
            admin_a,
            model_registry,
            chain,
            "early",
            "set",
            2,
            expression="trail + 'a'",
            variable="trail",
        )
        self._m(ChainStepManager, admin_a, model_registry).update(late.id, position=3)
        run = await self._run(admin_a, model_registry, chain, {"trail": ""})
        assert run.variables["trail"] == "ab"


class TestRealTransport(ChainFixtures):
    """A prompt step answered by a real OpenAI-compatible server through the
    AI extension's chat, with no transport handed in."""

    async def test_through_the_ai_extension(
        self, admin_a, model_registry, server, local_http_server, monkeypatch
    ):
        from zephyrex.extensions.ai.EXT_AI import EXT_AI
        from zephyrex.extensions.ai.PRV_OpenAICompatible import (
            PRV_OpenAICompatible_AI,
        )
        from zephyrex.lib.Environment import env
        from zephyrex.logic.BLL_Providers import (
            ProviderInstanceManager,
            ProviderInstanceSettingManager,
            ProviderManager,
            RotationManager,
            RotationProviderInstanceManager,
        )

        reply = {
            "model": "llama3.2",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Bonjour."},
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        }
        model_server = local_http_server(
            {
                "/v1/chat/completions": (
                    200,
                    {"Content-Type": "application/json"},
                    json.dumps(reply).encode(),
                )
            }
        )
        root = env("ROOT_ID")
        provider = ProviderManager(
            model_registry=model_registry, requester_id=root
        ).get(name=PRV_OpenAICompatible_AI.name)
        instance = ProviderInstanceManager(
            model_registry=model_registry, requester_id=root
        ).create(
            name=f"chains_{provider.id}_{id(self)}",
            provider_id=provider.id,
            model_name="llama3.2",
            scope="root",
        )
        settings = ProviderInstanceSettingManager(
            model_registry=model_registry, requester_id=root
        )
        settings.create(
            provider_instance_id=instance.id,
            key="base_url",
            value=f"{model_server.base_url}/v1",
        )
        rotations = RotationManager(model_registry=model_registry, requester_id=root)
        rotation = rotations.create(name=f"chains_rotation_{instance.id}")
        RotationProviderInstanceManager(
            model_registry=model_registry, requester_id=root
        ).create(rotation_id=rotation.id, provider_instance_id=instance.id)
        rotations.target_id = rotation.id
        monkeypatch.setattr(
            ModelRegistry, "attached", classmethod(lambda cls: model_registry)
        )
        monkeypatch.setattr(EXT_AI, "_root_rotation_cache", rotations)

        prompt = self._prompt(admin_a, model_registry, "Translate {WORD} to French")
        chain = self._chain(admin_a, model_registry)
        self._step(
            admin_a,
            model_registry,
            chain,
            "translate",
            "prompt",
            1,
            prompt_id=prompt.id,
            variable="french",
        )
        runs = self._m(ChainRunManager, admin_a, model_registry)
        run = runs.create(chain_id=chain.id, inputs={"WORD": "hello"})
        await ChainEngine(model_registry=model_registry, requester_id=admin_a.id).run(
            run.id
        )
        ended = runs.get(id=run.id)
        assert ended.status == "succeeded", ended.error
        assert ended.variables["french"] == "Bonjour."
        [sent] = model_server.requests
        body = json.loads(sent.body)
        assert body["model"] == "llama3.2"
        assert body["messages"][-1]["content"] == "Translate hello to French"
