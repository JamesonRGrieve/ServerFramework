"""REST upstream federation primitives (Item 16 — REST half).

Counterpart to :mod:`zephyrex.extensions.federation.BLL_Federation_GQL`. Federates external
REST upstreams two ways:

* **REST → REST.** The upstream stays REST. The framework's existing
  ``AbstractExternalModel`` + ``RouterMixin`` pipeline already covers this
  case — this module only adds the schema-importer side, generating Pydantic
  models from an OpenAPI document so an upstream that only ships a spec
  becomes a fully-typed external model without per-resource hand-coding.
* **REST → GQL.** The upstream stays REST but is also projected onto the
  framework's GraphQL surface. Generated Pydantic models flow into
  ``Pydantic2Strawberry`` automatically; this module supplies the transport
  adapter that translates ``*_via_provider`` calls into outbound REST
  requests through ``ProviderHTTPClient`` (so rotation, auth strategy,
  idempotency, and rate limiting all participate in upstream calls).

The design intent: do the schema lift once, into Pydantic. Both inbound
surfaces (REST and GraphQL) come for free via the existing pipelines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    ClassVar,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    Type,
)

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# OpenAPI → Pydantic
# ---------------------------------------------------------------------------


OPENAPI_TYPE_TO_PY: Dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}


@dataclass
class OperationSpec:
    """One importable REST operation discovered in an OpenAPI document.

    ``name`` is the synthesized Python identifier the transport uses to
    dispatch the call (typically the OpenAPI ``operationId``, with
    fallback to ``{method}_{path_to_snake}``). ``method`` is upper-case
    HTTP. ``path_template`` retains ``{param}`` placeholders so the transport
    can substitute them from the inbound call's keyword arguments.
    """

    name: str
    method: str
    path_template: str
    parameters: List[Dict[str, Any]] = field(default_factory=list)
    request_body_schema: Optional[str] = None
    response_schema: Optional[str] = None


@dataclass
class OpenAPIToPydanticResult:
    """Models, enums, and operations imported from an OpenAPI document.

    Returned from :func:`openapi_to_pydantic_models`. Models are keyed by
    the prefixed component name; operations are keyed by ``operation.name``.
    """

    models: Dict[str, Type[BaseModel]] = field(default_factory=dict)
    enums: Dict[str, type] = field(default_factory=dict)
    operations: Dict[str, OperationSpec] = field(default_factory=dict)


def openapi_to_pydantic_models(
    spec: Mapping[str, Any], *, prefix: Optional[str] = None
) -> OpenAPIToPydanticResult:
    """Generate Pydantic models and an :class:`OperationSpec` table from a
    parsed OpenAPI 3.x document.

    The importer handles:

    * ``components.schemas.<X>`` → Pydantic model class (prefixed if requested)
    * ``$ref`` — resolved against ``components.schemas`` for object-shape refs
    * ``allOf`` — flattened by merging properties
    * ``oneOf`` / ``anyOf`` — rendered as ``Union[...]`` annotations
    * ``enum`` — string-valued enums become Python ``Enum`` classes
    * ``paths.<path>.<method>`` → :class:`OperationSpec`

    Returns models keyed by the prefixed type name and operations keyed by
    a synthesized ``operation_id`` (preferring OpenAPI's ``operationId``).
    """

    schemas = (spec.get("components") or {}).get("schemas") or {}
    paths = spec.get("paths") or {}

    def _full(name: str) -> str:
        if not prefix or name.startswith(prefix):
            return name
        return f"{prefix}{name}"

    enums: Dict[str, type] = {}
    models: Dict[str, Type[BaseModel]] = {}

    # First pass: enum definitions (string-valued only).
    from enum import Enum

    for raw_name, schema in schemas.items():
        if isinstance(schema, Mapping) and schema.get("enum") and (
            schema.get("type") in (None, "string")
        ):
            full = _full(raw_name)
            enums[full] = Enum(  # type: ignore[assignment]
                full,
                {str(v).replace("-", "_").replace(" ", "_"): v for v in schema["enum"]},
            )

    # Second pass: object schemas → Pydantic models (forward refs by name).
    forward_specs: Dict[str, List[Tuple[str, Any, bool]]] = {}
    for raw_name, schema in schemas.items():
        full = _full(raw_name)
        if full in enums:
            continue
        flattened = _flatten_schema(schema, schemas)
        if flattened.get("type") not in (None, "object"):
            continue
        props = flattened.get("properties") or {}
        required = set(flattened.get("required") or [])
        specs: List[Tuple[str, Any, bool]] = []
        for prop_name, prop_schema in props.items():
            ann = _openapi_to_python(prop_schema, schemas, _full, enums)
            specs.append((prop_name, ann, prop_name in required))
        forward_specs[full] = specs

    for full, specs in forward_specs.items():
        from typing import Optional as _Optional

        annotations: Dict[str, Any] = {}
        defaults: Dict[str, Any] = {}
        for fname, ann, required in specs:  # type: ignore[assignment]
            annotations[fname] = ann if required else _Optional[ann]
            if not required:
                defaults[fname] = None
        cls_dict: Dict[str, Any] = {
            "__annotations__": annotations,
            "model_config": ConfigDict(arbitrary_types_allowed=True),
            **defaults,
        }
        models[full] = type(full, (BaseModel,), cls_dict)

    # Resolve string forward references via `model_rebuild`.
    namespace = {**models, **enums}
    for cls in models.values():
        try:
            cls.model_rebuild(_types_namespace=namespace)
        except Exception:
            continue

    # Operations from paths.
    operations: Dict[str, OperationSpec] = {}
    for path, methods in (paths.items() if isinstance(paths, Mapping) else ()):
        if not isinstance(methods, Mapping):
            continue
        for method, op in methods.items():
            method_upper = method.upper()
            if method_upper not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            if not isinstance(op, Mapping):
                continue
            op_name = op.get("operationId") or _synth_op_name(method_upper, path)
            params = list(op.get("parameters") or [])
            request_body = op.get("requestBody")
            req_schema_name: Optional[str] = None
            if isinstance(request_body, Mapping):
                content = request_body.get("content") or {}
                json_part = content.get("application/json") or {}
                req_schema_name = _ref_to_full(
                    (json_part.get("schema") or {}).get("$ref"), _full
                )
            responses = op.get("responses") or {}
            resp_schema_name: Optional[str] = None
            ok = responses.get("200") or responses.get("201") or responses.get("default")
            if isinstance(ok, Mapping):
                content = ok.get("content") or {}
                json_part = content.get("application/json") or {}
                resp_schema_name = _ref_to_full(
                    (json_part.get("schema") or {}).get("$ref"), _full
                )
            operations[op_name] = OperationSpec(
                name=op_name,
                method=method_upper,
                path_template=path,
                parameters=params,
                request_body_schema=req_schema_name,
                response_schema=resp_schema_name,
            )

    return OpenAPIToPydanticResult(models=models, enums=enums, operations=operations)


def _flatten_schema(
    schema: Mapping[str, Any], components: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Flatten an ``allOf`` schema into its merged properties."""

    if not isinstance(schema, Mapping):
        return {}
    if "allOf" in schema:
        merged: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}
        for piece in schema["allOf"]:
            resolved = _resolve_ref(piece, components) if "$ref" in piece else piece
            inner = _flatten_schema(resolved, components)
            merged["properties"].update(inner.get("properties") or {})
            merged["required"].extend(inner.get("required") or [])
        return merged
    if "$ref" in schema:
        return _flatten_schema(_resolve_ref(schema, components), components)
    return schema


def _resolve_ref(
    schema: Mapping[str, Any], components: Mapping[str, Any]
) -> Mapping[str, Any]:
    ref = schema.get("$ref")
    if not isinstance(ref, str):
        return schema
    target = ref.split("/")[-1]
    return components.get(target, {})  # type: ignore[no-any-return]


_REF_RE = re.compile(r"#/components/schemas/(.+)$")


def _ref_to_full(
    ref: Optional[str], fullname: Callable[[str], str]
) -> Optional[str]:
    if not isinstance(ref, str):
        return None
    m = _REF_RE.match(ref)
    if not m:
        return None
    return fullname(m.group(1))


def _openapi_to_python(
    schema: Any,
    components: Mapping[str, Any],
    fullname: Callable[[str], str],
    enums: Mapping[str, type],
) -> Any:
    """Translate an OpenAPI schema fragment into a Python annotation."""

    from typing import Union as _Union, List as _List

    if not isinstance(schema, Mapping):
        return Any
    if "$ref" in schema:
        full = _ref_to_full(schema["$ref"], fullname)
        if full and full in enums:
            return enums[full]
        return full or Any
    if schema.get("oneOf") or schema.get("anyOf"):
        members = tuple(
            _openapi_to_python(s, components, fullname, enums)
            for s in (schema.get("oneOf") or schema.get("anyOf") or ())
        )
        return _Union[members] if len(members) > 1 else (members[0] if members else Any)
    if schema.get("enum") and schema.get("type") in (None, "string"):
        # Inline enum — promote to a Literal-like Python type.
        from typing import Literal

        values = tuple(schema["enum"])
        return Literal[values] if values else str
    t = schema.get("type")
    if t == "array":
        return _List[  # type: ignore[misc]
            _openapi_to_python(schema.get("items") or {}, components, fullname, enums)
        ]
    if t in OPENAPI_TYPE_TO_PY:
        return OPENAPI_TYPE_TO_PY[t]
    return Any


def _synth_op_name(method: str, path: str) -> str:
    """Synthesize an ``operationId`` when the OpenAPI spec omits one."""

    cleaned = re.sub(r"[{}]", "", path)
    parts = [p for p in cleaned.split("/") if p]
    return f"{method.lower()}_" + "_".join(p.replace("-", "_") for p in parts) if parts else method.lower()


# ---------------------------------------------------------------------------
# REST upstream transport
# ---------------------------------------------------------------------------


class RESTUpstreamTransport:
    """Send REST operations against a federated upstream.

    Wraps ``ProviderHTTPClient`` (Item 31). Concrete external models call
    into this transport from their ``*_via_provider`` methods so rotation,
    auth strategy, idempotency, and rate limiting all participate in
    upstream calls without this module re-implementing them.

    ``base_url`` is prefixed onto the path template; ``operations`` is the
    ``OperationSpec`` table produced by :func:`openapi_to_pydantic_models`.
    The transport does not need the OpenAPI document at runtime — the
    operation table carries everything it needs.
    """

    def __init__(
        self,
        http_client: Any,
        base_url: str,
        operations: Mapping[str, OperationSpec],
    ) -> None:
        self._http = http_client
        self._base = base_url.rstrip("/")
        self._operations = dict(operations)

    def get_operation(self, name: str) -> OperationSpec:
        op = self._operations.get(name)
        if op is None:
            raise KeyError(f"Unknown REST operation: {name!r}")
        return op

    async def send(
        self,
        *,
        operation: str,
        path_args: Optional[Mapping[str, Any]] = None,
        query_args: Optional[Mapping[str, Any]] = None,
        body: Any = None,
        idempotency_key: Optional[str] = None,
        requester_id: Optional[str] = None,
    ) -> Any:
        """Send an operation upstream and return the JSON-decoded body."""

        op = self.get_operation(operation)
        url = self._render_path(op, path_args or {})
        kwargs: Dict[str, Any] = {
            "params": dict(query_args or {}),
            "requester_id": requester_id,
        }
        if idempotency_key is not None:
            kwargs["idempotency_key"] = idempotency_key
        if body is not None:
            kwargs["json"] = body
        method = op.method.lower()
        verb = getattr(self._http, method)
        return await verb(url, **kwargs)

    def send_sync(
        self,
        *,
        operation: str,
        path_args: Optional[Mapping[str, Any]] = None,
        query_args: Optional[Mapping[str, Any]] = None,
        body: Any = None,
        idempotency_key: Optional[str] = None,
        requester_id: Optional[str] = None,
    ) -> Any:
        op = self.get_operation(operation)
        url = self._render_path(op, path_args or {})
        kwargs: Dict[str, Any] = {
            "params": dict(query_args or {}),
            "requester_id": requester_id,
        }
        if idempotency_key is not None:
            kwargs["idempotency_key"] = idempotency_key
        if body is not None:
            kwargs["json"] = body
        method = op.method.lower()
        verb = getattr(self._http, method)
        return verb(url, **kwargs)

    def _render_path(
        self, op: OperationSpec, path_args: Mapping[str, Any]
    ) -> str:
        rendered = op.path_template
        for k, v in path_args.items():
            rendered = rendered.replace("{" + k + "}", str(v))
        return f"{self._base}{rendered}"


# ---------------------------------------------------------------------------
# REST → GQL projection
# ---------------------------------------------------------------------------


def derive_external_models(
    *,
    pydantic_result: "Any",
    transport: RESTUpstreamTransport,
    crud_map: Optional[Mapping[str, Mapping[str, str]]] = None,
) -> Dict[str, Type[BaseModel]]:
    """Bind imported Pydantic models to a REST transport.

    For each model we synthesize a small ``AbstractExternalModel``-shaped
    subclass whose ``*_via_provider`` static methods dispatch through the
    given transport. ``crud_map`` lets callers override the default
    ``{model_name: {action: operation_name}}`` mapping; by default the
    importer guesses ``get_<model>``, ``list_<model>``, ``create_<model>``,
    ``update_<model>``, ``delete_<model>``.

    The resulting classes can be registered with the framework's model
    registry alongside hand-written external models. Once registered, the
    existing ``Pydantic2FastAPI`` and ``Pydantic2Strawberry`` pipelines mount
    them on both inbound surfaces; this is the cleanest way to give a REST
    upstream a GraphQL face.
    """

    from zephyrex.extensions.AbstractExternalModel import AbstractExternalModel

    out: Dict[str, Type[BaseModel]] = {}
    crud_map = dict(crud_map or {})
    models = getattr(pydantic_result, "models", None) or {}
    for name, model_cls in models.items():
        actions = crud_map.get(name) or _guess_crud_actions(name)

        def _make_methods(actions: Mapping[str, str], transport=transport):
            def _create_via_provider(provider_instance, **kwargs):
                op = actions.get("create")
                if not op:
                    return None
                path_args, query_args, body = _split_args(transport, op, kwargs)
                return transport.send_sync(
                    operation=op,
                    path_args=path_args,
                    query_args=query_args,
                    body=body,
                )

            def _get_via_provider(provider_instance, external_id):
                op = actions.get("get")
                if not op:
                    return None
                return transport.send_sync(
                    operation=op,
                    path_args={"id": external_id},
                )

            def _list_via_provider(provider_instance, **kwargs):
                op = actions.get("list")
                if not op:
                    return []
                return transport.send_sync(
                    operation=op,
                    query_args=kwargs,
                )

            def _update_via_provider(provider_instance, external_id, **kwargs):
                op = actions.get("update")
                if not op:
                    return None
                return transport.send_sync(
                    operation=op,
                    path_args={"id": external_id},
                    body=kwargs,
                )

            def _delete_via_provider(provider_instance, external_id):
                op = actions.get("delete")
                if not op:
                    return None
                return transport.send_sync(
                    operation=op,
                    path_args={"id": external_id},
                )

            return {
                "create_via_provider": staticmethod(_create_via_provider),
                "get_via_provider": staticmethod(_get_via_provider),
                "list_via_provider": staticmethod(_list_via_provider),
                "update_via_provider": staticmethod(_update_via_provider),
                "delete_via_provider": staticmethod(_delete_via_provider),
            }

        external_attrs = {
            "external_resource": name.lower(),
            "raises_typed_errors": True,
            **_make_methods(actions),
        }
        # Compose the upstream-bound external model. Inheriting from both
        # the imported model and AbstractExternalModel preserves the schema
        # (so Pydantic2Strawberry sees a valid Pydantic model) while also
        # giving it the ``*_via_provider`` static methods AbstractExternalModel
        # subclasses are required to declare.
        bound = type(
            f"{name}Federated",
            (model_cls, AbstractExternalModel),
            external_attrs,
        )
        out[name] = bound
    return out


def _guess_crud_actions(model_name: str) -> Dict[str, str]:
    """Heuristic guess at OpenAPI-style operation IDs for a model."""

    base = model_name.lower()
    return {
        "get": f"get_{base}",
        "list": f"list_{base}",
        "create": f"create_{base}",
        "update": f"update_{base}",
        "delete": f"delete_{base}",
    }


def _split_args(
    transport: RESTUpstreamTransport,
    operation: str,
    kwargs: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], Any]:
    """Split inbound kwargs into ``(path_args, query_args, body)``.

    Path placeholders (``{id}``) take precedence; remaining keys go to query
    string for GET/DELETE and to the body otherwise.
    """

    op = transport.get_operation(operation)
    path_keys: Set[str] = set(re.findall(r"\{(\w+)\}", op.path_template))
    path_args: Dict[str, Any] = {}
    rest: Dict[str, Any] = {}
    for k, v in kwargs.items():
        if k in path_keys:
            path_args[k] = v
        else:
            rest[k] = v
    if op.method in {"GET", "DELETE"}:
        return path_args, rest, None
    return path_args, {}, rest


__all__ = [
    "OPENAPI_TYPE_TO_PY",
    "OperationSpec",
    "OpenAPIToPydanticResult",
    "openapi_to_pydantic_models",
    "RESTUpstreamTransport",
    "derive_external_models",
]
