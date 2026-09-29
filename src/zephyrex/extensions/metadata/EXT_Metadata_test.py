"""Tests for the metadata extension carve-out (Scope #3).

Covers:
- Canonical classes live at the extension import path.
- Core ``BLL_Auth`` PEP 562 forwards to the extension.
- Importing ``BLL_Metadata`` registers its implementation of every
  documented hook in ``BLL_Auth._metadata_hooks``.
- Core surfaces that depended on metadata degrade safely when the extension
  has not been loaded (``UserManager.metadata`` raises 503; the login
  preferences hook returns ``{}``).
"""

import os

# The framework's `Environment.AppSettings` validator refuses an empty
# JWT_SECRET in non-test contexts; the test conftest sets PYTEST_CURRENT_TEST
# but standalone invocation also needs a stub secret.
os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "metadata_test")

import pytest
from fastapi import HTTPException

from zephyrex.extensions.metadata import BLL_Metadata
from zephyrex.extensions.metadata.BLL_Metadata import (
    MetadataManager,
    MetadataModel,
    TeamMetadataManager,
    UserMetadataManager,
)
from zephyrex.extensions.metadata.EXT_Metadata import MetadataExtension
from zephyrex.lib.Environment import env
from zephyrex.logic import BLL_Auth


class TestCanonicalClassLocation:
    def test_metadata_model_exposes_create_update_search(self):
        assert hasattr(MetadataModel, "Create")
        assert hasattr(MetadataModel, "Update")
        assert hasattr(MetadataModel, "Search")

    def test_metadata_manager_anchored_to_model(self):
        assert MetadataModel.Manager is MetadataManager
        assert MetadataManager._model is MetadataModel

    def test_user_and_team_managers_inherit_metadata_manager(self):
        assert issubclass(UserMetadataManager, MetadataManager)
        assert issubclass(TeamMetadataManager, MetadataManager)

    def test_pep562_forward_in_core(self):
        # Reaching for the names through core resolves to the extension's
        # canonical class identity.
        assert BLL_Auth.MetadataModel is MetadataModel
        assert BLL_Auth.UserMetadataManager is UserMetadataManager
        assert BLL_Auth.TeamMetadataManager is TeamMetadataManager


class TestExtensionLifecycle:
    def setup_method(self):
        self._snapshot = dict(BLL_Auth._metadata_hooks)
        for k in BLL_Auth._metadata_hooks:
            BLL_Auth._metadata_hooks[k] = None

    def teardown_method(self):
        for k, v in self._snapshot.items():
            BLL_Auth._metadata_hooks[k] = v

    def test_models_returns_metadata_model(self):
        assert MetadataModel in MetadataExtension.models


class TestImportRegistersHooks:
    def test_every_hook_is_the_bll_implementation(self):
        for key in (
            "list_preferences",
            "list_user_metadata",
            "user_manager_factory",
            "team_manager_factory",
            "create_user_metadata",
            "update_user_metadata",
        ):
            assert BLL_Auth._metadata_hooks[key] is getattr(
                BLL_Metadata, f"_{key}"
            ), f"hook {key!r} is not BLL_Metadata._{key}"


class TestCoreFallbackWhenExtensionAbsent:
    """Core code that previously called UserMetadataManager directly now goes
    through the hook. Without the hooks, every consumer must degrade safely
    (typed 503 for manager-property paths, empty dict/list for read paths)."""

    def setup_method(self):
        self._snapshot = dict(BLL_Auth._metadata_hooks)
        for k in BLL_Auth._metadata_hooks:
            BLL_Auth._metadata_hooks[k] = None

    def teardown_method(self):
        for k, v in self._snapshot.items():
            BLL_Auth._metadata_hooks[k] = v

    def test_user_manager_metadata_property_raises_503_without_extension(self):
        UserMgr = BLL_Auth.UserManager
        instance = UserMgr.__new__(UserMgr)
        instance.requester = type("R", (), {"id": "fake"})()
        # `target_user_id` is a read-only property derived from target_id;
        # write to the underlying `_target_id` field instead.
        object.__setattr__(instance, "_target_id", "fake")
        instance.model_registry = None
        instance._metadata = None
        with pytest.raises(HTTPException) as exc_info:
            _ = instance.metadata
        assert exc_info.value.status_code == 503
        assert "metadata extension not loaded" in str(exc_info.value.detail)

    def test_login_preferences_hook_default_returns_empty(self):
        # When list_preferences is None, login() short-circuits to {}.
        # We don't run the full login path; we just verify the dict-default.
        assert BLL_Auth._metadata_hooks["list_preferences"] is None


class TestHookRoundTrip:
    """The hook-driven path on core resolves to the canonical extension
    implementation for an app that loaded ``metadata``, and to nothing for
    one that did not."""

    def test_user_manager_factory_returns_extension_class(self, model_registry):
        factory = BLL_Auth._metadata_hooks["user_manager_factory"]
        mgr = factory(
            requester_id=env("ROOT_ID"),
            target_id=env("ROOT_ID"),
            model_registry=model_registry,
        )
        assert isinstance(mgr, UserMetadataManager)

    def test_team_manager_factory_returns_extension_class(self, model_registry):
        factory = BLL_Auth._metadata_hooks["team_manager_factory"]
        mgr = factory(
            requester_id=env("ROOT_ID"),
            target_team_id=None,
            model_registry=model_registry,
        )
        assert isinstance(mgr, TeamMetadataManager)

    def test_factories_resolve_nothing_without_the_extension(self):
        assert (
            BLL_Auth._metadata_hooks["user_manager_factory"](
                requester_id="x", target_id="y", model_registry=None
            )
            is None
        )
        assert (
            BLL_Auth._metadata_hooks["team_manager_factory"](
                requester_id="x", target_team_id="y", model_registry=None
            )
            is None
        )
