"""Tests for the auth_marketplace extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "auth_marketplace_test")

from serverframework.extensions.auth_marketplace.BLL_Auth_Marketplace import (
    TeamPaymentPortalManager,
    TeamPaymentPortalModel,
    UserPaymentPortalManager,
    UserPaymentPortalModel,
)
from serverframework.extensions.auth_marketplace.EXT_Auth_Marketplace import (
    EXT_Auth_Marketplace,
)


class TestCanonicalWiring:
    def test_user_portal_round_trip(self):
        assert UserPaymentPortalModel.Manager is UserPaymentPortalManager
        assert UserPaymentPortalManager._model is UserPaymentPortalModel

    def test_team_portal_round_trip(self):
        assert TeamPaymentPortalModel.Manager is TeamPaymentPortalManager
        assert TeamPaymentPortalManager._model is TeamPaymentPortalModel

    def test_extension_metadata(self):
        assert EXT_Auth_Marketplace.name == "auth_marketplace"
        assert "payment" in EXT_Auth_Marketplace.extension_dependencies


class TestLifecycle:
    def test_on_initialize_returns_true(self):
        assert EXT_Auth_Marketplace.on_initialize() is True
