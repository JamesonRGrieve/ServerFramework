# SPDX-License-Identifier: AGPL-3.0-or-later
"""Chain abilities act as the user they name: run a chain and read back the
run with its steps, list chains, cancel a run; another user reaches none of
it, and no ability runs without a requester. The old abilities answered
canned chains, runs and timestamps from an in-process dict."""

import pytest
from fastapi import HTTPException

from zephyrex.extensions.ai_chains.BLL_AI_Chains import ChainRunManager
from zephyrex.extensions.ai_chains.chain_fixtures_test import ChainFixtures
from zephyrex.extensions.ai_chains.EXT_AI_Chains import EXT_AI_Chains
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.pydantic2.registry import ModelRegistry


class TestAbilities(ChainFixtures):
    @pytest.fixture(autouse=True)
    def attached(self, server, monkeypatch) -> None:
        registry = server.app.state.model_registry
        monkeypatch.setattr(
            ModelRegistry, "attached", classmethod(lambda cls: registry)
        )

    def _counting_chain(self, user, model_registry):
        chain = self._chain(user, model_registry)
        self._step(
            user,
            model_registry,
            chain,
            "add",
            "set",
            1,
            expression="a + b",
            variable="total",
        )
        return chain

    async def test_run_then_read_the_run(self, admin_a, model_registry):
        chain = self._counting_chain(admin_a, model_registry)
        run = await EXT_AI_Chains.run_chain(admin_a.id, chain.id, {"a": 2, "b": 3})
        assert run["status"] == "succeeded" and run["variables"]["total"] == 5
        fetched = await EXT_AI_Chains.get_chain_run(admin_a.id, run["id"])
        assert [s["step_name"] for s in fetched["steps"]] == ["add"]
        assert fetched["steps"][0]["output"] == "5"

    async def test_list_chains(self, admin_a, admin_b, model_registry):
        mine = self._chain(admin_a, model_registry, favourite=True)
        theirs = self._chain(admin_b, model_registry)
        listed = {c["id"] for c in await EXT_AI_Chains.list_chains(admin_a.id)}
        assert mine.id in listed and theirs.id not in listed
        favourites = await EXT_AI_Chains.list_chains(admin_a.id, favourites_only=True)
        assert all(c["favourite"] for c in favourites)

    async def test_cancel_a_pending_run(self, admin_a, model_registry):
        chain = self._counting_chain(admin_a, model_registry)
        run = self._m(ChainRunManager, admin_a, model_registry).create(
            chain_id=chain.id
        )
        cancelled = await EXT_AI_Chains.cancel_chain_run(admin_a.id, run.id, "no")
        assert (cancelled["status"], cancelled["error"]) == ("cancelled", "no")

    async def test_another_user_reaches_nothing(self, admin_a, admin_b, model_registry):
        chain = self._counting_chain(admin_a, model_registry)
        run = await EXT_AI_Chains.run_chain(admin_a.id, chain.id, {"a": 1, "b": 1})
        with pytest.raises(HTTPException):
            await EXT_AI_Chains.run_chain(admin_b.id, chain.id, {"a": 1, "b": 1})
        with pytest.raises(HTTPException):
            await EXT_AI_Chains.get_chain_run(admin_b.id, run["id"])
        with pytest.raises(HTTPException):
            await EXT_AI_Chains.cancel_chain_run(admin_b.id, run["id"])

    async def test_checks(self, admin_a, model_registry):
        chain = self._counting_chain(admin_a, model_registry)
        with pytest.raises(InvalidInputExternalError):
            await EXT_AI_Chains.run_chain(admin_a.id, chain.id, ["not", "an object"])
        with pytest.raises(HTTPException) as raised:
            await EXT_AI_Chains.list_chains("")
        assert raised.value.status_code == 400
