# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prompt abilities act as the user they name: save adds arguments for new
variables, build fills them, and another user sees nothing. The old
abilities returned canned answers."""

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_prompts.EXT_AI_Prompts import EXT_AI_Prompts
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.pydantic2.registry import ModelRegistry


class TestAbilities(ExtensionServerMixin):
    extension_class = EXT_AI_Prompts

    @pytest.fixture(autouse=True)
    def attached(self, server, monkeypatch) -> None:
        registry = server.app.state.model_registry
        monkeypatch.setattr(
            ModelRegistry, "attached", classmethod(lambda cls: registry)
        )

    async def test_save_then_build(self, admin_a):
        saved = await EXT_AI_Prompts.save_prompt(
            admin_a.id, "Greeting", "Hello {NAME}, it is {DAY}."
        )
        assert saved["arguments_added"] == ["NAME", "DAY"]
        built = await EXT_AI_Prompts.build_prompt(
            admin_a.id, saved["id"], {"NAME": "Ada"}
        )
        assert built == {"text": "Hello Ada, it is {DAY}.", "missing": ["DAY"]}
        fetched = await EXT_AI_Prompts.get_prompt(admin_a.id, saved["id"])
        assert fetched["arguments"] == {"NAME": None, "DAY": None}

    async def test_updating_adds_only_new_variables(self, admin_a):
        saved = await EXT_AI_Prompts.save_prompt(admin_a.id, "V", "{A}")
        again = await EXT_AI_Prompts.save_prompt(
            admin_a.id, "V", "{A} {B}", prompt_id=saved["id"]
        )
        assert again["id"] == saved["id"] and again["arguments_added"] == ["B"]

    async def test_another_user_sees_nothing(self, admin_a, admin_b):
        saved = await EXT_AI_Prompts.save_prompt(admin_a.id, "Private", "{X}")
        listed = {p["id"] for p in await EXT_AI_Prompts.list_prompts(admin_b.id)}
        assert saved["id"] not in listed
        with pytest.raises(HTTPException):
            await EXT_AI_Prompts.build_prompt(admin_b.id, saved["id"])

    async def test_checks(self, admin_a):
        with pytest.raises(InvalidInputExternalError):
            await EXT_AI_Prompts.save_prompt(admin_a.id, " ", "x")
        with pytest.raises(InvalidInputExternalError):
            await EXT_AI_Prompts.save_prompt(admin_a.id, "n", "")
        with pytest.raises(HTTPException) as raised:
            await EXT_AI_Prompts.list_prompts("")
        assert raised.value.status_code == 400
