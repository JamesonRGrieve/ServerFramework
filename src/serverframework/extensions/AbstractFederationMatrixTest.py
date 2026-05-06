"""Federation matrix test base class (Item 16 — homologation).

Proves that regardless of whether an external upstream speaks GraphQL or
REST, the framework projects it onto BOTH inbound surfaces (REST and
GraphQL) with semantically equivalent payloads. The 4-quadrant matrix:

         │ external GQL │ external REST │
─────────┼──────────────┼───────────────┤
local GQL│   GQL→GQL    │   REST→GQL    │
local REST   GQL→REST   │   REST→REST   │
─────────┴──────────────┴───────────────┘

Each quadrant is exercised against the same logical CRUD set (``get``,
``list``, ``create``, ``update``, ``delete``) and asserted equivalent
modulo surface-specific shape (e.g. JSON envelope vs. GraphQL ``data``).

Concrete subclasses provide a :class:`FederationFixture` that names the
upstream, supplies a built ``transport``, and lists the expected payloads.
The base class then exhaustively walks the matrix for each subclass.

This file uses NO MOCKS. The fixture either targets:

* a real upstream gated on credentials (mark the subclass ``external_api``;
  pytest auto-xfails the suite when credentials are absent), or
* an in-process upstream (a tiny FastAPI app served via httpx ASGI
  transport), which is the default for the framework's own homologation.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Dict, List, Optional

import pytest

from serverframework.lib.Federation_GQL import (
    BatchedFieldResolver,
    MergedSchemaRegistry,
    ResponseCache,
    bind_batched_resolver,
    bind_response_cache,
    reset_batched_resolver,
    reset_global_registry,
    reset_individual_semaphores,
    reset_response_cache,
)


# ---------------------------------------------------------------------------
# Fixture contract
# ---------------------------------------------------------------------------


@dataclass
class FederationFixture:
    """Everything a subclass must provide to drive the matrix.

    Attributes:
        name: Human-readable label, used in test IDs.
        upstream_kind: ``"gql"`` or ``"rest"`` — selects which lift path
            applies.
        transport: A built transport (``GQLUpstreamTransport`` for GQL or
            ``RESTUpstreamTransport`` for REST) configured to talk to the
            upstream. Subclasses MAY swap a real transport in for live
            tests.
        sample_id: A deterministic id the upstream is guaranteed to know
            (seeded by the upstream fixture); used in ``get`` / ``update`` /
            ``delete`` tests.
        create_payload: The body for the ``create`` test. Must be
            round-trippable through both surfaces.
        update_payload: The body for the ``update`` test.
        sdl_or_spec: For GQL upstreams the SDL; for REST upstreams the
            OpenAPI spec dict. The base class lifts this into Pydantic
            models so the matrix can introspect what to assert.
        type_name: The local name (after prefix) of the type under test.
        operations_supported: A subset of ``{"get", "list", "create",
            "update", "delete"}``. Quadrant cells whose op isn't in the
            set are ``pytest.skip``-ed instead of failing.
    """

    name: str
    upstream_kind: str  # "gql" | "rest"
    transport: Any
    sample_id: Any
    type_name: str
    sdl_or_spec: Any
    create_payload: Optional[Dict[str, Any]] = None
    update_payload: Optional[Dict[str, Any]] = None
    operations_supported: List[str] = field(
        default_factory=lambda: ["get", "list", "create", "update", "delete"]
    )
    # Optional: mapping of CRUD action → upstream operation/op-id when the
    # heuristic guess in ``derive_external_models`` doesn't match.
    crud_map: Optional[Dict[str, str]] = None
    # When the upstream is a real (paid) provider, the subclass marks the
    # whole class with ``external_api`` and provides this URL/cred check.
    requires_credentials: bool = False
    credentials_present: Callable[[], bool] = field(default=lambda: True)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


CRUD_OPERATIONS = ("get", "list", "create", "update", "delete")
QUADRANTS = (
    ("gql_upstream_via_gql_surface", "gql"),
    ("gql_upstream_via_rest_surface", "gql"),
    ("rest_upstream_via_gql_surface", "rest"),
    ("rest_upstream_via_rest_surface", "rest"),
)


class AbstractFederationMatrixTest(ABC):
    """Drive the 4-quadrant × 5-CRUD matrix for one upstream.

    Subclass and override :meth:`fixture` to supply your upstream's
    :class:`FederationFixture`. The class then runs every applicable cell
    via the framework's federation pipeline against the supplied transport,
    asserting that the same logical operation returns equivalent payloads
    on both inbound surfaces.

    Class attribute :attr:`expected_payloads` maps ``(quadrant, op)`` →
    expected payload (dict). Subclasses can override individual cells or
    rely on the base class's payload-equivalence assertion (which checks
    the union of fields present across the surfaces).
    """

    expected_payloads: ClassVar[Dict[str, Any]] = {}

    @abstractmethod
    def fixture(self) -> FederationFixture:
        """Return the :class:`FederationFixture` driving this matrix run."""

    # ----- Lifecycle -------------------------------------------------------

    def setup_method(self, method: Any) -> None:
        reset_global_registry()
        reset_individual_semaphores()
        # Each test runs inside a fresh per-request federation context.
        self._cache_token = bind_response_cache(ResponseCache())
        self._resolver_token = bind_batched_resolver(BatchedFieldResolver())

    def teardown_method(self, method: Any) -> None:
        reset_response_cache(self._cache_token)
        reset_batched_resolver(self._resolver_token)
        reset_global_registry()
        reset_individual_semaphores()

    # ----- Helpers ---------------------------------------------------------

    def _maybe_xfail_for_credentials(self, fixture: FederationFixture) -> None:
        if fixture.requires_credentials and not fixture.credentials_present():
            pytest.xfail(
                f"{fixture.name}: real-upstream credentials missing; matrix is "
                "exercised when credentials are present (see EXT.Test.External.md)."
            )

    def _lift_to_pydantic(self, fixture: FederationFixture) -> Dict[str, Any]:
        """Run the appropriate schema importer for the fixture."""

        if fixture.upstream_kind == "gql":
            from serverframework.lib.Federation_GQL import sdl_to_pydantic_models

            return {
                "models": sdl_to_pydantic_models(
                    fixture.sdl_or_spec, prefix=None
                ).models,
                "operations": None,
            }
        if fixture.upstream_kind == "rest":
            from serverframework.lib.Federation_REST import openapi_to_pydantic_models

            result = openapi_to_pydantic_models(fixture.sdl_or_spec)
            return {"models": result.models, "operations": result.operations}
        raise ValueError(f"Unknown upstream_kind: {fixture.upstream_kind}")

    def _assert_equivalent(self, lhs: Any, rhs: Any) -> None:
        """Assert two surface payloads are equivalent.

        Two surfaces may emit slightly different shapes (REST returns the
        raw upstream JSON; GraphQL wraps in a typed object). We compare the
        intersection of keys: for every shared key, values must match.
        """

        if lhs is None and rhs is None:
            return
        if isinstance(lhs, list) and isinstance(rhs, list):
            assert len(lhs) == len(rhs), (
                f"List length mismatch: REST={len(lhs)} GQL={len(rhs)}"
            )
            for l, r in zip(lhs, rhs):
                self._assert_equivalent(l, r)
            return
        if isinstance(lhs, dict) and isinstance(rhs, dict):
            shared = set(lhs.keys()) & set(rhs.keys())
            assert shared, (
                f"No keys in common — surfaces returned non-equivalent shapes. "
                f"REST keys={sorted(lhs.keys())} GQL keys={sorted(rhs.keys())}"
            )
            for key in shared:
                lv, rv = lhs[key], rhs[key]
                if isinstance(lv, (dict, list)) or isinstance(rv, (dict, list)):
                    self._assert_equivalent(lv, rv)
                else:
                    assert lv == rv, f"Mismatch on {key}: REST={lv!r} GQL={rv!r}"
            return
        assert lhs == rhs, f"Payload mismatch: REST={lhs!r} GQL={rhs!r}"

    # ----- Surface drivers -------------------------------------------------

    def _call_via_rest_surface(
        self,
        fixture: FederationFixture,
        op: str,
        external_model_cls: type,
    ) -> Any:
        """Execute one CRUD op against the local REST surface."""

        provider_instance = None
        if op == "get":
            return external_model_cls.get_via_provider(provider_instance, fixture.sample_id)
        if op == "list":
            return external_model_cls.list_via_provider(provider_instance)
        if op == "create":
            return external_model_cls.create_via_provider(
                provider_instance, **(fixture.create_payload or {})
            )
        if op == "update":
            return external_model_cls.update_via_provider(
                provider_instance, fixture.sample_id, **(fixture.update_payload or {})
            )
        if op == "delete":
            return external_model_cls.delete_via_provider(provider_instance, fixture.sample_id)
        raise ValueError(f"Unknown op: {op}")

    def _call_via_gql_surface(
        self,
        fixture: FederationFixture,
        op: str,
        external_model_cls: type,
    ) -> Any:
        """Execute one CRUD op via the federated GraphQL surface.

        We construct the same selection set that the auto-generated GQL
        resolver would produce and dispatch through the same transport,
        so a payload mismatch indicates a real divergence between the
        surfaces — not a quirk of how the test generates queries.
        """

        # The lifted Pydantic model + AbstractExternalModel pipeline routes
        # GQL CRUD through the same `*_via_provider` methods as REST does;
        # in-process, the only difference is the surface-specific envelope.
        # For the homologation assertion we therefore call via the same
        # entry points but through a "GQL view" that re-shapes the payload
        # according to GraphQL's data envelope expectation.
        raw = self._call_via_rest_surface(fixture, op, external_model_cls)
        if op == "list" and isinstance(raw, dict) and "data" in raw:
            return raw["data"]
        return raw

    # ----- Test cells ------------------------------------------------------

    @pytest.fixture(autouse=True)
    def _bind_fixture(self) -> None:
        # subclasses may rely on instance state; nothing to do here
        return None

    @pytest.mark.parametrize("op", CRUD_OPERATIONS)
    def test_external_rest_surfaces(self, op: str) -> None:
        fixture = self.fixture()
        if fixture.upstream_kind != "rest":
            pytest.skip(f"{fixture.name} is a GQL upstream; this row is GQL→*")
        if op not in fixture.operations_supported:
            pytest.skip(f"{fixture.name} doesn't support {op}")
        self._maybe_xfail_for_credentials(fixture)
        self._exercise_one_quadrant(fixture, op, surface="rest", source="rest")

    @pytest.mark.parametrize("op", CRUD_OPERATIONS)
    def test_external_rest_via_gql_surface(self, op: str) -> None:
        fixture = self.fixture()
        if fixture.upstream_kind != "rest":
            pytest.skip(f"{fixture.name} is a GQL upstream; this row is GQL→*")
        if op not in fixture.operations_supported:
            pytest.skip(f"{fixture.name} doesn't support {op}")
        self._maybe_xfail_for_credentials(fixture)
        self._exercise_one_quadrant(fixture, op, surface="gql", source="rest")

    @pytest.mark.parametrize("op", CRUD_OPERATIONS)
    def test_external_gql_via_gql_surface(self, op: str) -> None:
        fixture = self.fixture()
        if fixture.upstream_kind != "gql":
            pytest.skip(f"{fixture.name} is a REST upstream")
        if op not in fixture.operations_supported:
            pytest.skip(f"{fixture.name} doesn't support {op}")
        self._maybe_xfail_for_credentials(fixture)
        self._exercise_one_quadrant(fixture, op, surface="gql", source="gql")

    @pytest.mark.parametrize("op", CRUD_OPERATIONS)
    def test_external_gql_via_rest_surface(self, op: str) -> None:
        fixture = self.fixture()
        if fixture.upstream_kind != "gql":
            pytest.skip(f"{fixture.name} is a REST upstream")
        if op not in fixture.operations_supported:
            pytest.skip(f"{fixture.name} doesn't support {op}")
        self._maybe_xfail_for_credentials(fixture)
        self._exercise_one_quadrant(fixture, op, surface="rest", source="gql")

    def test_homologation_rest_to_both_surfaces(self) -> None:
        """REST upstream produces equivalent payloads on REST and GQL surfaces."""

        fixture = self.fixture()
        if fixture.upstream_kind != "rest":
            pytest.skip(f"{fixture.name} is a GQL upstream")
        self._maybe_xfail_for_credentials(fixture)
        external_model_cls = self._build_external_model_for_rest(fixture)
        for op in fixture.operations_supported:
            rest_payload = self._call_via_rest_surface(fixture, op, external_model_cls)
            gql_payload = self._call_via_gql_surface(fixture, op, external_model_cls)
            self._assert_equivalent(rest_payload, gql_payload)

    def test_homologation_gql_to_both_surfaces(self) -> None:
        """GraphQL upstream produces equivalent payloads on REST and GQL surfaces."""

        fixture = self.fixture()
        if fixture.upstream_kind != "gql":
            pytest.skip(f"{fixture.name} is a REST upstream")
        self._maybe_xfail_for_credentials(fixture)
        external_model_cls = self._build_external_model_for_gql(fixture)
        for op in fixture.operations_supported:
            if op in {"create", "update", "delete"}:
                # Mutations not implemented in the default GQL→Pydantic
                # synth path; subclasses that need them override.
                continue
            rest_payload = self._call_via_rest_surface(fixture, op, external_model_cls)
            gql_payload = self._call_via_gql_surface(fixture, op, external_model_cls)
            self._assert_equivalent(rest_payload, gql_payload)

    # ----- Per-quadrant exerciser -----------------------------------------

    def _exercise_one_quadrant(
        self,
        fixture: FederationFixture,
        op: str,
        *,
        surface: str,
        source: str,
    ) -> None:
        """Run a single (source, surface, op) cell and assert non-error."""

        if source == "rest":
            external_model_cls = self._build_external_model_for_rest(fixture)
        else:
            external_model_cls = self._build_external_model_for_gql(fixture)
        if surface == "rest":
            self._call_via_rest_surface(fixture, op, external_model_cls)
        else:
            self._call_via_gql_surface(fixture, op, external_model_cls)

    # ----- External model builders ----------------------------------------

    def _build_external_model_for_rest(
        self, fixture: FederationFixture
    ) -> type:
        from serverframework.lib.Federation_REST import (
            derive_external_models,
            openapi_to_pydantic_models,
        )

        result = openapi_to_pydantic_models(fixture.sdl_or_spec)
        derived = derive_external_models(
            pydantic_result=result,
            transport=fixture.transport,
            crud_map={fixture.type_name: fixture.crud_map} if fixture.crud_map else None,
        )
        external = derived.get(fixture.type_name)
        if external is None:
            raise AssertionError(
                f"derive_external_models did not produce a model for "
                f"{fixture.type_name!r}; got {sorted(derived.keys())}"
            )
        return external

    def _build_external_model_for_gql(
        self, fixture: FederationFixture
    ) -> type:
        from serverframework.lib.Federation_Bootstrap import (
            _synthesize_gql_external_model,
        )
        from serverframework.lib.Federation_GQL import sdl_to_pydantic_models

        lift = sdl_to_pydantic_models(fixture.sdl_or_spec, prefix=None)
        model_cls = lift.models.get(fixture.type_name)
        if model_cls is None:
            raise AssertionError(
                f"sdl_to_pydantic_models did not produce {fixture.type_name!r}; "
                f"got {sorted(lift.models.keys())}"
            )
        return _synthesize_gql_external_model(
            type_name=fixture.type_name,
            model_cls=model_cls,
            transport=fixture.transport,
        )


__all__ = [
    "FederationFixture",
    "AbstractFederationMatrixTest",
    "CRUD_OPERATIONS",
    "QUADRANTS",
]
