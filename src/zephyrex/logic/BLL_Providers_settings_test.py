# SPDX-License-Identifier: AGPL-3.0-or-later
"""ProviderInstanceModel.get_setting reads the instance's setting rows."""

import uuid

import pytest

from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderInstanceSettingManager,
    ProviderManager,
)
from zephyrex.pydantic2.registry import ModelRegistry


@pytest.fixture
def instance(isolated_extension_server) -> ProviderInstanceModel:
    """A provider instance of its own in a freshly built app (the app
    database outlives a test, so nothing seeded is relied on)."""
    isolated_extension_server("email")
    registry = ModelRegistry.attached()
    assert registry is not None
    root = env("ROOT_ID")
    provider = ProviderManager(model_registry=registry, requester_id=root).create(
        name=f"settings_provider_{uuid.uuid4().hex}"
    )
    created = ProviderInstanceManager(
        model_registry=registry, requester_id=root
    ).create(name=f"settings_instance_{uuid.uuid4().hex}", provider_id=provider.id)
    return ProviderInstanceModel.model_validate(created, from_attributes=True)


def _add_setting(instance: ProviderInstanceModel, key: str, value: str) -> None:
    ProviderInstanceSettingManager(
        model_registry=ModelRegistry.attached(), requester_id=env("ROOT_ID")
    ).create(provider_instance_id=instance.id, key=key, value=value)


def test_returns_the_instance_setting(instance):
    _add_setting(instance, "from_email", "billing@example.com")

    assert instance.get_setting("from_email") == "billing@example.com"


def test_returns_the_default_for_a_missing_setting(instance):
    assert instance.get_setting("from_email") is None
    assert instance.get_setting("from_email", "x@example.com") == "x@example.com"


def test_ignores_other_keys(instance):
    _add_setting(instance, "reply_to", "support@example.com")

    assert instance.get_setting("from_email") is None
