"""Tests for the proxy_auth_provider extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "proxy_auth_provider_test")

from zephyrex.extensions.proxy_auth_provider.BLL_ProxyAuthProvider import (
    ProxyAuthProviderManager,
    ProxyAuthTargetModel,
)
from zephyrex.extensions.proxy_auth_provider.EXT_ProxyAuthProvider import (
    EXT_ProxyAuthProvider,
)


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_ProxyAuthProvider.name == "proxy_auth_provider"

    def test_version(self):
        assert EXT_ProxyAuthProvider.version == "1.0.0"

    def test_abilities(self):
        abilities = EXT_ProxyAuthProvider.get_abilities()
        assert "proxy_auth_provider_inject_headers" in abilities
        assert "proxy_auth_provider_manage_targets" in abilities

    def test_dependencies(self):
        assert EXT_ProxyAuthProvider.extension_dependencies == ["auth_session"]


class TestModels:
    def test_target_fields(self):
        fields = set(ProxyAuthTargetModel.model_fields.keys())
        assert "target_url" in fields
        assert "user_header" in fields
        assert "strip_incoming" in fields

    def test_manager_model(self):
        assert ProxyAuthProviderManager._model is ProxyAuthTargetModel


class TestLifecycle:
    def test_on_initialize(self):
        assert EXT_ProxyAuthProvider.on_initialize() is True

    def test_on_start(self):
        assert EXT_ProxyAuthProvider.on_start() is True

    def test_on_stop(self):
        assert EXT_ProxyAuthProvider.on_stop() is True
