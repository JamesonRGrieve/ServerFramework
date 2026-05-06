"""Tests for the @custom_route contract (Item 40)."""

from __future__ import annotations

import asyncio
import dataclasses

import pytest
from pydantic import BaseModel

from serverframework.lib.CustomRoute import (
    AbstractActionEndpoint,
    CustomRouteSpec,
    ExposeIn,
    _infer_graphql_kind,
    custom_route,
    get_custom_route_spec,
    iter_custom_routes,
    register_custom_routes_to_graphql,
    reset_graphql_registrations,
)
from serverframework.lib.Pydantic2Strawberry import (
    FieldKind,
    GraphQLContributionRegistry,
)


class PromoteIn(BaseModel):
    target_id: str
    role: str


class PromoteOut(BaseModel):
    ok: bool
    new_role: str


class DemoteIn(BaseModel):
    target_id: str


class DemoteOut(BaseModel):
    ok: bool


class FetchOut(BaseModel):
    items: list[str]


def test_custom_route_attaches_spec():
    @custom_route(
        method="POST",
        path="/promote",
        input_model=PromoteIn,
        output_model=PromoteOut,
    )
    def promote(self, body: PromoteIn) -> PromoteOut:
        return PromoteOut(ok=True, new_role=body.role)

    spec = get_custom_route_spec(promote)
    assert spec is not None
    assert spec.method == "POST"
    assert spec.path == "/promote"
    assert spec.input_model is PromoteIn
    assert spec.output_model is PromoteOut
    assert spec.authentication_type == "session"
    assert spec.expose_in == frozenset({ExposeIn.ALL})


def test_missing_output_model_raises():
    with pytest.raises(ValueError, match="output_model is required"):
        @custom_route(method="POST", path="/x", input_model=PromoteIn)
        def bad(self, body):
            return None


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
def test_non_get_missing_input_model_raises(method):
    with pytest.raises(ValueError, match="requires an input_model"):
        @custom_route(method=method, path="/x", output_model=PromoteOut)
        def bad(self):
            return None


def test_get_without_input_model_is_ok():
    @custom_route(method="GET", path="/things", output_model=FetchOut)
    def fetch(self):
        return FetchOut(items=[])

    spec = get_custom_route_spec(fetch)
    assert spec is not None
    assert spec.method == "GET"
    assert spec.input_model is None


def test_delete_without_input_model_is_ok():
    @custom_route(method="DELETE", path="/things/{id}", output_model=FetchOut)
    def remove(self, id: str):
        return FetchOut(items=[])

    spec = get_custom_route_spec(remove)
    assert spec is not None
    assert spec.method == "DELETE"
    assert spec.input_model is None


def test_method_is_uppercased():
    @custom_route(method="post", path="/x", input_model=PromoteIn, output_model=PromoteOut)
    def lower(self, body):
        return None

    assert get_custom_route_spec(lower).method == "POST"


def test_iter_custom_routes_walks_class_sorted():
    class FakeManager:
        @custom_route(
            method="POST",
            path="/promote",
            input_model=PromoteIn,
            output_model=PromoteOut,
        )
        def promote(self, body):
            return None

        @custom_route(
            method="POST",
            path="/demote",
            input_model=DemoteIn,
            output_model=DemoteOut,
        )
        def demote(self, body):
            return None

        @custom_route(method="GET", path="/fetch", output_model=FetchOut)
        def fetch(self):
            return None

        def untagged(self):
            return None

    pairs = iter_custom_routes(FakeManager)
    names = [name for name, _ in pairs]
    assert names == ["demote", "fetch", "promote"]
    assert all(isinstance(spec, CustomRouteSpec) for _, spec in pairs)


def test_iter_custom_routes_empty_class():
    class Empty:
        pass

    assert iter_custom_routes(Empty) == []


def test_expose_in_str_enum_round_trip():
    assert ExposeIn.REST == "rest"
    assert ExposeIn.SDK == "sdk"
    assert ExposeIn.GRAPHQL == "graphql"
    assert ExposeIn.ALL == "all"
    assert ExposeIn("rest") is ExposeIn.REST
    assert ExposeIn("all") is ExposeIn.ALL
    assert str(ExposeIn.REST.value) == "rest"


def test_custom_route_spec_is_frozen():
    spec = CustomRouteSpec(
        method="GET",
        path="/x",
        input_model=None,
        output_model=FetchOut,
    )
    assert dataclasses.is_dataclass(spec)
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.method = "POST"  # type: ignore[misc]


def test_custom_route_full_options():
    @custom_route(
        method="POST",
        path="/promote",
        input_model=PromoteIn,
        output_model=PromoteOut,
        authentication_type="api_key",
        openapi_tags=["admin", "users"],
        expose_in=[ExposeIn.REST, ExposeIn.SDK],
        graphql_kind="mutation",
        summary="Promote a user",
        description="Promotes a user to a higher role.",
    )
    def promote(self, body):
        return None

    spec = get_custom_route_spec(promote)
    assert spec.authentication_type == "api_key"
    assert spec.openapi_tags == ("admin", "users")
    assert spec.expose_in == frozenset({ExposeIn.REST, ExposeIn.SDK})
    assert spec.graphql_kind == "mutation"
    assert spec.summary == "Promote a user"
    assert spec.description == "Promotes a user to a higher role."


def test_abstract_action_endpoint_supports_decorator():
    class PromoteAction(AbstractActionEndpoint):
        prefix = "/v1/actions"

        @custom_route(
            method="POST",
            path="/promote",
            input_model=PromoteIn,
            output_model=PromoteOut,
        )
        def run(self, body):
            return PromoteOut(ok=True, new_role=body.role)

    pairs = iter_custom_routes(PromoteAction)
    assert len(pairs) == 1
    assert pairs[0][0] == "run"
    assert pairs[0][1].path == "/promote"


def test_get_custom_route_spec_returns_none_for_undecorated():
    def plain():
        return None

    assert get_custom_route_spec(plain) is None


# ---------------------------------------------------------------------------
# GraphQL emission (Item 40 GraphQL half)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_graphql_registrations():
    """Each GraphQL test gets a fresh registration cache so manager classes
    declared inside one test don't leak into another."""
    reset_graphql_registrations()
    yield
    reset_graphql_registrations()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_infer_graphql_kind_get_is_query():
    spec = CustomRouteSpec(
        method="GET",
        path="/x",
        input_model=None,
        output_model=FetchOut,
    )
    assert _infer_graphql_kind(spec) == "query"


@pytest.mark.parametrize("verb", ["POST", "PUT", "PATCH", "DELETE"])
def test_infer_graphql_kind_non_get_is_mutation(verb):
    spec = CustomRouteSpec(
        method=verb,
        path="/x",
        input_model=PromoteIn if verb != "DELETE" else None,
        output_model=PromoteOut,
    )
    assert _infer_graphql_kind(spec) == "mutation"


def test_infer_graphql_kind_explicit_override_wins():
    spec = CustomRouteSpec(
        method="POST",
        path="/x",
        input_model=PromoteIn,
        output_model=PromoteOut,
        graphql_kind="query",
    )
    assert _infer_graphql_kind(spec) == "query"


def test_infer_graphql_kind_invalid_override_raises():
    spec = CustomRouteSpec(
        method="POST",
        path="/x",
        input_model=PromoteIn,
        output_model=PromoteOut,
        graphql_kind="subscription",
    )
    with pytest.raises(ValueError, match="Invalid graphql_kind"):
        _infer_graphql_kind(spec)


def test_register_post_emits_mutation():
    class ManagerA:
        @custom_route(
            method="POST",
            path="/promote",
            input_model=PromoteIn,
            output_model=PromoteOut,
        )
        def promote(self, body):
            return PromoteOut(ok=True, new_role=body.role)

    reg = GraphQLContributionRegistry()
    n = register_custom_routes_to_graphql(ManagerA, contribution_registry=reg)
    assert n == 1

    mutations = reg.resolve_fields(FieldKind.MUTATION)
    queries = reg.resolve_fields(FieldKind.QUERY)
    assert "promote" in mutations
    assert "promote" not in queries
    assert mutations["promote"].return_type is PromoteOut
    assert mutations["promote"].args == {"input": PromoteIn}


def test_register_get_emits_query():
    class ManagerB:
        @custom_route(method="GET", path="/items", output_model=FetchOut)
        def list_items(self):
            return FetchOut(items=["a", "b"])

    reg = GraphQLContributionRegistry()
    register_custom_routes_to_graphql(ManagerB, contribution_registry=reg)
    queries = reg.resolve_fields(FieldKind.QUERY)
    assert "list_items" in queries
    assert queries["list_items"].return_type is FetchOut
    assert queries["list_items"].args == {}


def test_explicit_graphql_kind_query_for_post():
    class ManagerC:
        @custom_route(
            method="POST",
            path="/search",
            input_model=PromoteIn,
            output_model=FetchOut,
            graphql_kind="query",
        )
        def search(self, body):
            return FetchOut(items=[body.role])

    reg = GraphQLContributionRegistry()
    register_custom_routes_to_graphql(ManagerC, contribution_registry=reg)
    assert "search" in reg.resolve_fields(FieldKind.QUERY)
    assert "search" not in reg.resolve_fields(FieldKind.MUTATION)


def test_expose_in_excluding_graphql_skips_registration():
    class ManagerD:
        @custom_route(
            method="POST",
            path="/promote",
            input_model=PromoteIn,
            output_model=PromoteOut,
            expose_in=[ExposeIn.REST, ExposeIn.SDK],
        )
        def promote(self, body):
            return None

    reg = GraphQLContributionRegistry()
    n = register_custom_routes_to_graphql(ManagerD, contribution_registry=reg)
    assert n == 0
    assert reg.resolve_fields(FieldKind.MUTATION) == {}


def test_expose_in_graphql_explicit_registers():
    class ManagerE:
        @custom_route(
            method="POST",
            path="/run",
            input_model=PromoteIn,
            output_model=PromoteOut,
            expose_in=[ExposeIn.GRAPHQL],
        )
        def run(self, body):
            return PromoteOut(ok=True, new_role=body.role)

    reg = GraphQLContributionRegistry()
    register_custom_routes_to_graphql(ManagerE, contribution_registry=reg)
    assert "run" in reg.resolve_fields(FieldKind.MUTATION)


def test_resolver_invokes_method_and_coerces_dict_to_output_model():
    class ManagerF:
        @custom_route(
            method="POST",
            path="/promote",
            input_model=PromoteIn,
            output_model=PromoteOut,
        )
        def promote(self, body):
            return {"ok": True, "new_role": body.role}

    reg = GraphQLContributionRegistry()
    register_custom_routes_to_graphql(
        ManagerF,
        manager_factory=lambda info=None: ManagerF(),
        contribution_registry=reg,
    )
    contribution = reg.resolve_fields(FieldKind.MUTATION)["promote"]
    payload = PromoteIn(target_id="u1", role="admin")
    result = _run(contribution.resolver(info=None, input=payload))
    assert isinstance(result, PromoteOut)
    assert result.ok is True
    assert result.new_role == "admin"


def test_resolver_passes_through_model_instance():
    class ManagerG:
        @custom_route(
            method="POST",
            path="/demote",
            input_model=DemoteIn,
            output_model=DemoteOut,
        )
        def demote(self, body):
            return DemoteOut(ok=True)

    reg = GraphQLContributionRegistry()
    register_custom_routes_to_graphql(
        ManagerG,
        manager_factory=lambda info=None: ManagerG(),
        contribution_registry=reg,
    )
    contribution = reg.resolve_fields(FieldKind.MUTATION)["demote"]
    result = _run(contribution.resolver(info=None, input=DemoteIn(target_id="u1")))
    assert isinstance(result, DemoteOut)
    assert result.ok is True


def test_register_is_idempotent_for_repeated_calls():
    class ManagerH:
        @custom_route(
            method="POST",
            path="/promote",
            input_model=PromoteIn,
            output_model=PromoteOut,
        )
        def promote(self, body):
            return PromoteOut(ok=True, new_role=body.role)

    reg = GraphQLContributionRegistry()
    n1 = register_custom_routes_to_graphql(ManagerH, contribution_registry=reg)
    n2 = register_custom_routes_to_graphql(ManagerH, contribution_registry=reg)
    assert n1 == 1
    assert n2 == 0
    mutations = reg.resolve_fields(FieldKind.MUTATION)
    assert len(mutations) == 1


def test_get_resolver_with_no_input_model():
    class ManagerI:
        @custom_route(method="GET", path="/things", output_model=FetchOut)
        def list_things(self):
            return FetchOut(items=["x"])

    reg = GraphQLContributionRegistry()
    register_custom_routes_to_graphql(
        ManagerI,
        manager_factory=lambda info=None: ManagerI(),
        contribution_registry=reg,
    )
    contribution = reg.resolve_fields(FieldKind.QUERY)["list_things"]
    result = _run(contribution.resolver(info=None))
    assert isinstance(result, FetchOut)
    assert result.items == ["x"]


def test_register_uses_method_description_or_summary():
    class ManagerJ:
        @custom_route(
            method="POST",
            path="/promote",
            input_model=PromoteIn,
            output_model=PromoteOut,
            summary="Promote a user",
            description="Detailed description",
        )
        def promote(self, body):
            return None

    reg = GraphQLContributionRegistry()
    register_custom_routes_to_graphql(ManagerJ, contribution_registry=reg)
    contribution = reg.resolve_fields(FieldKind.MUTATION)["promote"]
    assert contribution.description == "Detailed description"


def test_register_uses_summary_when_description_absent():
    class ManagerK:
        @custom_route(
            method="POST",
            path="/promote",
            input_model=PromoteIn,
            output_model=PromoteOut,
            summary="Promote a user",
        )
        def promote(self, body):
            return None

    reg = GraphQLContributionRegistry()
    register_custom_routes_to_graphql(ManagerK, contribution_registry=reg)
    contribution = reg.resolve_fields(FieldKind.MUTATION)["promote"]
    assert contribution.description == "Promote a user"


def test_multiple_routes_on_one_manager_register_independently():
    class ManagerL:
        @custom_route(
            method="POST",
            path="/promote",
            input_model=PromoteIn,
            output_model=PromoteOut,
        )
        def promote(self, body):
            return None

        @custom_route(method="GET", path="/things", output_model=FetchOut)
        def list_things(self):
            return None

        @custom_route(
            method="POST",
            path="/demote",
            input_model=DemoteIn,
            output_model=DemoteOut,
        )
        def demote(self, body):
            return None

    reg = GraphQLContributionRegistry()
    n = register_custom_routes_to_graphql(ManagerL, contribution_registry=reg)
    assert n == 3
    mutations = reg.resolve_fields(FieldKind.MUTATION)
    queries = reg.resolve_fields(FieldKind.QUERY)
    assert {"promote", "demote"} == set(mutations.keys())
    assert "list_things" in queries
