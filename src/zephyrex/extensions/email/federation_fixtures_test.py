# SPDX-License-Identifier: AGPL-3.0-or-later
"""Federation matrix fixtures for SendGrid's mail REST API.

Test-only: canned seed data for a deterministic in-process upstream.
``Federation_Matrix_Generator`` imports this module (each bundled
extension's ``federation_fixtures_test``) and emits one
``Test_Federation_EXT_EMail_<Type>_Matrix`` class per fixture into
``Federation_Matrix_test``. It used to be a classmethod on ``EXT_EMail``,
where the generator never found it.
"""

from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.testclient import TestClient

from zephyrex.extensions.AbstractFederationMatrixTest import FederationFixture
from zephyrex.extensions.federation.BLL_Federation_REST import (
    RESTUpstreamTransport,
    openapi_to_pydantic_models,
)
from zephyrex.lib.Environment import env

SENDGRID_TEST_BASE_URL = "http://sendgrid-test"
MAIL_SAMPLE_ID = "mail_test"

MAIL_SPEC: Dict[str, Any] = {
    "components": {
        "schemas": {
            "Mail": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string"},
                    "subject": {"type": "string"},
                    "to": {"type": "string"},
                },
            }
        }
    },
    "paths": {
        "/v3/mail/{id}": {
            "get": {
                "operationId": "get_mail",
                "parameters": [{"name": "id", "in": "path"}],
            }
        },
        "/v3/mail/send": {"post": {"operationId": "create_mail"}},
    },
}

MAIL_SEED: Dict[str, Dict[str, Any]] = {
    MAIL_SAMPLE_ID: {"id": MAIL_SAMPLE_ID, "subject": "Hi", "to": "x@y.com"},
}


def federation_matrix_fixtures() -> List[FederationFixture]:
    """SendGrid's Mail resource, read through an in-process upstream; live
    runs activate when ``SENDGRID_API_KEY`` is set."""
    return [
        FederationFixture(
            name="EXT_EMail.Mail",
            upstream_kind="rest",
            transport=_build_in_process_transport(MAIL_SPEC, MAIL_SEED),
            sample_id=MAIL_SAMPLE_ID,
            type_name="Mail",
            sdl_or_spec=MAIL_SPEC,
            operations_supported=["get"],
            crud_map={"get": "get_mail"},
            requires_credentials=False,
            credentials_present=lambda: bool(env("SENDGRID_API_KEY")),
        )
    ]


def _build_in_process_transport(
    spec: Dict[str, Any], seed: Dict[str, Dict[str, Any]]
) -> RESTUpstreamTransport:
    """A deterministic in-process SendGrid upstream serving ``seed``."""
    app = FastAPI()

    @app.get("/v3/mail/{mail_id}")
    async def _get(mail_id: str) -> Any:
        return seed.get(mail_id)

    sync_client = TestClient(app, base_url=SENDGRID_TEST_BASE_URL)

    class _SyncHTTP:
        def get(self, url: str, **kw: Any) -> Any:
            return sync_client.get(url, params=kw.get("params") or None).json()

        def post(self, url: str, **kw: Any) -> Any:
            return sync_client.post(url, json=kw.get("json")).json()

        def put(self, url: str, **kw: Any) -> Any:
            return sync_client.put(url, json=kw.get("json")).json()

        def patch(self, url: str, **kw: Any) -> Any:
            return sync_client.patch(url, json=kw.get("json")).json()

        def delete(self, url: str, **kw: Any) -> Any:
            return sync_client.delete(url).json()

    operations = openapi_to_pydantic_models(spec).operations
    return RESTUpstreamTransport(
        _SyncHTTP(), base_url=SENDGRID_TEST_BASE_URL, operations=operations
    )
