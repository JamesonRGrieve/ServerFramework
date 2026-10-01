# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixtures shared by every extension's tests: the extension's own app, and
real provider instances with settings in it, read the way a rotation reads
them."""

import os
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Type

import pytest

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticProvider
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderInstanceSettingManager,
    ProviderManager,
    RotationManager,
    RotationModel,
    RotationProviderInstanceManager,
    RotationProviderInstanceModel,
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
def extension_app(request: pytest.FixtureRequest) -> Any:
    """The app of the extension whose folder holds the test module, built
    once per module."""
    from zephyrex.app import instance
    from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

    extension = Path(str(request.node.path)).parent.name
    prepare_test_registry()
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
    return instance(
        db_prefix=f"test.{extension}_providers.{worker_id}", extensions=extension
    )


@pytest.fixture
def provider_instance(extension_app: Any) -> InstanceFactory:
    """Create a provider instance of one of the extension's providers, with
    settings.

    Each call makes a fresh instance: the app database outlives a test, so
    settings on a shared instance would leak between tests.
    """
    # This app's own registry: the process-wide attached one is whichever
    # app was built last in the worker, whose database is not this one.
    registry: ModelRegistry = extension_app.state.model_registry
    root_id = env("ROOT_ID")

    def _create(
        provider_cls: Type[AbstractStaticProvider],
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


@pytest.fixture
def rotation_over(provider_instance, extension_app) -> Any:
    """A rotation that tries the given instances in order."""
    registry: ModelRegistry = extension_app.state.model_registry
    root_id = env("ROOT_ID")

    def _build(*instances: ProviderInstanceModel) -> RotationManager:
        rotation_manager = RotationManager(
            model_registry=registry, requester_id=root_id
        )
        rotation = RotationModel.model_validate(
            rotation_manager.create(
                name=f"test_rotation_{uuid.uuid4().hex}",
                description="A test rotation over the given instances",
            ),
            from_attributes=True,
        )
        links = RotationProviderInstanceManager(
            model_registry=registry, requester_id=root_id
        )
        parent_id = None
        for instance in instances:
            link = RotationProviderInstanceModel.model_validate(
                links.create(
                    rotation_id=rotation.id,
                    provider_instance_id=instance.id,
                    parent_id=parent_id,
                ),
                from_attributes=True,
            )
            parent_id = link.id
        rotation_manager.target_id = rotation.id
        return rotation_manager

    return _build
