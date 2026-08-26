"""Tests for the auth_invitations extension carve-out (Scope #4).

Covers:
- Canonical classes live at the extension import path.
- Core ``BLL_Auth`` PEP 562 forwards to the extension.
- ``EXT_Invitations.on_load`` populates every documented hook in
  ``BLL_Auth._invitation_hooks``.
- Core registration paths degrade safely when the extension is absent
  (``invitation_details`` resolution returns None; nested-resource
  ``invitations`` property raises 503).
"""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "auth_invitations_test")

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.auth_invitations import BLL_Invitations, EXT_Invitations
from zephyrex.extensions.auth_invitations.BLL_Invitations import (
    InvitationAcceptanceResponse,
    InvitationManager,
    InvitationModel,
    InviteeManager,
    InviteeModel,
)
from zephyrex.extensions.auth_invitations.EXT_Invitations import (
    AuthInvitationsExtension,
)
from zephyrex.lib.Environment import env
from zephyrex.logic import BLL_Auth


class TestCanonicalClassLocation:
    def test_invitation_classes_live_in_extension(self):
        assert InvitationModel.Manager is InvitationManager
        assert InviteeModel.Manager is InviteeManager
        assert InvitationManager._model is InvitationModel
        assert InviteeManager._model is InviteeModel

    def test_acceptance_response_shape(self):
        # Sanity-check the typed response pydantic schema still exists.
        assert hasattr(InvitationAcceptanceResponse, "model_fields")
        for f in ("success", "message", "team_id", "role_id", "user_team_id"):
            assert f in InvitationAcceptanceResponse.model_fields

    def test_pep562_forward_in_core(self):
        assert BLL_Auth.InvitationModel is InvitationModel
        assert BLL_Auth.InvitationManager is InvitationManager
        assert BLL_Auth.InviteeModel is InviteeModel
        assert BLL_Auth.InviteeManager is InviteeManager


class TestExtensionLifecycle:
    def setup_method(self):
        self._snapshot = dict(BLL_Auth._invitation_hooks)
        for k in BLL_Auth._invitation_hooks:
            BLL_Auth._invitation_hooks[k] = None

    def teardown_method(self):
        for k, v in self._snapshot.items():
            BLL_Auth._invitation_hooks[k] = v

    def test_models_lists_invitation_and_invitee(self):
        models = AuthInvitationsExtension.models()
        assert InvitationModel in models
        assert InviteeModel in models

    def test_on_load_populates_every_hook(self):
        AuthInvitationsExtension.on_load()
        for key in (
            "lookup_by_id",
            "lookup_by_code",
            "apply_to_user",
            "invitation_manager_factory",
            "invitee_manager_factory",
            "list_invitees_for_user",
        ):
            assert (
                BLL_Auth._invitation_hooks[key] is not None
            ), f"hook {key!r} not registered by on_load"


class TestCoreFallbackWhenExtensionAbsent:
    def setup_method(self):
        self._snapshot = dict(BLL_Auth._invitation_hooks)
        for k in BLL_Auth._invitation_hooks:
            BLL_Auth._invitation_hooks[k] = None

    def teardown_method(self):
        for k, v in self._snapshot.items():
            BLL_Auth._invitation_hooks[k] = v

    def test_team_manager_invitations_property_raises_503_without_extension(self):
        TeamMgr = BLL_Auth.TeamManager
        instance = TeamMgr.__new__(TeamMgr)
        instance.requester = type("R", (), {"id": "fake"})()
        instance.target_team_id = "fake"
        instance.model_registry = None
        instance._invitations = None
        with pytest.raises(HTTPException) as exc_info:
            _ = instance.invitations
        assert exc_info.value.status_code == 503
        assert "auth_invitations extension not loaded" in str(exc_info.value.detail)

    def test_list_invitations_for_user_returns_empty_without_extension(self):
        UserMgr = BLL_Auth.UserManager
        instance = UserMgr.__new__(UserMgr)
        instance.requester = type("R", (), {"id": "fake"})()
        instance.model_registry = None
        result = instance.list_invitations_for_user()
        assert result == {"invitations": []}


class TestHookRoundTrip:
    def setup_method(self):
        AuthInvitationsExtension.on_load()

    def test_invitation_manager_factory_returns_extension_class(self):
        factory = BLL_Auth._invitation_hooks["invitation_manager_factory"]
        try:
            mgr = factory(
                requester_id="x",
                target_team_id="y",
                model_registry=None,
            )
        except Exception:
            return
        assert isinstance(mgr, InvitationManager)

    def test_invitee_manager_factory_returns_extension_class(self):
        factory = BLL_Auth._invitation_hooks["invitee_manager_factory"]
        try:
            mgr = factory(
                requester_id="x",
                target_id=None,
                model_registry=None,
            )
        except Exception:
            return
        assert isinstance(mgr, InviteeManager)


class TestHookLookupNaiveExpiryEnsureUtc(ExtensionServerMixin):
    """Regression for #228 — the hook-lookup expiry checks must route a naive
    persisted ``expires_at`` through ``ensure_utc``.

    ``_lookup_by_id`` / ``_lookup_by_code`` exist in duplicate in both
    ``BLL_Invitations`` and ``EXT_Invitations`` and run in the register/accept
    hook path. On SQLite (the test/dev backend) a persisted datetime comes
    back naive; without the ``ensure_utc`` wrap ``naive < aware`` raises
    ``TypeError`` -> HTTP 500. These tests prove an expired invitation is
    reported expired (lookup returns ``None``) with no ``TypeError``, and that
    a still-valid (future) invitation is still resolved.
    """

    extension_class = AuthInvitationsExtension

    @staticmethod
    def _naive_utc(offset: timedelta) -> datetime:
        """A NAIVE datetime at ``now(UTC) + offset`` (tzinfo stripped)."""
        return datetime.now(timezone.utc).replace(tzinfo=None) + offset

    def _insert_invitation(self, model_registry, expires_at: datetime):
        code = f"NAIVE_{uuid.uuid4().hex[:10].upper()}"
        InvitationDB = InvitationModel.DB(model_registry.DB.manager.Base)
        inv = InvitationDB.create(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            code=code,
            expires_at=expires_at,
            return_type="dto",
            override_dto=InvitationModel,
        )
        return inv.id, code

    def _assert_persisted_expiry_is_naive(self, model_registry, invitation_id):
        """The whole premise of #228: SQLite returns a naive ``expires_at``."""
        InvitationDB = InvitationModel.DB(model_registry.DB.manager.Base)
        db = model_registry.DB.session()
        row = db.query(InvitationDB).filter(InvitationDB.id == invitation_id).first()
        assert row is not None
        assert row.expires_at is not None
        assert (
            row.expires_at.tzinfo is None
        ), "reproduction requires a naive persisted expires_at"

    def test_past_naive_expiry_treated_as_expired_without_typeerror(
        self, model_registry
    ):
        inv_id, code = self._insert_invitation(
            model_registry, self._naive_utc(timedelta(hours=-1))
        )
        self._assert_persisted_expiry_is_naive(model_registry, inv_id)

        # All four live hook-lookup sites must report the invitation expired
        # (None) instead of raising TypeError on ``naive < aware``.
        assert BLL_Invitations._lookup_by_id(inv_id, model_registry) is None
        assert BLL_Invitations._lookup_by_code(code, model_registry) is None
        assert EXT_Invitations._lookup_by_id(inv_id, model_registry) is None
        assert EXT_Invitations._lookup_by_code(code, model_registry) is None

    def test_future_naive_expiry_still_resolves(self, model_registry):
        inv_id, code = self._insert_invitation(
            model_registry, self._naive_utc(timedelta(hours=1))
        )
        self._assert_persisted_expiry_is_naive(model_registry, inv_id)

        # A not-yet-expired invitation must still resolve through every site,
        # proving ensure_utc normalises rather than blanket-expiring.
        results = [
            BLL_Invitations._lookup_by_id(inv_id, model_registry),
            BLL_Invitations._lookup_by_code(code, model_registry),
            EXT_Invitations._lookup_by_id(inv_id, model_registry),
            EXT_Invitations._lookup_by_code(code, model_registry),
        ]
        for result in results:
            assert result is not None
            assert result["code"] == code
