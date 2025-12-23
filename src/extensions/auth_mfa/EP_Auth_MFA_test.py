import pytest
from faker import Faker
from typing import List

from AbstractTest import ParentEntity, SkipReason, SkipThisTest
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
    """
    Tests for the User Management and Session endpoints with MFA extension.
    Tests the same functionality as TestUserAndSessionEndpoints in EP_Auth_test.py,
    but with MFA extension enabled to ensure core functionality still works.
    """

    extension_class = EXT_Auth_MFA

    # Skip tests that are not applicable to user endpoint with MFA extension
    _skip_tests = [
        SkipThisTest(
            name="test_GET_404_nonexistent",
            details="Users and sessions are not retrievable by ID.",
        ),
        SkipThisTest(
            name="test_GET_404_other_user",
            details="Users and sessions are not retrievable by ID.",
        ),
        SkipThisTest(
            name="test_GET_200_fields",
            details="Users and sessions are not retrievable by ID.",
        ),
        SkipThisTest(
            name="test_GET_200_includes",
            details="Users and sessions are not retrievable by ID.",
        ),
        SkipThisTest(
            name="test_GET_422_invalid_fields",
            details="Users and sessions are not retrievable by ID.",
        ),
        SkipThisTest(
            name="test_GET_422_unknown_query_param",
            details="Users and sessions are not retrievable by ID.",
        ),
        SkipThisTest(
            name="test_DELETE_404_other_user",
            details="Users and sessions are not retrievable by ID.",
        ),
        SkipThisTest(
            name="test_POST_201_batch",
            details="Users cannot be batch created",
        ),
        SkipThisTest(
            name="test_POST_201_batch_minimal",
            details="Users cannot be batch created",
        ),
        SkipThisTest(
            name="test_POST_200_authorize_body",
            details="Not implemented yet",
        ),
        SkipThisTest(
            name="test_POST_201_header",
            details="Not implemented yet",
        ),
        SkipThisTest(
            name="test_GET_200_list",
            details="Not implemented yet",
        ),
        SkipThisTest(
            name="test_GET_422_list_fields_invalid",
            details="User entity does not have a standard LIST endpoint",
        ),
        SkipThisTest(
            name="test_GET_422_list_invalid_sort_by",
            details="User entity does not have a standard LIST endpoint",
        ),
        SkipThisTest(
            name="test_GET_422_list_invalid_sort_order",
            details="User entity does not have a standard LIST endpoint",
        ),
        SkipThisTest(
            name="test_PUT_404_other_user",
            details="PUT does not support update by user_id",
        ),
        SkipThisTest(
            name="test_GET_401_verify_jwt_empty",
            reason=SkipReason.NOT_IMPLEMENTED,
            details="Open Issue #46",
            gh_issue_number=46,
        ),
        SkipThisTest(
            name="test_POST_200_search",
            details="User search is restricted for privacy/security reasons - users should not be searchable globally",
        ),
        SkipThisTest(
            name="test_POST_200_search_includes",
            details="User search is restricted for privacy/security reasons - users should not be searchable globally",
        ),
        # Additional skips for tests not applicable to user endpoint
        SkipThisTest(
            name="test_POST_400",
            details="User registration endpoint handles malformed JSON differently",
        ),
        SkipThisTest(
            name="test_POST_400_batch",
            details="Users cannot be batch created",
        ),
        SkipThisTest(
            name="test_GET_200_list_fields",
            details="User entity does not have a standard LIST endpoint",
        ),
        SkipThisTest(
            name="test_POST_422_singular_with_plural",
            details="User registration endpoint returns 400 for invalid format",
        ),
        SkipThisTest(
            name="test_PUT_200_batch",
            details="Users cannot be batch updated",
        ),
        SkipThisTest(
            name="test_DELETE_204_batch",
            details="Users cannot be batch deleted",
        ),
        SkipThisTest(
            name="test_GET_200_search_fields",
            details="User search is restricted for privacy/security reasons",
        ),
        SkipThisTest(
            name="test_GET_422_invalid_includes",
            details="Users and sessions are not retrievable by ID.",
        ),
    ]
