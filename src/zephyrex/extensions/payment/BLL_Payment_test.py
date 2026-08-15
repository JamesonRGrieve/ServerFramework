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

    def test_subscription_validation_hook_bypass(self, admin_a, team_a):
        """Test that subscription validation hook bypasses for system users - verifies hook logic works correctly"""
        # This test ensures the hook logic works correctly for bypass scenarios
        # using a real HookContext (per AGENTS.md no-mock pillar).

        # Test root user bypass
        root_email = env("ROOT_EMAIL")
        if root_email:
            # Simulate login context for root user
            from zephyrex.extensions.payment.BLL_Payment import validate_subscription_on_login
            from zephyrex.logic.AbstractLogicManager import HookContext, HookTiming

            # Build a real HookContext for the bypass scenario. The hook
            # should examine kwargs/result and short-circuit for root.
            real_context = HookContext(
                manager=None,
                method_name="login",
                args=[],
                kwargs={"login_data": {"email": root_email}},
                timing=HookTiming.AFTER,
            )
            real_context.set_result({"id": env("ROOT_ID")})

            # This should not raise an exception (bypass)
            try:
                validate_subscription_on_login(real_context)
                # If we get here, the bypass worked
                assert True
            except Exception:
                # Hook should bypass for root user
                assert False, "Subscription validation should bypass for root user"
