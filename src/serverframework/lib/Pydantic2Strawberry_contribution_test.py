"""Tests for the Item 46 GraphQL composition contract.

Covers the contribution registry, three-stage collision resolution, custom
type registration, federation directive attachment, the per-request
DataLoader, and rebuild-on-mutation diff logging. No mocks: real
``GraphQLContributionRegistry`` instances and real ``RequestDataLoader``
fires drive the assertions.
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional

import pytest
import strawberry

from serverframework.lib.Pydantic2Strawberry import (
    DataLoaderSpec,
    FederationDirective,
    FieldContribution,
    FieldKind,
    GraphQLCompositionCollisionError,
    GraphQLContributionRegistry,
    RequestDataLoader,
    TypeContribution,
    _diff_signatures,
    build_request_dataloaders,
    gql_contribution_registry,
    gql_mutation,
    gql_query,
    gql_subscription,
    gql_type,
    register_dataloader,
    reset_gql_contribution_registry,
)


# ---------------------------------------------------------------------------
# Registry: three-stage collision rule
# ---------------------------------------------------------------------------


def _query_contribution(
    extension: str,
    name: str,
    resolver: Any,
    return_type: Any = str,
    args: Optional[dict] = None,
    namespace: bool = False,
) -> FieldContribution:
    return FieldContribution(
        extension_name=extension,
        kind=FieldKind.QUERY,
        name=name,
        resolver=resolver,
        return_type=return_type,
        args=dict(args or {}),
        namespace=namespace,
    )


def test_single_contribution_resolves_as_winner() -> None:
    reg = GraphQLContributionRegistry()
    fn = lambda info: "ok"  # noqa: E731
    reg.register_field(_query_contribution("ext_a", "search", fn))
    winners = reg.resolve_fields(FieldKind.QUERY)
    assert "search" in winners
    assert winners["search"].extension_name == "ext_a"


def test_identical_contributions_merge_to_one() -> None:
    reg = GraphQLContributionRegistry()
    fn = lambda info: "ok"  # noqa: E731
    reg.register_field(_query_contribution("ext_a", "search", fn))
    reg.register_field(_query_contribution("ext_b", "search", fn))
    winners = reg.resolve_fields(FieldKind.QUERY)
    assert len(winners) == 1
    assert winners["search"].resolver is fn


def test_non_identical_contributions_collide() -> None:
    reg = GraphQLContributionRegistry()
    reg.register_field(_query_contribution("ext_a", "search", lambda info: "a"))
    reg.register_field(_query_contribution("ext_b", "search", lambda info: "b"))
    with pytest.raises(GraphQLCompositionCollisionError) as excinfo:
        reg.resolve_fields(FieldKind.QUERY)
    msg = str(excinfo.value)
    assert "ext_a" in msg and "ext_b" in msg
    assert "search" in msg


def test_namespacing_bypasses_collision() -> None:
    reg = GraphQLContributionRegistry()
    reg.register_field(
        _query_contribution("ext_a", "search", lambda info: "a", namespace=True)
    )
    reg.register_field(
        _query_contribution("ext_b", "search", lambda info: "b", namespace=True)
    )
    winners = reg.resolve_fields(FieldKind.QUERY)
    assert "ext_a_search" in winners
    assert "ext_b_search" in winners


def test_mutation_collision_independent_from_query() -> None:
    reg = GraphQLContributionRegistry()
    fn_q = lambda info: "q"  # noqa: E731
    fn_m = lambda info: "m"  # noqa: E731
    reg.register_field(
        FieldContribution("ext_a", FieldKind.QUERY, "transfer", fn_q, str)
    )
    reg.register_field(
        FieldContribution("ext_a", FieldKind.MUTATION, "transfer", fn_m, str)
    )
    # Same name on different kinds is not a collision.
    assert "transfer" in reg.resolve_fields(FieldKind.QUERY)
    assert "transfer" in reg.resolve_fields(FieldKind.MUTATION)


# ---------------------------------------------------------------------------
# Custom types + federation directives
# ---------------------------------------------------------------------------


def test_custom_type_registration_and_resolve() -> None:
    reg = GraphQLContributionRegistry()

    @strawberry.type
    class Aggregate:
        total: int

    reg.register_type(TypeContribution("ext_a", Aggregate))
    winners = reg.resolve_types()
    assert "Aggregate" in winners
    assert winners["Aggregate"].type_class is Aggregate


def test_type_collision_distinct_classes_raises() -> None:
    reg = GraphQLContributionRegistry()

    @strawberry.type
    class Aggregate:
        total: int

    @strawberry.type
    class Aggregate2:  # different class, same emitted name via name override
        total: int

    reg.register_type(TypeContribution("ext_a", Aggregate, name="Aggregate"))
    reg.register_type(TypeContribution("ext_b", Aggregate2, name="Aggregate"))
    with pytest.raises(GraphQLCompositionCollisionError):
        reg.resolve_types()


def test_type_namespacing_isolates_emitted_name() -> None:
    reg = GraphQLContributionRegistry()

    @strawberry.type
    class Aggregate:
        total: int

    reg.register_type(TypeContribution("ext_a", Aggregate, namespace=True))
    reg.register_type(TypeContribution("ext_b", Aggregate, namespace=True))
    winners = reg.resolve_types()
    # Same class accepted twice would merge; namespacing keeps them apart.
    assert set(winners.keys()) == {"Ext_aAggregate", "Ext_bAggregate"}


def test_invalid_federation_directive_rejected_at_registration() -> None:
    reg = GraphQLContributionRegistry()

    @strawberry.type
    class Aggregate:
        total: int

    with pytest.raises(ValueError, match="Unsupported federation directive"):
        reg.register_type(
            TypeContribution(
                "ext_a",
                Aggregate,
                federation_directives=(FederationDirective(name="bogus"),),
            )
        )


def test_supported_federation_directives_accepted() -> None:
    reg = GraphQLContributionRegistry()
    for directive in ("key", "external", "requires", "provides", "shareable",
                      "inaccessible", "override", "tag"):
        @strawberry.type
        class _T:
            id: str
        reg.register_type(
            TypeContribution(
                "ext_a",
                _T,
                name=f"T_{directive}",
                federation_directives=(
                    FederationDirective(name=directive, args={"fields": "id"}),
                ),
            )
        )
    assert len(reg.resolve_types()) == 8


# ---------------------------------------------------------------------------
# DataLoader: per-request batching
# ---------------------------------------------------------------------------


def test_dataloader_batches_parallel_loads() -> None:
    call_log: List[List[Any]] = []

    def batch_load_fn(keys):
        call_log.append(list(keys))
        return [{"id": k, "value": k * 2} for k in keys]

    async def run():
        loader = RequestDataLoader(batch_load_fn)
        # Three parallel loads should collapse into a single batch.
        results = await asyncio.gather(loader.load(1), loader.load(2), loader.load(3))
        return results

    results = asyncio.run(run())
    assert len(call_log) == 1
    assert call_log[0] == [1, 2, 3]
    assert results == [{"id": 1, "value": 2}, {"id": 2, "value": 4}, {"id": 3, "value": 6}]


def test_dataloader_async_batch_fn_supported() -> None:
    async def batch_load_fn(keys):
        await asyncio.sleep(0)
        return [str(k) for k in keys]

    async def run():
        loader = RequestDataLoader(batch_load_fn)
        return await asyncio.gather(loader.load(10), loader.load(20))

    assert asyncio.run(run()) == ["10", "20"]


def test_dataloader_dedupes_repeated_keys() -> None:
    calls: List[List[Any]] = []

    def batch_load_fn(keys):
        calls.append(list(keys))
        return [k * 100 for k in keys]

    async def run():
        loader = RequestDataLoader(batch_load_fn)
        a, b = await asyncio.gather(loader.load(7), loader.load(7))
        return a, b

    a, b = asyncio.run(run())
    assert a == 700 and b == 700
    assert calls == [[7]]


def test_dataloader_propagates_batch_exception() -> None:
    def batch_load_fn(keys):
        raise RuntimeError("batch failed")

    async def run():
        loader = RequestDataLoader(batch_load_fn)
        with pytest.raises(RuntimeError, match="batch failed"):
            await loader.load("k")

    asyncio.run(run())


def test_dataloader_length_mismatch_raises() -> None:
    def batch_load_fn(keys):
        return [1]  # too short

    async def run():
        loader = RequestDataLoader(batch_load_fn)
        with pytest.raises(ValueError, match="lengths must match"):
            await asyncio.gather(loader.load("a"), loader.load("b"))

    asyncio.run(run())


def test_dataloader_non_sequence_return_raises() -> None:
    def batch_load_fn(keys):
        return {"a": 1}  # not a sequence

    async def run():
        loader = RequestDataLoader(batch_load_fn)
        with pytest.raises(TypeError, match="must return a sequence"):
            await loader.load("a")

    asyncio.run(run())


def test_dataloader_global_collision_on_distinct_fn() -> None:
    reg = GraphQLContributionRegistry()
    reg.register_dataloader(DataLoaderSpec("ext_a", "users", lambda ks: list(ks)))
    with pytest.raises(GraphQLCompositionCollisionError):
        reg.register_dataloader(DataLoaderSpec("ext_b", "users", lambda ks: list(ks)))


def test_dataloader_same_fn_re_registration_idempotent() -> None:
    reg = GraphQLContributionRegistry()
    fn = lambda ks: list(ks)  # noqa: E731
    reg.register_dataloader(DataLoaderSpec("ext_a", "users", fn))
    # Re-registering the SAME function from a different extension is allowed
    # (single source of truth, multiple consumers).
    reg.register_dataloader(DataLoaderSpec("ext_b", "users", fn))
    assert reg.dataloaders()["users"].batch_load_fn is fn


def test_build_request_dataloaders_returns_fresh_per_call() -> None:
    reg = GraphQLContributionRegistry()
    reg.register_dataloader(DataLoaderSpec("ext_a", "users", lambda ks: list(ks)))
    a = build_request_dataloaders(reg)
    b = build_request_dataloaders(reg)
    assert a["users"] is not b["users"]


# ---------------------------------------------------------------------------
# Rebuild subscription + diff
# ---------------------------------------------------------------------------


def test_register_field_notifies_subscriber() -> None:
    reg = GraphQLContributionRegistry()
    notifications: List[bool] = []
    reg.subscribe_rebuild(lambda: notifications.append(True))
    reg.register_field(_query_contribution("ext_a", "search", lambda info: "ok"))
    assert notifications == [True]


def test_suspend_defers_notifications_until_resume() -> None:
    reg = GraphQLContributionRegistry()
    notifications: List[bool] = []
    reg.subscribe_rebuild(lambda: notifications.append(True))
    reg.suspend()
    reg.register_field(_query_contribution("ext_a", "a", lambda info: "1"))
    reg.register_field(_query_contribution("ext_b", "b", lambda info: "2"))
    assert notifications == []
    reg.resume()
    assert notifications == [True]


def test_unsubscribe_rebuild_stops_notifications() -> None:
    reg = GraphQLContributionRegistry()
    notifications: List[int] = []

    def cb() -> None:
        notifications.append(1)

    reg.subscribe_rebuild(cb)
    reg.register_field(_query_contribution("ext_a", "a", lambda info: "1"))
    reg.unsubscribe_rebuild(cb)
    reg.register_field(_query_contribution("ext_b", "b", lambda info: "2"))
    assert notifications == [1]


def test_diff_signatures_reports_added_and_removed() -> None:
    prev = (("a",), ("m1",), (), ("T1",))
    curr = (("a", "b"), (), ("s1",), ("T2",))
    added, removed = _diff_signatures(prev, curr)
    assert added == {"query:b", "subscription:s1", "type:T2"}
    assert removed == {"mutation:m1", "type:T1"}


# ---------------------------------------------------------------------------
# Decorators wire into the global registry
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_global() -> None:
    reset_gql_contribution_registry()
    yield
    reset_gql_contribution_registry()


def test_gql_query_decorator_registers_globally() -> None:
    reset_gql_contribution_registry()

    @gql_query(name="hello", return_type=str, extension_name="demo")
    def _hello(info) -> str:
        return "hi"

    fields = gql_contribution_registry().fields(FieldKind.QUERY)
    assert "hello" in fields
    assert fields["hello"][0].extension_name == "demo"


def test_gql_mutation_and_subscription_decorators_register() -> None:
    reset_gql_contribution_registry()

    @gql_mutation(name="ping", return_type=bool, extension_name="demo")
    def _ping(info) -> bool:
        return True

    @gql_subscription(name="tick", return_type=str, extension_name="demo")
    async def _tick(info):
        yield "tick"

    reg = gql_contribution_registry()
    assert "ping" in reg.fields(FieldKind.MUTATION)
    assert "tick" in reg.fields(FieldKind.SUBSCRIPTION)


def test_gql_type_decorator_registers_globally() -> None:
    reset_gql_contribution_registry()

    @gql_type(extension_name="demo")
    @strawberry.type
    class Aggregate:
        total: int

    types = gql_contribution_registry().types()
    assert "Aggregate" in types


def test_register_dataloader_helper_records_spec() -> None:
    reset_gql_contribution_registry()
    register_dataloader(
        "ext_users",
        lambda keys: list(keys),
        extension_name="demo",
        description="batch user lookups",
    )
    assert "ext_users" in gql_contribution_registry().dataloaders()
