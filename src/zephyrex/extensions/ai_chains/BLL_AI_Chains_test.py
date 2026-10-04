# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who may do what with chains, their steps, runs and step results, and
what a step must be before it is saved.

The old extension's models had no owner forcing, no inherited access (a
step named any chain id and an agent id it never checked), no checks on
what a step referenced, and run fields any caller could write; none of its
tables was ever made by a migration."""

import uuid

import pytest
from fastapi import HTTPException

from zephyrex.extensions.ai_chains.BLL_AI_Chains import (
    MAX_INPUT_CHARACTERS,
    ChainManager,
    ChainRunManager,
    ChainStepManager,
    ChainStepResultManager,
    check_inputs,
    check_step,
)
from zephyrex.extensions.ai_chains.chain_fixtures_test import (
    ChainFixtures,
    ability_id,
)
from zephyrex.lib.Environment import env


def refused(status_codes, call, *args, **kwargs) -> None:
    with pytest.raises(HTTPException) as caught:
        call(*args, **kwargs)
    assert caught.value.status_code in status_codes, caught.value.detail


class TestStepShape:
    @pytest.mark.parametrize(
        "step",
        [
            {"name": "a", "kind": "set", "expression": "1", "variable": "x"},
            {"name": "a", "kind": "condition", "expression": "x", "on_true": "end"},
            {"name": "a", "kind": "prompt", "prompt_id": "p", "arguments": {"A": "1"}},
            {"name": "a", "kind": "ability", "ability_id": "b"},
        ],
    )
    def test_valid(self, step):
        check_step(step)

    @pytest.mark.parametrize(
        "step",
        [
            {"name": "end", "kind": "set", "expression": "1", "variable": "x"},
            {"name": "has space", "kind": "set", "expression": "1", "variable": "x"},
            {"name": "_hidden", "kind": "set", "expression": "1", "variable": "x"},
            {"name": "a", "kind": "set", "expression": "1"},
            {"name": "a", "kind": "set", "expression": "1", "variable": "__x"},
            {
                "name": "a",
                "kind": "set",
                "expression": "__import__('os')",
                "variable": "x",
            },
            {"name": "a", "kind": "condition", "expression": ""},
            {"name": "a", "kind": "condition", "expression": "x", "on_true": "a b"},
            {
                "name": "a",
                "kind": "set",
                "expression": "1",
                "variable": "x",
                "on_true": "b",
            },
            {"name": "a", "kind": "prompt"},
            {"name": "a", "kind": "ability"},
            {
                "name": "a",
                "kind": "ability",
                "ability_id": "b",
                "arguments": {"x": "("},
            },
            {
                "name": "a",
                "kind": "ability",
                "ability_id": "b",
                "arguments": {"a-b": "1"},
            },
        ],
    )
    def test_invalid(self, step):
        refused({422}, check_step, step)

    def test_inputs(self):
        assert check_inputs(None) == {} and check_inputs({"a": 1}) == {"a": 1}
        for bad in ([1], {"has space": 1}, {"__class__": 1}):
            refused({422}, check_inputs, bad)
        refused({422}, check_inputs, {"big": "x" * MAX_INPUT_CHARACTERS})


class TestChainAccess(ChainFixtures):
    def test_the_owner_is_the_creator(self, admin_a, admin_b, model_registry):
        chain = self._chain(admin_b, model_registry, user_id=admin_a.id)
        assert chain.user_id == admin_b.id
        batch = self._m(ChainManager, admin_b, model_registry).create(
            entities=[{"name": "one", "user_id": admin_a.id}, {"name": "two"}]
        )
        assert {c.user_id for c in batch} == {admin_b.id}

    def test_root_may_name_the_owner(self, admin_a, model_registry):
        chain = self._chain(env("ROOT_ID"), model_registry, user_id=admin_a.id)
        assert chain.user_id == admin_a.id

    def test_an_update_does_not_move_a_chain(
        self, admin_a, admin_b, team_b, model_registry
    ):
        chain = self._chain(admin_a, model_registry)
        moved = self._m(ChainManager, admin_a, model_registry).update(
            chain.id, name="Renamed", user_id=admin_b.id, team_id=team_b.id
        )
        assert (moved.name, moved.user_id, moved.team_id) == (
            "Renamed",
            admin_a.id,
            chain.team_id,
        )

    def test_bounds_have_ceilings(self, admin_a, model_registry):
        for field, value in (
            ("max_steps", 0),
            ("max_steps", 10**6),
            ("timeout_seconds", 10**6),
            ("max_output_characters", 10**7),
        ):
            with pytest.raises(Exception):
                self._chain(admin_a, model_registry, **{field: value})

    def test_no_one_else_sees_or_changes_a_chain(
        self, admin_a, admin_b, model_registry
    ):
        chain = self._chain(admin_a, model_registry)
        theirs = self._m(ChainManager, admin_b, model_registry)
        refused({404}, theirs.get, id=chain.id)
        refused({403, 404}, theirs.update, chain.id, name="taken")
        assert chain.id not in {c.id for c in theirs.list()}


class TestStepAccess(ChainFixtures):
    def _set_step(self, user, model_registry, chain, name="s", **fields):
        return self._step(
            user,
            model_registry,
            chain,
            name,
            "set",
            1,
            expression="1",
            variable="x",
            **fields,
        )

    def test_no_one_else_adds_steps_to_a_chain(self, admin_a, admin_b, model_registry):
        chain = self._chain(admin_a, model_registry)
        refused({403, 404}, self._set_step, admin_b, model_registry, chain)
        assert (
            self._m(ChainStepManager, admin_b, model_registry).list(chain_id=chain.id)
            == []
        )

    def test_steps_are_seen_through_their_chain(self, admin_a, admin_b, model_registry):
        chain = self._chain(admin_a, model_registry)
        step = self._set_step(admin_a, model_registry, chain)
        refused(
            {404}, self._m(ChainStepManager, admin_b, model_registry).get, id=step.id
        )
        refused(
            {403, 404},
            self._m(ChainStepManager, admin_b, model_registry).update,
            step.id,
            expression="2",
        )

    def test_a_step_stays_in_its_chain(self, admin_a, model_registry):
        chain = self._chain(admin_a, model_registry)
        other = self._chain(admin_a, model_registry)
        step = self._set_step(admin_a, model_registry, chain)
        moved = self._m(ChainStepManager, admin_a, model_registry).update(
            step.id, chain_id=other.id, expression="2"
        )
        assert (moved.chain_id, moved.expression) == (chain.id, "2")

    def test_names_are_unique_in_a_chain(self, admin_a, model_registry):
        chain = self._chain(admin_a, model_registry)
        self._set_step(admin_a, model_registry, chain, "same")
        refused({409}, self._set_step, admin_a, model_registry, chain, "same")
        other = self._set_step(admin_a, model_registry, chain, "other")
        refused(
            {409},
            self._m(ChainStepManager, admin_a, model_registry).update,
            other.id,
            name="same",
        )

    def test_an_update_is_checked_as_a_whole(self, admin_a, model_registry):
        chain = self._chain(admin_a, model_registry)
        step = self._set_step(admin_a, model_registry, chain)
        steps = self._m(ChainStepManager, admin_a, model_registry)
        refused({422}, steps.update, step.id, expression="__import__('os')")
        refused({422}, steps.update, step.id, kind="prompt")
        assert steps.get(id=step.id).expression == "1"

    def test_a_prompt_step_uses_a_prompt_its_author_sees(
        self, admin_a, admin_b, model_registry
    ):
        theirs = self._prompt(admin_b, model_registry, "private {X}")
        chain = self._chain(admin_a, model_registry)
        refused(
            {404},
            self._step,
            admin_a,
            model_registry,
            chain,
            "p",
            "prompt",
            1,
            prompt_id=theirs.id,
        )
        mine = self._prompt(admin_a, model_registry, "mine")
        step = self._step(
            admin_a, model_registry, chain, "p", "prompt", 1, prompt_id=mine.id
        )
        refused(
            {404},
            self._m(ChainStepManager, admin_a, model_registry).update,
            step.id,
            prompt_id=theirs.id,
        )

    @pytest.mark.parametrize(
        "extension, name",
        [("ai_chains", "run_chain"), ("ai_agents", "take_turn")],
    )
    def test_no_chain_calls_a_forbidden_ability(
        self, extension, name, admin_a, model_registry
    ):
        chain = self._chain(admin_a, model_registry)
        refused(
            {403},
            self._step,
            admin_a,
            model_registry,
            chain,
            "forbidden",
            "ability",
            1,
            ability_id=ability_id(model_registry, extension, name),
        )

    def test_an_ability_step_names_an_ability_that_exists(
        self, admin_a, model_registry
    ):
        chain = self._chain(admin_a, model_registry)
        refused(
            {404},
            self._step,
            admin_a,
            model_registry,
            chain,
            "missing",
            "ability",
            1,
            ability_id=str(uuid.uuid4()),
        )


class TestRunAccess(ChainFixtures):
    async def test_no_one_else_runs_a_chain(self, admin_a, admin_b, model_registry):
        chain = self._chain(admin_a, model_registry)
        refused(
            {403, 404},
            self._m(ChainRunManager, admin_b, model_registry).create,
            chain_id=chain.id,
        )
        with pytest.raises(HTTPException):
            await self._m(ChainManager, admin_b, model_registry).run(chain.id, {})

    async def test_runs_and_results_are_seen_through_the_chain(
        self, admin_a, admin_b, model_registry
    ):
        chain = self._chain(admin_a, model_registry)
        self._step(
            admin_a,
            model_registry,
            chain,
            "s",
            "set",
            1,
            expression="1",
            variable="x",
        )
        run = await self._run(admin_a, model_registry, chain)
        [result] = self._results(admin_a, model_registry, run)
        refused({404}, self._m(ChainRunManager, admin_b, model_registry).get, id=run.id)
        refused(
            {404},
            self._m(ChainStepResultManager, admin_b, model_registry).get,
            id=result.id,
        )
        assert (
            self._m(ChainStepResultManager, admin_b, model_registry).list(
                chain_run_id=run.id
            )
            == []
        )
        refused(
            {403, 404}, self._m(ChainRunManager, admin_b, model_registry).cancel, run.id
        )

    def test_a_run_starts_pending_and_its_lifecycle_is_the_servers(
        self, admin_a, admin_b, model_registry
    ):
        chain = self._chain(admin_a, model_registry)
        runs = self._m(ChainRunManager, admin_a, model_registry)
        run = runs.create(
            chain_id=chain.id,
            user_id=admin_b.id,
            status="succeeded",
            inputs={"a": 1},
        )
        assert (run.status, run.user_id, run.inputs) == (
            "pending",
            admin_a.id,
            {"a": 1},
        )
        forged = runs.update(
            run.id,
            status="succeeded",
            output='"forged"',
            variables={"x": 1},
            steps_executed=99,
            error_kind="none",
        )
        assert (forged.status, forged.output, forged.steps_executed) == (
            "pending",
            None,
            0,
        )
        assert runs.update(run.id, cancel_requested=False).cancel_requested is False

    def test_run_inputs_are_checked(self, admin_a, model_registry):
        chain = self._chain(admin_a, model_registry)
        runs = self._m(ChainRunManager, admin_a, model_registry)
        refused({422}, runs.create, chain_id=chain.id, inputs={"not valid": 1})

    async def test_step_results_are_the_servers(self, admin_a, model_registry):
        chain = self._chain(admin_a, model_registry)
        run = await self._run(admin_a, model_registry, chain)
        refused(
            {403},
            self._m(ChainStepResultManager, admin_a, model_registry).create,
            chain_run_id=run.id,
            step_name="forged",
            kind="set",
            sequence=1,
            status="succeeded",
        )
