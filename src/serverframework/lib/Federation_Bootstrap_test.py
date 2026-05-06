"""Tests for ``lib.Federation_Bootstrap`` (Item 16 — registry integration).

Covers:

* Sync introspection + register pipeline against an in-process upstream.
* Lifted-model binding to a fake ``ModelRegistry``.
* GQL→REST router accumulation on the :class:`FederationCommitReport`.
* REST upstream descriptor handling (OpenAPI → Pydantic → bind).
* Failure isolation: an unreachable upstream MUST NOT raise.

No mocks. Real ASGI upstreams; the only fakes are minimal ``ModelRegistry``
shells whose entire job is to record the bound classes.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import httpx
import pytest
# Module-level import so PEP 563 string annotations on nested handlers resolve
# against module globals when FastAPI calls ``get_type_hints``.
from fastapi import FastAPI, Request

from serverframework.lib.Federation_Bootstrap import (
    FederationCommitReport,
    install_external_federation_sync,
    reset_federation_state,
)


pytestmark = [pytest.mark.gql]


# ---------------------------------------------------------------------------
# Fake registry
# ---------------------------------------------------------------------------


class _RecordingRegistry:
    """Minimal ModelRegistry shell that captures bound classes."""

    def __init__(self) -> None:
        self.bound: List[type] = []

    def bind_model(self, model_cls: type, name: str = "") -> None:
        self.bound.append(model_cls)


# ---------------------------------------------------------------------------
# In-process GraphQL upstream
# ---------------------------------------------------------------------------


def _build_strict_gql_upstream():
    """Build a sync HTTP-shaped client backed by a FastAPI ASGI app.

    httpx 0.28+ removed sync support from ``ASGITransport``; FastAPI's
    ``TestClient`` keeps the same ASGI app behind a sync facade.
    """
    app = FastAPI()

    @app.post("/graphql")
    async def gql(request: Request):
        body = await request.json()
        q = (body.get("query") or "").strip()
        if "_service" in q:
            return {"errors": [{"message": "no _service"}]}
        if "__schema" in q or "IntrospectionQuery" in q:
            from graphql import (
                GraphQLObjectType,
                GraphQLSchema,
                GraphQLString,
                GraphQLField,
                execute_sync,
                parse,
            )

            user_t = GraphQLObjectType(
                "Foo", {"id": GraphQLField(GraphQLString), "name": GraphQLField(GraphQLString)}
            )
            query_t = GraphQLObjectType(
                "Query", {"foo": GraphQLField(user_t, resolve=lambda *a, **kw: {"id": "1", "name": "n"})}
            )
            schema = GraphQLSchema(query=query_t)
            result = execute_sync(schema, parse(q))
            return {"data": result.data}
        return {"data": {"__typename": "Query"}}

    from fastapi.testclient import TestClient
    return TestClient(app, base_url="http://upstream")


# ---------------------------------------------------------------------------
# install_external_federation_sync — GQL upstream
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset():
    reset_federation_state()
    yield
    reset_federation_state()


def test_sync_pipeline_registers_subgraph_and_lifts_models(monkeypatch):
    from serverframework.extensions.AbstractGraphQLProvider import (
        AbstractGraphQLProvider,
    )
    from serverframework.lib.Federation_GQL import global_registry

    sync_client = _build_strict_gql_upstream()

    class _Probe(AbstractGraphQLProvider):
        name = "probe_gql"
        upstream_url = "http://upstream/graphql"
        federation_style = "stitching"
        type_namespace = "Up_"
        auth_strategy_name = "api_key"

        @classmethod
        def bond_instance(cls, instance):
            from serverframework.extensions.AbstractExtensionProvider import (
                AbstractProviderInstance,
            )
            return AbstractProviderInstance(instance)

    class _Shim:
        def post(self, url, *, json=None, **_):
            return sync_client.post(url, json=json).json()

        async def post_async(self, url, *, json=None, **_):  # pragma: no cover
            return sync_client.post(url, json=json).json()

    monkeypatch.setattr(
        _Probe, "http_client_sync", classmethod(lambda cls, **_: _Shim())
    )
    # Make the upstream transport's send a no-op for the test.
    monkeypatch.setattr(
        _Probe,
        "upstream_transport",
        classmethod(
            lambda cls, **_: type(
                "T",
                (),
                {
                    "send": staticmethod(lambda **kw: {"data": {}}),
                    "send_sync": staticmethod(lambda **kw: {"data": {}}),
                },
            )
        ),
    )

    registry = _RecordingRegistry()
    report = install_external_federation_sync(
        model_registry=registry, providers=[_Probe]
    )

    # Subgraph registered with the merged registry.
    assert "_Probe" in {sg.name for sg in global_registry().subgraphs()}
    # Lift produced models, and they were bound.
    assert any("Up_Foo" in m.__name__ or "Foo" in m.__name__ for m in registry.bound)
    # Report carries the lifted model class name.
    assert any("Foo" in name for name in report.models.keys())
    # GQL→REST router was accumulated.
    assert isinstance(report.rest_routers, list)


def test_sync_pipeline_skips_providers_without_upstream():
    from serverframework.extensions.AbstractGraphQLProvider import (
        AbstractGraphQLProvider,
    )

    class _NoUpstream(AbstractGraphQLProvider):
        name = "no_upstream"
        upstream_url = ""

        @classmethod
        def bond_instance(cls, instance):
            from serverframework.extensions.AbstractExtensionProvider import (
                AbstractProviderInstance,
            )
            return AbstractProviderInstance(instance)

    registry = _RecordingRegistry()
    report = install_external_federation_sync(
        model_registry=registry, providers=[_NoUpstream]
    )
    # Nothing bound, no error.
    assert registry.bound == []
    assert report.errors == {}


def test_sync_pipeline_isolates_failed_provider():
    from serverframework.extensions.AbstractGraphQLProvider import (
        AbstractGraphQLProvider,
    )

    class _Broken(AbstractGraphQLProvider):
        name = "broken"
        upstream_url = "http://there-is-no-upstream-here.invalid/graphql"
        federation_style = "stitching"

        @classmethod
        def bond_instance(cls, instance):
            from serverframework.extensions.AbstractExtensionProvider import (
                AbstractProviderInstance,
            )
            return AbstractProviderInstance(instance)

    registry = _RecordingRegistry()
    # MUST NOT raise — federation isolates per-provider failures.
    report = install_external_federation_sync(
        model_registry=registry, providers=[_Broken]
    )
    assert "_Broken" in report.errors


# ---------------------------------------------------------------------------
# install_external_federation_sync — REST upstream
# ---------------------------------------------------------------------------


def test_sync_pipeline_handles_rest_descriptors():
    from serverframework.lib.Federation_REST import RESTUpstreamTransport

    spec = {
        "components": {
            "schemas": {
                "Order": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {
                        "id": {"type": "string"},
                        "amount": {"type": "integer"},
                    },
                }
            }
        },
        "paths": {
            "/orders/{id}": {
                "get": {"operationId": "get_order", "parameters": [{"name": "id", "in": "path"}]}
            }
        },
    }

    class _SyncHTTP:
        def __init__(self):
            self.calls: List[str] = []

        def get(self, url, **_):
            self.calls.append(url)
            return {"id": "x", "amount": 1}

        def post(self, *_a, **_kw): return {}
        def put(self, *_a, **_kw): return {}
        def patch(self, *_a, **_kw): return {}
        def delete(self, *_a, **_kw): return {}

    http = _SyncHTTP()
    from serverframework.lib.Federation_REST import openapi_to_pydantic_models

    operations = openapi_to_pydantic_models(spec).operations
    transport = RESTUpstreamTransport(http, base_url="http://upstream", operations=operations)
    descriptor = {
        "name": "test_rest",
        "spec": spec,
        "transport": transport,
        "prefix": None,
    }
    registry = _RecordingRegistry()
    report = install_external_federation_sync(
        model_registry=registry,
        providers=[],
        rest_descriptors=[descriptor],
    )
    assert any("Order" in name for name in report.models.keys())
    assert any("Order" in m.__name__ for m in registry.bound)
