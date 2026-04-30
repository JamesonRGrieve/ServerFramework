import pytest
from faker import Faker

from serverframework.endpoints.EP_Auth_test import (
    TestUserAndSessionEndpoints as CoreUserAndSessionEndpointTests,
)
from serverframework.extensions.AbstractEXTTest import ExtensionServerMixin
from serverframework.extensions.auth_mfa.EXT_Auth_MFA import EXT_Auth_MFA

# Initialize faker
faker = Faker()


@pytest.mark.ep
@pytest.mark.auth
@pytest.mark.mfa
class TestAuth_MFA_UserAndSessionEndpoints(
    CoreUserAndSessionEndpointTests, ExtensionServerMixin
):
    extension_class = EXT_Auth_MFA
