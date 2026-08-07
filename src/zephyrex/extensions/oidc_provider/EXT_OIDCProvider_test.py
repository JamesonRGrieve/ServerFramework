"""Tests for the oidc_provider extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "oidc_provider_test")

from zephyrex.extensions.oidc_provider.BLL_OIDCProvider import (
    OIDCProviderConfigModel,
    OIDCProviderManager,
    OIDCSigningKeyModel,
)
from zephyrex.extensions.oidc_provider.EXT_OIDCProvider import (
    EXT_OIDCProvider,
)


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_OIDCProvider.name == "oidc_provider"

    def test_version(self):
        assert EXT_OIDCProvider.version == "1.0.0"

    def test_abilities(self):
        abilities = EXT_OIDCProvider.get_abilities()
        assert "oidc_provider_discovery" in abilities
        assert "oidc_provider_jwks" in abilities
        assert "oidc_provider_id_token" in abilities
        assert "oidc_provider_userinfo" in abilities

    def test_dependencies(self):
        assert "auth_session" in EXT_OIDCProvider.extension_dependencies
        assert "oauth_provider" in EXT_OIDCProvider.extension_dependencies


class TestModels:
    def test_signing_key_fields(self):
        fields = set(OIDCSigningKeyModel.model_fields.keys())
        assert "kid" in fields
        assert "algorithm" in fields
        assert "public_key_pem" in fields

    def test_provider_config_fields(self):
        fields = set(OIDCProviderConfigModel.model_fields.keys())
        assert "issuer_url" in fields
        assert "id_token_ttl_minutes" in fields
        assert "supported_scopes" in fields

    def test_manager_model(self):
        assert OIDCProviderManager._model is OIDCProviderConfigModel


class TestLifecycle:
    def test_on_initialize(self):
        assert EXT_OIDCProvider.on_initialize() is True

    def test_on_start(self):
        assert EXT_OIDCProvider.on_start() is True

    def test_on_stop(self):
        assert EXT_OIDCProvider.on_stop() is True
