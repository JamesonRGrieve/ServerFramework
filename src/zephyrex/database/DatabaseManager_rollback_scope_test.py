# SPDX-License-Identifier: AGPL-3.0-or-later
"""``DatabaseManager.rollback_scope``: writes that managers commit inside it
are visible inside and gone after; AFTER hooks are skipped and listed."""

from __future__ import annotations

import uuid

import pytest

from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import TeamManager


def _teams_named(model_registry, name: str) -> list:
    manager = TeamManager(model_registry=model_registry, requester_id=env("ROOT_ID"))
    return manager.list(name=name)


def test_committed_writes_roll_back_at_scope_exit(model_registry):
    name = f"rollback-{uuid.uuid4().hex[:8]}"
    db_manager = model_registry.database_manager
    with db_manager.rollback_scope():
        manager = TeamManager(
            model_registry=model_registry, requester_id=env("ROOT_ID")
        )
        team = manager.create(name=name)
        assert manager.get(id=team.id).name == name
        manager.update(team.id, description="changed")
        assert _teams_named(model_registry, name)[0].description == "changed"
    assert _teams_named(model_registry, name) == []
    assert db_manager.rollback is None


def test_writes_after_the_scope_persist(model_registry):
    name = f"rollback-{uuid.uuid4().hex[:8]}"
    with model_registry.database_manager.rollback_scope():
        pass
    TeamManager(model_registry=model_registry, requester_id=env("ROOT_ID")).create(
        name=name
    )
    assert len(_teams_named(model_registry, name)) == 1


def test_scope_rolls_back_when_the_body_raises(model_registry):
    name = f"rollback-{uuid.uuid4().hex[:8]}"
    with pytest.raises(RuntimeError):
        with model_registry.database_manager.rollback_scope():
            TeamManager(
                model_registry=model_registry, requester_id=env("ROOT_ID")
            ).create(name=name)
            raise RuntimeError("stop")
    assert _teams_named(model_registry, name) == []
    assert model_registry.database_manager.rollback is None


def test_scopes_do_not_nest(model_registry):
    with model_registry.database_manager.rollback_scope():
        with pytest.raises(RuntimeError):
            with model_registry.database_manager.rollback_scope():
                pass


def test_after_hooks_are_skipped_and_listed(model_registry):
    from zephyrex.logic.AbstractLogicManager import HookTiming, hook_bll

    ran: list = []

    @hook_bll(TeamManager.create, timing=HookTiming.AFTER)
    def note_team_created(context) -> None:
        ran.append(context.result)

    try:
        with model_registry.database_manager.rollback_scope() as scope:
            TeamManager(
                model_registry=model_registry, requester_id=env("ROOT_ID")
            ).create(name=f"rollback-{uuid.uuid4().hex[:8]}")
        assert ran == []
        assert any("note_team_created" in hook for hook in scope.skipped_hooks)
        assert all(
            hook.startswith("TeamManager.create: ")
            for hook in scope.skipped_hooks
            if "note_team" in hook
        )

        TeamManager(model_registry=model_registry, requester_id=env("ROOT_ID")).create(
            name=f"rollback-{uuid.uuid4().hex[:8]}"
        )
        assert len(ran) == 1
    finally:
        registry = TeamManager._hook_registry
        after = registry.hooks["create"]["after"]
        after[:] = [h for h in after if h["func"] is not note_team_created]
        registry._invalidate()
