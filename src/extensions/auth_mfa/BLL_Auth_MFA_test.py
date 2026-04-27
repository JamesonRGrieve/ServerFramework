from datetime import datetime

import pytest
from faker import Faker
from fastapi import HTTPException

from AbstractTest import CategoryOfTest, ClassOfTestsConfig, ParentEntity
from extensions.AbstractEXTTest import ExtensionServerMixin
from extensions.auth_mfa.BLL_Auth_MFA import (
    MultifactorMethodManager,
    MultifactorMethodType,
    MultifactorRecoveryCodeManager,
)
from extensions.auth_mfa.EXT_Auth_MFA import EXT_Auth_MFA
from logic.AbstractBLLTest import AbstractBLLTest
from logic.BLL_Auth_test import TestUserManager as CoreUserManagerTests

# Set default test configuration for all test classes
AbstractBLLTest.test_config = ClassOfTestsConfig(
    categories=[CategoryOfTest.LOGIC, CategoryOfTest.EXTENSION]
)

# Initialize faker
faker = Faker()


class TestMultifactorMethodManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = MultifactorMethodManager
    extension_class = EXT_Auth_MFA

    create_fields = {
        "method_type": MultifactorMethodType.TOTP,
        "identifier": None,  # Not required for TOTP
        "is_primary": False,
        "always_ask": False,
    }
    update_fields = {
        "is_enabled": True,
        "is_primary": True,
        "always_ask": True,
    }
    unique_fields = []  # No unique fields for MFA methods
    parent_entities = [
        ParentEntity(
            name="user",
            foreign_key="user_id",
            test_class=CoreUserManagerTests,
        ),
    ]

    def test_create_email_mfa_method(self, admin_a, model_registry):
        """Test creating an email MFA method"""
        manager = self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        )
        mfa_method = manager.create(
            user_id=admin_a.id,
            method_type=MultifactorMethodType.EMAIL,
            identifier="test@example.com",
            is_primary=False,
            always_ask=False,
        )

        assert mfa_method is not None
        assert mfa_method.method_type == MultifactorMethodType.EMAIL
        assert mfa_method.identifier == "test@example.com"
        assert mfa_method.user_id == admin_a.id

    def test_create_sms_mfa_method(self, admin_a, model_registry):
        """Test creating an SMS MFA method"""
        manager = self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        )
        mfa_method = manager.create(
            user_id=admin_a.id,
            method_type=MultifactorMethodType.SMS,
            identifier="+1234567890",
            is_primary=False,
            always_ask=False,
        )

        assert mfa_method is not None
        assert mfa_method.method_type == MultifactorMethodType.SMS
        assert mfa_method.identifier == "+1234567890"
        assert mfa_method.user_id == admin_a.id

    def test_create_sms_mfa_without_identifier_fails(self, admin_a, model_registry):
        """Test that creating SMS MFA without identifier fails"""
        manager = self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        )
        with pytest.raises(ValueError) as exc_info:
            manager.create(
                user_id=admin_a.id,
                method_type=MultifactorMethodType.SMS,
                identifier=None,  # Missing required identifier
            )
        assert "Identifier is required" in str(exc_info.value)

    def test_generate_totp_secret(self, admin_a, model_registry):
        """Test generating a TOTP secret"""
        manager = self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        )
        secret = manager.generate_totp_secret()

        assert secret is not None
        assert isinstance(secret, str)
        assert len(secret) > 0

    def test_verify_totp_code(self, admin_a, model_registry):
        """Test verifying a TOTP code"""
        manager = self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        )
        secret = manager.generate_totp_secret()

        # Generate a TOTP code for testing
        try:
            import pyotp

            totp = pyotp.TOTP(secret)
            code = totp.now()

            # Verify the code
            is_valid = manager.verify_totp_code(secret, code)
            assert is_valid is True

            # Test invalid code
            is_invalid = manager.verify_totp_code(secret, "000000")
            assert is_invalid is False
        except ImportError:
            pytest.skip("pyotp library not available")

    def test_send_mfa_code_not_implemented(self, admin_a, team_a, model_registry):
        """Test sending MFA code (should raise not implemented error)"""
        manager = self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        )
        # Create an email MFA method
        mfa_method = manager.create(
            user_id=admin_a.id,
            method_type=MultifactorMethodType.EMAIL,
            identifier="test@example.com",
        )

        # Test sending code (should raise HTTPException for not implemented)
        with pytest.raises(HTTPException) as exc_info:
            manager.send_mfa_code(mfa_method.id)

        # Verify it's the expected "not implemented" error
        assert exc_info.value.status_code == 501
        assert "Email MFA not yet implemented" in str(exc_info.value.detail)

    def test_verify_mfa_code_totp(self, admin_a, team_a, model_registry):
        """Test verifying MFA code for TOTP method"""
        try:
            import pyotp
        except ImportError:
            pytest.skip("pyotp library not available")

        manager = self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        )
        # Create TOTP method
        mfa_method = manager.create(
            user_id=admin_a.id,
            method_type=MultifactorMethodType.TOTP,
        )

        # Generate valid code
        totp = pyotp.TOTP(mfa_method.totp_secret)
        code = totp.now()

        # Verify valid code
        is_valid = manager.verify_mfa_code(mfa_method.id, code)
        assert is_valid is True

        # Verify invalid code
        is_invalid = manager.verify_mfa_code(mfa_method.id, "000000")
        assert is_invalid is False

    def test_update_primary_mfa_method(self, admin_a, team_a, model_registry):
        """Test updating primary MFA method"""
        manager = self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        )
        # Create two MFA methods
        method1 = manager.create(
            user_id=admin_a.id,
            method_type=MultifactorMethodType.TOTP,
            is_primary=True,
        )
        method2 = manager.create(
            user_id=admin_a.id,
            method_type=MultifactorMethodType.EMAIL,
            identifier="test@example.com",
            is_primary=True,  # This should remove primary from method1
        )

        # Check that method1 is no longer primary
        updated_method1 = manager.get(id=method1.id)
        assert updated_method1.is_primary is False

        # Check that method2 is primary
        assert method2.is_primary is True


class TestMultifactorRecoveryCodeManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = MultifactorRecoveryCodeManager
    extension_class = EXT_Auth_MFA

    create_fields = {
        "multifactormethod_id": None,  # Will be set by parent entity
        "created_ip": faker.ipv4(),
        "code_hash": "dummy_hash",
        "code_salt": "dummy_salt",
    }
    update_fields = {
        "is_used": True,
        "used_at": datetime.now(),
    }
    unique_fields = []  # No unique fields for recovery codes
    parent_entities = [
        ParentEntity(
            name="mfa_method",
            foreign_key="multifactormethod_id",
            test_class=TestMultifactorMethodManager,
        ),
    ]

    def test_generate_recovery_codes(self, admin_a, team_a, model_registry):
        """Test generating recovery codes for an MFA method"""
        # First create an MFA method
        mfa_manager = MultifactorMethodManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        mfa_method = mfa_manager.create(
            user_id=admin_a.id,
            method_type=MultifactorMethodType.TOTP,
        )

        # Generate recovery codes
        manager = MultifactorRecoveryCodeManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        codes = manager.generate_recovery_codes(mfa_method.id, count=5)

        assert codes is not None
        assert len(codes) == 5
        assert all(isinstance(code, str) for code in codes)
        assert all("-" in code for code in codes)  # Codes should have dashes
        assert all(len(code) == 9 for code in codes)  # XXXX-XXXX format

    def test_verify_recovery_code(self, admin_a, team_a, model_registry):
        """Test verifying a recovery code"""
        # Create MFA method
        mfa_manager = MultifactorMethodManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        mfa_method = mfa_manager.create(
            user_id=admin_a.id,
            method_type=MultifactorMethodType.TOTP,
        )

        # Generate and verify recovery codes
        manager = MultifactorRecoveryCodeManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        codes = manager.generate_recovery_codes(mfa_method.id, count=3)
        test_code = codes[0]

        # Verify the code
        is_valid = manager.verify_recovery_code(mfa_method.id, test_code)
        assert is_valid is True

        # Test code should now be marked as used
        is_used_again = manager.verify_recovery_code(mfa_method.id, test_code)
        assert is_used_again is False

        # Test invalid code
        is_invalid = manager.verify_recovery_code(mfa_method.id, "INVALID-CODE")
        assert is_invalid is False

    def test_recovery_code_persistence(self, admin_a, team_a, model_registry):
        """Test that recovery codes are properly persisted"""
        # Create MFA method
        mfa_manager = MultifactorMethodManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        mfa_method = mfa_manager.create(
            user_id=admin_a.id,
            method_type=MultifactorMethodType.TOTP,
        )

        # Generate recovery codes
        manager = MultifactorRecoveryCodeManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        codes = manager.generate_recovery_codes(mfa_method.id, count=2)

        # List recovery codes for the method
        recovery_codes = manager.list(multifactormethod_id=mfa_method.id)

        assert len(recovery_codes) == 2
        assert all(not rc.is_used for rc in recovery_codes)

    # ------------------------------------------------------------------
    # Security: explicit-deny / negative-path MFA tests.
    # ------------------------------------------------------------------

    @pytest.mark.security
    @pytest.mark.mfa
    def test_recovery_code_one_time_use(self, admin_a, model_registry):
        """A recovery code accepted once must be rejected on replay."""
        mfa_manager = MultifactorMethodManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        mfa_method = mfa_manager.create(
            user_id=admin_a.id,
            method_type=MultifactorMethodType.TOTP,
        )
        rec_manager = MultifactorRecoveryCodeManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        codes = rec_manager.generate_recovery_codes(mfa_method.id, count=2)
        # `generate_recovery_codes` returns the cleartext codes once.
        first_code = codes[0] if isinstance(codes, list) else None
        if first_code is None:
            pytest.skip("Recovery-code generator did not return cleartext codes")

        # First use must succeed.
        assert rec_manager.verify_recovery_code(mfa_method.id, first_code) is True

        # Replay must fail.
        replay = rec_manager.verify_recovery_code(mfa_method.id, first_code)
        assert replay is False, (
            "Recovery code accepted on replay; one-time-use flag is not "
            "enforced by MultifactorRecoveryCodeManager.verify_recovery_code."
        )

    @pytest.mark.security
    @pytest.mark.mfa
    def test_recovery_code_high_entropy(self, admin_a, model_registry):
        """Recovery codes must be high-entropy and unique across a sample."""
        mfa_manager = MultifactorMethodManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        mfa_method = mfa_manager.create(
            user_id=admin_a.id,
            method_type=MultifactorMethodType.TOTP,
        )
        rec_manager = MultifactorRecoveryCodeManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        seen = set()
        for _ in range(20):
            batch = rec_manager.generate_recovery_codes(mfa_method.id, count=5)
            if not isinstance(batch, list):
                pytest.skip("Recovery-code generator did not return cleartext codes")
            for code in batch:
                assert code not in seen, "Duplicate recovery code in 100-sample batch"
                assert len(code) >= 10, f"Recovery code too short: {len(code)}"
                seen.add(code)

    @pytest.mark.security
    @pytest.mark.mfa
    def test_totp_replay_rejected(self, admin_a, model_registry):
        """A valid TOTP code must NOT verify a second time within the same window.

        EXPECTED FAIL today — pyotp.TOTP.verify() does not track used codes,
        and BLL_Auth_MFA.py:328 has no per-(method, code) replay table.
        """
        import pyotp

        mfa_manager = MultifactorMethodManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        mfa_method = mfa_manager.create(
            user_id=admin_a.id,
            method_type=MultifactorMethodType.TOTP,
        )
        secret = mfa_method.totp_secret
        code = pyotp.TOTP(secret).now()

        first = mfa_manager.verify_totp_code(secret, code)
        replay = mfa_manager.verify_totp_code(secret, code)
        assert first is True, "First TOTP verification must succeed"
        assert replay is False, (
            "TOTP code accepted on replay within the same window. "
            "BLL_Auth_MFA.py needs a per-(method, time-step) used-codes table."
        )

    @pytest.mark.security
    @pytest.mark.mfa
    def test_totp_rejects_far_future_code(self, admin_a, model_registry):
        """A TOTP code from far outside the valid window must be rejected."""
        import pyotp
        import time

        mfa_manager = MultifactorMethodManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        mfa_method = mfa_manager.create(
            user_id=admin_a.id,
            method_type=MultifactorMethodType.TOTP,
        )
        secret = mfa_method.totp_secret

        # Generate a code 5 minutes in the future.
        future_code = pyotp.TOTP(secret).at(int(time.time()) + 300)
        accepted = mfa_manager.verify_totp_code(secret, future_code)
        assert accepted is False, (
            "TOTP code from 5 minutes in the future was accepted; "
            "valid_window=1 (30s drift) should reject it."
        )
