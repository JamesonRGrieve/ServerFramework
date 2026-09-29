from datetime import datetime

import pytest
from faker import Faker

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.payment.BLL_Payment import (
    get_user_payment_info,
    get_user_subscription_status,
)
from zephyrex.extensions.payment.EXT_Payment import EXT_Payment
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth_test import TestUserManager as CoreUserManagerTests

faker = Faker()


class TestPayment_UserManager(CoreUserManagerTests, ExtensionServerMixin):
    """
    Test the UserManager with payment extension functionality.
    Tests the same functionality as TestUserManager in BLL_Auth_test.py,
    but with payment extension enabled to ensure core functionality still works
    plus payment-specific features.
    """

    @property
    def create_fields(self):
        return {
            **super().create_fields,
            "external_payment_id": "abc123",  # Payment extension field
        }

    @property
    def update_fields(self):
        return {
            **super().update_fields,
            "external_payment_id": "xyz456",
        }

    # Extension configuration for ExtensionServerMixin
    extension_class = EXT_Payment

    def test_get_user_payment_info(self, server, admin_a, team_a):
        """Test getting payment information for a user."""
        self._create(admin_a.id, team_a.id, "payment_info", server=server)
        test_user = self.tracked_entities["payment_info"]

        model_registry = server.app.state.model_registry
        user_manager = self.class_under_test(
            requester_id=admin_a.id,
            target_team_id=team_a.id,
            model_registry=model_registry,
        )
        payment_info = get_user_payment_info(
            user_manager, user_id=test_user.id, requester_id=admin_a.id
        )

        assert payment_info["user_id"] == test_user.id
        assert "has_payment_setup" in payment_info

    def test_get_user_subscription_status(self, server, admin_a, team_a):
        """Test getting subscription status for a user."""
        self._create(admin_a.id, team_a.id, "subscription_status", server=server)
        test_user = self.tracked_entities["subscription_status"]

        model_registry = server.app.state.model_registry
        user_manager = self.class_under_test(
            requester_id=admin_a.id,
            target_team_id=team_a.id,
            model_registry=model_registry,
        )
        subscription_status = get_user_subscription_status(
            user_manager, user_id=test_user.id, requester_id=admin_a.id
        )

        assert "status" in subscription_status

    @pytest.mark.parametrize(
        "provider_status, blocked", [("inactive", True), ("active", False)]
    )
    def test_login_hook_enforces_the_provider_subscription_status(
        self, server, admin_a, team_a, monkeypatch, provider_status, blocked
    ):
        """A user with a payment customer is refused login (402) exactly when
        the payment provider reports the subscription inactive. The provider
        call is the external boundary, so only it is substituted."""
        from fastapi import HTTPException

        from zephyrex.extensions.payment import BLL_Payment
        from zephyrex.logic.AbstractLogicManager import HookContext, HookTiming

        self._create(admin_a.id, team_a.id, f"login_{provider_status}", server=server)
        user = self.tracked_entities[f"login_{provider_status}"]
        manager = self.class_under_test(
            requester_id=admin_a.id,
            target_team_id=team_a.id,
            model_registry=server.app.state.model_registry,
        )
        assert manager.get(id=user.id).external_payment_id, "precondition"
        seen = {}

        def provider_status_for(user_manager, user_id, requester_id=None):
            seen["user_id"] = user_id
            return {"status": provider_status}

        monkeypatch.setattr(
            BLL_Payment, "get_user_subscription_status", provider_status_for
        )
        # ``result`` is the method's return value an AFTER hook reads;
        # ``set_result`` would instead set an override and leave it None.
        context = HookContext(
            manager=manager,
            method_name="login",
            args=[],
            kwargs={"login_data": {"email": user.email}},
            result={"id": user.id},
            timing=HookTiming.AFTER,
        )

        if blocked:
            with pytest.raises(HTTPException) as exc:
                BLL_Payment.validate_subscription_on_login(context)
            assert exc.value.status_code == 402
        else:
            BLL_Payment.validate_subscription_on_login(context)
        assert seen["user_id"] == user.id

    def test_subscription_validation_hook_bypass(self, monkeypatch):
        """The root user's login is never subscription-checked: even with the
        provider reporting inactive, it is not blocked and the provider is
        never consulted."""
        from zephyrex.extensions.payment import BLL_Payment
        from zephyrex.logic.AbstractLogicManager import HookContext, HookTiming

        root_email = "root@example.com"
        monkeypatch.setenv("ROOT_EMAIL", root_email)

        def provider_must_not_be_called(*args, **kwargs):
            raise AssertionError("root login consulted the payment provider")

        monkeypatch.setattr(
            BLL_Payment, "get_user_subscription_status", provider_must_not_be_called
        )
        context = HookContext(
            manager=None,
            method_name="login",
            args=[],
            kwargs={"login_data": {"email": root_email}},
            result={"id": env("ROOT_ID")},
            timing=HookTiming.AFTER,
        )

        BLL_Payment.validate_subscription_on_login(context)
