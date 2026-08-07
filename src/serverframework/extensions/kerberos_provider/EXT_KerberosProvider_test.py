"""Tests for the kerberos_provider extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "kerberos_provider_test")

from serverframework.extensions.kerberos_provider.BLL_KerberosProvider import (
    KerberosPrincipalModel,
    KerberosProviderConfigModel,
    KerberosProviderManager,
)
from serverframework.extensions.kerberos_provider.EXT_KerberosProvider import (
    EXT_KerberosProvider,
)


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_KerberosProvider.name == "kerberos_provider"

    def test_version(self):
        assert EXT_KerberosProvider.version == "1.0.0"

    def test_abilities(self):
        abilities = EXT_KerberosProvider.get_abilities()
        assert "kerberos_provider_issue_ticket" in abilities
        assert "kerberos_provider_validate_ticket" in abilities
        assert "kerberos_provider_manage_principals" in abilities

    def test_dependencies(self):
        assert EXT_KerberosProvider.extension_dependencies == ["auth_session"]


class TestModels:
    def test_principal_fields(self):
        fields = set(KerberosPrincipalModel.model_fields.keys())
        assert "principal" in fields
        assert "principal_type" in fields
        assert "user_id" in fields

    def test_provider_config_fields(self):
        fields = set(KerberosProviderConfigModel.model_fields.keys())
        assert "realm" in fields
        assert "kdc_port" in fields
        assert "keytab_path" in fields

    def test_manager_model(self):
        assert KerberosProviderManager._model is KerberosProviderConfigModel


class TestLifecycle:
    def test_on_initialize(self):
        assert EXT_KerberosProvider.on_initialize() is True

    def test_on_start(self):
        assert EXT_KerberosProvider.on_start() is True

    def test_on_stop(self):
        assert EXT_KerberosProvider.on_stop() is True
