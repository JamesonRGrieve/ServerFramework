# SPDX-License-Identifier: AGPL-3.0-or-later
"""``create`` keeps only the fields its ``Create`` schema declares, so a
manager that sets a model field itself (a ``create`` override filling a
field the caller doesn't send) lost it silently and failed later, at a NOT
NULL column (Zephyrex RPG Parity, 2026-11-23). ``trusted_fields`` and
``create_trusted`` let server-side code set such fields, validated against
the full model; a model field dropped without them is logged.

ProviderInstanceModel's ``enabled`` (default True) is a model field its
Create schema doesn't declare."""

from __future__ import annotations

import uuid
from typing import Any, List

import pytest
from fastapi import HTTPException

import zephyrex.logic.AbstractLogicManager.manager as manager_module
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import ProviderInstanceManager, ProviderManager
from zephyrex.pydantic2.registry import ModelRegistry


@pytest.fixture
def instances(isolated_extension_server) -> Any:
    isolated_extension_server("email")
    registry = ModelRegistry.attached()
    assert registry is not None
    root = env("ROOT_ID")
    provider = ProviderManager(model_registry=registry, requester_id=root).create(
        name=f"trusted_provider_{uuid.uuid4().hex}"
    )
    manager = ProviderInstanceManager(model_registry=registry, requester_id=root)
    manager.test_provider_id = provider.id  # type: ignore[attr-defined]
    return manager


def _fields(manager: Any, **extra: Any) -> dict:
    return {
        "name": f"trusted_{uuid.uuid4().hex}",
        "provider_id": manager.test_provider_id,
        **extra,
    }


@pytest.fixture
def warnings(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    seen: List[str] = []
    original = manager_module.logger.warning

    def capture(message: str, *args: Any, **kwargs: Any) -> None:
        seen.append(message % args if args else message)
        original(message, *args, **kwargs)

    monkeypatch.setattr(manager_module.logger, "warning", capture)
    return seen


def test_a_plain_create_drops_the_field_and_says_so(instances, warnings):
    created = instances.create(**_fields(instances, enabled=False))
    assert created.enabled is True
    assert any("dropped enabled" in line for line in warnings), warnings


def test_trusted_fields_sets_the_named_field(instances, warnings):
    with instances.trusted_fields("enabled"):
        created = instances.create(**_fields(instances, enabled=False))
    assert created.enabled is False
    assert instances.get(id=created.id).enabled is False
    assert not any("dropped" in line for line in warnings)


def test_create_trusted_sets_every_model_field_given(instances):
    created = instances.create_trusted(**_fields(instances, enabled=False))
    assert created.enabled is False


def test_the_scope_ends_with_the_block(instances):
    with instances.trusted_fields("enabled"):
        pass
    assert instances.create(**_fields(instances, enabled=False)).enabled is True


@pytest.mark.parametrize(
    "extra", [{"enabled": "not a bool"}, {"no_such_field": 1}], ids=["bad", "unknown"]
)
def test_a_trusted_value_is_still_validated(instances, extra):
    with pytest.raises(HTTPException) as refused:
        instances.create_trusted(**_fields(instances, **extra))
    assert refused.value.status_code == 422
