# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prompts and their arguments: variables found, filled from values then
defaults, missing ones reported; arguments synced from content; and who
may do what.

Holes these close: a caller could name another user as a prompt's owner
or move a prompt to another owner or team by updating it, and anyone who
could see a prompt (a shared or system one included) could add arguments
to it, changing what it builds for everyone."""

import pytest
from faker import Faker
from fastapi import HTTPException

from AbstractTest import CategoryOfTest, ClassOfTestsConfig, ParentEntity
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_prompts.BLL_AI_Prompts import (
    PromptArgumentManager,
    PromptManager,
    fill,
    variables_in,
)
from zephyrex.extensions.ai_prompts.EXT_AI_Prompts import EXT_AI_Prompts
from zephyrex.lib.Environment import env
from zephyrex.logic.AbstractBLLTest import AbstractBLLTest
from zephyrex.logic.BLL_Auth_test import TestUserManager as CoreUserManagerTests

AbstractBLLTest.test_config = ClassOfTestsConfig(
    categories=[CategoryOfTest.LOGIC, CategoryOfTest.EXTENSION]
)

faker = Faker()


class TestVariables:
    def test_variables_in_order_of_first_use(self):
        content = "Hi {USER_NAME}, {ITEM_COUNT} items; bye {USER_NAME}. {lower} {X1}"
        assert variables_in(content) == ["USER_NAME", "ITEM_COUNT", "X1"]

    def test_fill_leaves_what_has_no_value(self):
        assert fill("{A} and {B}", {"A": "one"}) == "one and {B}"


class TestPromptManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = PromptManager
    extension_class = EXT_AI_Prompts

    create_fields = {
        "name": lambda: f"Test Prompt {faker.word()}",
        "description": lambda: faker.sentence(),
        "content": lambda: f"Hello {{USER_NAME}}, {faker.sentence()}",
        "favourite": False,
    }
    update_fields = {
        "name": "Updated Prompt Name",
        "description": "Updated prompt description",
        "content": "Updated: {USER_NAME}, this is the updated content.",
        "favourite": True,
    }
    unique_fields = []
    parent_entities = [
        ParentEntity(
            name="user",
            foreign_key="user_id",
            test_class=CoreUserManagerTests,
        ),
    ]

    def _prompt(self, manager, content):
        return manager.create(
            name=f"Prompt {faker.word()}", description="test", content=content
        )

    def test_build_from_values_then_defaults(self, admin_a, model_registry):
        manager = PromptManager(requester_id=admin_a.id, model_registry=model_registry)
        prompt = self._prompt(manager, "Hello {USER_NAME}, {MESSAGE_COUNT} new.")
        manager.arguments.create(
            prompt_id=prompt.id, name="USER_NAME", default_value="Guest"
        )
        manager.arguments.create(
            prompt_id=prompt.id, name="MESSAGE_COUNT", default_value="0"
        )
        assert manager.build(
            prompt.id, {"USER_NAME": "John", "MESSAGE_COUNT": "5"}
        ) == {
            "text": "Hello John, 5 new.",
            "missing": [],
        }
        assert manager.build(prompt.id, {})["text"] == "Hello Guest, 0 new."

    def test_missing_variables_are_reported(self, admin_a, model_registry):
        manager = PromptManager(requester_id=admin_a.id, model_registry=model_registry)
        prompt = self._prompt(manager, "Required: {REQUIRED}, Optional: {OPTIONAL}")
        manager.arguments.create(
            prompt_id=prompt.id, name="REQUIRED", default_value=None
        )
        manager.arguments.create(
            prompt_id=prompt.id, name="OPTIONAL", default_value="d"
        )
        built = manager.build(prompt.id, {})
        assert built == {
            "text": "Required: {REQUIRED}, Optional: d",
            "missing": ["REQUIRED"],
        }
        assert manager.build(prompt.id, {"REQUIRED": "x"})["missing"] == []

    def test_sync_arguments(self, admin_a, model_registry):
        manager = PromptManager(requester_id=admin_a.id, model_registry=model_registry)
        prompt = self._prompt(manager, "Variables: {VAR1}, {VAR2}, {VAR3}")
        manager.arguments.create(prompt_id=prompt.id, name="VAR2", default_value="two")
        assert manager.sync_arguments(prompt.id) == ["VAR1", "VAR3"]
        assert manager.defaults(prompt.id) == {
            "VAR1": None,
            "VAR2": "two",
            "VAR3": None,
        }
        assert manager.sync_arguments(prompt.id) == []

    def test_the_owner_is_the_creator(self, admin_a, admin_b, model_registry):
        manager = PromptManager(requester_id=admin_a.id, model_registry=model_registry)
        prompt = manager.create(
            name="Mine", description="test", content="x", user_id=admin_b.id
        )
        assert prompt.user_id == admin_a.id

    def test_an_update_does_not_move_a_prompt(self, admin_a, admin_b, model_registry):
        manager = PromptManager(requester_id=admin_a.id, model_registry=model_registry)
        prompt = self._prompt(manager, "x")
        updated = manager.update(prompt.id, name="Renamed", user_id=admin_b.id)
        assert updated.name == "Renamed" and updated.user_id == admin_a.id

    def test_root_may_name_the_owner(self, admin_a, model_registry):
        root = PromptManager(requester_id=env("ROOT_ID"), model_registry=model_registry)
        prompt = root.create(
            name="For A", description="test", content="x", user_id=admin_a.id
        )
        assert prompt.user_id == admin_a.id


class TestPromptArgumentManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = PromptArgumentManager
    extension_class = EXT_AI_Prompts

    create_fields = {
        "name": lambda: f"ARG_{faker.word().upper()}",
        "default_value": lambda: faker.word(),
    }
    update_fields = {
        "name": "UPDATED_ARG",
        "default_value": "updated_value",
    }
    unique_fields = []
    parent_entities = [
        ParentEntity(
            name="prompt",
            foreign_key="prompt_id",
            test_class=TestPromptManager,
        ),
    ]

    def test_an_argument_with_and_without_a_default(self, admin_a, model_registry):
        prompt = PromptManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).create(
            name="Args", description="test", content="Hello {TEST_VAR} {REQUIRED_VAR}!"
        )
        manager = PromptArgumentManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        with_default = manager.create(
            prompt_id=prompt.id, name="TEST_VAR", default_value="World"
        )
        required = manager.create(prompt_id=prompt.id, name="REQUIRED_VAR")
        assert with_default.default_value == "World"
        assert required.default_value is None
        assert {with_default.prompt_id, required.prompt_id} == {prompt.id}

    def test_an_argument_name_is_a_variable_name(self, admin_a, model_registry):
        prompt = PromptManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).create(name="Names", description="test", content="x")
        manager = PromptArgumentManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        with pytest.raises(HTTPException) as refused:
            manager.create(prompt_id=prompt.id, name="not a variable")
        assert refused.value.status_code == 422

    def test_another_users_prompt_takes_no_arguments_from_you(
        self, admin_a, admin_b, model_registry
    ):
        prompt = PromptManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).create(name="A's", description="test", content="{SECRET}")
        with pytest.raises(HTTPException):
            PromptArgumentManager(
                requester_id=admin_b.id, model_registry=model_registry
            ).create(prompt_id=prompt.id, name="SECRET", default_value="injected")

    def test_a_system_prompt_takes_no_arguments_from_users(
        self, admin_a, model_registry
    ):
        """A prompt every user can see (owned by SYSTEM) is still not theirs
        to change."""
        prompt = PromptManager(
            requester_id=env("SYSTEM_ID"), model_registry=model_registry
        ).create(
            name="Shared",
            description="test",
            content="{TONE}",
            user_id=env("SYSTEM_ID"),
        )
        with pytest.raises(HTTPException):
            PromptArgumentManager(
                requester_id=admin_a.id, model_registry=model_registry
            ).create(prompt_id=prompt.id, name="TONE", default_value="rude")
