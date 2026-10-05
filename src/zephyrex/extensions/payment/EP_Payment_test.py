# SPDX-License-Identifier: AGPL-3.0-or-later
"""The user endpoints with the payment extension loaded: the core suite,
plus the customer link fields, which users read but never write."""

import json
import uuid
from typing import Any, Dict, List, Optional

import pytest

from zephyrex.AbstractTest import ParentEntity, SkipReason, SkipThisTest
from zephyrex.endpoints.EP_Auth_test import (
    TestUserAndSessionEndpoints as CoreUserAndSessionEndpointsTests,
)
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.payment.EXT_Payment import EXT_Payment
from zephyrex.pydantic2.strawberry import convert_field_name


@pytest.mark.ep
@pytest.mark.payment
class TestPayment_UserAndSessionEndpoints(
    CoreUserAndSessionEndpointsTests, ExtensionServerMixin
):
    """The core user and session endpoint tests, with payment loaded.

    The customer link (``external_payment_id``, ``payment_instance_id``)
    is server-set: these tests used to write it as the user (create and
    update payloads), which let a user name another customer as theirs, so
    the payloads no longer carry it and the writes are tested as refused.
    """

    extension_class = EXT_Payment

    base_endpoint = "user"
    entity_name = "user"
    required_fields = ["id", "email", "created_at", "created_by_user_id"]
    string_field_to_update = "display_name"
    # Mirror the core User test class: User search is restricted for privacy and
    # the /v1/user/search endpoint is intentionally not exposed.
    supports_search = False
    searchable_fields = ["email", "display_name", "first_name", "last_name"]

    parent_entities: List[ParentEntity] = []
    system_entity = False
    user_scoped = True

    related_entities = ["sessions", "credentials", "metadata"]

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
            name="test_GET_200_list",
            details="User entity does not have a standard LIST endpoint",
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
    ]

    def create_payload(
        self,
        name: Optional[str] = None,
        parent_ids: Optional[Dict[str, str]] = None,
        team_id: Optional[str] = None,
        minimal: bool = False,
        invalid_data: bool = False,
    ) -> Dict[str, Any]:
        if invalid_data:
            return {
                "email": "not_an_email",
                "password": "short",
                "display_name": 12345,
            }
        password = self.faker.password(
            length=12,
            special_chars=True,
            digits=True,
            upper_case=True,
            lower_case=True,
        )
        email = (
            name if name and "@" in name else f"user_{uuid.uuid4().hex[:8]}@example.com"
        )
        if minimal:
            return {"email": email, "password": password}
        return {
            "email": email,
            "password": password,
            "display_name": name or self.faker.name(),
            "first_name": self.faker.first_name(),
            "last_name": self.faker.last_name(),
            "_test_password": password,
        }

    def test_GET_200_with_payment_field(self, server: Any, admin_a: Any) -> None:
        """A user reads their own customer link."""
        response = server.get(
            "/v1/user", headers=self._get_appropriate_headers(admin_a.jwt)
        )
        self._assert_response_status(response, 200, "GET current user", "/v1/user")
        user = response.json()[self.entity_name]
        assert "external_payment_id" in user and "payment_instance_id" in user

    def test_PUT_403_payment_field(self, server: Any, admin_a: Any) -> None:
        response = server.put(
            "/v1/user",
            json={"user": {"external_payment_id": "cus_someone_else"}},
            headers=self._self_save_headers(server, admin_a.jwt),
        )
        assert response.status_code == 403, response.text

    def test_GQL_mutation_update_with_payment_is_refused(
        self, server: Any, admin_a: Any
    ) -> None:
        field = convert_field_name("external_payment_id")
        headers = self._get_appropriate_headers(admin_a.jwt)
        # The save names the current version: the refusal is for the field.
        if_match_arg = self._gql_if_match_arg(server, {"id": admin_a.id}, headers)
        mutation = (
            'mutation { updateUser(input: {%s: "cus_someone_else"}%s) { id %s } }'
            % (field, if_match_arg, field)
        )
        response = server.post("/graphql", json={"query": mutation}, headers=headers)
        data = response.json()
        assert data.get("errors"), json.dumps(data)
        assert all(
            error.get("extensions", {}).get("code") != "PRECONDITION_REQUIRED"
            for error in data["errors"]
        ), json.dumps(data)
        assert not (data.get("data") or {}).get("updateUser")

    def test_a_new_user_logs_in(self, server: Any) -> None:
        """Login is untouched for a user with no subscriptions."""
        user_data = self.create_payload()
        created = server.post("/v1/user", json={"user": user_data})
        self._assert_response_status(created, 201, "POST create user", "/v1/user")
        login = server.post(
            "/v1/user/authorize",
            json={
                "auth": {"email": user_data["email"], "password": user_data["password"]}
            },
        )
        assert login.status_code == 200, login.text
        assert login.json().get("token")
