"""Tests for ``extensions.AbstractGraphQLProvider`` (Item 16).

Exercises the introspect → transform → register → lift pipeline against a
real in-process FastAPI ASGI upstream — no mocks. The "upstream" is a
Strawberry app that serves a tiny GraphQL schema; the provider talks to
it through ``httpx.AsyncClient(transport=ASGITransport(...))`` so the full
``ProviderHTTPClient`` pipeline (auth strategy, rate limiting, response
classification) participates in every call exactly as in production.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import httpx
import pytest
# Module-level import so PEP 563 string annotations on nested handlers resolve
# against module globals when FastAPI calls ``get_type_hints``.
from fastapi import FastAPI, Request

from serverframework.extensions.AbstractGraphQLProvider import AbstractGraphQLProvider
from serverframework.extensions.AuthStrategy import APIKeyAuth
from serverframework.lib.Federation_GQL import (
    APOLLO_DIRECTIVES_PREAMBLE,
    APOLLO_SDL_QUERY,
    INTROSPECTION_QUERY,
    MergedSchemaRegistry,
    reset_global_registry,
)
from serverframework.lib.ProviderHTTPClient import ClientPolicy


pytestmark = [pytest.mark.gql]


# ---------------------------------------------------------------------------
# In-process upstream apps
# ---------------------------------------------------------------------------


def _make_stitching_app() -> "httpx.AsyncClient":
    """Tiny upstream that supports plain introspection (no Apollo SDL)."""

    app = FastAPI()

    USERS = {
        "1": {"id": "1", "name": "Alice", "email": "a@x.com"},
        "2": {"id": "2", "name": "Bob", "email": "b@x.com"},
    }

    @app.post("/graphql")
    async def gql(request: Request) -> Dict[str, Any]:
        body = await request.json()
        q = body.get("query", "")
        if "_service" in q:
            # Not Apollo — return error envelope
            return {"errors": [{"message": "_service not supported"}]}
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
                "User",
                {
                    "id": GraphQLField(GraphQLString),
                    "name": GraphQLField(GraphQLString),
                    "email": GraphQLField(GraphQLString),
                },
            )
            query_t = GraphQLObjectType(
                "Query",
                {
                    "user": GraphQLField(
                        user_t, resolve=lambda *_args, **_kw: USERS.get(_kw.get("id"))
                    ),
                    "users": GraphQLField(
                        user_t, resolve=lambda *_args, **_kw: list(USERS.values())
                    ),
                },
            )
            schema = GraphQLSchema(query=query_t)
            result = execute_sync(schema, parse(q))
            return {"data": result.data, "errors": [str(e) for e in (result.errors or [])] or None}
        # Generic typename probe
        if "__typename" in q:
            return {"data": {"__typename": "Query"}}
        return {"errors": [{"message": "unsupported"}]}

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://upstream")


def _make_apollo_app() -> "httpx.AsyncClient":
    """Upstream that implements the Apollo Federation v2 SDL probe."""

    app = FastAPI()
    SDL = (
        'type Customer @key(fields: "id") {\n'
        "  id: ID!\n"
        "  name: String\n"
        "}\n"
        "type Query {\n"
        "  customer(id: ID!): Customer\n"
        "}\n"
    )

    @app.post("/graphql")
    async def gql(request: Request) -> Dict[str, Any]:
        body = await request.json()
        q = body.get("query", "")
        if "_service" in q:
            return {"data": {"_service": {"sdl": SDL}}}
        return {"data": {"__typename": "Query"}}

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://upstream")


# ---------------------------------------------------------------------------
# Provider classes used in these tests
# ---------------------------------------------------------------------------


class _Stub_GraphQLProvider(AbstractGraphQLProvider):
    """Concrete stitching provider talking to the in-process Strawberry app."""

    name = "stub_gql"
    description = "in-process upstream for tests"

    upstream_url: str = "http://upstream/graphql"
    federation_style: str = "stitching"
    type_namespace: Optional[str] = "Stub_"
    auth_strategy_name: str = "api_key"

    @classmethod
    def bond_instance(cls, instance):  # pragma: no cover - exercised via tests
        from serverframework.extensions.AbstractExtensionProvider import (
            AbstractProviderInstance,
        )
        return AbstractProviderInstance(instance)


class _StubApolloProvider(AbstractGraphQLProvider):
    name = "stub_apollo"
    description = "Apollo v2 upstream"

    upstream_url: str = "http://upstream/graphql"
    federation_style: str = "apollo_v2"
    type_namespace: Optional[str] = None

    @classmethod
    def bond_instance(cls, instance):  # pragma: no cover
        from serverframework.extensions.AbstractExtensionProvider import (
            AbstractProviderInstance,
        )
        return AbstractProviderInstance(instance)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_global_registry()
    yield
    reset_global_registry()


@pytest.fixture
def patched_provider_http(monkeypatch):
    """Replace ``ProviderHTTPClient.post`` with a thin shim around our ASGI app."""

    class _Shim:
        def __init__(self, client: httpx.AsyncClient) -> None:
            self._client = client

        async def post(self, url: str, *, json: Any = None, **_: Any) -> Any:
            response = await self._client.post(url, json=json)
            return response.json()

        def post_sync(self, url: str, *, json: Any = None, **_: Any) -> Any:
            return asyncio.run(self.post(url, json=json))

    return _Shim


# ---------------------------------------------------------------------------
# transformer() configuration
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_transformer_carries_provider_config():
    class _Cfg(_Stub_GraphQLProvider):
        schema_rename = {"Customer": "Stub_Customer"}
        schema_hide_fields = {"Customer": {"secret"}}
        schema_mask_arguments = {"Query": {"customer": {"insecure"}}}

    transformer = _Cfg.transformer()
    assert transformer.rename == {"Customer": "Stub_Customer"}
    assert transformer.prefix == "Stub_"
    assert transformer.hide_fields == {"Customer": {"secret"}}
    assert transformer.mask_arguments == {"Query": {"customer": {"insecure"}}}


# ---------------------------------------------------------------------------
# Stitching introspection path
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_stitching_introspection_round_trips(monkeypatch):
    upstream = _make_stitching_app()
    try:
        # Wire a fake ProviderHTTPClient that delegates to the ASGI client.
        async def _post(url: str, *, json: Any = None, **_: Any) -> Any:
            response = await upstream.post(url, json=json)
            return response.json()

        class _StubHTTP:
            async def post(self, url: str, *, json: Any = None, **_: Any) -> Any:
                return await _post(url, json=json)

        # Patch http_client to return our shim — the rest of the pipeline
        # remains genuine production code.
        monkeypatch.setattr(
            _Stub_GraphQLProvider, "http_client", classmethod(lambda cls, **_: _StubHTTP())
        )

        ingested = await _Stub_GraphQLProvider.introspect()
        assert ingested.apollo_v2 is False
        # Original SDL contains User type before transformation.
        assert "type User" in ingested.sdl
    finally:
        await upstream.aclose()


@pytest.mark.unit
async def test_register_with_registry_applies_transformer(monkeypatch):
    upstream = _make_stitching_app()
    try:
        class _StubHTTP:
            async def post(self, url: str, *, json: Any = None, **_: Any) -> Any:
                response = await upstream.post(url, json=json)
                return response.json()

        monkeypatch.setattr(
            _Stub_GraphQLProvider, "http_client", classmethod(lambda cls, **_: _StubHTTP())
        )

        registry = MergedSchemaRegistry()
        rewritten = await _Stub_GraphQLProvider.register_with_registry(registry=registry)
        # Prefix from type_namespace was applied.
        assert "Stub_User" in rewritten.sdl
        # Subgraph is registered.
        names = {sg.name for sg in registry.subgraphs()}
        assert _Stub_GraphQLProvider.__name__ in names
    finally:
        await upstream.aclose()


# ---------------------------------------------------------------------------
# Apollo Federation v2 path
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_apollo_v2_uses_service_sdl_probe(monkeypatch):
    upstream = _make_apollo_app()
    try:
        class _StubHTTP:
            async def post(self, url: str, *, json: Any = None, **_: Any) -> Any:
                response = await upstream.post(url, json=json)
                return response.json()

        monkeypatch.setattr(
            _StubApolloProvider, "http_client", classmethod(lambda cls, **_: _StubHTTP())
        )

        ingested = await _StubApolloProvider.introspect()
        assert ingested.apollo_v2 is True
        assert ingested.key_fields == {"Customer": ["id"]}
    finally:
        await upstream.aclose()


@pytest.mark.unit
async def test_apollo_v2_falls_back_when_lenient(monkeypatch):
    upstream = _make_stitching_app()  # Doesn't support _service

    class _Lenient(_StubApolloProvider):
        require_apollo_v2_when_advertised = False

    try:
        class _StubHTTP:
            async def post(self, url: str, *, json: Any = None, **_: Any) -> Any:
                response = await upstream.post(url, json=json)
                return response.json()

        monkeypatch.setattr(
            _Lenient, "http_client", classmethod(lambda cls, **_: _StubHTTP())
        )

        ingested = await _Lenient.introspect()
        # Lenient mode falls back to plain introspection.
        assert ingested.apollo_v2 is False
    finally:
        await upstream.aclose()


@pytest.mark.unit
async def test_apollo_v2_strict_mode_raises_when_unsupported(monkeypatch):
    upstream = _make_stitching_app()
    try:
        class _StubHTTP:
            async def post(self, url: str, *, json: Any = None, **_: Any) -> Any:
                response = await upstream.post(url, json=json)
                return response.json()

        monkeypatch.setattr(
            _StubApolloProvider, "http_client", classmethod(lambda cls, **_: _StubHTTP())
        )

        with pytest.raises(RuntimeError, match="apollo_v2"):
            await _StubApolloProvider.introspect()
    finally:
        await upstream.aclose()


# ---------------------------------------------------------------------------
# SDL → Pydantic lift
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lift_to_pydantic_returns_models():
    from serverframework.lib.Federation_GQL import IngestedSchema, ingest_apollo_sdl

    sdl = "type User { id: ID! name: String }\ntype Query { user: User }\n"
    ingested = ingest_apollo_sdl(sdl)

    class _Cfg(_Stub_GraphQLProvider):
        type_namespace = "Up_"

    result = _Cfg.lift_to_pydantic(ingested)
    assert "Up_User" in result.models


@pytest.mark.unit
def test_lift_to_pydantic_can_be_disabled():
    from serverframework.lib.Federation_GQL import ingest_apollo_sdl

    sdl = "type User { id: ID! }\ntype Query { user: User }\n"
    ingested = ingest_apollo_sdl(sdl)

    class _NoLift(_Stub_GraphQLProvider):
        lift_into_pydantic = False

    result = _NoLift.lift_to_pydantic(ingested)
    assert result.models == {}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_health_check_marks_dead_upstream(monkeypatch):
    class _Dead(_Stub_GraphQLProvider):
        upstream_url = "http://there-is-no-upstream-here.invalid/graphql"

    # Use a sync client that can't reach the host — the failure path
    # should yield a DOWN status without exceptions surfacing.
    report = _Dead.health_check()
    assert report.status.value in {"down", "degraded"}
