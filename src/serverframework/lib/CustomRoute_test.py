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
    generate_test_scaffold,
    get_custom_route_spec,
    has_existing_scaffold,
    iter_custom_routes,
    register_custom_routes_to_graphql,
    reset_graphql_registrations,
    write_test_scaffold,
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
    return asyncio.run(coro)


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


# ---------------------------------------------------------------------------
# Test scaffolding generation (Item 40 — auto-emitted baseline tests)
# ---------------------------------------------------------------------------


class _ScaffoldManager:
    """Module-level so the generated scaffold has a stable import path."""

    @custom_route(
        method="POST",
        path="/promote",
        input_model=PromoteIn,
        output_model=PromoteOut,
        summary="Promote a member",
    )
    def promote(self, body: PromoteIn) -> PromoteOut:
        return PromoteOut(ok=True, new_role=body.role)

    @custom_route(
        method="GET",
        path="/items",
        output_model=FetchOut,
    )
    def list_items(self) -> FetchOut:
        return FetchOut(items=[])

    @custom_route(
        method="DELETE",
        path="/items/{item_id}",
        output_model=DemoteOut,
    )
    def delete_item(self, item_id: str) -> DemoteOut:
        return DemoteOut(ok=True)


def test_generate_test_scaffold_emits_three_tests_per_post_route():
    """A POST route with input_model produces auth + validation + happy
    path tests (3 tests total)."""

    class M:
        @custom_route(
            method="POST",
            path="/promote",
            input_model=PromoteIn,
            output_model=PromoteOut,
        )
        def promote(self, body): ...

    text = generate_test_scaffold(M)
    assert "def test_promote_unauthenticated_returns_401" in text
    assert "def test_promote_invalid_body_returns_422" in text
    assert "def test_promote_happy_path" in text


def test_generate_test_scaffold_skips_validation_for_get_routes():
    """A GET route has no body to malform, so the validation test
    is skipped — only auth and happy-path tests are emitted."""

    class M:
        @custom_route(method="GET", path="/items", output_model=FetchOut)
        def list_items(self): ...

    text = generate_test_scaffold(M)
    assert "def test_list_items_unauthenticated_returns_401" in text
    assert "def test_list_items_invalid_body_returns_422" not in text
    assert "def test_list_items_happy_path" in text


def test_generate_test_scaffold_skips_validation_for_delete_routes():
    """DELETE behaves like GET for the scaffold (no body validation)."""

    class M:
        @custom_route(method="DELETE", path="/x", output_model=DemoteOut)
        def remove(self): ...

    text = generate_test_scaffold(M)
    assert "def test_remove_invalid_body_returns_422" not in text


def test_generate_test_scaffold_substitutes_path_params():
    """``{id}`` style path params get scaffold values inlined into the
    request URL so the generated test doesn't 404."""

    class M:
        @custom_route(method="GET", path="/items/{item_id}", output_model=FetchOut)
        def get_item(self): ...

    text = generate_test_scaffold(M)
    assert "/items/scaffold-item_id" in text


def test_generate_test_scaffold_populates_required_fields():
    """Body fields with str annotations get ``scaffold-{name}`` values;
    int fields get ``1``; the generator picks deterministic defaults so
    the scaffold is byte-stable across regenerations."""

    class IntInput(BaseModel):
        amount: int
        memo: str

    class M:
        @custom_route(
            method="POST",
            path="/charge",
            input_model=IntInput,
            output_model=DemoteOut,
        )
        def charge(self, body): ...

    text = generate_test_scaffold(M)
    assert "'amount': 1" in text
    assert "'memo': 'scaffold-memo'" in text


def test_generate_test_scaffold_is_byte_stable():
    """Item 25's deterministic-regeneration contract: regenerating the
    scaffold produces the same bytes."""

    class M:
        @custom_route(
            method="POST",
            path="/promote",
            input_model=PromoteIn,
            output_model=PromoteOut,
        )
        def promote(self, body): ...

    a = generate_test_scaffold(M)
    b = generate_test_scaffold(M)
    assert a == b


def test_generate_test_scaffold_empty_for_no_routes():
    """A class with no @custom_route methods returns the empty string."""

    class M:
        def regular_method(self): ...

    assert generate_test_scaffold(M) == ""


def test_generate_test_scaffold_includes_import_for_manager():
    """The generated header imports the manager class from its real
    module path so the test client can build a router against it."""

    text = generate_test_scaffold(_ScaffoldManager)
    # Under pytest's ``--import-mode=importlib`` the same test module can
    # surface as either the dotted (``serverframework.lib.CustomRoute_test``)
    # or the short (``CustomRoute_test``) form depending on collection
    # order across xdist workers; both target the same module file so
    # accept either as a correct ``from <module> import _ScaffoldManager``.
    expected_module = _ScaffoldManager.__module__
    assert f"from {expected_module} import _ScaffoldManager" in text


def test_generate_test_scaffold_emits_test_client_fixture():
    """The fixture builds a FastAPI app with register_custom_routes
    so generated tests share the production routing path."""

    text = generate_test_scaffold(_ScaffoldManager)
    assert "def client():" in text
    assert "register_custom_routes(app.router, _ScaffoldManager)" in text


def test_generate_test_scaffold_handles_multi_route_manager():
    """A manager with multiple routes emits all three test functions
    per POST/PUT/PATCH route, two per GET/DELETE route."""

    text = generate_test_scaffold(_ScaffoldManager)
    # promote: 3 tests (POST)
    assert "def test_promote_unauthenticated_returns_401" in text
    assert "def test_promote_invalid_body_returns_422" in text
    assert "def test_promote_happy_path" in text
    # list_items: 2 tests (GET)
    assert "def test_list_items_unauthenticated_returns_401" in text
    assert "def test_list_items_happy_path" in text
    assert "def test_list_items_invalid_body_returns_422" not in text
    # delete_item: 2 tests (DELETE) + path-param substitution
    assert "def test_delete_item_unauthenticated_returns_401" in text
    assert "def test_delete_item_happy_path" in text
    assert "def test_delete_item_invalid_body_returns_422" not in text


def test_generate_test_scaffold_output_compiles():
    """The generated source is valid Python — compile() must succeed."""

    text = generate_test_scaffold(_ScaffoldManager)
    compile(text, "<scaffold>", "exec")


def test_write_test_scaffold_creates_file(tmp_path):
    """``write_test_scaffold`` writes the scaffold under the convention
    ``{ManagerName}_custom_routes_scaffold_test.py``."""

    target = write_test_scaffold(_ScaffoldManager, str(tmp_path))
    assert target is not None
    assert target.endswith("_ScaffoldManager_custom_routes_scaffold_test.py")
    import os

    assert os.path.exists(target)


def test_write_test_scaffold_preserves_existing_by_default(tmp_path):
    """Codegen must not clobber author-edited scaffolds — the second
    call returns None and leaves the existing file untouched."""

    first = write_test_scaffold(_ScaffoldManager, str(tmp_path))
    assert first is not None
    with open(first, "r", encoding="utf-8") as fh:
        contents = fh.read()
    # Author "edits" the scaffold.
    sentinel = "# AUTHOR EDIT: do not regenerate"
    with open(first, "w", encoding="utf-8") as fh:
        fh.write(sentinel + "\n" + contents)
    second = write_test_scaffold(_ScaffoldManager, str(tmp_path))
    assert second is None
    with open(first, "r", encoding="utf-8") as fh:
        assert sentinel in fh.read()


def test_write_test_scaffold_overwrites_when_explicit(tmp_path):
    """``overwrite=True`` re-emits the scaffold even when the file
    exists — used by the ``regenerate-all`` codegen command."""

    first = write_test_scaffold(_ScaffoldManager, str(tmp_path))
    assert first is not None
    with open(first, "w", encoding="utf-8") as fh:
        fh.write("# author edit")
    second = write_test_scaffold(_ScaffoldManager, str(tmp_path), overwrite=True)
    assert second == first
    with open(first, "r", encoding="utf-8") as fh:
        assert "# author edit" not in fh.read()


def test_has_existing_scaffold(tmp_path):
    """The codegen helper that callers consult before regenerating."""

    assert not has_existing_scaffold(_ScaffoldManager, str(tmp_path))
    write_test_scaffold(_ScaffoldManager, str(tmp_path))
    assert has_existing_scaffold(_ScaffoldManager, str(tmp_path))


def test_write_test_scaffold_returns_none_for_no_routes(tmp_path):
    """A manager with no @custom_route methods does not emit a scaffold."""

    class M: ...

    assert write_test_scaffold(M, str(tmp_path)) is None


def test_generate_test_scaffold_no_header_omits_imports():
    """``include_header=False`` produces just the test functions — used
    when appending scaffolds to an existing test file."""

    text = generate_test_scaffold(_ScaffoldManager, include_header=False)
    assert "from fastapi.testclient" not in text
    assert "def test_promote_" in text
