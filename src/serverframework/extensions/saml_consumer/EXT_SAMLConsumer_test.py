"""Tests for the saml_consumer extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "saml_consumer_test")

from serverframework.extensions.saml_consumer.BLL_SAMLConsumer import (
    SAMLConsumerManager,
    SAMLIdPConfigModel,
    UserSAMLLinkModel,
)
from serverframework.extensions.saml_consumer.EXT_SAMLConsumer import (
    EXT_SAMLConsumer,
)


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_SAMLConsumer.name == "saml_consumer"

    def test_version(self):
        assert EXT_SAMLConsumer.version == "1.0.0"

    def test_abilities(self):
        abilities = EXT_SAMLConsumer.get_abilities()
        assert "saml_consumer_login" in abilities
        assert "saml_consumer_acs" in abilities
        assert "saml_consumer_metadata" in abilities

    def test_dependencies(self):
        assert EXT_SAMLConsumer.extension_dependencies == ["auth_session"]


class TestModels:
    def test_idp_config_fields(self):
        fields = set(SAMLIdPConfigModel.model_fields.keys())
        assert "entity_id" in fields
        assert "sso_url" in fields
        assert "want_assertions_signed" in fields

    def test_user_link_fields(self):
        fields = set(UserSAMLLinkModel.model_fields.keys())
        assert "user_id" in fields
        assert "name_id" in fields
        assert "session_index" in fields

    def test_manager_model(self):
        assert SAMLConsumerManager._model is SAMLIdPConfigModel


class TestLifecycle:
    def test_on_initialize(self):
        assert EXT_SAMLConsumer.on_initialize() is True

    def test_on_start(self):
        assert EXT_SAMLConsumer.on_start() is True

    def test_on_stop(self):
        assert EXT_SAMLConsumer.on_stop() is True
