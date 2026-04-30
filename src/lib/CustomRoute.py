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

GraphQL field emission lands alongside Item 46 (GraphQL composition); SDK
method emission lands alongside Item 25 (already shipped — extend
``SDKGenerator.py`` in a follow-up).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import (
    Callable,
    FrozenSet,
    Iterable,
    List,
    Optional,
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
    graphql_kind: Optional[str] = None
    summary: Optional[str] = None
    description: Optional[str] = None


def custom_route(
    *,
    method: str,
    path: str,
    input_model: Optional[Type[BaseModel]] = None,
    output_model: Optional[Type[BaseModel]] = None,
    authentication_type: str = "session",
    openapi_tags: Iterable[str] = (),
    expose_in: Iterable[ExposeIn] = (ExposeIn.ALL,),
    graphql_kind: Optional[str] = None,
    summary: Optional[str] = None,
    description: Optional[str] = None,
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

    prefix: Optional[str] = None
    tags: Optional[List[str]] = None


def register_custom_routes(router, manager_cls, manager_factory: Optional[Callable] = None) -> int:
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
                        # Splat validated body fields into method kwargs.
                        body = method_args["body"]
                        if hasattr(body, "model_dump"):
                            for k, v in body.model_dump().items():
                                if k in sig.parameters:
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
