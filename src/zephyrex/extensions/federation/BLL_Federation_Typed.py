# SPDX-License-Identifier: AGPL-3.0-or-later
"""Typed, table-less federated models: an upstream's record types served on
REST and GraphQL as typed models, live, with nothing stored here.

**The contract.** A federated source (:class:`AbstractFederatedSource`) is
one upstream as one account sees it: an ERPNext site, a WordPress site. It
names itself with a stable ``namespace`` and supplies

- a type catalogue (:meth:`AbstractFederatedSource.catalogue`), read once
  when the app boots: one :class:`FederatedType` per record type, each with
  the JSON Schema of a record (nested types under ``$defs``; ``readOnly``
  marking what only the upstream sets), the field naming a record, the
  field holding its version, and the operations it supports;
- per-type CRUD, through a :class:`FederatedSession` made for one requester
  (:meth:`AbstractFederatedSource.session`), which refuses with 404 whoever
  may not use the source.

**The framework.** :func:`lift_type` lifts each type's schema into two
Pydantic models by way of the OpenAPI importer
(``BLL_Federation_REST.openapi_to_pydantic_models``): the record, every
field optional (an upstream may leave any out), and its write payload,
without the read-only fields and refusing a field the type lacks. Each
nested object becomes a model of its own. :func:`synthesize_manager` makes a
``RouterMixin`` manager whose typed routes call the session;
:func:`bind_federated_sources` binds the record with
``ModelRegistry.bind_external``, so the registry routes it on REST and
GraphQL and never makes a table for it.

**Names.** A source's types are namespaced by its ``namespace``
(``[a-z][a-z0-9_]*``, e.g. ``erpnext_3f2a9c1d``), a type by its slug, its
upstream name in snake case (``Sales Invoice`` -> ``sales_invoice``;
``_2``, ``_3``... for a slug two names share, in catalogue order):

- REST: ``POST /v1/federated/<namespace>/<slug>/{list,get,create,update,delete}``
- GraphQL fields: ``<namespace>_<slug>_<operation>`` (camelCased by the
  schema: ``erpnext3f2a9c1dSalesInvoiceGet``)
- models: namespace and slug in Pascal case (``Erpnext3f2a9c1dSalesInvoice``;
  GraphQL type ``Erpnext3f2a9c1dSalesInvoiceType``), a nested type the
  name of what holds it and its own (``Erpnext3f2a9c1dSalesInvoiceSalesInvoiceItem``
  for a definition, ``WordpressAb12cd34PostTitle`` for an inline object), a
  write payload suffixed ``Write``. No underscore: GraphQL type names are
  re-cased at each one.

A namespace two sources share is served for the first only. A name taken by
a local model is refused by the registry and the type is left out.

**Versions.** A record's version is its ``version_field``, its ETag. An
update or a delete names the version it was based on in ``If-Match`` (REST)
or ``if_match`` (the input, as on GraphQL); with ``IF_MATCH_REQUIRED`` one
that names none is 428 before the upstream is called. The session compares
or forwards the version and raises :class:`StaleRecord` for a record that
has moved on, answered 412 with the record as it now is. A type with no
version field is served without update and delete: a save held to no
version would overwrite blindly.

Every operation is live: nothing is kept between requests.
"""

from __future__ import annotations

import asyncio
import re
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    NoReturn,
    Optional,
    Sequence,
    Set,
    Tuple,
    Type,
)

from fastapi import HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from zephyrex.extensions.federation.BLL_Federation_REST import (
    openapi_to_pydantic_models,
)
from zephyrex.lib.CustomRoute import custom_route
from zephyrex.lib.Logging import logger
from zephyrex.lib.Preconditions import (
    IF_MATCH_HEADER,
    STALE_DETAIL,
    IfMatch,
    PreconditionError,
    save_expectation,
)
from zephyrex.lib.TypeUnions import unwrap_optional
from zephyrex.logic.AbstractLogicManager import AbstractBLLManager, _cache_sync_run
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin, RouteType

FEDERATED_PREFIX = "/v1/federated"
DEFAULT_PAGE_LENGTH = 20
MAX_PAGE_LENGTH = 500
# How long a source's catalogue may take to read at boot, every type's
# schema included, before its types are left out.
CATALOGUE_TIMEOUT_SECONDS = 300.0
NAMESPACE_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_FIELD_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_NOT_ALPHANUMERIC = re.compile(r"[^0-9A-Za-z]+")
_LOCAL_REF = re.compile(r"^#/(?:\$defs|definitions)/(.+)$")
# Pydantic warns when a field shadows a BaseModel attribute (an upstream may
# well have a ``json`` or ``copy`` field); the field still works.
SHADOW_WARNING = r'Field name ".*" in ".*" shadows an attribute in parent'
_WRITE_SUFFIX = "Write"
_JSON_SCALARS = frozenset({"string", "integer", "number", "boolean"})


class Operation(str, Enum):
    """What a federated type may serve."""

    LIST = "list"
    GET = "get"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


ALL_OPERATIONS: FrozenSet[Operation] = frozenset(Operation)
# What needs a version to be held to.
SAVES: FrozenSet[Operation] = frozenset({Operation.UPDATE, Operation.DELETE})


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FederatedType:
    """One record type a source serves.

    ``json_schema`` is a record's JSON Schema: an object whose
    ``properties`` are its fields, nested types under ``$defs`` (or
    ``definitions``) referred to by ``$ref``, inline objects allowed.
    ``readOnly`` (or WordPress's ``readonly``) marks a field only the
    upstream sets. ``required`` is the upstream's to enforce: every lifted
    field is optional. ``key_field`` names a record (its value is what get,
    update and delete take); ``version_field`` holds its version."""

    name: str
    json_schema: Mapping[str, Any]
    key_field: str
    version_field: Optional[str] = None
    operations: FrozenSet[Operation] = ALL_OPERATIONS
    description: Optional[str] = None


@dataclass(frozen=True)
class FederatedCall:
    """Who a session acts for, in which app."""

    model_registry: Any
    requester_id: str


@dataclass(frozen=True)
class FederatedQuery:
    """A page of a type's records. ``filters`` is the source's own filter
    language (Frappe's dictionary filters, WordPress's query arguments),
    checked by the source."""

    filters: Optional[Dict[str, Any]]
    order_by: Optional[str]
    start: int
    page_length: int


class StaleRecord(Exception):
    """A save named a version the record is no longer at; ``current`` is
    the record as the requester may now see it."""

    def __init__(self, current: Mapping[str, Any]) -> None:
        super().__init__("The record was changed since it was read")
        self.current: Dict[str, Any] = dict(current)


class FederatedSession(ABC):
    """One requester's access to a source, for one request. A source
    implements the operations its types declare; the others are never
    called. Refusals are ``HTTPException`` carrying the upstream's reason
    (never its raw body or a credential)."""

    def _unsupported(self, operation: Operation) -> NoReturn:
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail=f"The source does not {operation.value} records",
        )

    async def list(
        self, type_name: str, query: FederatedQuery
    ) -> Sequence[Mapping[str, Any]]:
        self._unsupported(Operation.LIST)

    async def get(self, type_name: str, key: Any) -> Mapping[str, Any]:
        self._unsupported(Operation.GET)

    async def create(
        self, type_name: str, data: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self._unsupported(Operation.CREATE)

    async def update(
        self,
        type_name: str,
        key: Any,
        data: Mapping[str, Any],
        expected: Optional[IfMatch],
    ) -> Mapping[str, Any]:
        """Save ``data`` onto the record; :class:`StaleRecord` when it is
        not at a version ``expected`` names (None: the save names none, and
        need not)."""
        self._unsupported(Operation.UPDATE)

    async def delete(
        self, type_name: str, key: Any, expected: Optional[IfMatch]
    ) -> None:
        self._unsupported(Operation.DELETE)


class AbstractFederatedSource(ABC):
    """One upstream, as one account sees it, whose record types are served
    as typed models."""

    @property
    @abstractmethod
    def namespace(self) -> str:
        """Stable across restarts, distinct among the app's sources:
        ``[a-z][a-z0-9_]*``."""

    @property
    def reference(self) -> Optional[str]:
        """What the source is known by elsewhere in the app (an ERP
        instance's ``provider_instance_id``), for clients matching the
        catalogue to it; None when it has nothing else to be known by."""
        return None

    @property
    @abstractmethod
    def title(self) -> str:
        """What the upstream is called, for documentation."""

    @abstractmethod
    async def catalogue(self) -> Sequence[FederatedType]:
        """Every record type to serve, read when the app boots."""

    @abstractmethod
    def session(self, call: FederatedCall) -> FederatedSession:
        """The source for ``call``'s requester; 404 (as for a source that
        does not exist) when they may not use it."""


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def holdable_field_name(name: str) -> bool:
    """Whether a Pydantic model can hold a field under this name."""
    return bool(_FIELD_NAME.match(name)) and not (
        name.startswith("model_") and hasattr(BaseModel, name)
    )


def _words(text: str) -> List[str]:
    return [word for word in _NOT_ALPHANUMERIC.split(text) if word]


def _pascal(text: str) -> str:
    return "".join(word[:1].upper() + word[1:] for word in _words(text)) or "Type"


def type_slugs(names: Sequence[str]) -> List[str]:
    """Each type name's slug: snake case, distinct (``_2``... for a slug
    two names share, in the order given)."""
    slugs: List[str] = []
    taken: Set[str] = set()
    for name in names:
        base = "_".join(word.lower() for word in _words(name)) or "type"
        if not base[0].isalpha():
            base = f"t_{base}"
        slug, suffix = base, 2
        while slug in taken:
            slug, suffix = f"{base}_{suffix}", suffix + 1
        taken.add(slug)
        slugs.append(slug)
    return slugs


def model_stem(namespace: str, slug: str) -> str:
    """``erpnext_3f2a9c1d`` and ``sales_invoice`` -> ``Erpnext3f2a9c1dSalesInvoice``
    (no underscore: GraphQL type names are re-cased at each one)."""
    return f"{_pascal(namespace)}{_pascal(slug)}"


# ---------------------------------------------------------------------------
# JSON Schema -> models
# ---------------------------------------------------------------------------


class _SchemaComponents:
    """A type's JSON Schema as two sets of OpenAPI component schemas, the
    record's and the write payload's, one component per object."""

    def __init__(self, stem: str, schema: Mapping[str, Any]) -> None:
        self.stem = stem
        definitions = schema.get("$defs") or schema.get("definitions") or {}
        self.definitions: Mapping[str, Any] = (
            definitions if isinstance(definitions, Mapping) else {}
        )
        self.definition_names: Dict[str, str] = {}
        for key in self.definitions:
            name, suffix = f"{stem}{_pascal(str(key))}", 2
            while name in self.definition_names.values():
                name, suffix = f"{stem}{_pascal(str(key))}{suffix}", suffix + 1
            self.definition_names[str(key)] = name
        self.read: Dict[str, Any] = {}
        self.write: Dict[str, Any] = {}
        # Write components being built (a type nested in itself is taken
        # as having writable fields).
        self._writing: Set[str] = set()
        self.root = self._component(stem, schema)
        self.root_write = self._write_component(stem, schema)

    @staticmethod
    def _read_only(schema: Mapping[str, Any]) -> bool:
        return bool(schema.get("readOnly") or schema.get("readonly"))

    @staticmethod
    def _object(schema: Mapping[str, Any]) -> bool:
        return isinstance(schema.get("properties"), Mapping)

    def _properties(self, schema: Mapping[str, Any]) -> List[Tuple[str, Any]]:
        properties = schema.get("properties") or {}
        return [
            (str(name), value)
            for name, value in properties.items()
            if holdable_field_name(str(name)) and isinstance(value, Mapping)
        ]

    def _definition(self, ref: Any) -> Optional[Tuple[str, Mapping[str, Any]]]:
        match = _LOCAL_REF.match(ref) if isinstance(ref, str) else None
        if match is None or match.group(1) not in self.definitions:
            return None
        key = match.group(1)
        target = self.definitions[key]
        if not isinstance(target, Mapping):
            return None
        return self.definition_names[key], target

    def _inline(self, owner: str, prop: str) -> str:
        """The component name of an object nested inline in ``owner``'s
        ``prop`` (never a definition's)."""
        name = f"{owner}{_pascal(prop)}"
        return f"{name}Value" if name in self.definition_names.values() else name

    def _component(self, name: str, schema: Mapping[str, Any]) -> str:
        if name not in self.read:
            self.read[name] = {}
            self.read[name] = {
                "type": "object",
                "properties": {
                    prop: self._value(self._inline(name, prop), value)
                    for prop, value in self._properties(schema)
                },
            }
        return name

    def _value(self, name: str, schema: Mapping[str, Any]) -> Dict[str, Any]:
        """The OpenAPI schema of a record's value."""
        found = self._definition(schema.get("$ref"))
        if found is not None:
            return {"$ref": f"#/components/schemas/{self._component(*found)}"}
        if self._object(schema):
            return {"$ref": f"#/components/schemas/{self._component(name, schema)}"}
        return self._shape(schema, lambda item: self._value(name, item))

    def _write_component(self, name: str, schema: Mapping[str, Any]) -> Optional[str]:
        """The write payload's component, or None when nothing in it is
        writable."""
        written = f"{name}{_WRITE_SUFFIX}"
        if written in self.write or written in self._writing:
            return written if self.write.get(written, True) is not None else None
        self._writing.add(written)
        try:
            properties: Dict[str, Any] = {}
            for prop, value in self._properties(schema):
                if self._read_only(value):
                    continue
                converted = self._write_value(self._inline(name, prop), value)
                if converted is not None:
                    properties[prop] = converted
        finally:
            self._writing.discard(written)
        if not properties:
            self.write[written] = None
            return None
        self.write[written] = {"type": "object", "properties": properties}
        return written

    def _write_value(
        self, name: str, schema: Mapping[str, Any]
    ) -> Optional[Dict[str, Any]]:
        found = self._definition(schema.get("$ref"))
        if found is not None:
            component = self._write_component(*found)
        elif self._object(schema):
            component = self._write_component(name, schema)
        else:
            items = schema.get("items")
            if (
                isinstance(items, Mapping)
                and (self._definition(items.get("$ref")) or self._object(items))
                and self._write_value(name, items) is None
            ):
                return None
            return self._shape(schema, lambda item: self._write_value(name, item) or {})
        if component is None:
            return None
        return {"$ref": f"#/components/schemas/{component}"}

    @staticmethod
    def _shape(
        schema: Mapping[str, Any], item_value: Callable[[Mapping[str, Any]], Any]
    ) -> Dict[str, Any]:
        """A value that is not an object: its type (``["x", "null"]`` is
        an optional x; several types any of them), an array's items, an
        enumeration of strings, a choice of schemas."""
        choices = schema.get("oneOf") or schema.get("anyOf")
        if isinstance(choices, list) and choices:
            members = [
                _SchemaComponents._shape(choice, item_value)
                for choice in choices
                if isinstance(choice, Mapping) and choice.get("type") != "null"
            ]
            return members[0] if len(members) == 1 else {"anyOf": members}
        declared = schema.get("type")
        kinds = [
            kind
            for kind in (declared if isinstance(declared, list) else [declared])
            if isinstance(kind, str) and kind != "null"
        ]
        if len(kinds) > 1:
            return {"anyOf": [{"type": kind} for kind in kinds]}
        kind = kinds[0] if kinds else None
        if kind == "array":
            items = schema.get("items")
            return {
                "type": "array",
                "items": item_value(items) if isinstance(items, Mapping) else {},
            }
        if kind == "object":
            return {"type": "object"}
        if kind in _JSON_SCALARS:
            enum = schema.get("enum")
            if (
                kind == "string"
                and isinstance(enum, list)
                and enum
                and all(isinstance(value, str) for value in enum)
            ):
                return {"type": "string", "enum": list(enum)}
            return {"type": kind}
        return {}

    def write_components(self) -> Dict[str, Any]:
        return {name: schema for name, schema in self.write.items() if schema}


def _lift_components(
    components: Mapping[str, Any], config: ConfigDict
) -> Dict[str, Type[BaseModel]]:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=SHADOW_WARNING, category=UserWarning)
        lifted = openapi_to_pydantic_models(
            {"components": {"schemas": dict(components)}}, config=config
        ).models
        for model in lifted.values():
            # The importer resolves forward references best-effort; a model
            # left incomplete must fail here, at boot, not on its first use.
            model.model_rebuild(force=True, _types_namespace=dict(lifted))
    return lifted


@dataclass(frozen=True)
class LiftedType:
    """A federated type as models: the record (table-less) and the write
    payload (None for a type that is only read)."""

    federated: FederatedType
    slug: str
    record: Type[BaseModel]
    write: Optional[Type[BaseModel]]
    operations: FrozenSet[Operation]
    nested: Tuple[Type[BaseModel], ...] = field(default=())

    @property
    def key_annotation(self) -> Any:
        key = self.record.model_fields[self.federated.key_field]
        declared = unwrap_optional(key.annotation)
        return declared if declared in (int, str) else str

    @classmethod
    def of_models(
        cls,
        federated: FederatedType,
        slug: str,
        record: Type[BaseModel],
        write: Optional[Type[BaseModel]],
        nested: Tuple[Type[BaseModel], ...] = (),
    ) -> "LiftedType":
        """The type over its models. The record must be table-less and hold
        the key (and the version, when the type has one). A type without a
        version is not updated or deleted, one without a write payload not
        written."""
        if getattr(record, "is_external_model", False) is not True:
            raise ValueError(f"{record.__name__} is not table-less")
        for role, needed in (
            ("key", federated.key_field),
            ("version", federated.version_field),
        ):
            if needed is not None and needed not in record.model_fields:
                raise ValueError(f"{federated.name} has no {role} field {needed}")
        operations = federated.operations
        if federated.version_field is None:
            operations = operations - SAVES
        if write is None:
            operations = operations - {Operation.CREATE, Operation.UPDATE}
        return cls(federated, slug, record, write, frozenset(operations), nested)


def lift_type(namespace: str, slug: str, federated: FederatedType) -> LiftedType:
    """``federated``'s JSON Schema as a table-less record model and a write
    payload model, named for ``namespace`` and ``slug``."""
    stem = model_stem(namespace, slug)
    components = _SchemaComponents(stem, federated.json_schema)
    read = _lift_components(components.read, ConfigDict(extra="ignore"))
    record = read[components.root]
    setattr(record, "is_external_model", True)
    write: Optional[Type[BaseModel]] = None
    if components.root_write is not None:
        written = _lift_components(
            components.write_components(), ConfigDict(extra="forbid")
        )
        write = written[components.root_write]
    nested = tuple(model for name, model in read.items() if name != components.root)
    return LiftedType.of_models(federated, slug, record, write, nested)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class FederatedRecordDeleted(BaseModel):
    """A record the upstream deleted."""

    key: str
    deleted: bool


def record_etag(lifted: LiftedType, record: BaseModel) -> Dict[str, str]:
    """The record's ETag header: its version, quoted."""
    version_field = lifted.federated.version_field
    version = getattr(record, version_field, None) if version_field else None
    return {"ETag": f'"{version}"'} if version not in (None, "") else {}


class FederatedTypeEndpoints(AbstractBLLManager, RouterMixin):
    """What every federated type's synthesized manager shares: typed routes,
    authenticated, each a live call through the source's session for the
    requester."""

    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []
    source: ClassVar[AbstractFederatedSource]
    lifted: ClassVar[LiftedType]
    # Each served operation's input model.
    route_inputs: ClassVar[Dict[Operation, Type[BaseModel]]]

    def _session(self) -> FederatedSession:
        return self.source.session(
            FederatedCall(
                model_registry=self.model_registry, requester_id=self.requester.id
            )
        )

    @classmethod
    def _record(cls, row: Mapping[str, Any]) -> BaseModel:
        """A row as the type's record; a row the upstream answered outside
        the type's schema is the upstream's fault (502), not the caller's."""
        try:
            return cls.lifted.record.model_validate(row)
        except ValidationError as exc:
            logger.warning(
                "%s answered a %s outside its schema: %s",
                cls.source.title,
                cls.lifted.federated.name,
                [(error["loc"], error["msg"]) for error in exc.errors()],
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"{cls.source.title} answered a {cls.lifted.federated.name} "
                f"outside its schema",
            ) from None

    @classmethod
    def _versioned(cls, response: Response, row: Mapping[str, Any]) -> BaseModel:
        record = cls._record(row)
        response.headers.update(record_etag(cls.lifted, record))
        return record

    @classmethod
    def _stale(cls, stale: StaleRecord) -> PreconditionError:
        record = cls._record(stale.current)
        return PreconditionError(
            status.HTTP_412_PRECONDITION_FAILED,
            STALE_DETAIL,
            {"current": record.model_dump()},
            record_etag(cls.lifted, record) or None,
        )

    @staticmethod
    def _expected(request: Request, if_match: Optional[str]) -> Optional[IfMatch]:
        """The version a save names: the If-Match header (REST), else the
        input's ``if_match`` (GraphQL); 428 when it must name one."""
        return save_expectation(request.headers.get(IF_MATCH_HEADER) or if_match)


def _payload(data: Optional[BaseModel]) -> Dict[str, Any]:
    """What a write sends: the fields given (nested rows likewise)."""
    return data.model_dump(exclude_unset=True) if data is not None else {}


def _list_of(model: Type[BaseModel]) -> Any:
    return List[model]  # type: ignore[valid-type]


def _route_models(stem: str, lifted: LiftedType) -> Dict[str, Type[BaseModel]]:
    key = lifted.federated.key_field
    key_field: Any = (lifted.key_annotation, Field(description="The record's key"))
    if_match: Any = (
        Optional[str],
        Field(
            None,
            description="The version it was read at (its ETag); the If-Match "
            "header names it on REST",
        ),
    )
    models: Dict[str, Type[BaseModel]] = {
        "list_input": create_model(
            f"{stem}ListArgs",
            filters=(
                Optional[Dict[str, Any]],
                Field(None, description="The source's own filters"),
            ),
            order_by=(Optional[str], None),
            start=(int, Field(0, ge=0)),
            page_length=(int, Field(DEFAULT_PAGE_LENGTH, ge=1, le=MAX_PAGE_LENGTH)),
        ),
        "page": create_model(
            f"{stem}Page",
            items=(_list_of(lifted.record), ...),
            start=(int, ...),
            page_length=(int, ...),
        ),
        "get_input": create_model(f"{stem}GetArgs", **{key: key_field}),
        "delete_input": create_model(
            f"{stem}DeleteArgs", **{key: key_field, "if_match": if_match}
        ),
    }
    if lifted.write is not None:
        models["create_input"] = create_model(
            f"{stem}CreateArgs", data=(lifted.write, ...)
        )
        models["update_input"] = create_model(
            f"{stem}UpdateArgs",
            **{key: key_field, "data": (lifted.write, ...), "if_match": if_match},
        )
    return models


def _typed(
    function: Callable[..., Any], body: Type[BaseModel], output: Type[BaseModel]
) -> None:
    """Give a synthesized route its real annotations (the route machinery
    reads them; postponed annotations would name this module's locals)."""
    function.__annotations__ = {
        "body": body,
        "request": Request,
        "response": Response,
        "return": output,
    }


def _routes(source: AbstractFederatedSource, lifted: LiftedType) -> Dict[str, Any]:
    stem = lifted.record.__name__
    name = lifted.federated.name
    key = lifted.federated.key_field
    models = _route_models(stem, lifted)
    tags = (f"Federated: {source.title}",)
    inputs: Dict[Operation, Type[BaseModel]] = {}
    routes: Dict[str, Any] = {"route_inputs": inputs}

    def declare(
        operation: Operation,
        function: Callable[..., Any],
        input_model: Type[BaseModel],
        output_model: Type[BaseModel],
        summary: str,
        kind: Optional[str] = None,
    ) -> None:
        _typed(function, input_model, output_model)
        inputs[operation] = input_model
        routes[function.__name__] = custom_route(
            method="POST",
            path=f"/{operation.value}",
            input_model=input_model,
            output_model=output_model,
            authentication_type="jwt",
            graphql_kind=kind,
            openapi_tags=tags,
            summary=summary,
        )(function)

    async def list_route(
        self: Any, body: Any, request: Request, response: Response
    ) -> Any:
        query = FederatedQuery(
            filters=body.filters,
            order_by=body.order_by,
            start=body.start,
            page_length=body.page_length,
        )
        rows = await self._session().list(name, query)
        return models["page"](
            items=[self._record(row) for row in rows],
            start=body.start,
            page_length=body.page_length,
        )

    async def get_route(
        self: Any, body: Any, request: Request, response: Response
    ) -> Any:
        return self._versioned(
            response, await self._session().get(name, getattr(body, key))
        )

    async def create_route(
        self: Any, body: Any, request: Request, response: Response
    ) -> Any:
        created = await self._session().create(name, _payload(body.data))
        return self._versioned(response, created)

    async def update_route(
        self: Any, body: Any, request: Request, response: Response
    ) -> Any:
        expected = self._expected(request, body.if_match)
        try:
            saved = await self._session().update(
                name, getattr(body, key), _payload(body.data), expected
            )
        except StaleRecord as stale:
            raise self._stale(stale) from None
        return self._versioned(response, saved)

    async def delete_route(
        self: Any, body: Any, request: Request, response: Response
    ) -> Any:
        expected = self._expected(request, body.if_match)
        try:
            await self._session().delete(name, getattr(body, key), expected)
        except StaleRecord as stale:
            raise self._stale(stale) from None
        return FederatedRecordDeleted(key=str(getattr(body, key)), deleted=True)

    operations = lifted.operations
    if Operation.LIST in operations:
        declare(
            Operation.LIST,
            list_route,
            models["list_input"],
            models["page"],
            f"A page of {name} records",
            "query",
        )
    if Operation.GET in operations:
        declare(
            Operation.GET,
            get_route,
            models["get_input"],
            lifted.record,
            f"One {name}; its ETag is its version",
            "query",
        )
    if Operation.CREATE in operations:
        declare(
            Operation.CREATE,
            create_route,
            models["create_input"],
            lifted.record,
            f"Create a {name}",
        )
    if Operation.UPDATE in operations:
        declare(
            Operation.UPDATE,
            update_route,
            models["update_input"],
            lifted.record,
            f"Save onto a {name} read at the If-Match version (412 if changed)",
        )
    if Operation.DELETE in operations:
        declare(
            Operation.DELETE,
            delete_route,
            models["delete_input"],
            FederatedRecordDeleted,
            f"Delete a {name} read at the If-Match version (412 if changed)",
        )
    return routes


def synthesize_manager(source: AbstractFederatedSource, lifted: LiftedType) -> type:
    """The manager serving ``lifted``'s typed routes for ``source``. Its
    class name is its wire name (``<namespace>_<slug>Manager``), so its
    GraphQL fields are ``<namespace>_<slug>_<operation>``."""
    resource = f"{source.namespace}_{lifted.slug}"
    attributes: Dict[str, Any] = {
        "__module__": __name__,
        "__doc__": f"{lifted.federated.name} records of {source.title}, live.",
        "_model": lifted.record,
        "prefix": f"{FEDERATED_PREFIX}/{source.namespace}/{lifted.slug}",
        "tags": [f"Federated: {source.title}"],
        "source": source,
        "lifted": lifted,
        **_routes(source, lifted),
    }
    return type(f"{resource}Manager", (FederatedTypeEndpoints,), attributes)


# ---------------------------------------------------------------------------
# Discovery: which typed models the requester may use
# ---------------------------------------------------------------------------


def graphql_type_name(model: Type[BaseModel]) -> str:
    """The GraphQL object type a model is served as."""
    return f"{model.__name__.removesuffix('Model')}Type"


def graphql_input_name(model: Type[BaseModel], suffix: str = "Input") -> str:
    """The GraphQL input type a model is taken as: a route's input with the
    suffix ``Input`` (``<Args>InputInput``), a nested write payload with
    none (``<Write>Input``)."""
    return f"{model.__name__.removesuffix('Model')}{suffix}Input"


class FederatedTypeInfo(BaseModel):
    """One typed model: where it is served and what it holds."""

    namespace: str
    source: str = Field(description="What the upstream is called")
    source_reference: Optional[str] = Field(
        None, description="What the source is known by elsewhere (an ERP instance's id)"
    )
    name: str = Field(description="The upstream's own name for the type")
    slug: str
    key_field: str
    version_field: Optional[str] = Field(
        None, description="The field holding the version a save names (its ETag)"
    )
    operations: List[str]
    rest: Dict[str, str] = Field(description="Each operation's REST path (POST)")
    graphql_type: str
    graphql_write_input: Optional[str] = None
    graphql_fields: Dict[str, str] = Field(description="Each operation's GraphQL field")
    graphql_inputs: Dict[str, str] = Field(description="Each operation's input type")
    record_schema: Dict[str, Any] = Field(description="The record's JSON Schema")
    write_schema: Optional[Dict[str, Any]] = Field(
        None, description="The write payload's JSON Schema"
    )


class FederatedCatalogue(BaseModel):
    types: List[FederatedTypeInfo]


def type_info(manager: Any) -> FederatedTypeInfo:
    """What a typed manager serves, as a client needs to call it."""
    from strawberry.utils.str_converters import to_camel_case

    from zephyrex.lib.CustomRoute import graphql_field_name

    lifted: LiftedType = manager.lifted
    served = sorted(manager.route_inputs, key=list(Operation).index)
    return FederatedTypeInfo(
        namespace=manager.source.namespace,
        source=manager.source.title,
        source_reference=manager.source.reference,
        name=lifted.federated.name,
        slug=lifted.slug,
        key_field=lifted.federated.key_field,
        version_field=lifted.federated.version_field,
        operations=[op.value for op in served],
        rest={op.value: f"{manager.prefix}/{op.value}" for op in served},
        graphql_type=graphql_type_name(lifted.record),
        graphql_write_input=(
            graphql_input_name(lifted.write, "") if lifted.write is not None else None
        ),
        graphql_fields={
            op.value: to_camel_case(graphql_field_name(manager, f"{op.value}_route"))
            for op in served
        },
        graphql_inputs={
            op.value: graphql_input_name(manager.route_inputs[op]) for op in served
        },
        record_schema=lifted.record.model_json_schema(),
        write_schema=lifted.write.model_json_schema() if lifted.write else None,
    )


class FederatedManager(AbstractBLLManager, RouterMixin):
    """The typed models of the app's federated sources, as the requester
    may use them: snapshotted at boot, so a type the upstream gains later
    is not here (nor served typed) until the app restarts."""

    prefix: ClassVar[Optional[str]] = FEDERATED_PREFIX
    tags: ClassVar[Optional[List[str]]] = ["Federated"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    def _usable(self, source: AbstractFederatedSource) -> bool:
        try:
            source.session(
                FederatedCall(
                    model_registry=self.model_registry, requester_id=self.requester.id
                )
            )
        except HTTPException as refused:
            if refused.status_code == status.HTTP_404_NOT_FOUND:
                return False
            raise
        return True

    @custom_route(
        method="GET",
        path="/catalogue",
        output_model=FederatedCatalogue,
        authentication_type="jwt",
        graphql_kind="query",
        openapi_tags=("Federated",),
        summary="Every typed model the requester may use: routes, GraphQL names, schemas",
    )
    async def catalogue_route(self) -> FederatedCatalogue:
        managers = [
            manager
            for manager in self.model_registry.external_managers()
            if issubclass(manager, FederatedTypeEndpoints)
        ]
        usable: Dict[int, bool] = {}
        for manager in managers:
            if id(manager.source) not in usable:
                usable[id(manager.source)] = self._usable(manager.source)
        return FederatedCatalogue(
            types=[
                type_info(manager) for manager in managers if usable[id(manager.source)]
            ]
        )


# ---------------------------------------------------------------------------
# Boot: catalogue -> models -> registry
# ---------------------------------------------------------------------------

SourceFactory = Callable[[Any], Iterable[AbstractFederatedSource]]
# Per extension: the sources it federates in an app (an extension
# registers its factory at on_initialize; only the factories of the
# extensions an app loads are asked).
_SOURCE_FACTORIES: Dict[str, SourceFactory] = {}


def register_federated_sources(extension_name: str, factory: SourceFactory) -> None:
    """Register the factory of the sources ``extension_name`` federates
    (idempotent per extension): ``factory(model_registry)`` yields them,
    reading what it needs from the app's database."""
    _SOURCE_FACTORIES[extension_name] = factory


def unregister_federated_sources(extension_name: str) -> None:
    _SOURCE_FACTORIES.pop(extension_name, None)


def federated_sources(model_registry: Any) -> List[AbstractFederatedSource]:
    """The sources of the app's extensions. A factory that fails leaves its
    sources out, logged."""
    loaded = model_registry.loaded_extension_names()
    found: List[AbstractFederatedSource] = []
    for extension_name, factory in list(_SOURCE_FACTORIES.items()):
        if extension_name not in loaded:
            continue
        try:
            found.extend(factory(model_registry))
        except Exception as exc:
            logger.warning(
                "Federated sources of %s are not served: %s", extension_name, exc
            )
    return found


def _reason(exc: BaseException) -> str:
    if isinstance(exc, HTTPException):
        return f"{exc.status_code} {exc.detail}"
    if isinstance(exc, asyncio.TimeoutError):
        return f"its catalogue took longer than {CATALOGUE_TIMEOUT_SECONDS:.0f}s"
    return str(exc) or type(exc).__name__


def read_catalogue(source: AbstractFederatedSource) -> Sequence[FederatedType]:
    """The source's catalogue, read now (the registry commits synchronously)."""

    async def bounded() -> Sequence[FederatedType]:
        return await asyncio.wait_for(source.catalogue(), CATALOGUE_TIMEOUT_SECONDS)

    found: Sequence[FederatedType] = _cache_sync_run(bounded(), timeout=None)
    return found


@dataclass
class FederatedBinding:
    """What binding the sources served, and why anything is missing."""

    models: Dict[str, Type[BaseModel]] = field(default_factory=dict)
    managers: Dict[str, type] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)


def bind_lifted(
    model_registry: Any,
    source: AbstractFederatedSource,
    lifted: LiftedType,
    binding: FederatedBinding,
) -> None:
    """Synthesize ``lifted``'s manager and bind its record, table-less."""
    manager = synthesize_manager(source, lifted)
    model_registry.bind_external(lifted.record, manager)
    binding.models[lifted.record.__name__] = lifted.record
    binding.managers[lifted.record.__name__] = manager


def bind_federated_sources(
    model_registry: Any,
    sources: Iterable[AbstractFederatedSource],
    binding: Optional[FederatedBinding] = None,
) -> FederatedBinding:
    """Read each source's catalogue, lift its types and bind them. A source
    whose catalogue cannot be read, or a type that cannot be lifted or
    bound, is left out and logged with the reason; the app boots without
    it."""
    binding = binding or FederatedBinding()
    namespaces: Set[str] = set()
    for source in sources:
        namespace = source.namespace
        if not NAMESPACE_PATTERN.match(namespace) or namespace in namespaces:
            reason = (
                "namespace is already served"
                if namespace in namespaces
                else "namespace is not [a-z][a-z0-9_]*"
            )
            logger.warning("Federated source %r is not served: %s", namespace, reason)
            binding.errors[namespace] = reason
            continue
        namespaces.add(namespace)
        try:
            catalogue = read_catalogue(source)
        except Exception as exc:
            logger.warning(
                "%s (%s): its typed models are not served, the catalogue could "
                "not be read: %s",
                source.title,
                namespace,
                _reason(exc),
            )
            binding.errors[namespace] = _reason(exc)
            continue
        for federated, slug in zip(
            catalogue, type_slugs([federated.name for federated in catalogue])
        ):
            try:
                bind_lifted(
                    model_registry,
                    source,
                    lift_type(namespace, slug, federated),
                    binding,
                )
            except Exception as exc:
                logger.warning(
                    "%s (%s): %s is not served: %s",
                    source.title,
                    namespace,
                    federated.name,
                    _reason(exc),
                )
                binding.errors[f"{namespace}.{federated.name}"] = _reason(exc)
    return binding


__all__ = [
    "AbstractFederatedSource",
    "FederatedBinding",
    "FederatedCall",
    "FederatedQuery",
    "FederatedRecordDeleted",
    "FederatedSession",
    "FederatedType",
    "FederatedTypeEndpoints",
    "LiftedType",
    "Operation",
    "StaleRecord",
    "bind_federated_sources",
    "bind_lifted",
    "federated_sources",
    "holdable_field_name",
    "lift_type",
    "model_stem",
    "register_federated_sources",
    "synthesize_manager",
    "type_slugs",
    "unregister_federated_sources",
]
