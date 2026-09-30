# SPDX-License-Identifier: AGPL-3.0-or-later
"""Custom routes over GraphQL decide the requester as REST does: a route
that needs a signed-in caller runs as that caller (and refuses anyone
else), and a public route runs for anyone."""

from typing import Any, Dict

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.auth_api_keys.EXT_Auth_APIKeys import EXT_Auth_APIKeys

ISSUE = 'mutation { apiKeyIssue(input: {name: "gql"}) { id name key } }'


def _graphql(server: Any, query: str, jwt: str = "") -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {jwt}"} if jwt else {}
    response = server.post("/graphql", json={"query": query}, headers=headers)
    assert response.status_code == 200, response.text
    body: Dict[str, Any] = response.json()
    return body


class TestCustomRoutesOverGraphQL(ExtensionServerMixin):
    extension_class = EXT_Auth_APIKeys

    def test_a_signed_in_caller_runs_the_route_as_themselves(self, server, admin_a):
        issued = _graphql(server, ISSUE, admin_a.jwt)
        assert "errors" not in issued, issued
        key = issued["data"]["apiKeyIssue"]
        assert key["name"] == "gql" and key["key"]

        validated = _graphql(
            server,
            'mutation { apiKeyValidate(input: {apiKey: "%s"}) { valid userId } }'
            % key["key"],
        )
        assert "errors" not in validated, validated
        assert validated["data"]["apiKeyValidate"] == {
            "valid": True,
            "userId": admin_a.id,
        }

    def test_each_app_runs_the_route_on_its_own_database(self, server):
        """Resolvers are shared by every app in the process; a second app
        must not run its callers' routes against the first app's database."""
        import uuid

        from fastapi.testclient import TestClient

        from conftest import CORE_COMPANION_EXTENSIONS
        from zephyrex.app import instance
        from zephyrex.pydantic2.sqlalchemy import prepare_test_registry
        from zephyrex.testing.factories import create_user

        prepare_test_registry()
        second = TestClient(
            instance(
                db_prefix=f"test.api_keys_second.{uuid.uuid4().hex[:8]}",
                extensions=",".join(["auth_api_keys", *CORE_COMPANION_EXTENSIONS]),
            )
        )
        caller = create_user(second)

        issued = _graphql(second, ISSUE, caller.jwt)
        assert "errors" not in issued, issued

    def test_an_anonymous_caller_is_refused(self, server):
        refused = _graphql(server, ISSUE)
        assert refused["data"] is None
        assert "authenticate" in refused["errors"][0]["message"]
