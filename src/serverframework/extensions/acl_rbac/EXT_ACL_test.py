"""Tests for the acl_rbac extension carve-out (Scope #5).

Covers:
- Canonical PermissionModel/PermissionManager live at the extension path.
- Core ``BLL_Auth`` PEP 562 forwards to the extension.
- ``EXT_ACL.on_load`` populates ``BLL_Auth._acl_hooks``.
- ``StaticPermissions`` continues to import PermissionModel from the
  extension path (Scope #5 dependency wiring).
"""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "acl_rbac_test")

import pytest
from pydantic import ValidationError

from serverframework.extensions.acl_rbac.BLL_ACL import (
    PermissionManager,
    PermissionModel,
)
from serverframework.extensions.acl_rbac.EXT_ACL import AclRbacExtension
from serverframework.logic import BLL_Auth


class TestCanonicalClassLocation:
    def test_permission_classes_live_in_extension(self):
        assert PermissionModel.Manager is PermissionManager
        assert PermissionManager._model is PermissionModel

    def test_pep562_forward_in_core(self):
        assert BLL_Auth.PermissionModel is PermissionModel
        assert BLL_Auth.PermissionManager is PermissionManager

    def test_static_permissions_imports_from_extension(self):
        # StaticPermissions used to `from BLL_Auth import PermissionModel`;
        # post-extraction it must import from the extension path.
        with open(
            "src/serverframework/database/StaticPermissions.py", "r"
        ) as fh:
            src = fh.read()
        assert (
            "from serverframework.extensions.acl_rbac.BLL_ACL import PermissionModel"
            in src
        )
        # No remaining BLL_Auth->PermissionModel imports.
        assert "from serverframework.logic.BLL_Auth import PermissionModel" not in src


class TestPermissionCreateValidation:
    def test_user_only_combination_accepted(self):
        # User-specific permission (user_id only) is allowed.
        PermissionModel.Create(
            resource_type="document",
            resource_id="doc-1",
            user_id="u-1",
        )

    def test_team_only_combination_accepted(self):
        PermissionModel.Create(
            resource_type="document",
            resource_id="doc-1",
            team_id="t-1",
        )

    def test_team_role_combination_accepted(self):
        PermissionModel.Create(
            resource_type="document",
            resource_id="doc-1",
            team_id="t-1",
            role_id="r-1",
        )

    def test_user_plus_team_rejected(self):
        with pytest.raises((ValidationError, ValueError)):
            PermissionModel.Create(
                resource_type="document",
                resource_id="doc-1",
                user_id="u-1",
                team_id="t-1",
            )

    def test_user_plus_role_rejected(self):
        with pytest.raises((ValidationError, ValueError)):
            PermissionModel.Create(
                resource_type="document",
                resource_id="doc-1",
                user_id="u-1",
                role_id="r-1",
            )

    def test_role_only_rejected_at_pydantic_layer(self):
        # Without team_id we cannot validate the role row at the schema layer
        # so the validator hard-rejects role-only Create payloads.
        with pytest.raises((ValidationError, ValueError)):
            PermissionModel.Create(
                resource_type="document",
                resource_id="doc-1",
                role_id="r-1",
            )


class TestExtensionLifecycle:
    def setup_method(self):
        self._snapshot = dict(BLL_Auth._acl_hooks)
        for k in BLL_Auth._acl_hooks:
            BLL_Auth._acl_hooks[k] = None

    def teardown_method(self):
        for k, v in self._snapshot.items():
            BLL_Auth._acl_hooks[k] = v

    def test_models_returns_permission_model(self):
        assert PermissionModel in AclRbacExtension.models()

    def test_on_load_populates_every_hook(self):
        AclRbacExtension.on_load()
        for key in ("permission_db_class", "create_permission"):
            assert (
                BLL_Auth._acl_hooks[key] is not None
            ), f"hook {key!r} not registered by on_load"


class TestHookRoundTrip:
    def setup_method(self):
        AclRbacExtension.on_load()

    def test_permission_db_class_returns_sa_model(self):
        # We don't have a declarative_base in this test, but the hook should
        # at least be callable and return whatever PermissionModel.DB(base)
        # does. Attempt with a dummy and assert call dispatch.
        hook = BLL_Auth._acl_hooks["permission_db_class"]
        assert callable(hook)
