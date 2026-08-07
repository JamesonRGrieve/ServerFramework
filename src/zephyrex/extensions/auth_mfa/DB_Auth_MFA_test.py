from datetime import datetime

from faker import Faker

from zephyrex.AbstractTest import CategoryOfTest, ClassOfTestsConfig, ParentEntity
from zephyrex.database.AbstractDBTest import AbstractDBTest
from zephyrex.database.DB_Auth_test import TestUser as CoreUserTests
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.auth_mfa.BLL_Auth_MFA import (
    MultifactorMethodModel,
    MultifactorMethodType,
    MultifactorRecoveryCodeModel,
)
from zephyrex.extensions.auth_mfa.EXT_Auth_MFA import EXT_Auth_MFA

# Set default test configuration for all test classes
AbstractDBTest.test_config = ClassOfTestsConfig(
    categories=[CategoryOfTest.DATABASE, CategoryOfTest.EXTENSION]
)

faker = Faker()


class TestMultifactorMethod(AbstractDBTest, ExtensionServerMixin):
    class_under_test = MultifactorMethodModel
    extension_class = EXT_Auth_MFA

    create_fields = {
        "user_id": None,  # Will be populated by parent_entities
        "method_type": MultifactorMethodType.TOTP,
        "identifier": None,  # Not required for TOTP
        "totp_secret": "JBSWY3DPEHPK3PXP",  # Base32 encoded secret
        "totp_algorithm": "SHA1",
        "totp_digits": 6,
        "totp_period": 30,
        "is_enabled": True,
        "is_primary": False,
        "always_ask": False,
        "verification": False,
    }
    update_fields = {
        "identifier": "+1234567890",
        "is_enabled": False,
        "is_primary": True,
        "verification": True,
        "last_used": datetime.now(),
    }
    parent_entities = [
        ParentEntity(
            name="user",
            foreign_key="user_id",
            test_class=CoreUserTests,
        )
    ]


class TestMultifactorRecoveryCode(AbstractDBTest, ExtensionServerMixin):
    """Test MultifactorRecoveryCode database operations."""

    class_under_test = MultifactorRecoveryCodeModel
    extension_class = EXT_Auth_MFA

    create_fields = {
        "multifactor_method_id": "550e8400-e29b-41d4-a716-446655440000",
        "code_hash": "test_hash",
        "code_salt": "test_salt",
        "is_used": False,
        "created_ip": "192.168.1.1",
    }
    update_fields = {
        "is_used": True,
        "used_at": datetime.now(),
    }
    # Recovery codes need the MFA method as parent
    parent_entities = [
        ParentEntity(
            name="usermfamethod",
            foreign_key="multifactor_method_id",
            test_class=TestMultifactorMethod,
        )
    ]
