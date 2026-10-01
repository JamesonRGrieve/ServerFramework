"""Tests for the ldap_provider extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "ldap_provider_test")

from zephyrex.extensions.ldap_provider.BLL_LDAPProvider import (
    LDAPDirectoryEntryModel,
    LDAPProviderConfigModel,
    LDAPProviderManager,
)
from zephyrex.extensions.ldap_provider.EXT_LDAPProvider import (
    EXT_LDAPProvider,
)


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_LDAPProvider.name == "ldap_provider"

    def test_version(self):
        assert EXT_LDAPProvider.version == "1.0.0"

    def test_abilities(self):
        abilities = EXT_LDAPProvider.get_abilities()
        assert "ldap_provider_bind" in abilities
        assert "ldap_provider_search" in abilities
        assert "ldap_provider_compare" in abilities

    def test_dependencies(self):
        assert EXT_LDAPProvider.extension_dependencies == ["auth_session"]


class TestModels:
    def test_directory_entry_fields(self):
        fields = set(LDAPDirectoryEntryModel.model_fields.keys())
        assert "dn" in fields
        assert "object_class" in fields
        assert "user_id" in fields

    def test_provider_config_fields(self):
        fields = set(LDAPProviderConfigModel.model_fields.keys())
        assert "listen_port" in fields
        assert "base_dn" in fields
        assert "tls_cert_path" in fields

    def test_manager_model(self):
        assert LDAPProviderManager._model is LDAPProviderConfigModel


class TestLifecycle:
    def test_on_initialize(self):
        assert EXT_LDAPProvider.on_initialize() is True

    def test_on_start(self):
        assert EXT_LDAPProvider.on_start() is True

    def test_on_stop(self):
        assert EXT_LDAPProvider.on_stop() is True
