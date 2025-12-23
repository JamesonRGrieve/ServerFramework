import pytest
from faker import Faker

from endpoints.EP_Auth_test import (
    TestUserAndSessionEndpoints as CoreUserAndSessionEndpointTests,
)
from extensions.AbstractEXTTest import ExtensionServerMixin
from extensions.auth_mfa.EXT_Auth_MFA import EXT_Auth_MFA

# Initialize faker
faker = Faker()


@pytest.mark.ep
@pytest.mark.auth
@pytest.mark.mfa
class TestAuth_MFA_UserAndSessionEndpoints(
    CoreUserAndSessionEndpointTests, ExtensionServerMixin
):
    extension_class = EXT_Auth_MFA
