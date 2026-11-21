# SPDX-License-Identifier: AGPL-3.0-or-later
"""``UserManager.create``: ROOT creates users directly (the admin CLI),
checked and stored as ``register`` does; everyone else is refused."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import UserManager
from zephyrex.testing.factories import TEST_PASSWORD, authorize_user, create_user


def _email() -> str:
    return f"root-made-{uuid.uuid4().hex[:10]}@example.com"


def _root(model_registry) -> UserManager:
    return UserManager(model_registry=model_registry, requester_id=env("ROOT_ID"))


def test_root_creates_a_user_with_a_password(server, model_registry):
    email = _email()
    user = _root(model_registry).create(
        email=email.upper(), username=email.split("@")[0], password=TEST_PASSWORD
    )
    assert user.email == email
    assert authorize_user(server, email)


def test_root_creates_a_passwordless_inactive_user(model_registry):
    user = _root(model_registry).create(email=_email(), active=False)
    assert user.active is False


def test_others_cannot_create_users(server, model_registry):
    member = create_user(server)
    manager = UserManager(model_registry=model_registry, requester_id=member.id)
    with pytest.raises(HTTPException) as err:
        manager.create(email=_email())
    assert err.value.status_code == 403


@pytest.mark.parametrize(
    "fields, status",
    [
        ({"username": "no-email"}, 422),
        ({"email": "x@example.com", "is_admin": True}, 422),
        ({"email": "short@example.com", "password": "a"}, 422),
    ],
)
def test_root_create_is_checked(model_registry, fields, status):
    with pytest.raises(HTTPException) as err:
        _root(model_registry).create(**fields)
    assert err.value.status_code == status


def test_taken_email_and_username_conflict(server, model_registry):
    existing = create_user(server)
    with pytest.raises(HTTPException) as err:
        _root(model_registry).create(email=existing.email)
    assert err.value.status_code == 409
    with pytest.raises(HTTPException) as err:
        _root(model_registry).create(email=_email(), username=existing.username)
    assert err.value.status_code == 409
