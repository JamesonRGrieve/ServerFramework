"""Tests for the oidc_consumer extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "oidc_consumer_test")

from serverframework.extensions.oidc_consumer.BLL_OIDCConsumer import (
    OIDCConsumerManager,
    OIDCProviderConfigModel,
    UserOIDCLinkModel,
)
from serverframework.extensions.oidc_consumer.EXT_OIDCConsumer import (
    EXT_OIDCConsumer,
)


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_OIDCConsumer.name == "oidc_consumer"

    def test_version(self):
        assert EXT_OIDCConsumer.version == "1.0.0"

    def test_abilities(self):
        abilities = EXT_OIDCConsumer.get_abilities()
        assert "oidc_consumer_authorize" in abilities
        assert "oidc_consumer_callback" in abilities
        assert "oidc_consumer_userinfo" in abilities

    def test_dependencies(self):
        assert EXT_OIDCConsumer.extension_dependencies == ["auth_session"]


class TestModels:
    def test_provider_config_fields(self):
        fields = set(OIDCProviderConfigModel.model_fields.keys())
        assert "issuer_url" in fields
        assert "client_id" in fields
        assert "scopes" in fields
        assert "use_pkce" in fields

    def test_user_link_fields(self):
        fields = set(UserOIDCLinkModel.model_fields.keys())
        assert "user_id" in fields
        assert "subject" in fields
        assert "provider_config_id" in fields

    def test_manager_model(self):
        assert OIDCConsumerManager._model is OIDCProviderConfigModel


class TestLifecycle:
    def test_on_initialize(self):
        assert EXT_OIDCConsumer.on_initialize() is True

    def test_on_start(self):
        assert EXT_OIDCConsumer.on_start() is True

    def test_on_stop(self):
        assert EXT_OIDCConsumer.on_stop() is True
