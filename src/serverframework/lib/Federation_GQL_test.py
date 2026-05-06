"""Tests for ``lib.Federation_GQL`` (Item 16 — GQL upstream federation).

Covers schema-stitching, Apollo Federation v2, selection-set push-down,
batched cross-subgraph resolution, per-request response cache, and the
SDL→Pydantic importer that lifts an upstream GraphQL into local Pydantic
models so the existing ``Pydantic2Strawberry``/``Pydantic2FastAPI``
pipelines project the upstream onto BOTH inbound surfaces.

No mocks: real HTTP transports use ``httpx.AsyncClient`` with an
in-process ASGI app as upstream; SDL processing is exercised against
actual ``graphql-core`` parsing/printing.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

import pytest

from serverframework.lib.Federation_GQL import (
    APOLLO_DIRECTIVES_PREAMBLE,
    APOLLO_SDL_QUERY,
    BatchedFieldResolver,
    FederatedSubgraph,
    GQLUpstreamTransport,
    INTROSPECTION_QUERY,
    IngestedSchema,
    MergedSchemaRegistry,
    ResponseCache,
    SchemaTransformer,
    bind_batched_resolver,
    bind_response_cache,
    build_proxy_resolver,
    build_query_document,
    cache_key,
    detect_federation_directives,
    get_persistent_cache,
    ingest_apollo_sdl,
    ingest_introspection,
    project_gql_as_rest,
    reconstruct_selection_set,
    reset_batched_resolver,
    reset_global_registry,
    reset_individual_semaphores,
    reset_response_cache,
    sdl_to_pydantic_models,
)


pytestmark = [pytest.mark.gql]


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _SelectedField:
    """Stand-in for Strawberry's ``SelectedField`` shape."""

    def __init__(
        self,
        name: str,
        selections: Optional[List["_SelectedField"]] = None,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.name = name
        self.selections = selections or []
        self.arguments = arguments or {}


@pytest.fixture(autouse=True)
def _reset_federation_state():
    reset_global_registry()
    reset_individual_semaphores()
    yield
    reset_global_registry()
    reset_individual_semaphores()


# ---------------------------------------------------------------------------
# Selection-set push-down
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_reconstruct_selection_set_flat():
    sel = reconstruct_selection_set([_SelectedField("id"), _SelectedField("name")])
    assert sel == "id name"


@pytest.mark.unit
def test_reconstruct_selection_set_nested():
    sel = reconstruct_selection_set(
        [
            _SelectedField("id"),
            _SelectedField(
                "address",
                selections=[_SelectedField("street"), _SelectedField("city")],
            ),
        ]
    )
    assert sel == "id address { street city }"


@pytest.mark.unit
def test_reconstruct_selection_set_skips_introspection():
    sel = reconstruct_selection_set(
        [_SelectedField("id"), _SelectedField("__typename")]
    )
    assert sel == "id"


@pytest.mark.unit
def test_build_query_document_renders_operation():
    doc = build_query_document(
        operation='customer(id: "abc")', selection_body="id name"
    )
    assert doc == 'query ProxyOp { customer(id: "abc") { id name } }'


@pytest.mark.unit
def test_build_query_document_supports_mutations():
    doc = build_query_document(
        operation="createOrder(input: {})",
        selection_body="id total",
        operation_type="mutation",
    )
    assert doc.startswith("mutation ProxyOp")


# ---------------------------------------------------------------------------
# Schema transformer pipeline
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_schema_transformer_prefix_renames_types():
    sdl = (
        "type User { id: ID! }\n"
        "type Query { user(id: ID!): User }\n"
    )
    transformed = SchemaTransformer(prefix="Stripe_").transform(sdl)
    assert "type Stripe_User" in transformed
    # References to the renamed type are also rewritten.
    assert "Stripe_User" in transformed
    # Roots keep their canonical name so the registry can fold them into
    # the merged schema's Query/Mutation.
    assert "type Query" in transformed


@pytest.mark.unit
def test_schema_transformer_explicit_rename_takes_precedence():
    sdl = "type Customer { id: ID! }\ntype Query { customer(id: ID!): Customer }\n"
    transformed = SchemaTransformer(
        rename={"Customer": "Stripe_Customer"}
    ).transform(sdl)
    assert "type Stripe_Customer" in transformed
    assert "Stripe_Customer" in transformed


@pytest.mark.unit
def test_schema_transformer_hides_fields():
    sdl = (
        "type User { id: ID! password: String email: String }\n"
        "type Query { user: User }\n"
    )
    transformed = SchemaTransformer(hide_fields={"User": {"password"}}).transform(sdl)
    assert "password" not in transformed
    assert "email" in transformed


@pytest.mark.unit
def test_schema_transformer_masks_arguments():
    sdl = (
        "type Query {\n"
        "  user(id: ID!, secretToken: String): String\n"
        "}\n"
    )
    transformed = SchemaTransformer(
        mask_arguments={"Query": {"user": {"secretToken"}}}
    ).transform(sdl)
    assert "secretToken" not in transformed
    assert "id: ID!" in transformed


@pytest.mark.unit
def test_schema_transformer_validates_output_is_parseable():
    from graphql import parse

    sdl = "type User { id: ID! }\ntype Query { user: User }\n"
    transformed = SchemaTransformer(prefix="X_").transform(sdl)
    parse(transformed)  # raises if invalid


# ---------------------------------------------------------------------------
# Apollo Federation v2 detection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_detect_federation_directives_extracts_keys():
    sdl = (
        'type Customer @key(fields: "id") {\n'
        "  id: ID!\n"
        "  email: String\n"
        "}\n"
        "type Query { customer(id: ID!): Customer }\n"
    )
    keys = detect_federation_directives(sdl)
    assert keys == {"Customer": ["id"]}


@pytest.mark.unit
def test_detect_federation_directives_handles_multiple_keys():
    sdl = (
        'type Customer @key(fields: "id") @key(fields: "email") {\n'
        "  id: ID!\n"
        "  email: String!\n"
        "}\n"
        "type Query { customer(id: ID!): Customer }\n"
    )
    keys = detect_federation_directives(sdl)
    assert keys == {"Customer": ["id", "email"]}


@pytest.mark.unit
def test_detect_federation_directives_empty_when_no_directive():
    sdl = "type User { id: ID! }\ntype Query { user: User }\n"
    assert detect_federation_directives(sdl) == {}


# ---------------------------------------------------------------------------
# Ingestion paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ingest_apollo_sdl_marks_apollo_v2():
    sdl = 'type Customer @key(fields: "id") { id: ID! }\ntype Query { customer: Customer }\n'
    ingested = ingest_apollo_sdl(sdl)
    assert ingested.apollo_v2 is True
    assert ingested.key_fields == {"Customer": ["id"]}
    assert isinstance(ingested.hash, str) and len(ingested.hash) == 64


@pytest.mark.unit
def test_ingest_introspection_round_trips_via_client_schema():
    from graphql import (
        GraphQLObjectType,
        GraphQLSchema,
        GraphQLString,
        GraphQLField,
        execute_sync,
        parse,
    )

    user = GraphQLObjectType(
        "User",
        {"id": GraphQLField(GraphQLString), "name": GraphQLField(GraphQLString)},
    )
    query = GraphQLObjectType(
        "Query",
        {"hello": GraphQLField(GraphQLString, resolve=lambda *a: "hi"),
         "user": GraphQLField(user, resolve=lambda *a: {"id": "1", "name": "n"})},
    )
    schema = GraphQLSchema(query=query)
    response = execute_sync(schema, parse(INTROSPECTION_QUERY))
    ingested = ingest_introspection(response.data)
    assert ingested.apollo_v2 is False
    assert "type User" in ingested.sdl
    assert "type Query" in ingested.sdl


# ---------------------------------------------------------------------------
# MergedSchemaRegistry
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_registry_merges_disjoint_subgraphs():
    reg = MergedSchemaRegistry()
    reg.register(
        name="stripe",
        ingested=ingest_apollo_sdl(
            'type Customer @key(fields: "id") { id: ID! email: String }\n'
            "type Query { customer(id: ID!): Customer }\n"
        ),
        style="apollo_v2",
    )
    reg.register(
        name="shop",
        ingested=ingest_apollo_sdl(
            "type Order { id: ID! total: Float }\n"
            "type Query { order(id: ID!): Order }\n"
        ),
        style="stitching",
    )
    schema = reg.build()
    assert schema.query_type is not None
    assert "customer" in schema.query_type.fields
    assert "order" in schema.query_type.fields
    sdl = reg.merged_sdl()
    assert "type Customer" in sdl
    assert "type Order" in sdl


@pytest.mark.unit
def test_registry_detects_root_field_collisions():
    reg = MergedSchemaRegistry()
    reg.register(
        name="a",
        ingested=ingest_apollo_sdl(
            "type Order { id: ID! }\ntype Query { order(id: ID!): Order }\n"
        ),
        style="stitching",
    )
    reg.register(
        name="b",
        ingested=ingest_apollo_sdl(
            "type OrderB { id: ID! }\ntype Query { order(id: ID!): OrderB }\n"
        ),
        style="stitching",
    )
    with pytest.raises(ValueError, match="collision on 'order'"):
        reg.build()


@pytest.mark.unit
def test_registry_invalidates_on_register():
    reg = MergedSchemaRegistry()
    reg.register(
        name="a",
        ingested=ingest_apollo_sdl(
            "type Foo { id: ID! }\ntype Query { foo(id: ID!): Foo }\n"
        ),
        style="stitching",
    )
    reg.build()
    first_hash = reg.merged_hash()
    reg.register(
        name="b",
        ingested=ingest_apollo_sdl(
            "type Bar { id: ID! }\ntype Query { bar(id: ID!): Bar }\n"
        ),
        style="stitching",
    )
    reg.build()
    assert reg.merged_hash() != first_hash


@pytest.mark.unit
def test_registry_listener_fires_with_diff():
    reg = MergedSchemaRegistry()
    diffs: List[Dict[str, str]] = []
    reg.add_listener(lambda schema, diff: diffs.append(diff))
    reg.register(
        name="a",
        ingested=ingest_apollo_sdl(
            "type Foo { id: ID! }\ntype Query { foo: Foo }\n"
        ),
        style="stitching",
    )
    reg.build()
    assert diffs[-1] == {"a": "added"}
    reg.unregister("a")
    reg.build()
    assert diffs[-1] == {"a": "removed"}


@pytest.mark.unit
def test_registry_unknown_style_rejected():
    reg = MergedSchemaRegistry()
    with pytest.raises(ValueError, match="Unknown federation style"):
        reg.register(
            name="x",
            ingested=ingest_apollo_sdl("type Query { _empty: Boolean }\n"),
            style="bogus",
        )


# ---------------------------------------------------------------------------
# Per-request response cache
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cache_key_partitions_by_credentials():
    a = cache_key("query { x }", None, "credA")
    b = cache_key("query { x }", None, "credB")
    assert a != b


@pytest.mark.unit
def test_cache_key_partitions_by_variables():
    a = cache_key("query ($id: ID!){ x(id: $id) }", {"id": "1"}, "")
    b = cache_key("query ($id: ID!){ x(id: $id) }", {"id": "2"}, "")
    assert a != b


@pytest.mark.unit
def test_response_cache_get_set():
    cache = ResponseCache()
    hit, _ = cache.get("k")
    assert hit is False
    cache.set("k", "v")
    hit, value = cache.get("k")
    assert hit is True and value == "v"


@pytest.mark.unit
async def test_response_cache_dedupes_pending_calls():
    cache = ResponseCache()
    loop = asyncio.get_event_loop()
    fut1, owner1 = cache.get_or_register_pending("k", lambda: loop.create_future())
    fut2, owner2 = cache.get_or_register_pending("k", lambda: loop.create_future())
    assert owner1 is True and owner2 is False
    assert fut1 is fut2
    cache.resolve_pending("k", "result")
    assert await fut1 == "result"
    assert await fut2 == "result"


@pytest.mark.unit
async def test_response_cache_propagates_exceptions():
    cache = ResponseCache()
    loop = asyncio.get_event_loop()
    fut, _ = cache.get_or_register_pending("k", lambda: loop.create_future())
    cache.fail_pending("k", RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        await fut


@pytest.mark.unit
def test_persistent_cache_respects_ttl():
    import time as _time

    cache = get_persistent_cache()
    cache.set("ttl_key", "v", ttl_seconds=0.05)
    hit, value = cache.get("ttl_key")
    assert hit and value == "v"
    _time.sleep(0.1)
    hit, _ = cache.get("ttl_key")
    assert hit is False


# ---------------------------------------------------------------------------
# Batched field resolver
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_batched_resolver_collapses_concurrent_calls():
    upstream_calls: List[str] = []

    async def transport(query: str, variables: Any = None) -> Any:
        upstream_calls.append(query)
        return {
            "data": {
                "customer": [
                    {"id": "A", "name": "Alpha"},
                    {"id": "B", "name": "Beta"},
                ]
            }
        }

    resolver = BatchedFieldResolver()
    a, b = await asyncio.gather(
        resolver.resolve(
            provider_key="stripe",
            operation="customer",
            key="A",
            selection_body="id name",
            transport=transport,
            list_by_id_arg="ids",
        ),
        resolver.resolve(
            provider_key="stripe",
            operation="customer",
            key="B",
            selection_body="id name",
            transport=transport,
            list_by_id_arg="ids",
        ),
    )
    assert a == {"id": "A", "name": "Alpha"}
    assert b == {"id": "B", "name": "Beta"}
    assert len(upstream_calls) == 1, "Expected exactly one batched upstream call"


@pytest.mark.unit
async def test_batched_resolver_falls_back_to_individual_calls():
    upstream_calls: List[str] = []

    async def transport(query: str, variables: Any = None) -> Any:
        upstream_calls.append(query)
        return {"data": {"customer": [{"id": "X", "name": "result"}]}}

    resolver = BatchedFieldResolver(max_concurrency=2)
    result = await resolver.resolve(
        provider_key="stripe",
        operation="customer",
        key="X",
        selection_body="id name",
        transport=transport,
        list_by_id_arg=None,  # Upstream does NOT support list-by-id
    )
    assert result == {"id": "X", "name": "result"}
    assert len(upstream_calls) == 1


@pytest.mark.unit
async def test_batched_resolver_uses_response_cache():
    upstream_calls: List[str] = []

    async def transport(query: str, variables: Any = None) -> Any:
        upstream_calls.append(query)
        return {"data": {"customer": [{"id": "A", "name": "x"}]}}

    cache = ResponseCache()
    token = bind_response_cache(cache)
    try:
        resolver = BatchedFieldResolver()
        first = await resolver.resolve(
            provider_key="stripe",
            operation="customer",
            key="A",
            selection_body="id name",
            transport=transport,
            list_by_id_arg="ids",
            requester_credentials="cred1",
        )
        second = await resolver.resolve(
            provider_key="stripe",
            operation="customer",
            key="A",
            selection_body="id name",
            transport=transport,
            list_by_id_arg="ids",
            requester_credentials="cred1",
        )
        assert first == second == {"id": "A", "name": "x"}
        # The second resolve hits the per-request cache populated during the
        # first batch; no new upstream call is issued.
        assert len(upstream_calls) == 1
    finally:
        reset_response_cache(token)


@pytest.mark.unit
async def test_batched_resolver_propagates_transport_failure():
    async def transport(query: str, variables: Any = None) -> Any:
        raise RuntimeError("upstream down")

    resolver = BatchedFieldResolver()
    with pytest.raises(RuntimeError, match="upstream down"):
        await asyncio.gather(
            resolver.resolve(
                provider_key="stripe",
                operation="customer",
                key="A",
                selection_body="id",
                transport=transport,
                list_by_id_arg="ids",
            )
        )


@pytest.mark.unit
def test_batched_resolver_contextvar_binding():
    resolver = BatchedFieldResolver()
    token = bind_batched_resolver(resolver)
    try:
        from serverframework.lib.Federation_GQL import get_batched_resolver

        assert get_batched_resolver() is resolver
    finally:
        reset_batched_resolver(token)


# ---------------------------------------------------------------------------
# SDL → Pydantic
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sdl_to_pydantic_basic_types():
    sdl = (
        "type User {\n"
        "  id: ID!\n"
        "  name: String!\n"
        "  age: Int\n"
        "  active: Boolean\n"
        "  score: Float\n"
        "}\n"
        "type Query { user: User }\n"
    )
    result = sdl_to_pydantic_models(sdl)
    user = result.models["User"]
    fields = user.model_fields
    assert fields["id"].annotation is str
    assert fields["name"].annotation is str
    # Nullable fields wrap in Optional[...]
    from typing import get_args, get_origin, Union

    age_ann = fields["age"].annotation
    assert get_origin(age_ann) is Union
    assert int in get_args(age_ann)


@pytest.mark.unit
def test_sdl_to_pydantic_prefixes_types():
    sdl = "type User { id: ID! }\ntype Query { user: User }\n"
    result = sdl_to_pydantic_models(sdl, prefix="Acme_")
    assert "Acme_User" in result.models
    assert "User" not in result.models


@pytest.mark.unit
def test_sdl_to_pydantic_handles_enums():
    from enum import Enum

    sdl = (
        "enum Status { ACTIVE INACTIVE }\n"
        "type User { id: ID! status: Status }\n"
        "type Query { user: User }\n"
    )
    result = sdl_to_pydantic_models(sdl)
    assert "Status" in result.enums
    assert issubclass(result.enums["Status"], Enum)


@pytest.mark.unit
def test_sdl_to_pydantic_handles_lists():
    from typing import List, get_args, get_origin

    sdl = (
        "type User { id: ID! tags: [String!]! }\n"
        "type Query { users: [User!]! }\n"
    )
    result = sdl_to_pydantic_models(sdl)
    user = result.models["User"]
    tags_ann = user.model_fields["tags"].annotation
    assert get_origin(tags_ann) is list


@pytest.mark.unit
def test_sdl_to_pydantic_imports_input_types():
    sdl = (
        "input UserInput { name: String! age: Int }\n"
        "type User { id: ID! name: String! }\n"
        "type Mutation { createUser(input: UserInput!): User }\n"
    )
    result = sdl_to_pydantic_models(sdl)
    assert "UserInput" in result.inputs
    assert "User" in result.models


@pytest.mark.unit
def test_sdl_to_pydantic_skips_root_types():
    sdl = "type User { id: ID! }\ntype Query { user: User }\n"
    result = sdl_to_pydantic_models(sdl)
    assert "Query" not in result.models
    assert "Mutation" not in result.models


# ---------------------------------------------------------------------------
# build_proxy_resolver
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_proxy_resolver_pushes_down_selection_set():
    captured: List[str] = []

    async def transport(query: str, variables: Any = None) -> Any:
        captured.append(query)
        return {"data": {"customer": {"id": "1", "name": "n"}}}

    resolver = build_proxy_resolver(operation="customer", transport=transport)

    class _Info:
        selected_fields = [_SelectedField("id"), _SelectedField("name")]

    result = await resolver(_Info(), id="1")
    assert result == {"id": "1", "name": "n"}
    assert "customer(id: " in captured[0]
    assert "id name" in captured[0]


@pytest.mark.unit
async def test_proxy_resolver_dedupes_via_response_cache():
    upstream_calls = 0

    async def transport(query: str, variables: Any = None) -> Any:
        nonlocal upstream_calls
        upstream_calls += 1
        return {"data": {"customer": {"id": "1"}}}

    cache = ResponseCache()
    token = bind_response_cache(cache)
    try:
        resolver = build_proxy_resolver(operation="customer", transport=transport)

        class _Info:
            selected_fields = [_SelectedField("id")]

        a = await resolver(_Info(), id="1")
        b = await resolver(_Info(), id="1")
        assert a == b == {"id": "1"}
        assert upstream_calls == 1
    finally:
        reset_response_cache(token)


# ---------------------------------------------------------------------------
# GQL → REST projection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_project_gql_as_rest_mounts_routes_for_each_root_field():
    sdl = (
        "type User { id: ID! name: String }\n"
        "type Query { user(id: ID!): User users: [User!]! }\n"
        "type Mutation { createUser(name: String!): User }\n"
    )
    ingested = ingest_apollo_sdl(sdl)

    async def transport(query: str, variables: Any = None) -> Any:
        return {"data": {"user": {"id": "1", "name": "x"}}}

    reg = MergedSchemaRegistry()
    sg = reg.register(
        name="t", ingested=ingested, style="stitching", transport=transport
    )
    router = project_gql_as_rest(subgraph=sg, transport=transport, prefix="/upstream")
    paths = sorted({r.path for r in router.routes})
    assert "/upstream/user" in paths
    assert "/upstream/users" in paths
    assert "/upstream/createUser" in paths
    methods_for_create = next(
        r.methods for r in router.routes if r.path == "/upstream/createUser"
    )
    assert "POST" in methods_for_create


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_gql_upstream_transport_sends_query_in_body():
    captured: Dict[str, Any] = {}

    class _FakeHTTP:
        async def post(self, url: str, **kwargs: Any) -> Any:
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return {"data": {"x": 1}}

    transport = GQLUpstreamTransport(_FakeHTTP(), "https://upstream/graphql")
    result = await transport.send(query="{ x }", variables={"y": 2})
    assert result == {"data": {"x": 1}}
    assert captured["url"] == "https://upstream/graphql"
    assert captured["json"] == {"query": "{ x }", "variables": {"y": 2}}


@pytest.mark.unit
def test_gql_upstream_transport_sync_path():
    captured: Dict[str, Any] = {}

    class _FakeHTTP:
        def post(self, url: str, **kwargs: Any) -> Any:
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return {"data": {"x": 2}}

    transport = GQLUpstreamTransport(_FakeHTTP(), "https://upstream/graphql")
    result = transport.send_sync(query="{ x }")
    assert result == {"data": {"x": 2}}
    assert captured["json"] == {"query": "{ x }"}
