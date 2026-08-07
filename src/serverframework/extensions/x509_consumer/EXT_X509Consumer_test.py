"""Tests for the x509_consumer extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "x509_consumer_test")

from serverframework.extensions.x509_consumer.BLL_X509Consumer import (
    UserX509LinkModel,
    X509ConsumerManager,
    X509TrustedCAModel,
)
from serverframework.extensions.x509_consumer.EXT_X509Consumer import (
    EXT_X509Consumer,
)


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_X509Consumer.name == "x509_consumer"

    def test_version(self):
        assert EXT_X509Consumer.version == "1.0.0"

    def test_abilities(self):
        abilities = EXT_X509Consumer.get_abilities()
        assert "x509_consumer_authenticate" in abilities
        assert "x509_consumer_verify" in abilities

    def test_dependencies(self):
        assert EXT_X509Consumer.extension_dependencies == ["auth_session"]


class TestModels:
    def test_trusted_ca_fields(self):
        fields = set(X509TrustedCAModel.model_fields.keys())
        assert "ca_cert_pem" in fields
        assert "fingerprint_sha256" in fields

    def test_user_link_fields(self):
        fields = set(UserX509LinkModel.model_fields.keys())
        assert "user_id" in fields
        assert "subject_dn" in fields
        assert "fingerprint_sha256" in fields

    def test_manager_model(self):
        assert X509ConsumerManager._model is X509TrustedCAModel


class TestLifecycle:
    def test_on_initialize(self):
        assert EXT_X509Consumer.on_initialize() is True

    def test_on_start(self):
        assert EXT_X509Consumer.on_start() is True

    def test_on_stop(self):
        assert EXT_X509Consumer.on_stop() is True
