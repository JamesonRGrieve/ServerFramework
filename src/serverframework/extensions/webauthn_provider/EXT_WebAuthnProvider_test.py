"""Tests for the webauthn_provider extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "webauthn_provider_test")

from serverframework.extensions.webauthn_provider.BLL_WebAuthnProvider import (
    WebAuthnProviderCredentialModel,
    WebAuthnProviderManager,
    WebAuthnRelyingPartyModel,
)
from serverframework.extensions.webauthn_provider.EXT_WebAuthnProvider import (
    EXT_WebAuthnProvider,
)


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_WebAuthnProvider.name == "webauthn_provider"

    def test_version(self):
        assert EXT_WebAuthnProvider.version == "1.0.0"

    def test_abilities(self):
        abilities = EXT_WebAuthnProvider.get_abilities()
        assert "webauthn_provider_register" in abilities
        assert "webauthn_provider_authenticate" in abilities
        assert "webauthn_provider_manage_rp" in abilities

    def test_dependencies(self):
        assert EXT_WebAuthnProvider.extension_dependencies == ["auth_session"]


class TestModels:
    def test_rp_fields(self):
        fields = set(WebAuthnRelyingPartyModel.model_fields.keys())
        assert "rp_id" in fields
        assert "rp_name" in fields
        assert "origin" in fields
        assert "attestation" in fields
        assert "user_verification" in fields
        assert "timeout_ms" in fields

    def test_credential_fields(self):
        fields = set(WebAuthnProviderCredentialModel.model_fields.keys())
        assert "rp_id" in fields
        assert "external_user_id" in fields
        assert "credential_id" in fields
        assert "public_key" in fields
        assert "sign_count" in fields
        assert "is_discoverable" in fields
        assert "transports" in fields

    def test_manager_model(self):
        assert WebAuthnProviderManager._model is WebAuthnRelyingPartyModel


class TestLifecycle:
    def test_on_initialize(self):
        assert EXT_WebAuthnProvider.on_initialize() is True

    def test_on_start(self):
        assert EXT_WebAuthnProvider.on_start() is True

    def test_on_stop(self):
        assert EXT_WebAuthnProvider.on_stop() is True
