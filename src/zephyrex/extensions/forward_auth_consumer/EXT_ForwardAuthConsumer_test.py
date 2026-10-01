"""Tests for the forward_auth_consumer extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "forward_auth_consumer_test")

from zephyrex.extensions.forward_auth_consumer.BLL_ForwardAuthConsumer import (
    ForwardAuthConsumerManager,
    ForwardAuthEndpointModel,
)
from zephyrex.extensions.forward_auth_consumer.EXT_ForwardAuthConsumer import (
    EXT_ForwardAuthConsumer,
)


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_ForwardAuthConsumer.name == "forward_auth_consumer"

    def test_version(self):
        assert EXT_ForwardAuthConsumer.version == "1.0.0"

    def test_abilities(self):
        abilities = EXT_ForwardAuthConsumer.get_abilities()
        assert "forward_auth_consumer_verify" in abilities

    def test_dependencies(self):
        assert EXT_ForwardAuthConsumer.extension_dependencies == ["auth_session"]


class TestModels:
    def test_endpoint_fields(self):
        fields = set(ForwardAuthEndpointModel.model_fields.keys())
        assert "url" in fields
        assert "user_header" in fields
        assert "pass_cookies" in fields
        assert "timeout_seconds" in fields

    def test_manager_model(self):
        assert ForwardAuthConsumerManager._model is ForwardAuthEndpointModel


class TestLifecycle:
    def test_on_initialize(self):
        assert EXT_ForwardAuthConsumer.on_initialize() is True

    def test_on_start(self):
        assert EXT_ForwardAuthConsumer.on_start() is True

    def test_on_stop(self):
        assert EXT_ForwardAuthConsumer.on_stop() is True
