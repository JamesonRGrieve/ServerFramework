"""Tests for the forward_auth_provider extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "forward_auth_provider_test")

from zephyrex.extensions.forward_auth_provider.BLL_ForwardAuthProvider import (
    ForwardAuthProviderManager,
    ForwardAuthRuleModel,
)
from zephyrex.extensions.forward_auth_provider.EXT_ForwardAuthProvider import (
    EXT_ForwardAuthProvider,
)


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_ForwardAuthProvider.name == "forward_auth_provider"

    def test_version(self):
        assert EXT_ForwardAuthProvider.version == "1.0.0"

    def test_abilities(self):
        abilities = EXT_ForwardAuthProvider.get_abilities()
        assert "forward_auth_provider_verify" in abilities
        assert "forward_auth_provider_manage_rules" in abilities

    def test_dependencies(self):
        assert EXT_ForwardAuthProvider.extension_dependencies == ["auth_session"]


class TestModels:
    def test_rule_fields(self):
        fields = set(ForwardAuthRuleModel.model_fields.keys())
        assert "path_pattern" in fields
        assert "required_roles" in fields
        assert "deny_action" in fields
        assert "priority" in fields

    def test_manager_model(self):
        assert ForwardAuthProviderManager._model is ForwardAuthRuleModel


class TestLifecycle:
    def test_on_initialize(self):
        assert EXT_ForwardAuthProvider.on_initialize() is True

    def test_on_start(self):
        assert EXT_ForwardAuthProvider.on_start() is True

    def test_on_stop(self):
        assert EXT_ForwardAuthProvider.on_stop() is True
