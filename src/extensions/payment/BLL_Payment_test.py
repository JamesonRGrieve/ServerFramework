from datetime import datetime
from unittest.mock import Mock

import pytest
from faker import Faker

from extensions.AbstractEXTTest import ExtensionServerMixin
from extensions.payment.BLL_Payment import (
    get_user_payment_info,
    get_user_subscription_status,
)
from extensions.payment.EXT_Payment import EXT_Payment
from lib.Environment import env
from logic.BLL_Auth_test import TestUserManager as CoreUserManagerTests

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

    @pytest.mark.xfail(reason="Needs an API key.")
    def test_get_user_payment_info(self, mock_get_payment_info, admin_a, team_a):
        """Test getting payment information for a user - verifies payment info functions work"""
        self._create(admin_a.id, team_a.id, "payment_info")
        test_user = self.tracked_entities["payment_info"]

        # Mock the payment info response
        expected_payment_info = {
            "user_id": test_user.id,
            "has_payment_setup": True,
            "external_payment_id": "cus_test_customer",
            "stripe_customer": {"id": "cus_test_customer", "email": test_user.email},
        }
        mock_get_payment_info.return_value = expected_payment_info

        user_manager = self.class_under_test(
            requester_id=admin_a.id,
            target_team_id=team_a.id,
            db=self.db,
            db_manager=self.server.app.state.model_registry.database_manager,
            model_registry=self.server.app.state.model_registry,
        )
        # Simulate the method being attached to the class
        payment_info = get_user_payment_info(
            user_manager, user_id=test_user.id, requester_id=admin_a.id
        )

        assert payment_info["user_id"] == test_user.id
        assert payment_info["has_payment_setup"] is True

    @pytest.mark.xfail(reason="Needs an API key.")
    def test_get_user_subscription_status(
        self, mock_get_subscription_status, admin_a, team_a
    ):
        """Test getting subscription status for a user - verifies subscription status functions work"""
        self._create(admin_a.id, team_a.id, "subscription_status")
        test_user = self.tracked_entities["subscription_status"]

        # Mock the subscription status response
        expected_status = {
            "status": "active",
            "subscription_id": "sub_test_subscription",
            "current_period_end": datetime.now().isoformat(),
        }
        mock_get_subscription_status.return_value = expected_status

        user_manager = self.class_under_test(
            requester_id=admin_a.id,
            target_team_id=team_a.id,
            db=self.db,
            db_manager=self.server.app.state.model_registry.database_manager,
            model_registry=self.server.app.state.model_registry,
        )
        subscription_status = get_user_subscription_status(
            user_manager, user_id=test_user.id, requester_id=admin_a.id
        )

        assert subscription_status["status"] == "active"
        assert subscription_status["subscription_id"] == "sub_test_subscription"

    def test_subscription_validation_hook_bypass(self, admin_a, team_a):
        """Test that subscription validation hook bypasses for system users - verifies hook logic works correctly"""
        # This test ensures the hook logic works correctly for bypass scenarios
        # Since we can't easily test the actual hook, we test the bypass conditions

        # Test root user bypass
        root_email = env("ROOT_EMAIL")
        if root_email:
            # Simulate login context for root user
            from extensions.payment.BLL_Payment import validate_subscription_on_login
            from logic.AbstractLogicManager import HookContext

            # Create mock context for root user login
            mock_context = Mock()
            mock_context.kwargs = {"login_data": {"email": root_email}}
            mock_context.result = {"id": env("ROOT_ID")}

            # This should not raise an exception (bypass)
            try:
                validate_subscription_on_login(mock_context)
                # If we get here, the bypass worked
                assert True
            except Exception:
                # Hook should bypass for root user
                assert False, "Subscription validation should bypass for root user"
