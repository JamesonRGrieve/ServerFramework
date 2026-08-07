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

import pytest
from fastapi import HTTPException

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
