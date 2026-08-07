"""Tests for the ldap_consumer extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "ldap_consumer_test")

from zephyrex.extensions.ldap_consumer.BLL_LDAPConsumer import (
    LDAPConsumerManager,
    LDAPServerConfigModel,
    UserLDAPLinkModel,
)
from zephyrex.extensions.ldap_consumer.EXT_LDAPConsumer import (
    EXT_LDAPConsumer,
)


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_LDAPConsumer.name == "ldap_consumer"

    def test_version(self):
        assert EXT_LDAPConsumer.version == "1.0.0"

    def test_abilities(self):
        abilities = EXT_LDAPConsumer.get_abilities()
        assert "ldap_consumer_authenticate" in abilities
        assert "ldap_consumer_search" in abilities

    def test_dependencies(self):
        assert EXT_LDAPConsumer.extension_dependencies == ["auth_session"]


class TestModels:
    def test_server_config_model_fields(self):
        fields = set(LDAPServerConfigModel.model_fields.keys())
        assert "host" in fields
        assert "port" in fields
        assert "base_dn" in fields
        assert "use_ssl" in fields

    def test_user_link_model_fields(self):
        fields = set(UserLDAPLinkModel.model_fields.keys())
        assert "user_id" in fields
        assert "ldap_dn" in fields
        assert "ldap_server_id" in fields

    def test_manager_model(self):
        assert LDAPConsumerManager._model is LDAPServerConfigModel


class TestLifecycle:
    def test_on_initialize(self):
        assert EXT_LDAPConsumer.on_initialize() is True

    def test_on_start(self):
        assert EXT_LDAPConsumer.on_start() is True

    def test_on_stop(self):
        assert EXT_LDAPConsumer.on_stop() is True
