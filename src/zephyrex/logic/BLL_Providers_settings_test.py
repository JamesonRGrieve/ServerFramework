# SPDX-License-Identifier: AGPL-3.0-or-later
"""ProviderInstanceModel.get_setting reads the instance's setting rows."""

import uuid

import pytest

from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderInstanceSettingManager,
)
from zephyrex.pydantic2.registry import ModelRegistry


@pytest.fixture
def root_sendgrid(isolated_extension_server) -> ProviderInstanceModel:
    """The seeded Root_Sendgrid instance of a freshly built email app."""
    isolated_extension_server("email")
    registry = ModelRegistry.attached()
    assert registry is not None
    instance = ProviderInstanceManager(
        model_registry=registry, requester_id=env("ROOT_ID")
    ).get(name="Root_Sendgrid")
    return ProviderInstanceModel.model_validate(instance, from_attributes=True)


def _add_setting(instance: ProviderInstanceModel, key: str, value: str) -> None:
    ProviderInstanceSettingManager(
        model_registry=ModelRegistry.attached(), requester_id=env("ROOT_ID")
    ).create(provider_instance_id=instance.id, key=key, value=value)


# The app database outlives a test, so each test names its own keys.
def _key() -> str:
    return f"setting_{uuid.uuid4().hex}"


def test_returns_the_instance_setting(root_sendgrid):
    key = _key()
    _add_setting(root_sendgrid, key, "billing@example.com")

    assert root_sendgrid.get_setting(key) == "billing@example.com"


def test_returns_the_default_for_a_missing_setting(root_sendgrid):
    key = _key()

    assert root_sendgrid.get_setting(key) is None
    assert root_sendgrid.get_setting(key, "x@example.com") == "x@example.com"


def test_ignores_other_keys(root_sendgrid):
    _add_setting(root_sendgrid, _key(), "support@example.com")

    assert root_sendgrid.get_setting(_key()) is None
