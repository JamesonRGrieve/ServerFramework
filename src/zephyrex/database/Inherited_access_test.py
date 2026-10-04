# SPDX-License-Identifier: AGPL-3.0-or-later
"""A record that inherits its access (``permission_references``) answers
only to its parents and to Permission rows, on a real database.

The hole this closes: the permission filter also granted such a record to
whoever created it (and to its ``user_id`` and team), as it does a record
that stands alone. A user whose share of a parent was revoked went on
reading and editing the children they had written under it, and a row
planted on someone else's parent stayed its planter's. The reverse also
held: a child the server wrote (as ROOT) on a user's parent was hidden
from everyone who could see that parent.

Provider instances and their settings stand in for any parent and child.
"""

import uuid
from typing import Any

import pytest
from fastapi import HTTPException

from zephyrex.database.StaticPermissions import (
    PermissionResult,
    PermissionType,
    check_permission,
    inherits_access,
    user_can_edit,
)
from zephyrex.extensions.acl_rbac.BLL_ACL import PermissionManager
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderInstanceSettingManager,
    ProviderInstanceSettingModel,
    ProviderManager,
)
from zephyrex.testing.factories import add_user_to_team, create_team, create_user


def _refused(status: int, call: Any, *args: Any, **kwargs: Any) -> None:
    with pytest.raises(HTTPException) as refused:
        call(*args, **kwargs)
    assert refused.value.status_code == status, refused.value.detail


@pytest.fixture
def instance_of(model_registry):
    with ProviderManager(
        requester_id=env("ROOT_ID"), model_registry=model_registry
    ) as providers:
        provider_id = str(providers.create(name=f"Inherit {uuid.uuid4()}").id)

    def _make(owner_id: str, **fields: Any) -> Any:
        with ProviderInstanceManager(
            requester_id=owner_id, model_registry=model_registry
        ) as instances:
            return instances.create(
                name=f"Instance {uuid.uuid4()}", provider_id=provider_id, **fields
            )

    return _make


def _settings(model_registry, requester_id: str) -> ProviderInstanceSettingManager:
    return ProviderInstanceSettingManager(
        requester_id=requester_id, model_registry=model_registry
    )


def _permission(model_registry, user_id: str, row_id: str, level: PermissionType):
    base = model_registry.DB.manager.Base
    session = model_registry.DB.session()
    try:
        result, _ = check_permission(
            user_id,
            ProviderInstanceSettingModel.DB(base),
            row_id,
            session,
            declarative_base=base,
            required_level=level,
        )
        return result
    finally:
        session.close()


def test_the_settings_model_inherits(model_registry):
    base = model_registry.DB.manager.Base
    assert inherits_access(ProviderInstanceSettingModel.DB(base))
    assert not inherits_access(ProviderInstanceModel.DB(base))


def test_a_revoked_share_takes_the_children_its_user_wrote(
    server, model_registry, instance_of
):
    owner, grantee = create_user(server), create_user(server)
    # A grant names only a user the granter can see. The instance names no
    # team, so the shared team grants nothing on it.
    team = create_team(server, owner.id, name=f"inherit {uuid.uuid4().hex[:8]}")
    add_user_to_team(server, grantee.id, team.id, env("USER_ROLE_ID"))
    instance = instance_of(owner.id)
    grants = PermissionManager(requester_id=owner.id, model_registry=model_registry)
    grant = grants.create(
        resource_type="provider_instances",
        resource_id=instance.id,
        user_id=grantee.id,
        can_view=True,
        can_edit=True,
    )
    with _settings(model_registry, grantee.id) as settings:
        row = settings.create(
            provider_instance_id=instance.id, key="api_base", value="https://shared"
        )
        assert settings.update(row.id, value="https://edited").value == (
            "https://edited"
        )

    grants.delete(id=grant.id)

    with _settings(model_registry, grantee.id) as settings:
        _refused(404, settings.get, id=row.id)
        assert settings.list(provider_instance_id=instance.id) == []
        _refused(404, settings.update, row.id, value="https://still-mine")
        _refused(404, settings.delete, row.id)
    for level in (PermissionType.VIEW, PermissionType.EDIT):
        assert _permission(model_registry, grantee.id, row.id, level) == (
            PermissionResult.DENIED
        )
    # The owner still holds it, through the instance.
    with _settings(model_registry, owner.id) as settings:
        assert settings.get(id=row.id).value == "https://edited"


def test_children_follow_their_parent(
    admin_b, user_b, team_b, server, model_registry, instance_of
):
    """Who sees the parent sees its children, and who may edit the parent
    may edit them, whoever wrote them."""
    shared = instance_of(admin_b.id, team_id=team_b.id)
    with _settings(model_registry, admin_b.id) as settings:
        row = settings.create(
            provider_instance_id=shared.id, key="api_base", value="https://ok"
        )
    with _settings(model_registry, user_b.id) as settings:
        assert settings.get(id=row.id).value == "https://ok"
        assert [s.id for s in settings.list(provider_instance_id=shared.id)] == [row.id]
    assert _permission(model_registry, user_b.id, row.id, PermissionType.VIEW) == (
        PermissionResult.GRANTED
    )
    assert _permission(model_registry, user_b.id, row.id, PermissionType.EDIT) == (
        PermissionResult.DENIED
    )
    assert _permission(model_registry, admin_b.id, row.id, PermissionType.EDIT) == (
        PermissionResult.GRANTED
    )


def test_a_child_the_server_wrote_is_seen_through_its_parent(
    admin_a, admin_b, server, model_registry, instance_of
):
    """A row ROOT writes on a user's instance is the instance's to show,
    and stays the server's to change."""
    mine = instance_of(admin_a.id)
    with _settings(model_registry, env("ROOT_ID")) as settings:
        row = settings.create(
            provider_instance_id=mine.id, key="api_base", value="https://server"
        )
    with _settings(model_registry, admin_a.id) as settings:
        assert settings.get(id=row.id).value == "https://server"
        assert row.id in {s.id for s in settings.list(provider_instance_id=mine.id)}
    assert _permission(model_registry, admin_a.id, row.id, PermissionType.VIEW) == (
        PermissionResult.GRANTED
    )
    base = model_registry.DB.manager.Base
    session = model_registry.DB.session()
    try:
        assert not user_can_edit(
            admin_a.id,
            ProviderInstanceSettingModel.DB(base),
            row.id,
            session,
            declarative_base=base,
        )
    finally:
        session.close()
    with _settings(model_registry, admin_a.id) as settings:
        # The edit filter holds it back, as it does any server-written row.
        _refused(404, settings.update, row.id, value="https://mine")
    # Someone who cannot see the instance sees none of it.
    with _settings(model_registry, admin_b.id) as settings:
        _refused(404, settings.get, id=row.id)
