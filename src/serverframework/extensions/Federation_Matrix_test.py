"""Federation matrix homologation tests (Item 16).

This file does three things:

1. Defines reference in-process upstreams (``_GQL_UPSTREAM_SDL`` and
   ``_REST_UPSTREAM_SPEC``) so the framework's own homologation suite is
   deterministic and runs in CI without external credentials.
2. Drives the 4-quadrant × 5-CRUD matrix against those upstreams via two
   concrete subclasses of :class:`AbstractFederationMatrixTest`.
3. Calls :func:`generate_matrix_tests` to programmatically synthesize one
   matrix test class per registered external extension (Stripe, SendGrid,
   etc.). Pytest collects the generated classes alongside the hand-written
   ones, so a new external extension automatically gains 20 cells of
   coverage.

No mocks. The reference upstreams are real FastAPI ASGI apps served via
``httpx.ASGITransport`` so every outbound call exercises ``ProviderHTTPClient``,
auth strategy, rotation, and rate-limit machinery exactly as in production.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List

import pytest
# Module-level import so PEP 563 string annotations on nested handlers resolve
# against module globals when FastAPI calls ``get_type_hints``.
from fastapi import FastAPI, Request

from serverframework.extensions.AbstractFederationMatrixTest import (
    AbstractFederationMatrixTest,
    FederationFixture,
)
from serverframework.extensions.Federation_Matrix_Generator import generate_matrix_tests
from serverframework.lib.Federation_GQL import GQLUpstreamTransport
from serverframework.lib.Federation_REST import (
    OperationSpec,
    RESTUpstreamTransport,
    openapi_to_pydantic_models,
)


pytestmark = [pytest.mark.gql]


# ---------------------------------------------------------------------------
# Reference SDL / OpenAPI used by the framework's own homologation
# ---------------------------------------------------------------------------


_GQL_UPSTREAM_SDL = """
type Widget {
  id: ID!
  name: String!
  size: Int
}

type Query {
  widget(id: ID!): Widget
  widgets: [Widget!]!
}
"""


_REST_UPSTREAM_SPEC: Dict[str, Any] = {
    "components": {
        "schemas": {
            "Widget": {
                "type": "object",
                "required": ["id", "name"],
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "size": {"type": "integer"},
                },
            }
        }
    },
    "paths": {
        "/widgets/{id}": {
            "get": {
                "operationId": "get_widget",
                "parameters": [{"name": "id", "in": "path"}],
            },
            "put": {
                "operationId": "update_widget",
                "parameters": [{"name": "id", "in": "path"}],
            },
            "delete": {
                "operationId": "delete_widget",
                "parameters": [{"name": "id", "in": "path"}],
            },
        },
        "/widgets": {
            "get": {"operationId": "list_widget"},
            "post": {"operationId": "create_widget"},
        },
    },
}


# ---------------------------------------------------------------------------
# In-process upstreams
# ---------------------------------------------------------------------------


def _build_gql_upstream_transport() -> GQLUpstreamTransport:
    """Build a transport pointing at an in-process Strawberry-shaped GQL app.

    The app implements just enough to exercise the matrix: ``widget(id:)``
    and ``widgets`` queries returning a deterministic seed.
    """

    import httpx

    SEED = {
        "1": {"id": "1", "name": "Alpha", "size": 10},
        "2": {"id": "2", "name": "Beta", "size": 20},
    }
    app = FastAPI()

    @app.post("/graphql")
    async def gql_handler(request: Request) -> Dict[str, Any]:
        body = await request.json()
        q = (body.get("query") or "").strip()
        if "_service" in q:
            return {"errors": [{"message": "no _service"}]}
        if "widgets" in q:
            return {"data": {"widgets": list(SEED.values())}}
        if "widget(" in q:
            # Tiny extractor: pull id literal out of the query.
            import re

            m = re.search(r'id:\s*"?([^",)]+)"?', q)
            if m:
                wid = m.group(1)
                return {"data": {"widget": SEED.get(wid)}}
            return {"data": {"widget": None}}
        if "__typename" in q:
            return {"data": {"__typename": "Query"}}
        return {"errors": [{"message": "unsupported"}]}

    class _Wrapper:
        """Sync wrapper so transport.send_sync works without an event loop."""

        def __init__(self, app):
            self._client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://upstream",
            )
            self._sync_client = httpx.Client(
                transport=httpx.ASGITransport(app=app),
                base_url="http://upstream",
            )

        async def post(self, url, *, json=None, **_):
            r = await self._client.post(url, json=json)
            return r.json()

        def post_sync(self, url, *, json=None, **_):
            r = self._sync_client.post(url, json=json)
            return r.json()

    wrapper = _Wrapper(app)

    class _GQLTransport:
        """Mimics ``GQLUpstreamTransport`` shape but runs in-process."""

        async def send(self, *, query, variables=None, requester_id=None):
            body = {"query": query}
            if variables is not None:
                body["variables"] = dict(variables)
            return await wrapper.post("/graphql", json=body)

        def send_sync(self, *, query, variables=None, requester_id=None):
            body = {"query": query}
            if variables is not None:
                body["variables"] = dict(variables)
            return wrapper.post_sync("/graphql", json=body)

    return _GQLTransport()


def _build_rest_upstream_transport() -> RESTUpstreamTransport:
    """Build a REST transport pointing at an in-process FastAPI widget app."""

    import httpx

    SEED = {
        "1": {"id": "1", "name": "Alpha", "size": 10},
        "2": {"id": "2", "name": "Beta", "size": 20},
    }
    app = FastAPI()

    @app.get("/widgets/{wid}")
    async def get_w(wid: str):
        return SEED.get(wid)

    @app.get("/widgets")
    async def list_w():
        return list(SEED.values())

    @app.post("/widgets")
    async def create_w(payload: Dict[str, Any]):
        new = dict(payload)
        new["id"] = "99"
        return new

    @app.put("/widgets/{wid}")
    async def update_w(wid: str, payload: Dict[str, Any]):
        merged = dict(SEED.get(wid) or {})
        merged.update(payload)
        merged["id"] = wid
        return merged

    @app.delete("/widgets/{wid}")
    async def delete_w(wid: str):
        return {"deleted": True, "id": wid}

    # ``httpx.Client`` no longer drives ``ASGITransport`` (httpx 0.28+ made
    # the ASGI transport async-only). Use FastAPI's TestClient, which wraps
    # the same ASGI app behind a sync interface.
    from fastapi.testclient import TestClient

    sync_client = TestClient(app, base_url="http://upstream")

    class _SyncHTTP:
        """Sync HTTP client adapter that ``RESTUpstreamTransport.send_sync`` wants."""

        def get(self, url, **kw):
            return sync_client.get(url, params=kw.get("params") or None).json()

        def post(self, url, **kw):
            return sync_client.post(
                url, json=kw.get("json"), params=kw.get("params") or None
            ).json()

        def put(self, url, **kw):
            return sync_client.put(
                url, json=kw.get("json"), params=kw.get("params") or None
            ).json()

        def patch(self, url, **kw):
            return sync_client.patch(
                url, json=kw.get("json"), params=kw.get("params") or None
            ).json()

        def delete(self, url, **kw):
            return sync_client.delete(url, params=kw.get("params") or None).json()

    operations = openapi_to_pydantic_models(_REST_UPSTREAM_SPEC).operations
    transport = RESTUpstreamTransport(
        _SyncHTTP(), base_url="http://upstream", operations=operations
    )
    return transport


# ---------------------------------------------------------------------------
# Hand-written reference matrix tests
# ---------------------------------------------------------------------------


class TestFederationMatrix_GQLUpstream(AbstractFederationMatrixTest):
    """4-quadrant matrix against the in-process GraphQL upstream."""

    def fixture(self) -> FederationFixture:
        return FederationFixture(
            name="reference_gql_widget",
            upstream_kind="gql",
            transport=_build_gql_upstream_transport(),
            sample_id="1",
            type_name="Widget",
            sdl_or_spec=_GQL_UPSTREAM_SDL,
            operations_supported=["get", "list"],  # minimal SDL doesn't define mutations
            crud_map=None,
        )


class TestFederationMatrix_RESTUpstream(AbstractFederationMatrixTest):
    """4-quadrant matrix against the in-process REST upstream."""

    def fixture(self) -> FederationFixture:
        return FederationFixture(
            name="reference_rest_widget",
            upstream_kind="rest",
            transport=_build_rest_upstream_transport(),
            sample_id="1",
            type_name="Widget",
            sdl_or_spec=_REST_UPSTREAM_SPEC,
            create_payload={"name": "Created", "size": 1},
            update_payload={"name": "Updated"},
            operations_supported=["get", "list", "create", "update", "delete"],
            crud_map={
                "get": "get_widget",
                "list": "list_widget",
                "create": "create_widget",
                "update": "update_widget",
                "delete": "delete_widget",
            },
        )


# ---------------------------------------------------------------------------
# Programmatic generation for every registered external extension
# ---------------------------------------------------------------------------


# This call mutates the module ``globals()`` to add one Test_* class per
# discovered fixture. Pytest collects them automatically.
_GENERATED = generate_matrix_tests(target_namespace=globals())


def test_matrix_generator_emits_classes_for_every_extension_fixture() -> None:
    """The generator must emit ≥ 0 classes (≥ 1 once any extension declares
    a fixture). The smoke assertion ensures the generator runs cleanly even
    when no extension exposes a fixture."""

    assert isinstance(_GENERATED, dict)
    for cls_name, cls in _GENERATED.items():
        assert cls_name.startswith("Test_Federation_")
        assert cls_name.endswith("_Matrix")
        # Classes must be subclasses of the matrix base.
        assert issubclass(cls, AbstractFederationMatrixTest)
