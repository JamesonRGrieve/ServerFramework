# SPDX-License-Identifier: AGPL-3.0-or-later
"""A revoked grant grants nothing, on a real database.

The hole this closes: deleting a Permission row soft-deletes it, and the
permission filter reads grants (and team memberships) through core
``select()`` subqueries that the ORM's automatic ``deleted_at IS NULL``
filter never reaches. A user whose access was revoked went on reading
and editing the record."""

import uuid

import pytest
from fastapi import HTTPException

from zephyrex.extensions.acl_rbac.BLL_ACL import PermissionManager
from zephyrex.logic.BLL_Providers import RotationManager


def test_a_revoked_grant_grants_nothing(admin_a, admin_b, model_registry):
    owned = RotationManager(requester_id=admin_a.id, model_registry=model_registry)
    rotation = owned.create(
        name=f"revocation {uuid.uuid4().hex}", description="shared, then not"
    )
    other = RotationManager(requester_id=admin_b.id, model_registry=model_registry)
    with pytest.raises(HTTPException):
        other.get(id=rotation.id)

    grants = PermissionManager(requester_id=admin_a.id, model_registry=model_registry)
    grant = grants.create(
        resource_type="rotations",
        resource_id=rotation.id,
        user_id=admin_b.id,
        can_view=True,
        can_edit=True,
    )
    assert other.get(id=rotation.id).id == rotation.id
    assert rotation.id in {r.id for r in other.list()}

    grants.delete(id=grant.id)
    with pytest.raises(HTTPException) as refused:
        other.get(id=rotation.id)
    assert refused.value.status_code == 404
    assert rotation.id not in {r.id for r in other.list()}
    with pytest.raises(HTTPException):
        other.update(rotation.id, description="still mine?")
