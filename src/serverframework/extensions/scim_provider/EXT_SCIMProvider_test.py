"""Tests for the scim_provider extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "scim_provider_test")

from serverframework.extensions.scim_provider.BLL_SCIMProvider import (
    SCIMProviderManager,
    SCIMSyncLogModel,
    SCIMTargetModel,
)
from serverframework.extensions.scim_provider.EXT_SCIMProvider import (
    EXT_SCIMProvider,
)


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_SCIMProvider.name == "scim_provider"

    def test_version(self):
        assert EXT_SCIMProvider.version == "1.0.0"

    def test_abilities(self):
        abilities = EXT_SCIMProvider.get_abilities()
        assert "scim_provider_push_users" in abilities
        assert "scim_provider_push_groups" in abilities
        assert "scim_provider_manage_targets" in abilities

    def test_dependencies(self):
        assert EXT_SCIMProvider.extension_dependencies == ["auth_session"]


class TestModels:
    def test_target_fields(self):
        fields = set(SCIMTargetModel.model_fields.keys())
        assert "name" in fields
        assert "base_url" in fields
        assert "bearer_token" in fields

    def test_sync_log_fields(self):
        fields = set(SCIMSyncLogModel.model_fields.keys())
        assert "target_id" in fields
        assert "operation" in fields
        assert "status" in fields

    def test_manager_model(self):
        assert SCIMProviderManager._model is SCIMTargetModel


class TestLifecycle:
    def test_on_initialize(self):
        assert EXT_SCIMProvider.on_initialize() is True

    def test_on_start(self):
        assert EXT_SCIMProvider.on_start() is True

    def test_on_stop(self):
        assert EXT_SCIMProvider.on_stop() is True
