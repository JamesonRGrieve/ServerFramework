# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared test doubles for exercising ``RotationManager.rotate`` without a DB."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from zephyrex.logic import BLL_Providers as _bll_mod  # canonical module reference
from zephyrex.logic.BLL_Providers import RotationManager


def fake_rotation_manager(
    monkeypatch: pytest.MonkeyPatch,
    instances: list[Any],
    *,
    team_id: str | None = None,
) -> RotationManager:
    """Build a RotationManager whose `_get_ordered_rotation_provider_instances`
    returns one fake RPI per supplied instance, and whose provider-instance
    lookup returns the matching instance. Bypasses the DB entirely; every
    patch is registered on ``monkeypatch`` so pytest restores it at teardown.

    The fake DB lookup honors the `id` kwarg so that skipping a provider
    (e.g. via auth cooldown) does not desynchronize the lookup from the RPI
    iteration order.
    """
    rm = RotationManager(model_registry=None, requester_id="r1")
    rm.target_id = "rotation-1"
    rm.requester = (
        MagicMock(id="r1") if team_id is None else MagicMock(id="r1", team_id=team_id)
    )

    fake_rpis = [
        MagicMock(provider_instance_id=f"pi-{i}") for i in range(len(instances))
    ]
    monkeypatch.setattr(
        rm, "_get_ordered_rotation_provider_instances", lambda: fake_rpis
    )

    # Map pi-id -> instance so the fake lookup returns the right instance
    # regardless of which RPIs the rotation actually visits.
    pi_map: dict[str, Any] = {f"pi-{i}": inst for i, inst in enumerate(instances)}

    class _FakeDB:
        def get(self, *_args: Any, **kwargs: Any) -> Any:
            return pi_map.get(str(kwargs.get("id")))

    rm.model_registry = MagicMock()
    rm.model_registry.DB.Base = object()
    rm.model_registry.DB.get_session.return_value.__enter__ = lambda self_: None
    rm.model_registry.DB.get_session.return_value.__exit__ = lambda *a: None

    # Patch through the canonical `_bll_mod` reference (bound at import time)
    # so the patched class is the same one the OLD wrapped rotate method
    # resolves through its function globals — even after another test's
    # `_scoped_import` has swapped `sys.modules["zephyrex.logic.BLL_Providers"]`
    # for a new module object. Patching via a string path or a fresh import
    # would land on the NEW class while rotate keeps reading from the OLD
    # class, leaving the DB unstubbed.
    monkeypatch.setattr(_bll_mod.ProviderInstanceModel, "DB", lambda base: _FakeDB())
    return rm
