"""Tests for the kerberos_consumer extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "kerberos_consumer_test")

from serverframework.extensions.kerberos_consumer.BLL_KerberosConsumer import (
    KerberosConsumerManager,
    KerberosRealmConfigModel,
    UserKerberosLinkModel,
)
from serverframework.extensions.kerberos_consumer.EXT_KerberosConsumer import (
    EXT_KerberosConsumer,
)


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_KerberosConsumer.name == "kerberos_consumer"

    def test_version(self):
        assert EXT_KerberosConsumer.version == "1.0.0"

    def test_abilities(self):
        abilities = EXT_KerberosConsumer.get_abilities()
        assert "kerberos_consumer_authenticate" in abilities
        assert "kerberos_consumer_negotiate" in abilities

    def test_dependencies(self):
        assert EXT_KerberosConsumer.extension_dependencies == ["auth_session"]


class TestModels:
    def test_realm_config_fields(self):
        fields = set(KerberosRealmConfigModel.model_fields.keys())
        assert "realm" in fields
        assert "kdc_host" in fields
        assert "keytab_path" in fields

    def test_user_link_fields(self):
        fields = set(UserKerberosLinkModel.model_fields.keys())
        assert "user_id" in fields
        assert "principal" in fields

    def test_manager_model(self):
        assert KerberosConsumerManager._model is KerberosRealmConfigModel


class TestLifecycle:
    def test_on_initialize(self):
        assert EXT_KerberosConsumer.on_initialize() is True

    def test_on_start(self):
        assert EXT_KerberosConsumer.on_start() is True

    def test_on_stop(self):
        assert EXT_KerberosConsumer.on_stop() is True
