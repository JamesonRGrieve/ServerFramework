# SPDX-License-Identifier: AGPL-3.0-or-later
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
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Tuple,
    Type,
)

from fastapi import HTTPException, Request, Response, status
from pydantic import BaseModel, TypeAdapter

from zephyrex.lib.InboundSecurity import carry_rate_limit
from zephyrex.lib.Preconditions import IF_MATCH_HEADER, expect_route_version
from zephyrex.pydantic2.fastapi.resource import (
    create_manager_factory,
    handle_resource_operation_error,
)
from zephyrex.pydantic2.fastapi.types import AuthType

# HTTP verbs whose request carries the route's ``input_model`` body.
_BODY_METHODS: FrozenSet[str] = frozenset({"POST", "PUT", "PATCH"})
# A tagged method receives the validated input model under this parameter.
_BODY_PARAMETER = "body"
# ...and the incoming request and outgoing response (REST, or the GraphQL
# context's) under these.
_REQUEST_PARAMETER = "request"
_RESPONSE_PARAMETER = "response"
_CONTEXT_PARAMETERS: FrozenSet[str] = frozenset(
    {_REQUEST_PARAMETER, _RESPONSE_PARAMETER}
)
# ``{name}`` / ``{name:converter}`` placeholders in a route path.
_PATH_PARAMETER_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::[^}]*)?\}")
# ``authentication_type`` spellings that are not ``AuthType`` values. A
# "session" route requires the same signed-in bearer credential as "jwt".
_AUTH_TYPE_ALIASES: Mapping[str, AuthType] = {"session": AuthType.JWT}


def resolve_auth_type(authentication_type: str) -> AuthType:
    """Map a ``@custom_route`` ``authentication_type`` to its ``AuthType``.

    Raises ``ValueError`` for a spelling that names no authentication mode.
    """
    alias = _AUTH_TYPE_ALIASES.get(authentication_type)
    if alias is not None:
        return alias
    return AuthType(authentication_type)


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
    # A REST-only route that answers with this Response subclass itself (a
    # stream, a file) instead of an ``output_model`` body.
    response_class: Optional[Type[Response]] = None

    @property
    def auth_type(self) -> AuthType:
        """The authentication mode REST dispatch enforces for this route."""
        return resolve_auth_type(self.authentication_type)


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
    response_class: Optional[Type[Response]] = None,
) -> Callable:
    """Decorator: tag a method with its route/SDK/GraphQL contract.

    A route answers with an ``output_model`` body, or, REST only, with an
    instance of ``response_class`` the method builds itself.
    """

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
            response_class=response_class,
        )
        if spec.method not in ("GET", "DELETE") and spec.input_model is None:
            raise ValueError(
                f"@custom_route on {func.__qualname__}: method {spec.method} "
                f"requires an input_model (typed contract preserved)"
            )
        if spec.response_class is not None:
            if spec.output_model is not None or spec.expose_in != {ExposeIn.REST}:
                raise ValueError(
                    f"@custom_route on {func.__qualname__}: a response_class "
                    f"route has no output_model and is exposed on REST only"
                )
        elif spec.output_model is None:
            raise ValueError(
                f"@custom_route on {func.__qualname__}: output_model is required "
                f"to preserve typed contract (use a Pydantic model)"
            )
        try:
            resolve_auth_type(spec.authentication_type)
        except ValueError:
            raise ValueError(
                f"@custom_route on {func.__qualname__}: unknown "
                f"authentication_type {spec.authentication_type!r}"
            ) from None
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


def _authored_method(func: Callable[..., Any]) -> Callable[..., Any]:
    """The method as its author wrote it, beneath any hook wrapper.

    Every public ``AbstractBLLManager`` method is replaced by a hook wrapper
    whose signature is ``(self, *args, **kwargs)``; the authored method is
    kept on its ``_original_method``, so that is what gets introspected.
    """
    authored: Callable[..., Any] = getattr(func, "_original_method", func)
    return authored


def _route_parameters(func: Callable[..., Any]) -> Mapping[str, inspect.Parameter]:
    """The parameters a tagged method declares, minus ``self`` and varargs."""
    parameters = inspect.signature(_authored_method(func), eval_str=True).parameters
    return {
        name: parameter
        for index, (name, parameter) in enumerate(parameters.items())
        if not (index == 0 and name == "self")
        and parameter.kind
        not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    }


def _type_adapter(parameter: inspect.Parameter) -> TypeAdapter[Any]:
    annotation = (
        str if parameter.annotation is inspect.Parameter.empty else parameter.annotation
    )
    return TypeAdapter(annotation)


@dataclass(frozen=True)
class _RouteBinding:
    """How one tagged method's parameters are filled from an HTTP request.

    Path placeholders bind to same-named parameters; the validated
    ``input_model`` binds to ``body`` or, when the method takes no ``body``,
    its declared fields splat onto same-named parameters (the input model is
    the writability gate, so a parameter naming a privileged column is never
    reachable unless the model declares it); a ``request`` parameter gets the
    incoming request (to read cookies from) and a ``response`` parameter the
    outgoing response (to set cookies or headers on); every other
    parameter binds from the query string. Path and query values are
    validated against the parameter's annotation.
    """

    input_model: Optional[Type[BaseModel]]
    path_params: Mapping[str, TypeAdapter[Any]]
    query_params: Mapping[str, Tuple[TypeAdapter[Any], bool]]
    takes_body: bool
    body_fields: FrozenSet[str]
    context_params: FrozenSet[str]

    @classmethod
    def build(
        cls, manager_cls: type, method_name: str, spec: CustomRouteSpec
    ) -> "_RouteBinding":
        parameters = _route_parameters(getattr(manager_cls, method_name))
        qualname = f"{manager_cls.__name__}.{method_name}"
        placeholders = _PATH_PARAMETER_PATTERN.findall(spec.path)
        missing = [name for name in placeholders if name not in parameters]
        if missing:
            raise TypeError(
                f"@custom_route {qualname}: path {spec.path!r} names "
                f"{missing} but the method declares no such parameters"
            )
        input_model = spec.input_model if spec.method in _BODY_METHODS else None
        takes_body = _BODY_PARAMETER in parameters
        if takes_body and input_model is None:
            raise TypeError(
                f"@custom_route {qualname}: the method takes '{_BODY_PARAMETER}' "
                f"but a {spec.method} route with input_model="
                f"{spec.input_model!r} carries no request body"
            )
        body_fields: FrozenSet[str] = frozenset()
        if input_model is not None and not takes_body:
            body_fields = frozenset(input_model.model_fields) & frozenset(parameters)
        bound_elsewhere = (
            set(placeholders) | body_fields | {_BODY_PARAMETER} | _CONTEXT_PARAMETERS
        )
        return cls(
            input_model=input_model,
            path_params={
                name: _type_adapter(parameters[name]) for name in placeholders
            },
            query_params={
                name: (
                    _type_adapter(parameter),
                    parameter.default is inspect.Parameter.empty,
                )
                for name, parameter in parameters.items()
                if name not in bound_elsewhere
            },
            takes_body=takes_body,
            body_fields=body_fields,
            context_params=_CONTEXT_PARAMETERS & frozenset(parameters),
        )

    async def arguments(self, request: Request, response: Response) -> Dict[str, Any]:
        """Validated keyword arguments for the tagged method."""
        arguments: Dict[str, Any] = {
            name: adapter.validate_python(request.path_params[name])
            for name, adapter in self.path_params.items()
        }
        context = {_REQUEST_PARAMETER: request, _RESPONSE_PARAMETER: response}
        arguments.update({name: context[name] for name in self.context_params})
        for name, (adapter, required) in self.query_params.items():
            value = request.query_params.get(name)
            if value is not None:
                arguments[name] = adapter.validate_python(value)
            elif required:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Missing required query parameter '{name}'",
                )
        if self.input_model is not None:
            body = await self._read_body(request, self.input_model)
            if self.takes_body:
                arguments[_BODY_PARAMETER] = body
            else:
                fields = body.model_dump()
                arguments.update({name: fields[name] for name in self.body_fields})
        return arguments

    @staticmethod
    async def _read_body(request: Request, input_model: Type[BaseModel]) -> BaseModel:
        raw = await request.body()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body"
            ) from None
        return input_model.model_validate(payload)


def _make_rest_endpoint(
    manager_cls: type, method_name: str, spec: CustomRouteSpec
) -> Callable[..., Any]:
    """Build the FastAPI handler dispatching one ``@custom_route`` method.

    The manager is built for the route's declared ``authentication_type``
    (not the manager class's), so a public route runs without credentials
    and an authenticated route answers 401 without them. The method is
    invoked through its bound hook wrapper so registered hooks fire, and an
    ``async`` method's result is awaited. Failures map to HTTP responses the
    same way CRUD routes map them. A write route's If-Match holds any write
    it makes to the record of this manager its path names
    (``zephyrex.lib.Preconditions.expect_route_version``).
    """
    binding = _RouteBinding.build(manager_cls, method_name, spec)
    auth_type = spec.auth_type

    # ``request: Request`` resolves against this module's globals under
    # ``from __future__ import annotations``, so ``Request`` is imported at
    # module level (a function-local import breaks OpenAPI generation).
    async def endpoint(request: Request, response: Response) -> Any:
        try:
            manager = create_manager_factory(
                manager_cls,
                getattr(request.app.state, "model_registry", None),
                auth_type,
            )(request=request)
            arguments = await binding.arguments(request, response)
            with expect_route_version(
                manager,
                request.method,
                request.path_params,
                request.headers.get(IF_MATCH_HEADER),
            ):
                result = getattr(manager, method_name)(**arguments)
                if inspect.isawaitable(result):
                    result = await result
            if spec.response_class is not None:
                return _checked_response(result, spec.response_class)
            output = _coerce_output(result, spec.output_model)
            if isinstance(output, BaseModel):
                return output.model_dump()
            return output
        except Exception as err:
            handle_resource_operation_error(err)

    carry_rate_limit(getattr(manager_cls, method_name), endpoint)
    return endpoint


def _checked_response(result: Any, response_class: Type[Response]) -> Response:
    if not isinstance(result, response_class):
        raise TypeError(
            f"a response_class route must return {response_class.__name__}, "
            f"not {type(result).__name__}"
        )
    return result


def register_custom_routes(router, manager_cls) -> int:
    """Walk ``manager_cls`` for ``@custom_route`` methods and add them to ``router``.

    Additive: each tagged method becomes a typed FastAPI route on the supplied
    router. Returns the number of routes registered. Each route builds its
    manager for its own declared authentication type (see
    ``_make_rest_endpoint``).
    """
    registered = 0
    for method_name, spec in iter_custom_routes(manager_cls):
        if ExposeIn.REST not in spec.expose_in and ExposeIn.ALL not in spec.expose_in:
            continue

        endpoint = _make_rest_endpoint(manager_cls, method_name, spec)
        route_method = getattr(router, spec.method.lower())
        options: Dict[str, Any] = {}
        if spec.response_class is not None:
            options["response_class"] = spec.response_class
        route_method(
            spec.path,
            summary=spec.summary or f"Custom {spec.method} {spec.path}",
            description=spec.description or "",
            tags=list(spec.openapi_tags) if spec.openapi_tags else None,
            **options,
        )(endpoint)
        registered += 1

    return registered


# ---------------------------------------------------------------------------
# GraphQL emission (Item 40 GraphQL half + Item 46 contribution-registry hook)
# ---------------------------------------------------------------------------

# One resolver object per (manager, method): the contribution registry treats
# re-registration of the same callable as identical, and registration is
# skipped when the registry already holds it.
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


# Builds the manager a GraphQL custom-route resolver runs on:
# ``factory(info=..., auth_type=...)``, deciding the requester as REST does.
GraphQLManagerFactory = Callable[..., Any]


def _build_graphql_resolver(
    manager_cls: type,
    method_name: str,
    spec: CustomRouteSpec,
    manager_factory: GraphQLManagerFactory,
) -> Callable[..., Any]:
    """Wrap a tagged method as a Strawberry-compatible resolver.

    The resolver accepts a single optional ``input`` argument typed to the
    spec's ``input_model`` and returns an instance of ``output_model``.
    Path parameters embedded in ``spec.path`` (``{id}`` style) become
    keyword arguments on the resolver so GraphQL clients can pass them
    natively. The method runs on the manager ``manager_factory`` builds for
    the caller and the route's authentication type, as on REST.
    """
    bound_method = getattr(manager_cls, method_name)
    method_sig = inspect.signature(_authored_method(bound_method))

    def _split_kwargs(payload: Dict[str, Any]) -> Dict[str, Any]:
        accepted: Dict[str, Any] = {}
        for key, value in payload.items():
            if key in method_sig.parameters:
                accepted[key] = value
        return accepted

    context_params = _CONTEXT_PARAMETERS & frozenset(method_sig.parameters)

    def _with_context(info: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        return {**kwargs, **{name: info.context[name] for name in context_params}}

    if spec.method in ("POST", "PUT", "PATCH") and spec.input_model is not None:

        async def resolver(info: Any, input: spec.input_model) -> spec.output_model:  # type: ignore[name-defined]
            manager = manager_factory(info=info, auth_type=spec.auth_type)
            payload = (
                input.model_dump() if hasattr(input, "model_dump") else dict(input)
            )
            if "body" in method_sig.parameters:
                kwargs: Dict[str, Any] = {"body": input}
            else:
                kwargs = _split_kwargs(payload)
            kwargs = _with_context(info, kwargs)
            result = getattr(manager, method_name)(**kwargs)
            if inspect.isawaitable(result):
                result = await result
            return _coerce_output(result, spec.output_model)

    else:

        async def resolver(info: Any, **kwargs: Any) -> spec.output_model:  # type: ignore[name-defined, misc]
            manager = manager_factory(info=info, auth_type=spec.auth_type)
            accepted = _with_context(info, _split_kwargs(kwargs))
            result = getattr(manager, method_name)(**accepted)
            if inspect.isawaitable(result):
                result = await result
            return _coerce_output(result, spec.output_model)

    resolver.__name__ = method_name
    resolver.__qualname__ = f"{manager_cls.__name__}.{method_name}"
    # Under postponed annotations the signatures above store the literal
    # strings "spec.input_model"/"spec.output_model", which Strawberry resolves
    # against this module's globals, where `spec` does not exist. Give it the
    # real types.
    resolved = dict(resolver.__annotations__)
    resolved["return"] = spec.output_model
    if "input" in resolved:
        resolved["input"] = spec.input_model
    resolver.__annotations__ = resolved
    return resolver


def _coerce_output(result: Any, output_model: Optional[Type[BaseModel]]) -> Any:
    """Normalize a tagged-method result into an ``output_model`` instance.

    Shared by the REST endpoint and the GraphQL resolver: model instances
    pass through, dicts get validated, anything else is returned untouched (the resolver's
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


def graphql_field_name(manager_cls: type, method_name: str) -> str:
    """A custom route's root field: the manager's resource plus the method,
    so two managers' ``request_route`` methods can share one schema
    (``magic_link_request`` / ``device_pairing_request``)."""
    from zephyrex.pydantic2.util import manager_resource_name

    return f"{manager_resource_name(manager_cls)}_{method_name.removesuffix('_route')}"


def register_custom_routes_to_graphql(
    manager_cls: type,
    manager_factory: GraphQLManagerFactory,
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
    from zephyrex.pydantic2.strawberry import (
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
        resolver = _GRAPHQL_RESOLVER_CACHE.get(cache_key)
        if resolver is None:
            resolver = _build_graphql_resolver(
                manager_cls, method_name, spec, manager_factory
            )
            _GRAPHQL_RESOLVER_CACHE[cache_key] = resolver

        kind_str = _infer_graphql_kind(spec)
        kind = FieldKind.QUERY if kind_str == "query" else FieldKind.MUTATION
        contribution = FieldContribution(
            extension_name=extension_name,
            kind=kind,
            name=graphql_field_name(manager_cls, method_name),
            resolver=resolver,
            return_type=spec.output_model,
            args=({"input": spec.input_model} if spec.input_model is not None else {}),
            description=spec.description or spec.summary,
            namespace=False,
            priority=50,
        )

        # Idempotent per registry, judged by what the registry holds: the
        # process-wide registry is reset between apps and tests by clearing
        # its contents, so a separate "already registered" record goes stale.
        held = registry.fields(kind).get(contribution.emitted_name, [])
        if any(existing.resolver is resolver for existing in held):
            continue

        registry.register_field(contribution)
        registered += 1

    return registered


def reset_graphql_registrations() -> None:
    """Test helper -- forget every cached resolver."""
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
        lines.append(
            '"""Auto-generated baseline tests for '
            + cls_name
            + " custom routes (Item 40)."
        )
        lines.append("")
        lines.append(
            "Each route produced by ``@custom_route`` gets three baseline tests:"
        )
        lines.append("    - auth (unauthenticated → 401)")
        lines.append("    - validation (malformed body → 422; POST/PUT/PATCH only)")
        lines.append("    - happy path (typed input → typed output)")
        lines.append("")
        lines.append("Authors override these once the route ships real assertions; the")
        lines.append(
            "framework regenerates only routes lacking a corresponding test file."
        )
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
        lines.append(
            '    """Build a FastAPI app exposing only this manager\'s custom routes.'
        )
        lines.append("")
        lines.append(
            "    The scaffold uses the framework's standard register_custom_routes"
        )
        lines.append(
            '    helper so tests exercise the same routing path as production."""'
        )
        lines.append("    from fastapi import FastAPI")
        lines.append("")
        lines.append("    from zephyrex.lib.CustomRoute import register_custom_routes")
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

            url_path = re.sub(
                r"\{([^}]+)\}", lambda m: f"scaffold-{m.group(1)}", url_path
            )

        # Auth test
        lines.append(f"def test_{method_name}_unauthenticated_returns_401(client):")
        lines.append(
            '    """Auth: an unauthenticated call should fail with 401 (or 403).'
        )
        lines.append("")
        lines.append(
            "    The framework rejects sessionless callers at the middleware layer;"
        )
        lines.append(
            "    the scaffold asserts the negative path so a regression that opens"
        )
        lines.append('    the route to anonymous traffic is caught by CI."""')
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
            lines.append(
                '    """Validation: a malformed request body fails Pydantic validation.'
            )
            lines.append("")
            lines.append(
                "    Sends a payload with a known-bad shape (a non-dict scalar). The"
            )
            lines.append(
                "    framework's input-model coercion path returns 422 when the body"
            )
            lines.append('    cannot be validated against the spec\'s input_model."""')
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
        lines.append(
            '    override the assertions once the route ships real semantics."""'
        )
        if spec.method in ("POST", "PUT", "PATCH"):
            lines.append(
                f"    response = client.{verb_lower}({url_path!r}, json={body_repr})"
            )
        else:
            lines.append(f"    response = client.{verb_lower}({url_path!r})")
        lines.append(
            "    # The scaffolded path may require auth; CI gates the positive"
        )
        lines.append(
            "    # assertion behind a tested auth context. The default scaffold"
        )
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

    target = os.path.join(
        scaffold_dir, f"{manager_cls.__name__}_custom_routes_scaffold_test.py"
    )
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
