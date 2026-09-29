# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixtures for the database providers: a built database app, so provider
instances and their settings are real rows read the way a rotation reads
them."""

import os
import uuid
from typing import Any, Callable, Dict, Optional, Type

import pytest

from zephyrex.extensions.database.EXT_Database import (
    AbstractDatabaseExtensionProvider,
)
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderInstanceSettingManager,
    ProviderManager,
)
from zephyrex.pydantic2.registry import ModelRegistry

InstanceFactory = Callable[..., ProviderInstanceModel]


@pytest.fixture
def set_env(monkeypatch: pytest.MonkeyPatch) -> Callable[[str, str], None]:
    """Set an environment value as ``env()`` reads it, for this test only.

    ``env()`` prefers the registered settings snapshot over ``os.environ``,
    so both are set.
    """
    from zephyrex.lib import Environment

    def _set(name: str, value: str) -> None:
        monkeypatch.setenv(name, value)
        if hasattr(Environment.settings, name):
            monkeypatch.setattr(Environment.settings, name, value)

    return _set


@pytest.fixture(scope="module")
def database_app() -> Any:
    """The database extension's app, built once per test module."""
    from zephyrex.app import instance
    from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

    prepare_test_registry()
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
    return instance(
        db_prefix=f"test.database_providers.{worker_id}", extensions="database"
    )


@pytest.fixture
def provider_instance(database_app: Any) -> InstanceFactory:
    """Create a provider instance of a database provider, with settings.

    Each call makes a fresh instance: the app database outlives a test, so
    settings on a shared instance would leak between tests.
    """
    registry = ModelRegistry.attached()
    assert registry is not None
    root_id = env("ROOT_ID")

    def _create(
        provider_cls: Type[AbstractDatabaseExtensionProvider],
        *,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        settings: Optional[Dict[str, str]] = None,
    ) -> ProviderInstanceModel:
        provider = ProviderManager(model_registry=registry, requester_id=root_id).get(
            name=provider_cls.name
        )
        instance = ProviderInstanceModel.model_validate(
            ProviderInstanceManager(
                model_registry=registry, requester_id=root_id
            ).create(
                name=f"{provider_cls.name}_{uuid.uuid4().hex}",
                provider_id=provider.id,
                api_key=api_key,
                model_name=model_name,
                scope="root",
            ),
            from_attributes=True,
        )
        setting_manager = ProviderInstanceSettingManager(
            model_registry=registry, requester_id=root_id
        )
        for key, value in (settings or {}).items():
            setting_manager.create(
                provider_instance_id=instance.id, key=key, value=value
            )
        return instance

    return _create
