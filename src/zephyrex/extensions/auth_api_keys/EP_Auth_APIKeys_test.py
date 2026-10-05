# SPDX-License-Identifier: AGPL-3.0-or-later
"""Endpoint tests for the auth_api_keys extension.

Runs against a real app with ``auth_api_keys`` loaded (``ExtensionServerMixin``).
The CRUD surface (GET/LIST/SEARCH/DELETE) and issued-key authentication are
exercised over HTTP. The issue/validate/rotate flows are exercised through a
real ``APIKeyManager`` bound to the same app registry and database, because
the framework's ``@custom_route`` REST adapter (``lib/CustomRoute.py``) cannot
yet dispatch these routes: it inspects the hook-wrapped method's
``(self, *args, **kwargs)`` signature and so never passes ``body``, it does
not await ``async`` route methods, and it resolves the manager with the
manager's JWT auth even for ``authentication_type="none"`` routes.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.auth_api_keys.BLL_Auth_APIKeys import (
    APIKeyIssueResponse,
    APIKeyManager,
    APIKeyModel,
)
from zephyrex.extensions.auth_api_keys.EXT_Auth_APIKeys import EXT_Auth_APIKeys
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import UserManager, _api_key_hooks
from zephyrex.testing.factories import current_if_match

API_KEYS = "/v1/auth/api-keys"
# The envelope key is the model's wire name, acronyms kept whole.
SEARCH_BODY_KEY = "api_key"


def _bearer(credential: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {credential}"}


def _assert_no_secret(payload: Any, raw: Optional[str] = None) -> None:
    """Walk a response body: no ``key_hash``/``key`` field, no raw key value."""
    if isinstance(payload, dict):
        assert "key_hash" not in payload, payload
        assert "key" not in payload, payload
        for value in payload.values():
            _assert_no_secret(value, raw)
    elif isinstance(payload, list):
        for value in payload:
            _assert_no_secret(value, raw)
    elif raw is not None:
        assert payload != raw


@pytest.mark.ep
@pytest.mark.auth
class TestAPIKeyEndpoints(ExtensionServerMixin):
    extension_class = EXT_Auth_APIKeys

    @staticmethod
    def _manager(model_registry: Any, requester_id: str) -> APIKeyManager:
        return APIKeyManager(requester_id=requester_id, model_registry=model_registry)

    def _issue(
        self, model_registry: Any, requester_id: str, **fields: Any
    ) -> APIKeyIssueResponse:
        fields.setdefault("name", "test key")
        return self._manager(model_registry, requester_id).issue_key(**fields)

    def _is_valid(self, model_registry: Any, raw: str) -> bool:
        return (
            self._manager(model_registry, env("ROOT_ID")).validate_key(raw) is not None
        )

    # -- issuance & write-only secrets -------------------------------------

    def test_issue_returns_raw_key_once(self, server, model_registry, admin_a):
        issued = self._issue(model_registry, admin_a.id, name="issue-once")
        assert issued.key
        assert issued.name == "issue-once"
        assert "key_hash" not in issued.model_dump()

        fetched = server.get(f"{API_KEYS}/{issued.id}", headers=_bearer(admin_a.jwt))
        assert fetched.status_code == 200, fetched.text
        assert issued.id in fetched.text
        assert issued.key not in fetched.text
        _assert_no_secret(fetched.json(), issued.key)

    def test_list_and_search_never_expose_key_or_hash(
        self, server, model_registry, admin_a
    ):
        issued = self._issue(model_registry, admin_a.id, name="list-hidden")

        listed = server.get(API_KEYS, headers=_bearer(admin_a.jwt))
        assert listed.status_code == 200, listed.text
        assert issued.id in listed.text
        assert issued.key not in listed.text
        _assert_no_secret(listed.json(), issued.key)

        searched = server.post(
            f"{API_KEYS}/search",
            json={SEARCH_BODY_KEY: {"name": {"eq": "list-hidden"}}},
            headers=_bearer(admin_a.jwt),
        )
        assert searched.status_code == 200, searched.text
        assert issued.id in searched.text
        assert issued.key not in searched.text
        _assert_no_secret(searched.json(), issued.key)

    # -- validation --------------------------------------------------------

    def test_validate_accepts_valid_key(self, model_registry, admin_a):
        issued = self._issue(model_registry, admin_a.id)
        record = self._manager(model_registry, env("ROOT_ID")).validate_key(issued.key)
        assert record is not None
        assert record.id == issued.id
        assert record.user_id == admin_a.id
        assert record.role_id is None

    @staticmethod
    def _stamped_last_used(model_registry: Any, key_id: str) -> bool:
        KeyDB = APIKeyModel.DB(model_registry.DB.manager.Base)
        row = KeyDB.get(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            id=key_id,
            return_type="dto",
            override_dto=APIKeyModel,
        )
        return row is not None and row.last_used_at is not None

    def test_validate_stamps_last_used_at(self, model_registry, admin_a):
        issued = self._issue(model_registry, admin_a.id)
        assert not self._stamped_last_used(model_registry, issued.id)
        assert self._is_valid(model_registry, issued.key)
        assert self._stamped_last_used(model_registry, issued.id)

    def test_validate_rejects_unknown_key(self, model_registry):
        assert not self._is_valid(model_registry, "not-an-issued-key")

    def test_validate_rejects_revoked_key(self, server, model_registry, admin_a):
        issued = self._issue(model_registry, admin_a.id)
        headers = _bearer(admin_a.jwt)
        revoked = server.delete(
            f"{API_KEYS}/{issued.id}",
            headers={
                **headers,
                **current_if_match(server, f"{API_KEYS}/{issued.id}", headers),
            },
        )
        assert revoked.status_code in (200, 204), revoked.text
        assert not self._is_valid(model_registry, issued.key)

    def test_validate_rejects_expired_key(self, model_registry, admin_a):
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        issued = self._issue(model_registry, admin_a.id, expires_at=past)
        assert not self._is_valid(model_registry, issued.key)

    # -- rotation & revocation authorization -------------------------------

    def test_rotate_issues_working_key_and_retires_old(self, model_registry, admin_a):
        issued = self._issue(model_registry, admin_a.id, name="to-rotate")
        rotated = self._manager(model_registry, admin_a.id).rotate_key(issued.id)
        assert rotated.key != issued.key
        assert rotated.id != issued.id
        assert rotated.name == "to-rotate"
        assert self._is_valid(model_registry, rotated.key)
        assert not self._is_valid(model_registry, issued.key)

    def test_rotate_preserves_root_assigned_role(self, model_registry, admin_a):
        issued = self._issue(
            model_registry,
            env("ROOT_ID"),
            name="root-scoped",
            user_id=admin_a.id,
            role_id=env("USER_ROLE_ID"),
        )
        rotated = self._manager(model_registry, admin_a.id).rotate_key(issued.id)
        record = self._manager(model_registry, env("ROOT_ID")).validate_key(rotated.key)
        assert record is not None
        assert record.role_id == env("USER_ROLE_ID")

    def test_user_cannot_revoke_another_users_key(
        self, server, model_registry, admin_a, admin_b
    ):
        issued = self._issue(model_registry, admin_a.id)
        response = server.delete(
            f"{API_KEYS}/{issued.id}", headers=_bearer(admin_b.jwt)
        )
        assert response.status_code == 403, response.text
        with pytest.raises(HTTPException) as excinfo:
            self._manager(model_registry, admin_b.id).revoke_key(issued.id)
        assert excinfo.value.status_code == 403
        assert self._is_valid(model_registry, issued.key)

    def test_user_cannot_rotate_another_users_key(
        self, model_registry, admin_a, admin_b
    ):
        issued = self._issue(model_registry, admin_a.id)
        with pytest.raises(HTTPException) as excinfo:
            self._manager(model_registry, admin_b.id).rotate_key(issued.id)
        assert excinfo.value.status_code == 403
        assert self._is_valid(model_registry, issued.key)

    def test_user_cannot_issue_key_for_another_user(
        self, model_registry, admin_a, admin_b
    ):
        with pytest.raises(HTTPException) as excinfo:
            self._issue(model_registry, admin_a.id, user_id=admin_b.id)
        assert excinfo.value.status_code == 403

    def test_team_key_requires_membership(self, model_registry, admin_a, team_b):
        with pytest.raises(HTTPException) as excinfo:
            self._issue(model_registry, admin_a.id, team_id=team_b.id)
        assert excinfo.value.status_code == 403

    # -- direct CRUD creation ----------------------------------------------

    def test_direct_crud_post_has_no_route(self, server, admin_a):
        response = server.post(
            API_KEYS,
            json={"api_key": {"name": "direct"}},
            headers=_bearer(admin_a.jwt),
        )
        assert response.status_code == 405, response.text

    def test_direct_bll_create_refused_for_non_root(self, model_registry, admin_a):
        with pytest.raises(HTTPException) as excinfo:
            self._manager(model_registry, admin_a.id).create(name="direct")
        assert excinfo.value.status_code == 403

    # -- role escalation ---------------------------------------------------

    def test_non_root_cannot_assign_role(self, model_registry, admin_a):
        with pytest.raises(HTTPException) as excinfo:
            self._issue(model_registry, admin_a.id, role_id=env("ADMIN_ROLE_ID"))
        assert excinfo.value.status_code == 403

    def test_root_can_assign_role(self, model_registry):
        issued = self._issue(
            model_registry, env("ROOT_ID"), role_id=env("USER_ROLE_ID")
        )
        record = self._manager(model_registry, env("ROOT_ID")).validate_key(issued.key)
        assert record is not None
        assert record.role_id == env("USER_ROLE_ID")

    # -- issued keys authenticate requests ---------------------------------

    def test_extension_registers_core_resolver(self):
        assert _api_key_hooks["resolve_principal"] is not None

    def test_core_resolver_maps_issued_key_and_ignores_jwts(
        self, model_registry, admin_a
    ):
        issued = self._issue(model_registry, admin_a.id)
        resolve = UserManager._resolve_issued_api_key
        assert resolve(model_registry, (None, issued.key)) == admin_a.id
        assert resolve(model_registry, (issued.key, "unused")) == admin_a.id
        assert resolve(model_registry, (None, admin_a.jwt)) is None
        assert resolve(model_registry, (None, "unknown-key")) is None

    def test_issued_key_authenticates_as_its_user(
        self, server, model_registry, admin_a
    ):
        issued = self._issue(model_registry, admin_a.id)
        response = server.get("/v1/user", headers=_bearer(issued.key))
        assert response.status_code == 200, response.text
        assert response.json()["user"]["id"] == admin_a.id

    def test_issued_key_authenticates_via_x_api_key_header(
        self, server, model_registry, admin_a
    ):
        issued = self._issue(model_registry, admin_a.id)
        response = server.get("/v1/user", headers={"X-API-Key": issued.key})
        assert response.status_code == 200, response.text
        assert response.json()["user"]["id"] == admin_a.id

    def test_revoked_key_does_not_authenticate(self, server, model_registry, admin_a):
        issued = self._issue(model_registry, admin_a.id)
        self._manager(model_registry, admin_a.id).revoke_key(issued.id)
        response = server.get("/v1/user", headers=_bearer(issued.key))
        assert response.status_code == 401, response.text

    def test_expired_key_does_not_authenticate(self, server, model_registry, admin_a):
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        issued = self._issue(model_registry, admin_a.id, expires_at=past)
        response = server.get("/v1/user", headers=_bearer(issued.key))
        assert response.status_code == 401, response.text

    def test_team_only_key_does_not_authenticate_a_user(
        self, server, model_registry, admin_a, team_a
    ):
        issued = self._issue(model_registry, admin_a.id, team_id=team_a.id)
        record = self._manager(model_registry, env("ROOT_ID")).validate_key(issued.key)
        assert record is not None
        assert record.user_id is None
        assert record.team_id == team_a.id

        response = server.get("/v1/user", headers=_bearer(issued.key))
        assert response.status_code == 401, response.text
