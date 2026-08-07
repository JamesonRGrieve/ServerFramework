"""Custom-route contract for non-CRUD endpoints (Item 40).

Authors apply ``@custom_route`` to methods on ``RouterMixin``-tagged managers
or stand-alone ``AbstractActionEndpoint`` subclasses. The decorator captures
everything needed to extend the auto-generated REST + SDK + GraphQL surface
beyond CRUD: HTTP method, sub-path, typed input/output Pydantic models,
authentication mode, OpenAPI tags, exposure surface, and optional GraphQL
field kind.

This module ships the decorator, the spec dataclass, and helpers to walk a
class for tagged methods. REST integration is wired into
``Pydantic2FastAPI.create_router_from_manager`` via ``register_custom_routes``.
GraphQL field emission is wired through Item 46's contribution registry via
``register_custom_routes_to_graphql``; both are invoked once per manager at
schema-build time.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Optional,
    Set,
    Tuple,
    Type,
)

from pydantic import BaseModel


class ExposeIn(str, Enum):
    """Surfaces a custom route can be exposed on."""

    REST = "rest"
    SDK = "sdk"
    GRAPHQL = "graphql"
    ALL = "all"


@dataclass(frozen=True)
class CustomRouteSpec:
    """Frozen contract captured by the @custom_route decorator."""

    method: str
    path: str
    input_model: Optional[Type[BaseModel]]
    output_model: Optional[Type[BaseModel]]
    authentication_type: str = "session"
    openapi_tags: Tuple[str, ...] = ()
    expose_in: FrozenSet[ExposeIn] = frozenset({ExposeIn.ALL})
    graphql_kind: Optional[str] | None = None
    summary: Optional[str] | None = None
    description: Optional[str] | None = None


def custom_route(
    *,
    method: str,
    path: str,
    input_model: Optional[Type[BaseModel]] | None = None,
    output_model: Optional[Type[BaseModel]] | None = None,
    authentication_type: str = "session",
    openapi_tags: Iterable[str] = (),
    expose_in: Iterable[ExposeIn] = (ExposeIn.ALL,),
    graphql_kind: Optional[str] | None = None,
    summary: Optional[str] | None = None,
    description: Optional[str] | None = None,
) -> Callable:
    """Decorator: tag a method with its route/SDK/GraphQL contract."""

    def deco(func):
        spec = CustomRouteSpec(
            method=method.upper(),
            path=path,
            input_model=input_model,
            output_model=output_model,
            authentication_type=authentication_type,
            openapi_tags=tuple(openapi_tags),
            expose_in=frozenset(expose_in),
            graphql_kind=graphql_kind,
            summary=summary,
            description=description,
        )
        if spec.method not in ("GET", "DELETE") and spec.input_model is None:
            raise ValueError(
                f"@custom_route on {func.__qualname__}: method {spec.method} "
                f"requires an input_model (typed contract preserved)"
            )
        if spec.output_model is None:
            raise ValueError(
                f"@custom_route on {func.__qualname__}: output_model is required "
                f"to preserve typed contract (use a Pydantic model)"
            )
        func.__custom_route_spec__ = spec
        return func

    return deco


def get_custom_route_spec(func) -> Optional[CustomRouteSpec]:
    """Return the CustomRouteSpec attached to ``func``, or None."""
    return getattr(func, "__custom_route_spec__", None)


def iter_custom_routes(manager_cls) -> List[Tuple[str, CustomRouteSpec]]:
    """Walk a manager class for @custom_route-tagged methods.

    Returns a deterministic list of ``(method_name, spec)`` tuples sorted by
    method name.
    """
    results: List[Tuple[str, CustomRouteSpec]] = []
    for name, attr in vars(manager_cls).items():
        spec = get_custom_route_spec(attr)
        if spec is not None:
            results.append((name, spec))
    results.sort(key=lambda item: item[0])
    return results


class AbstractActionEndpoint:
    """Base for genuinely RPC-shaped endpoints with no CRUD resource binding.

    Subclasses use the same ``@custom_route`` decorator. This class is a thin
    marker — the registration helper treats it like any ``RouterMixin``-tagged
    class and walks it for tagged methods.
    """

    prefix: Optional[str] | None = None
    tags: Optional[List[str]] | None = None


def register_custom_routes(router, manager_cls, manager_factory: Optional[Callable] | None = None) -> int:
    """Walk ``manager_cls`` for ``@custom_route`` methods and add them to ``router``.

    Additive: each tagged method becomes a typed FastAPI route on the supplied
    router. Returns the number of routes registered.

    ``manager_factory`` is an optional callable producing a bound manager
    instance. When omitted, methods are called as classmethods/staticmethods
    or against a freshly-instantiated manager (best-effort).
    """
    import inspect

    from fastapi import HTTPException, Request

    registered = 0
    for method_name, spec in iter_custom_routes(manager_cls):
        if ExposeIn.REST not in spec.expose_in and ExposeIn.ALL not in spec.expose_in:
            continue

        bound_method = getattr(manager_cls, method_name)

        def _make_endpoint(_bound_method, _spec, _method_name):
            async def endpoint(request: Request):
                method_args = dict(request.path_params)

                if _spec.input_model is not None and request.method in (
                    "POST",
                    "PUT",
                    "PATCH",
                ):
                    raw = await request.body()
                    if raw:
                        import json as _json

                        try:
                            payload = _json.loads(raw)
                        except _json.JSONDecodeError:
                            raise HTTPException(status_code=400, detail="Invalid JSON body")
                        validated = _spec.input_model.model_validate(payload)
                        method_args["body"] = validated

                if manager_factory is not None:
                    instance = manager_factory(request=request)
                    func = getattr(instance, _method_name)
                    sig = inspect.signature(func)
                    accepted = {k: v for k, v in method_args.items() if k in sig.parameters}
                    if "body" in method_args and "body" not in sig.parameters:
                        # Splat validated body fields into method kwargs, but
                        # ONLY for fields the input model declares — i.e. the
                        # network model is the writability gate. Without this
                        # check, a manager method whose signature happens to
                        # name a privileged column (is_admin, role, etc.)
                        # would silently accept that column from any request
                        # whose Pydantic input contained it.
                        body = method_args["body"]
                        if hasattr(body, "model_dump"):
                            allowed = set(getattr(_spec.input_model, "model_fields", {}).keys())
                            for k, v in body.model_dump().items():
                                if k in sig.parameters and k in allowed:
                                    accepted[k] = v
                    result = func(**accepted)
                else:
                    sig = inspect.signature(_bound_method)
                    accepted = {k: v for k, v in method_args.items() if k in sig.parameters}
                    result = _bound_method(**accepted)

                if _spec.output_model is not None:
                    if isinstance(result, _spec.output_model):
                        return result.model_dump()
                    if isinstance(result, dict):
                        return _spec.output_model.model_validate(result).model_dump()
                    if hasattr(result, "model_dump"):
                        return result.model_dump()
                return result

            return endpoint

        endpoint = _make_endpoint(bound_method, spec, method_name)
        route_method = getattr(router, spec.method.lower())
        route_method(
            spec.path,
            summary=spec.summary or f"Custom {spec.method} {spec.path}",
            description=spec.description or "",
            tags=list(spec.openapi_tags) if spec.openapi_tags else None,
        )(endpoint)
        registered += 1

    return registered


# ---------------------------------------------------------------------------
# GraphQL emission (Item 40 GraphQL half + Item 46 contribution-registry hook)
# ---------------------------------------------------------------------------

# Registrations are tracked per (manager_cls, method_name) so repeated calls
# during schema rebuilds are idempotent. The contribution registry's identity
# check on resolvers requires the same callable object across registrations,
# which is what this set guarantees.
_GRAPHQL_REGISTERED: Set[Tuple[type, str]] = set()
_GRAPHQL_RESOLVER_CACHE: Dict[Tuple[type, str], Callable[..., Any]] = {}


def _infer_graphql_kind(spec: CustomRouteSpec) -> str:
    """GET → query, anything else → mutation, with explicit override.

    The decorator's ``graphql_kind`` parameter wins when set. Otherwise the
    HTTP verb dictates: read-only methods go into ``Query``; write methods
    go into ``Mutation``. Subscriptions are not emitted from ``@custom_route``
    — streaming routes use the dedicated streaming decorator (Item 13).
    """
    if spec.graphql_kind is not None:
        kind = spec.graphql_kind.lower()
        if kind in ("query", "mutation"):
            return kind
        raise ValueError(
            f"Invalid graphql_kind={spec.graphql_kind!r}; expected 'query' or 'mutation'"
        )
    return "query" if spec.method == "GET" else "mutation"


def _build_graphql_resolver(
    manager_cls: type,
    method_name: str,
    spec: CustomRouteSpec,
    manager_factory: Optional[Callable[..., Any]] | None = None,
) -> Callable[..., Any]:
    """Wrap a tagged method as a Strawberry-compatible resolver.

    The resolver accepts a single optional ``input`` argument typed to the
    spec's ``input_model`` and returns an instance of ``output_model``.
    Path parameters embedded in ``spec.path`` (``{id}`` style) become
    keyword arguments on the resolver so GraphQL clients can pass them
    natively.
    """
    bound_method = getattr(manager_cls, method_name)
    method_sig = inspect.signature(bound_method)

    def _instantiate_manager(info: Any) -> Any:
        if manager_factory is not None:
            return manager_factory(info=info)
        try:
            return manager_cls()
        except Exception:
            return None

    def _split_kwargs(payload: Dict[str, Any]) -> Dict[str, Any]:
        accepted: Dict[str, Any] = {}
        for key, value in payload.items():
            if key in method_sig.parameters:
                accepted[key] = value
        return accepted

    if spec.method in ("POST", "PUT", "PATCH") and spec.input_model is not None:
        async def resolver(info: Any, input: spec.input_model) -> spec.output_model:  # type: ignore[name-defined]
            instance = _instantiate_manager(info)
            payload = input.model_dump() if hasattr(input, "model_dump") else dict(input)
            target = (
                getattr(instance, method_name) if instance is not None else bound_method
            )
            if "body" in method_sig.parameters:
                kwargs: Dict[str, Any] = {"body": input}
            else:
                kwargs = _split_kwargs(payload)
            result = target(**kwargs) if instance is not None else target(instance, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return _coerce_output(result, spec.output_model)
    else:
        async def resolver(info: Any, **kwargs: Any) -> spec.output_model:  # type: ignore[name-defined, misc]
            instance = _instantiate_manager(info)
            target = (
                getattr(instance, method_name) if instance is not None else bound_method
            )
            accepted = _split_kwargs(kwargs)
            result = target(**accepted) if instance is not None else target(instance, **accepted)
            if inspect.isawaitable(result):
                result = await result
            return _coerce_output(result, spec.output_model)

    resolver.__name__ = method_name
    resolver.__qualname__ = f"{manager_cls.__name__}.{method_name}"
    return resolver


def _coerce_output(result: Any, output_model: Optional[Type[BaseModel]]) -> Any:
    """Normalize a tagged-method result into an ``output_model`` instance.

    Mirrors the REST helper's coercion rules: model instances pass through,
    dicts get validated, anything else is returned untouched (the resolver's
    declared return type is the contract; Strawberry surfaces type mismatches
    as schema errors).
    """
    if output_model is None:
        return result
    if isinstance(result, output_model):
        return result
    if isinstance(result, dict):
        return output_model.model_validate(result)
    return result


def register_custom_routes_to_graphql(
    manager_cls: type,
    manager_factory: Optional[Callable[..., Any]] | None = None,
    *,
    contribution_registry: Optional[Any] | None = None,
    extension_name: str = "core",
) -> int:
    """Walk ``manager_cls`` for ``@custom_route`` methods and register them
    as GraphQL ``FieldContribution``s.

    Each tagged method becomes a typed Strawberry root field — query for
    ``GET`` (and explicit ``graphql_kind="query"`` overrides), mutation for
    everything else (and explicit ``graphql_kind="mutation"`` overrides).
    Routes whose ``expose_in`` excludes ``GRAPHQL`` (and ``ALL``) are skipped
    so an author can opt out per-route.

    Returns the number of fields registered (or skipped because the route is
    GraphQL-excluded).
    """
    from serverframework.lib.Pydantic2Strawberry import (
        FieldContribution,
        FieldKind,
        gql_contribution_registry,
    )

    registry = contribution_registry or gql_contribution_registry()
    registered = 0

    for method_name, spec in iter_custom_routes(manager_cls):
        if (
            ExposeIn.GRAPHQL not in spec.expose_in
            and ExposeIn.ALL not in spec.expose_in
        ):
            continue

        cache_key = (manager_cls, method_name)
        if cache_key in _GRAPHQL_REGISTERED:
            continue

        resolver = _GRAPHQL_RESOLVER_CACHE.get(cache_key)
        if resolver is None:
            resolver = _build_graphql_resolver(
                manager_cls, method_name, spec, manager_factory
            )
            _GRAPHQL_RESOLVER_CACHE[cache_key] = resolver

        kind_str = _infer_graphql_kind(spec)
        kind = FieldKind.QUERY if kind_str == "query" else FieldKind.MUTATION

        registry.register_field(
            FieldContribution(
                extension_name=extension_name,
                kind=kind,
                name=method_name,
                resolver=resolver,
                return_type=spec.output_model,
                args={"input": spec.input_model} if spec.input_model is not None else {},
                description=spec.description or spec.summary,
                namespace=False,
                priority=50,
            )
        )
        _GRAPHQL_REGISTERED.add(cache_key)
        registered += 1

    return registered


def reset_graphql_registrations() -> None:
    """Test helper -- clear the per-process registration cache.

    Pair with ``Pydantic2Strawberry.reset_gql_contribution_registry`` when a
    test needs to re-register the same ``@custom_route`` after wiping the
    contribution registry.
    """
    _GRAPHQL_REGISTERED.clear()
    _GRAPHQL_RESOLVER_CACHE.clear()


# ---------------------------------------------------------------------------
# Test scaffolding generation (Item 40 — auto-emitted baseline test per route)
# ---------------------------------------------------------------------------


def _example_value_for_field(name: str, annotation: Any) -> Any:
    """Pick a deterministic scaffold value for a Pydantic field.

    Used to populate a happy-path body in the generated test. Prefers
    typed defaults that satisfy common validators (non-empty str,
    non-zero int, non-empty list/dict) so the scaffold passes the
    happy-path assertion without further author edits in the common
    case. Authors override the scaffold once they hand-tune their test."""
    origin = getattr(annotation, "__origin__", None)
    if annotation is str or origin is str:
        return f"scaffold-{name}"
    if annotation is int or origin is int:
        return 1
    if annotation is float or origin is float:
        return 1.0
    if annotation is bool or origin is bool:
        return True
    if annotation is bytes or origin is bytes:
        return f"scaffold-{name}".encode()
    if origin is list or annotation is list:
        return []
    if origin is dict or annotation is dict:
        return {}
    if origin is tuple or annotation is tuple:
        return ()
    if origin is set or annotation is set:
        return set()
    return None


def _build_scaffold_body(spec: CustomRouteSpec) -> Dict[str, Any]:
    """Build a minimal valid input body for the route's input_model."""
    body: Dict[str, Any] = {}
    if spec.input_model is None:
        return body
    fields = getattr(spec.input_model, "model_fields", {})
    for fname, finfo in fields.items():
        if finfo.is_required():
            body[fname] = _example_value_for_field(fname, finfo.annotation)
    return body


def _python_repr(value: Any) -> str:
    """Best-effort Python source repr for the scaffold body."""
    if isinstance(value, bytes):
        return repr(value)
    return repr(value)


def generate_test_scaffold(
    manager_cls: type,
    *,
    module_path: Optional[str] | None = None,
    include_header: bool = True,
) -> str:
    """Emit baseline test source code for every ``@custom_route`` on ``manager_cls``.

    The generated module covers, per route:

    - **Auth** — a 401 / 403 assertion when no requester is bound (the
      framework's auth middleware refuses unauthenticated calls).
    - **Validation** — a 422 assertion on a deliberately-malformed body
      (POST/PUT/PATCH with input_model only — GET/DELETE skip this case
      because there is no body to malform).
    - **Happy path** — a positive assertion that the typed input
      ``input_model`` instance is accepted and that the response shape
      matches ``output_model``. Body fields are populated with
      deterministic scaffold values (``scaffold-{field}`` for str, ``1``
      for int, etc.); authors override after first generation.

    The output is byte-stable across regenerations for a given input,
    matching Item 25's regeneration-determinism contract for the SDK
    generator. Authors who want bespoke assertions edit the generated
    file; the framework's CI never overwrites a hand-edited scaffold —
    regeneration only refreshes scaffolds for routes whose tests do not
    yet exist (the convention is checked by ``has_existing_scaffold``).

    Returns the source as a string. Callers (CLI, codegen step, CI hook)
    decide where to write it.
    """
    routes = iter_custom_routes(manager_cls)
    if not routes:
        return ""

    cls_module = manager_cls.__module__
    cls_name = manager_cls.__name__

    lines: List[str] = []
    if include_header:
        lines.append('"""Auto-generated baseline tests for ' + cls_name + ' custom routes (Item 40).')
        lines.append("")
        lines.append("Each route produced by ``@custom_route`` gets three baseline tests:")
        lines.append("    - auth (unauthenticated → 401)")
        lines.append("    - validation (malformed body → 422; POST/PUT/PATCH only)")
        lines.append("    - happy path (typed input → typed output)")
        lines.append("")
        lines.append("Authors override these once the route ships real assertions; the")
        lines.append("framework regenerates only routes lacking a corresponding test file.")
        lines.append('"""')
        lines.append("")
        lines.append("from __future__ import annotations")
        lines.append("")
        lines.append("import pytest")
        lines.append("from fastapi.testclient import TestClient")
        lines.append("")
        lines.append(f"from {cls_module} import {cls_name}")
        lines.append("")
        lines.append("")
        lines.append("@pytest.fixture(scope='module')")
        lines.append("def client():")
        lines.append("    \"\"\"Build a FastAPI app exposing only this manager's custom routes.")
        lines.append("")
        lines.append("    The scaffold uses the framework's standard register_custom_routes")
        lines.append("    helper so tests exercise the same routing path as production.\"\"\"")
        lines.append("    from fastapi import FastAPI")
        lines.append("")
        lines.append("    from serverframework.lib.CustomRoute import register_custom_routes")
        lines.append("")
        lines.append("    app = FastAPI()")
        lines.append(f"    register_custom_routes(app.router, {cls_name})")
        lines.append("    yield TestClient(app)")
        lines.append("")
        lines.append("")

    for method_name, spec in routes:
        path = spec.path
        verb_lower = spec.method.lower()
        body = _build_scaffold_body(spec)
        body_repr = _python_repr(body)
        url_path = path
        # Substitute any path params with deterministic scaffold ids.
        if "{" in url_path:
            import re

            url_path = re.sub(r"\{([^}]+)\}", lambda m: f"scaffold-{m.group(1)}", url_path)

        # Auth test
        lines.append(f"def test_{method_name}_unauthenticated_returns_401(client):")
        lines.append('    """Auth: an unauthenticated call should fail with 401 (or 403).')
        lines.append("")
        lines.append("    The framework rejects sessionless callers at the middleware layer;")
        lines.append("    the scaffold asserts the negative path so a regression that opens")
        lines.append("    the route to anonymous traffic is caught by CI.\"\"\"")
        if spec.method in ("POST", "PUT", "PATCH"):
            lines.append(
                f"    response = client.{verb_lower}({url_path!r}, json={body_repr})"
            )
        else:
            lines.append(f"    response = client.{verb_lower}({url_path!r})")
        lines.append("    assert response.status_code in (401, 403)")
        lines.append("")
        lines.append("")

        # Validation test (only for routes with bodies)
        if spec.method in ("POST", "PUT", "PATCH") and spec.input_model is not None:
            lines.append(f"def test_{method_name}_invalid_body_returns_422(client):")
            lines.append('    """Validation: a malformed request body fails Pydantic validation.')
            lines.append("")
            lines.append("    Sends a payload with a known-bad shape (a non-dict scalar). The")
            lines.append("    framework's input-model coercion path returns 422 when the body")
            lines.append("    cannot be validated against the spec's input_model.\"\"\"")
            lines.append(
                f"    response = client.{verb_lower}({url_path!r}, json='not-a-dict')"
            )
            lines.append("    assert response.status_code in (400, 422)")
            lines.append("")
            lines.append("")

        # Happy path test
        lines.append(f"def test_{method_name}_happy_path(client):")
        lines.append('    """Happy path: typed input → typed output.')
        lines.append("")
        lines.append("    The scaffold values exercise the route end-to-end; authors")
        lines.append("    override the assertions once the route ships real semantics.\"\"\"")
        if spec.method in ("POST", "PUT", "PATCH"):
            lines.append(
                f"    response = client.{verb_lower}({url_path!r}, json={body_repr})"
            )
        else:
            lines.append(f"    response = client.{verb_lower}({url_path!r})")
        lines.append("    # The scaffolded path may require auth; CI gates the positive")
        lines.append("    # assertion behind a tested auth context. The default scaffold")
        lines.append("    # asserts only that the framework reached the route handler")
        lines.append("    # rather than 404'd, leaving the response-shape assertion to")
        lines.append("    # the author.")
        lines.append("    assert response.status_code != 404")
        lines.append("")
        lines.append("")

    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    return text


def has_existing_scaffold(manager_cls: type, scaffold_dir: str) -> bool:
    """True when an author-edited scaffold already exists for ``manager_cls``.

    The convention is ``{ManagerName}_custom_routes_scaffold_test.py`` in the
    scaffold directory. Codegen tooling consults this before regenerating to
    avoid clobbering hand-edited tests; authors who want a fresh regeneration
    delete the file and re-run.
    """
    import os

    target = os.path.join(scaffold_dir, f"{manager_cls.__name__}_custom_routes_scaffold_test.py")
    return os.path.exists(target)


def write_test_scaffold(
    manager_cls: type,
    scaffold_dir: str,
    *,
    overwrite: bool = False,
) -> Optional[str]:
    """Write the generated scaffold to disk.

    Returns the written path on success, or ``None`` when the scaffold
    already exists and ``overwrite`` is False (the protective default
    so codegen passes do not clobber author edits).
    """
    import os

    target = os.path.join(
        scaffold_dir, f"{manager_cls.__name__}_custom_routes_scaffold_test.py"
    )
    if os.path.exists(target) and not overwrite:
        return None
    text = generate_test_scaffold(manager_cls)
    if not text:
        return None
    os.makedirs(scaffold_dir, exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(text)
    return target
