"""Tests for the radius_provider extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "radius_provider_test")

from serverframework.extensions.radius_provider.BLL_RADIUSProvider import (
    RADIUSNASClientModel,
    RADIUSProviderConfigModel,
    RADIUSProviderManager,
)
from serverframework.extensions.radius_provider.EXT_RADIUSProvider import (
    EXT_RADIUSProvider,
)


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_RADIUSProvider.name == "radius_provider"

    def test_version(self):
        assert EXT_RADIUSProvider.version == "1.0.0"

    def test_abilities(self):
        abilities = EXT_RADIUSProvider.get_abilities()
        assert "radius_provider_authenticate" in abilities
        assert "radius_provider_accounting" in abilities
        assert "radius_provider_manage_clients" in abilities

    def test_dependencies(self):
        assert EXT_RADIUSProvider.extension_dependencies == ["auth_session"]


class TestModels:
    def test_nas_client_fields(self):
        fields = set(RADIUSNASClientModel.model_fields.keys())
        assert "name" in fields
        assert "ip_address" in fields
        assert "shared_secret" in fields

    def test_provider_config_fields(self):
        fields = set(RADIUSProviderConfigModel.model_fields.keys())
        assert "auth_port" in fields
        assert "acct_port" in fields

    def test_manager_model(self):
        assert RADIUSProviderManager._model is RADIUSProviderConfigModel


class TestLifecycle:
    def test_on_initialize(self):
        assert EXT_RADIUSProvider.on_initialize() is True

    def test_on_start(self):
        assert EXT_RADIUSProvider.on_start() is True

    def test_on_stop(self):
        assert EXT_RADIUSProvider.on_stop() is True
