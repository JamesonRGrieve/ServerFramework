"""Tests for the @custom_route contract (Item 40)."""

from __future__ import annotations

import dataclasses

import pytest
from pydantic import BaseModel

from serverframework.lib.CustomRoute import (
    AbstractActionEndpoint,
    CustomRouteSpec,
    ExposeIn,
    custom_route,
    get_custom_route_spec,
    iter_custom_routes,
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
