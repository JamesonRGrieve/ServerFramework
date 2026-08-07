"""Tests for the scim_consumer extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "scim_consumer_test")

from zephyrex.extensions.scim_consumer.BLL_SCIMConsumer import (
    SCIMConsumerConfigModel,
    SCIMConsumerManager,
    SCIMProvisioningLogModel,
)
from zephyrex.extensions.scim_consumer.EXT_SCIMConsumer import (
    EXT_SCIMConsumer,
)


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_SCIMConsumer.name == "scim_consumer"

    def test_version(self):
        assert EXT_SCIMConsumer.version == "1.0.0"

    def test_abilities(self):
        abilities = EXT_SCIMConsumer.get_abilities()
        assert "scim_consumer_users" in abilities
        assert "scim_consumer_groups" in abilities
        assert "scim_consumer_schemas" in abilities

    def test_dependencies(self):
        assert EXT_SCIMConsumer.extension_dependencies == ["auth_session"]


class TestModels:
    def test_provisioning_log_fields(self):
        fields = set(SCIMProvisioningLogModel.model_fields.keys())
        assert "scim_id" in fields
        assert "resource_type" in fields
        assert "operation" in fields

    def test_consumer_config_fields(self):
        fields = set(SCIMConsumerConfigModel.model_fields.keys())
        assert "bearer_token_hash" in fields
        assert "auto_create_users" in fields

    def test_manager_model(self):
        assert SCIMConsumerManager._model is SCIMConsumerConfigModel


class TestLifecycle:
    def test_on_initialize(self):
        assert EXT_SCIMConsumer.on_initialize() is True

    def test_on_start(self):
        assert EXT_SCIMConsumer.on_start() is True

    def test_on_stop(self):
        assert EXT_SCIMConsumer.on_stop() is True
