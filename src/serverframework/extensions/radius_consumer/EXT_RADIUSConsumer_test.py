"""Tests for the radius_consumer extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "radius_consumer_test")

from serverframework.extensions.radius_consumer.BLL_RADIUSConsumer import (
    RADIUSConsumerManager,
    RADIUSServerConfigModel,
    UserRADIUSLinkModel,
)
from serverframework.extensions.radius_consumer.EXT_RADIUSConsumer import (
    EXT_RADIUSConsumer,
)


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_RADIUSConsumer.name == "radius_consumer"

    def test_version(self):
        assert EXT_RADIUSConsumer.version == "1.0.0"

    def test_abilities(self):
        abilities = EXT_RADIUSConsumer.get_abilities()
        assert "radius_consumer_authenticate" in abilities
        assert "radius_consumer_accounting" in abilities

    def test_dependencies(self):
        assert EXT_RADIUSConsumer.extension_dependencies == ["auth_session"]


class TestModels:
    def test_server_config_fields(self):
        fields = set(RADIUSServerConfigModel.model_fields.keys())
        assert "host" in fields
        assert "auth_port" in fields
        assert "shared_secret" in fields
        assert "auth_method" in fields

    def test_user_link_fields(self):
        fields = set(UserRADIUSLinkModel.model_fields.keys())
        assert "user_id" in fields
        assert "radius_username" in fields

    def test_manager_model(self):
        assert RADIUSConsumerManager._model is RADIUSServerConfigModel


class TestLifecycle:
    def test_on_initialize(self):
        assert EXT_RADIUSConsumer.on_initialize() is True

    def test_on_start(self):
        assert EXT_RADIUSConsumer.on_start() is True

    def test_on_stop(self):
        assert EXT_RADIUSConsumer.on_stop() is True
