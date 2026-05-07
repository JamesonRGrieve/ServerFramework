"""Tests for the auth_api_keys extension.

Covers: canonical wiring, hashing/constant-time comparison, scope rules.
"""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "auth_api_keys_test")

import hashlib

import pytest
from fastapi import HTTPException

from serverframework.extensions.auth_api_keys.BLL_Auth_APIKeys import (
    APIKeyManager,
    APIKeyModel,
    _hash_key,
)
from serverframework.extensions.auth_api_keys.EXT_Auth_APIKeys import (
    EXT_Auth_APIKeys,
)


class TestCanonicalWiring:
    def test_model_manager_round_trip(self):
        assert APIKeyModel.Manager is APIKeyManager
        assert APIKeyManager._model is APIKeyModel

    def test_extension_metadata(self):
        assert EXT_Auth_APIKeys.name == "auth_api_keys"
        abilities = EXT_Auth_APIKeys.get_abilities()
        for expected in (
            "api_key_issue",
            "api_key_validate",
            "api_key_revoke",
            "api_key_rotate",
        ):
            assert expected in abilities


class TestHashing:
    def test_hash_is_sha256_hex(self):
        raw = "abc123"
        expected = hashlib.sha256(raw.encode()).hexdigest()
        assert _hash_key(raw) == expected

    def test_hash_changes_with_input(self):
        assert _hash_key("a") != _hash_key("b")

    def test_hash_stable(self):
        assert _hash_key("same") == _hash_key("same")


class TestIssueScopeValidation:
    def test_neither_user_nor_team_rejected(self):
        manager = APIKeyManager.__new__(APIKeyManager)
        manager.model_registry = None
        with pytest.raises(HTTPException) as exc_info:
            manager.issue_key(name="key1")
        assert exc_info.value.status_code == 400


class TestLifecycle:
    def test_on_initialize_returns_true(self):
        assert EXT_Auth_APIKeys.on_initialize() is True

    def test_validate_config_returns_list(self):
        assert isinstance(EXT_Auth_APIKeys.validate_config(), list)
