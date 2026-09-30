# SPDX-License-Identifier: AGPL-3.0-or-later
"""GraphQL serves exactly the CRUD operations REST serves for a manager.

Regression tests: GraphQL generated create/update/delete for every manager,
ignoring the routes a manager declares, so what REST deliberately withheld
was one GraphQL mutation away:
- deleteMultifactorMethod removed a verified MFA method with no current code
  (REST offers only the code-gated POST /{id}/delete);
- updateAPIKey could reactivate a revoked key (REST has no update for keys);
- createMultifactorRecoveryCode planted a recovery code of the caller's
  choosing (recovery codes have no REST surface at all)."""

import os
from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import CORE_COMPANION_EXTENSIONS
from zephyrex.testing.factories import create_user

pyotp = pytest.importorskip("pyotp")


@pytest.fixture(scope="module")
def app() -> FastAPI:
    from zephyrex.app import instance
    from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

    prepare_test_registry()
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    built: FastAPI = instance(
        db_prefix=f"test.graphql_parity.{worker}",
        extensions=",".join(["auth_mfa", "auth_api_keys", *CORE_COMPANION_EXTENSIONS]),
    )
    return built


@pytest.fixture(scope="module")
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _schema(app: FastAPI) -> str:
    return str(app.state.model_registry.gql)


def _graphql(client: TestClient, query: str, jwt: str) -> Dict[str, Any]:
    response = client.post(
        "/graphql", json={"query": query}, headers={"Authorization": f"Bearer {jwt}"}
    )
    assert response.status_code == 200, response.text
    body: Dict[str, Any] = response.json()
    return body


def test_a_verified_mfa_method_cannot_be_deleted_without_a_code(app, client):
    from zephyrex.extensions.auth_mfa.BLL_Auth_MFA import (
        MultifactorMethodManager,
        MultifactorMethodType,
    )

    user = create_user(client)
    manager = MultifactorMethodManager(
        requester_id=user.id, model_registry=app.state.model_registry
    )
    method = manager.create(method_type=MultifactorMethodType.TOTP)
    secret = manager.totp_provisioning_route(method.id)["secret"]
    assert manager.verify_mfa_code(method.id, pyotp.TOTP(secret).now())

    refused = _graphql(
        client, 'mutation { deleteMultifactorMethod(id: "%s") }' % method.id, user.jwt
    )
    assert "errors" in refused, refused
    assert manager.get(id=method.id).verification is True


@pytest.mark.parametrize(
    "mutation",
    [
        "deleteMultifactorMethod",
        "updateAPIKey",
        "createAPIKey",
        "createMultifactorRecoveryCode",
        "updateMultifactorRecoveryCode",
        "deleteMultifactorRecoveryCode",
        "createSession",
        "updateSession",
    ],
)
def test_operations_rest_withholds_are_not_in_the_graphql_schema(app, mutation):
    assert f"{mutation}(" not in _schema(app)


@pytest.mark.parametrize(
    "operation",
    ["createMultifactorMethod", "updateMultifactorMethod", "deleteAPIKey", "aPIKeys"],
)
def test_operations_rest_serves_are_in_the_graphql_schema(app, operation):
    """The parity cuts both ways: what REST serves, GraphQL still serves."""
    assert f"{operation}(" in _schema(app) or f"{operation}:" in _schema(app)
