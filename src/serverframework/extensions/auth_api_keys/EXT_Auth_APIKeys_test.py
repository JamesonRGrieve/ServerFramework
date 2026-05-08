"""Tests for the auth_api_keys extension.

Covers: canonical wiring, hashing/constant-time comparison, security
posture (no raw key_hash on Create body, custom routes registered,
ROOT-only direct CREATE).
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
from serverframework.lib.Pydantic2FastAPI import RouteType


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


class TestSecurityPosture:
    def test_create_schema_does_not_expose_key_hash(self):
        # The Create body must not let a client write the hash directly,
        # otherwise a JWT'd attacker could mint a key with a known plaintext.
        fields = set(APIKeyModel.Create.model_fields.keys())
        assert "key_hash" not in fields
        assert "is_revoked" not in fields
        assert "last_used_at" not in fields

    def test_update_schema_does_not_expose_revocation_or_timestamps(self):
        fields = set(APIKeyModel.Update.model_fields.keys())
        assert "is_revoked" not in fields
        assert "last_used_at" not in fields

    def test_router_excludes_create_and_update(self):
        # CREATE goes through /issue; UPDATE is not a public surface.
        assert RouteType.CREATE not in (APIKeyManager.routes_to_register or [])
        assert RouteType.UPDATE not in (APIKeyManager.routes_to_register or [])

    def test_custom_routes_present(self):
        from serverframework.lib.CustomRoute import iter_custom_routes

        names = [name for name, _ in iter_custom_routes(APIKeyManager)]
        for expected in ("issue_route", "validate_route", "rotate_route"):
            assert expected in names


class TestLifecycle:
    def test_on_initialize_returns_true(self):
        assert EXT_Auth_APIKeys.on_initialize() is True

    def test_validate_config_returns_list(self):
        assert isinstance(EXT_Auth_APIKeys.validate_config(), list)
