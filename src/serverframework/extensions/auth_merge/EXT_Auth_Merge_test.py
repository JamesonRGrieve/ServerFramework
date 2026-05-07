"""Tests for the auth_merge extension.

Covers:
- Canonical model/manager wiring (``UserMergeModel.Manager`` is set).
- Extension lifecycle: ``on_initialize`` returns True.
- ``UserMergeManager._validate`` rejects self-merge with 400.
"""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "auth_merge_test")

import pytest
from fastapi import HTTPException

from serverframework.extensions.auth_merge.BLL_Auth_Merge import (
    UserMergeManager,
    UserMergeModel,
)
from serverframework.extensions.auth_merge.EXT_Auth_Merge import EXT_Auth_Merge


class TestCanonicalWiring:
    def test_model_manager_round_trip(self):
        assert UserMergeModel.Manager is UserMergeManager
        assert UserMergeManager._model is UserMergeModel

    def test_extension_metadata(self):
        assert EXT_Auth_Merge.name == "auth_merge"
        assert "auth_session" in EXT_Auth_Merge.extension_dependencies
        assert "user_merge" in EXT_Auth_Merge.get_abilities()


class TestLifecycle:
    def test_on_initialize_returns_true(self):
        assert EXT_Auth_Merge.on_initialize() is True

    def test_validate_config_returns_list(self):
        issues = EXT_Auth_Merge.validate_config()
        assert isinstance(issues, list)


class TestValidation:
    def test_self_merge_rejected(self):
        manager = UserMergeManager.__new__(UserMergeManager)
        manager.model_registry = None
        with pytest.raises(HTTPException) as exc_info:
            manager._validate("user-1", "user-1")
        assert exc_info.value.status_code == 400
        assert "themselves" in exc_info.value.detail
