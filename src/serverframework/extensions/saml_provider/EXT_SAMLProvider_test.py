"""Tests for the saml_provider extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "saml_provider_test")

from serverframework.extensions.saml_provider.BLL_SAMLProvider import (
    SAMLProviderConfigModel,
    SAMLProviderManager,
    SAMLServiceProviderModel,
)
from serverframework.extensions.saml_provider.EXT_SAMLProvider import (
    EXT_SAMLProvider,
)


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_SAMLProvider.name == "saml_provider"

    def test_version(self):
        assert EXT_SAMLProvider.version == "1.0.0"

    def test_abilities(self):
        abilities = EXT_SAMLProvider.get_abilities()
        assert "saml_provider_sso" in abilities
        assert "saml_provider_metadata" in abilities
        assert "saml_provider_manage_sp" in abilities

    def test_dependencies(self):
        assert EXT_SAMLProvider.extension_dependencies == ["auth_session"]


class TestModels:
    def test_sp_fields(self):
        fields = set(SAMLServiceProviderModel.model_fields.keys())
        assert "entity_id" in fields
        assert "acs_url" in fields
        assert "name_id_format" in fields

    def test_provider_config_fields(self):
        fields = set(SAMLProviderConfigModel.model_fields.keys())
        assert "entity_id" in fields
        assert "cert_path" in fields
        assert "sign_assertions" in fields

    def test_manager_model(self):
        assert SAMLProviderManager._model is SAMLProviderConfigModel


class TestLifecycle:
    def test_on_initialize(self):
        assert EXT_SAMLProvider.on_initialize() is True

    def test_on_start(self):
        assert EXT_SAMLProvider.on_start() is True

    def test_on_stop(self):
        assert EXT_SAMLProvider.on_stop() is True
