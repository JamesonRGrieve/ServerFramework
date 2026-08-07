"""Tests for the x509_provider extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "x509_provider_test")

from serverframework.extensions.x509_provider.BLL_X509Provider import (
    IssuedCertificateModel,
    X509ProviderConfigModel,
    X509ProviderManager,
)
from serverframework.extensions.x509_provider.EXT_X509Provider import (
    EXT_X509Provider,
)


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_X509Provider.name == "x509_provider"

    def test_version(self):
        assert EXT_X509Provider.version == "1.0.0"

    def test_abilities(self):
        abilities = EXT_X509Provider.get_abilities()
        assert "x509_provider_issue_cert" in abilities
        assert "x509_provider_revoke_cert" in abilities
        assert "x509_provider_crl" in abilities

    def test_dependencies(self):
        assert EXT_X509Provider.extension_dependencies == ["auth_session"]


class TestModels:
    def test_issued_cert_fields(self):
        fields = set(IssuedCertificateModel.model_fields.keys())
        assert "serial_number" in fields
        assert "fingerprint_sha256" in fields
        assert "is_revoked" in fields

    def test_provider_config_fields(self):
        fields = set(X509ProviderConfigModel.model_fields.keys())
        assert "ca_cert_path" in fields
        assert "ca_key_path" in fields
        assert "key_size" in fields

    def test_manager_model(self):
        assert X509ProviderManager._model is X509ProviderConfigModel


class TestLifecycle:
    def test_on_initialize(self):
        assert EXT_X509Provider.on_initialize() is True

    def test_on_start(self):
        assert EXT_X509Provider.on_start() is True

    def test_on_stop(self):
        assert EXT_X509Provider.on_stop() is True
