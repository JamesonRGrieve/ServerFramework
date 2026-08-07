"""Tests for the webauthn_consumer extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "webauthn_consumer_test")

from serverframework.extensions.webauthn_consumer.BLL_WebAuthnConsumer import (
    WebAuthnConsumerManager,
    WebAuthnCredentialModel,
)
from serverframework.extensions.webauthn_consumer.EXT_WebAuthnConsumer import (
    EXT_WebAuthnConsumer,
)


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_WebAuthnConsumer.name == "webauthn_consumer"

    def test_version(self):
        assert EXT_WebAuthnConsumer.version == "1.0.0"

    def test_abilities(self):
        abilities = EXT_WebAuthnConsumer.get_abilities()
        assert "webauthn_consumer_register" in abilities
        assert "webauthn_consumer_authenticate" in abilities

    def test_dependencies(self):
        assert EXT_WebAuthnConsumer.extension_dependencies == ["auth_session"]


class TestModels:
    def test_credential_fields(self):
        fields = set(WebAuthnCredentialModel.model_fields.keys())
        assert "credential_id" in fields
        assert "public_key" in fields
        assert "sign_count" in fields
        assert "is_discoverable" in fields
        assert "transports" in fields

    def test_manager_model(self):
        assert WebAuthnConsumerManager._model is WebAuthnCredentialModel


class TestLifecycle:
    def test_on_initialize(self):
        assert EXT_WebAuthnConsumer.on_initialize() is True

    def test_on_start(self):
        assert EXT_WebAuthnConsumer.on_start() is True

    def test_on_stop(self):
        assert EXT_WebAuthnConsumer.on_stop() is True
