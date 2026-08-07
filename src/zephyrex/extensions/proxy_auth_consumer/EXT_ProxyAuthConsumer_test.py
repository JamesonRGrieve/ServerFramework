"""Tests for the proxy_auth_consumer extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "proxy_auth_consumer_test")

from zephyrex.extensions.proxy_auth_consumer.BLL_ProxyAuthConsumer import (
    ProxyAuthConsumerManager,
    ProxyAuthTrustedSourceModel,
)
from zephyrex.extensions.proxy_auth_consumer.EXT_ProxyAuthConsumer import (
    EXT_ProxyAuthConsumer,
)


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_ProxyAuthConsumer.name == "proxy_auth_consumer"

    def test_version(self):
        assert EXT_ProxyAuthConsumer.version == "1.0.0"

    def test_abilities(self):
        abilities = EXT_ProxyAuthConsumer.get_abilities()
        assert "proxy_auth_consumer_authenticate" in abilities

    def test_dependencies(self):
        assert EXT_ProxyAuthConsumer.extension_dependencies == ["auth_session"]


class TestModels:
    def test_trusted_source_fields(self):
        fields = set(ProxyAuthTrustedSourceModel.model_fields.keys())
        assert "ip_address" in fields
        assert "user_header" in fields
        assert "auto_create_users" in fields

    def test_manager_model(self):
        assert ProxyAuthConsumerManager._model is ProxyAuthTrustedSourceModel


class TestLifecycle:
    def test_on_initialize(self):
        assert EXT_ProxyAuthConsumer.on_initialize() is True

    def test_on_start(self):
        assert EXT_ProxyAuthConsumer.on_start() is True

    def test_on_stop(self):
        assert EXT_ProxyAuthConsumer.on_stop() is True
