# SPDX-License-Identifier: AGPL-3.0-or-later
"""ERP documents, live: who may use an instance, how a DocType becomes a
model, how a save is held to its version, and the ERP's webhooks.

**Access.** An instance serves whoever :func:`can_use` it: ROOT and SYSTEM;
everyone, when it is the operator's (root or system scope, which only they
configure); otherwise whoever the framework lets see the instance (its user,
its team). Anyone else is answered 404, as for an instance that does not
exist. The ERP then applies its own permissions for the instance's account.

**Schema.** A DocType is described by the ERP on every request that needs
it (cached for that request only, in :class:`ERPDocuments`). Its metadata
and its child tables' is translated to OpenAPI component schemas and lifted
by the federation's REST importer (``openapi_to_pydantic_models``) into one
Pydantic model per DocType, a child table a list of its child DocType's
model (:func:`lift_doctype`). The model is the DocType's published JSON
Schema, and every create and update is checked against it before it is
sent: an unknown field, or a value of the wrong type, is refused with 422.

**Versions.** A document's version is its ``modified``, carried as
``updated_at`` and as its ETag. An update sends that version with the
change, so the ERP refuses it, under its own row lock, when the document has
moved on; a submission sends the document as read. A delete or a cancel is
checked against the document read just before. A refusal from a changed
document is answered 412 with the document as it now is, a save that names
no version 428 (``IF_MATCH_REQUIRED``).

**Webhooks.** The ERP posts its webhooks to ``POST
/v1/erp/webhook/{provider_instance_id}``; one verified as the instance
signs it, and not seen before, is passed on as ``erp.<event>`` to outbound
subscribers who may use the instance (``dispatch_webhook_event`` with
:func:`can_use` as its audience).
"""

import json
import re
import warnings
from dataclasses import asdict, dataclass, field
from typing import (
    Any,
    Awaitable,
    ClassVar,
    Dict,
    FrozenSet,
    List,
    Mapping,
    NoReturn,
    Optional,
    Sequence,
    Tuple,
    Type,
    TypeVar,
)

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel as RouteModel
from pydantic import Field, ValidationError

from zephyrex.extensions.erp.EXT_ERP import (
    AbstractERPProvider,
    Document,
    ERPConfigurationError,
)
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    BaseExternalError,
    InvalidInputExternalError,
    RateLimitExternalError,
)
from zephyrex.extensions.federation.BLL_Federation_REST import (
    openapi_to_pydantic_models,
)
from zephyrex.extensions.federation.BLL_Federation_Typed import (
    SHADOW_WARNING,
    holdable_field_name,
)
from zephyrex.lib.ContentNegotiation import skip_negotiation
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import rate_limit
from zephyrex.lib.Preconditions import (
    IF_MATCH_HEADER,
    IfMatch,
    PreconditionFailed,
    etag_headers,
    save_expectation,
)
from zephyrex.lib.ReplayCache import get_replay_cache
from zephyrex.lib.RequestBody import capped_body
from zephyrex.lib.SessionCookies import accept_cross_site_writes
from zephyrex.lib.SignedRequests import lower_case_headers, unsigned
from zephyrex.logic.AbstractLogicManager import AbstractBLLManager
from zephyrex.logic.AbstractLogicManager.ownership import server_side
from zephyrex.logic.BLL_Providers import (
    OPERATOR_SCOPES,
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderModel,
)
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin, RouteType

T = TypeVar("T")

ERP_PREFIX = "/v1/erp"
WEBHOOK_PREFIX = "/v1/erp/webhook"
WEBHOOK_PATH = "/{provider_instance_id}"
# Deliveries one address may make in a minute: a site's burst of changes.
WEBHOOK_RATE_LIMIT = "300/min"
MAX_WEBHOOK_BYTES = 1024 * 1024
# A signed delivery carries no timestamp (Frappe signs the body alone), so
# the same body is refused for this long after it was first taken.
WEBHOOK_REPLAY_SECONDS = 24 * 60 * 60
# Frappe's document events (Webhook.webhook_docevent).
DOC_EVENTS = (
    "after_insert",
    "on_update",
    "on_submit",
    "on_cancel",
    "on_trash",
    "on_update_after_submit",
    "on_change",
)
DEFAULT_PAGE_LENGTH = 20
MAX_PAGE_LENGTH = 500
MAX_FIELDS = 100
MAX_FILTERS = 50
# Frappe names (DocTypes and documents) are varchar(140).
MAX_NAME_LENGTH = 140
MAX_ID_LENGTH = 64

# Frappe field types (frappe.model) and how a value of each is typed.
TABLE_TYPES = frozenset({"Table", "Table MultiSelect"})
NO_VALUE_TYPES = frozenset(
    {
        "Section Break",
        "Column Break",
        "Tab Break",
        "Attachment Gallery",
        "HTML",
        "Button",
        "Image",
        "Fold",
        "Heading",
    }
)
INTEGER_TYPES = frozenset({"Int", "Long Int", "Check"})
NUMBER_TYPES = frozenset({"Float", "Currency", "Percent", "Rating", "Duration"})
# Held as JSON text or structured values: typed as any value.
ANY_TYPES = frozenset({"JSON", "Geolocation"})
# Every document's own columns (frappe.model.default_fields), and a child
# row's link to its parent (child_table_fields).
STANDARD_FIELDS: Dict[str, str] = {
    "name": "string",
    "owner": "string",
    "creation": "string",
    "modified": "string",
    "modified_by": "string",
    "docstatus": "integer",
    "idx": "integer",
    "doctype": "string",
}
CHILD_FIELDS: Dict[str, str] = {
    "parent": "string",
    "parentfield": "string",
    "parenttype": "string",
}
# Set by the ERP alone: a write naming one is refused. ``docstatus`` moves
# by submit and cancel; ``modified`` is the version, sent from If-Match.
SYSTEM_FIELDS = frozenset(
    {"owner", "creation", "modified", "modified_by", "docstatus", "doctype"}
    | set(CHILD_FIELDS)
)
_FIELDNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_ORDER = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*(?: (?:asc|desc))?"
    r"(?:, ?[A-Za-z][A-Za-z0-9_]*(?: (?:asc|desc))?){0,4}$",
    re.IGNORECASE,
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_COMPONENT_UNSAFE = re.compile(r"[^0-9A-Za-z]")


def _refused(detail: Any, status_code: int = 422) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


def checked_doctype(value: Any) -> str:
    """A DocType's name: text of at most 140 characters, no control
    characters, no slash, not a dot path."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_NAME_LENGTH
        or _CONTROL.search(value)
        or "/" in value
        or value.strip(".") == ""
    ):
        raise _refused("doctype names a DocType")
    return value


def checked_name(value: Any) -> str:
    """A document's name: text of at most 140 characters, no control
    characters, not a dot path."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_NAME_LENGTH
        or _CONTROL.search(value)
        or value.strip(".") == ""
    ):
        raise _refused("name names a document")
    return value


def _checked_filters(value: Any, what: str) -> Optional[Dict[str, Any]]:
    """Frappe's dictionary filters: ``{fieldname: value}`` or
    ``{fieldname: [operator, value]}``; the ERP checks the operators."""
    if value is None:
        return None
    if not isinstance(value, Mapping) or len(value) > MAX_FILTERS:
        raise _refused(f"{what} is an object of at most {MAX_FILTERS} fields")
    for key in value:
        if not isinstance(key, str) or not _FIELDNAME.match(key):
            raise _refused(f"{what} names fields by their fieldname")
    return dict(value)


@dataclass(frozen=True)
class ListQuery:
    """A page of a DocType's documents, checked."""

    doctype: str
    fields: Tuple[str, ...]
    filters: Optional[Dict[str, Any]]
    or_filters: Optional[Dict[str, Any]]
    order_by: Optional[str]
    start: int
    page_length: int

    @classmethod
    def checked(
        cls,
        doctype: Any,
        fields: Optional[Sequence[Any]] = None,
        filters: Any = None,
        or_filters: Any = None,
        order_by: Optional[str] = None,
        start: Any = 0,
        page_length: Optional[Any] = None,
    ) -> "ListQuery":
        wanted = list(fields) if fields else ["*"]
        if len(wanted) > MAX_FIELDS or not all(
            isinstance(name, str) and (name == "*" or _FIELDNAME.match(name))
            for name in wanted
        ):
            raise _refused(f"fields is at most {MAX_FIELDS} fieldnames, or *")
        if "*" not in wanted:
            # Each row carries what identifies it and its version.
            wanted += [n for n in ("name", "modified", "docstatus") if n not in wanted]
        if order_by is not None and (
            not isinstance(order_by, str) or not _ORDER.match(order_by)
        ):
            raise _refused("order_by is fieldnames, each optionally asc or desc")
        length = DEFAULT_PAGE_LENGTH if page_length is None else page_length
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise _refused("start is a whole number")
        if (
            isinstance(length, bool)
            or not isinstance(length, int)
            or not 1 <= length <= MAX_PAGE_LENGTH
        ):
            raise _refused(f"page_length is 1-{MAX_PAGE_LENGTH}")
        return cls(
            doctype=checked_doctype(doctype),
            fields=tuple(dict.fromkeys(wanted)),
            filters=_checked_filters(filters, "filters"),
            or_filters=_checked_filters(or_filters, "or_filters"),
            order_by=order_by,
            start=start,
            page_length=length,
        )


# ---------------------------------------------------------------------------
# Who may use an instance
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ERPInstance:
    """A live instance of one of the extension's providers."""

    model: ProviderInstanceModel
    provider: Type[AbstractERPProvider]

    @property
    def id(self) -> str:
        return str(self.model.id)


def _erp_provider_named(name: str) -> Optional[Type[AbstractERPProvider]]:
    from zephyrex.extensions.erp.EXT_ERP import EXT_ERP

    for provider in EXT_ERP.providers:
        if provider.name == name and issubclass(provider, AbstractERPProvider):
            return provider
    return None


def live_instance(
    model_registry: Any, provider_instance_id: Any
) -> Optional[ERPInstance]:
    """The undeleted instance ``provider_instance_id`` when one of the
    extension's providers serves it, else None. Read as ROOT: whether the
    caller may use it is :func:`can_use`'s to say."""
    if not isinstance(provider_instance_id, str) or not (
        0 < len(provider_instance_id) <= MAX_ID_LENGTH
    ):
        return None
    base, root = model_registry.DB.manager.Base, env("ROOT_ID")
    instance_db = ProviderInstanceModel.DB(base)
    instances: List[ProviderInstanceModel] = instance_db.list(
        requester_id=root,
        model_registry=model_registry,
        return_type="dto",
        override_dto=ProviderInstanceModel,
        filters=[
            instance_db.id == provider_instance_id,
            instance_db.deleted_at.is_(None),
        ],
    )
    if not instances:
        return None
    provider_db = ProviderModel.DB(base)
    providers: List[ProviderModel] = provider_db.list(
        requester_id=root,
        model_registry=model_registry,
        return_type="dto",
        override_dto=ProviderModel,
        filters=[provider_db.id == instances[0].provider_id],
    )
    provider = _erp_provider_named(providers[0].name) if providers else None
    if provider is None:
        return None
    return ERPInstance(model=instances[0], provider=provider)


def can_use(model_registry: Any, user_id: str, instance: ProviderInstanceModel) -> bool:
    """Whether ``user_id`` may use the instance: it is enabled, and they are
    ROOT or SYSTEM, or it is the operator's (which serves everyone), or the
    framework lets them see it."""
    if instance.enabled is False:
        return False
    if server_side(user_id) or instance.scope in OPERATOR_SCOPES:
        return True
    try:
        ProviderInstanceManager(
            model_registry=model_registry, requester_id=user_id
        ).get(id=str(instance.id))
    except HTTPException as exc:
        if exc.status_code in (401, 403, 404):
            return False
        raise
    return True


def usable_instance(
    model_registry: Any, requester_id: str, provider_instance_id: Any
) -> ERPInstance:
    """The instance, for one who may use it; 404 otherwise, as for one that
    does not exist."""
    found = live_instance(model_registry, provider_instance_id)
    if found is None or not can_use(model_registry, requester_id, found.model):
        raise HTTPException(status_code=404, detail="No such ERP instance")
    return found


# ---------------------------------------------------------------------------
# DocTypes as models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocTypeField:
    fieldname: str
    label: Optional[str]
    fieldtype: str
    options: Optional[str]
    reqd: bool
    read_only: bool
    allow_on_submit: bool


@dataclass(frozen=True)
class DocTypeSchema:
    """A DocType as the ERP describes it now, lifted to a model."""

    doctype: str
    module: Optional[str]
    custom: bool
    istable: bool
    issingle: bool
    is_submittable: bool
    model: Type[RouteModel]
    fields: Tuple[DocTypeField, ...]
    tables: Mapping[str, "DocTypeSchema"]
    # Fields the model cannot hold under their own name (``model_config``):
    # written as given, the ERP checking them.
    untyped: FrozenSet[str] = field(default_factory=frozenset)

    @property
    def writable(self) -> FrozenSet[str]:
        own = {f.fieldname for f in self.fields} | {"name"}
        if self.istable:
            own.add("idx")
        return frozenset(own - SYSTEM_FIELDS)

    def checked_write(self, data: Any) -> Dict[str, Any]:
        """``data`` as sent to the ERP: only this DocType's fields, each of
        its type, child rows checked against their DocType. 422 otherwise."""
        if not isinstance(data, Mapping):
            raise _refused(f"The data of a {self.doctype} is an object")
        unknown = sorted(str(key) for key in data if key not in self.writable)
        if unknown:
            raise _refused(f"{self.doctype} has no writable field {', '.join(unknown)}")
        rows: Dict[str, List[Dict[str, Any]]] = {}
        typed: Dict[str, Any] = {}
        given: Dict[str, Any] = {}
        for key, value in data.items():
            if key in self.tables:
                if not isinstance(value, list):
                    raise _refused(f"{self.doctype}.{key} is a list of rows")
                rows[key] = [self.tables[key].checked_write(row) for row in value]
            elif key in self.untyped:
                given[key] = value
            else:
                typed[key] = value
        try:
            validated = self.model.model_validate(typed)
        except ValidationError as exc:
            raise _refused(
                {
                    "message": f"Invalid field values for {self.doctype}",
                    "errors": [
                        {"loc": list(error["loc"]), "msg": error["msg"]}
                        for error in exc.errors()
                    ],
                }
            ) from None
        return {**validated.model_dump(exclude_unset=True), **rows, **given}

    def described(self) -> Dict[str, Any]:
        """What a client needs to read and write the DocType."""
        return {
            "doctype": self.doctype,
            "module": self.module,
            "custom": self.custom,
            "istable": self.istable,
            "issingle": self.issingle,
            "is_submittable": self.is_submittable,
            "fields": [asdict(f) for f in self.fields],
            "tables": {name: child.doctype for name, child in self.tables.items()},
            "json_schema": self.model.model_json_schema(),
        }


def _flag(meta: Mapping[str, Any], name: str) -> bool:
    return bool(meta.get(name))


def _value_fields(meta: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    return [
        f
        for f in meta.get("fields") or []
        if isinstance(f, Mapping)
        and isinstance(f.get("fieldname"), str)
        and f.get("fieldtype") not in NO_VALUE_TYPES
    ]


def _component_names(doctypes: Sequence[str]) -> Dict[str, str]:
    """A distinct Python identifier for each DocType's component schema."""
    names: Dict[str, str] = {}
    taken: set = set()
    for doctype in doctypes:
        base = "ERP_" + _COMPONENT_UNSAFE.sub("_", doctype)
        candidate, suffix = base, 2
        while candidate in taken:
            candidate, suffix = f"{base}_{suffix}", suffix + 1
        taken.add(candidate)
        names[doctype] = candidate
    return names


def _property(
    fieldtype: Any, options: Any, components: Mapping[str, str]
) -> Dict[str, Any]:
    """The OpenAPI schema of a value of a Frappe field type."""
    if fieldtype in TABLE_TYPES:
        child = components.get(str(options))
        items = (
            {"$ref": f"#/components/schemas/{child}"} if child else {"type": "object"}
        )
        return {"type": "array", "items": items}
    if fieldtype in INTEGER_TYPES:
        return {"type": "integer"}
    if fieldtype in NUMBER_TYPES:
        return {"type": "number"}
    if fieldtype in ANY_TYPES:
        return {}
    return {"type": "string"}


def doctype_openapi(bundle: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The bundle's DocTypes (a DocType, then its child tables) as an
    OpenAPI document of component schemas, one per DocType."""
    components = _component_names([str(meta.get("name")) for meta in bundle])
    schemas: Dict[str, Any] = {}
    for meta in bundle:
        doctype = str(meta.get("name"))
        standard = {
            **STANDARD_FIELDS,
            **(CHILD_FIELDS if _flag(meta, "istable") else {}),
        }
        properties: Dict[str, Any] = {
            name: {"type": kind} for name, kind in standard.items()
        }
        for f in _value_fields(meta):
            if holdable_field_name(f["fieldname"]):
                properties[f["fieldname"]] = _property(
                    f.get("fieldtype"), f.get("options"), components
                )
        schemas[components[doctype]] = {
            "type": "object",
            "title": doctype,
            "properties": properties,
        }
    return {
        "openapi": "3.1.0",
        "info": {"title": "ERP DocTypes", "version": "1"},
        "paths": {},
        "components": {"schemas": schemas},
    }


def lift_doctype(bundle: Sequence[Mapping[str, Any]]) -> DocTypeSchema:
    """The bundle's first DocType as a model, its child tables nested, by
    way of the federation's OpenAPI importer."""
    if not bundle:
        raise ValueError("An empty DocType bundle")
    metas = {str(meta.get("name")): meta for meta in bundle}
    components = _component_names(list(metas))
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=SHADOW_WARNING, category=UserWarning)
        lifted = openapi_to_pydantic_models(doctype_openapi(bundle))
        for model in lifted.models.values():
            # The importer resolves forward references best-effort; a model
            # left incomplete must fail here, not on its first write.
            model.model_rebuild(force=True)

    def schema_of(doctype: str, parents: FrozenSet[str]) -> DocTypeSchema:
        meta = metas[doctype]
        fields = _value_fields(meta)
        tables = {
            str(f["fieldname"]): schema_of(str(f.get("options")), parents | {doctype})
            for f in fields
            if f.get("fieldtype") in TABLE_TYPES
            and str(f.get("options")) in metas
            and str(f.get("options")) not in parents | {doctype}
        }
        return DocTypeSchema(
            doctype=doctype,
            module=meta.get("module"),
            custom=_flag(meta, "custom"),
            istable=_flag(meta, "istable"),
            issingle=_flag(meta, "issingle"),
            is_submittable=_flag(meta, "is_submittable"),
            model=lifted.models[components[doctype]],
            fields=tuple(
                DocTypeField(
                    fieldname=str(f["fieldname"]),
                    label=f.get("label"),
                    fieldtype=str(f.get("fieldtype")),
                    options=f.get("options"),
                    reqd=_flag(f, "reqd"),
                    read_only=_flag(f, "read_only"),
                    allow_on_submit=_flag(f, "allow_on_submit"),
                )
                for f in fields
            ),
            tables=tables,
            untyped=frozenset(
                str(f["fieldname"])
                for f in fields
                if not holdable_field_name(str(f["fieldname"]))
                and f.get("fieldtype") not in TABLE_TYPES
            ),
        )

    return schema_of(str(bundle[0].get("name")), frozenset())


# ---------------------------------------------------------------------------
# What the routes and abilities exchange
# ---------------------------------------------------------------------------


class ERPDocument(RouteModel):
    """A document as the ERP has it, with its child tables."""

    provider_instance_id: str
    doctype: str
    name: str
    docstatus: int = Field(0, description="0 draft, 1 submitted, 2 cancelled")
    updated_at: Optional[str] = Field(
        None,
        description="The document's version (its modified): what If-Match names",
    )
    data: Dict[str, Any] = Field(description="Every field the account may read")


class ERPDocumentList(RouteModel):
    documents: List[ERPDocument]
    start: int
    page_length: int


class ERPDocTypeInfo(RouteModel):
    name: str
    module: Optional[str] = None
    custom: bool = False
    istable: bool = False
    issingle: bool = False
    is_submittable: bool = False


class ERPDocTypeList(RouteModel):
    doctypes: List[ERPDocTypeInfo]


class ERPField(RouteModel):
    fieldname: str
    label: Optional[str] = None
    fieldtype: str
    options: Optional[str] = None
    reqd: bool = False
    read_only: bool = False
    allow_on_submit: bool = False


class ERPDocTypeSchema(RouteModel):
    doctype: str
    module: Optional[str] = None
    custom: bool
    istable: bool
    issingle: bool
    is_submittable: bool
    fields: List[ERPField]
    tables: Dict[str, str] = Field(description="Each child table field's DocType")
    json_schema: Dict[str, Any] = Field(description="The DocType's lifted model")


class ERPDeleted(RouteModel):
    doctype: str
    name: str
    deleted: bool


class ERPInstanceRef(RouteModel):
    provider_instance_id: str


class ERPDocTypeRef(ERPInstanceRef):
    doctype: str


class ERPListInput(ERPDocTypeRef):
    fields: Optional[List[str]] = Field(None, description="Fieldnames, or * (default)")
    filters: Optional[Dict[str, Any]] = Field(
        None, description="{fieldname: value} or {fieldname: [operator, value]}"
    )
    or_filters: Optional[Dict[str, Any]] = None
    order_by: Optional[str] = Field(None, description="e.g. 'modified desc'")
    start: int = 0
    page_length: int = DEFAULT_PAGE_LENGTH


class ERPDocumentRef(ERPDocTypeRef):
    name: str


class ERPCreateInput(ERPDocTypeRef):
    data: Dict[str, Any]


class ERPActionInput(ERPDocumentRef):
    if_match: Optional[str] = Field(
        None,
        description="The version it was read at (updated_at); the If-Match "
        "header names it on REST",
    )


class ERPUpdateInput(ERPActionInput):
    data: Dict[str, Any]


class ERPWebhookAccepted(RouteModel):
    event_type: str
    queued: int = Field(description="Deliveries queued for subscribers")


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def refusal(
    provider: Type[AbstractERPProvider], exc: BaseExternalError
) -> HTTPException:
    """What the caller is told of an ERP's refusal: its own reason where it
    gives one, never its raw body."""
    name = provider.friendly_name
    detail = provider.refusal_detail(exc.upstream_payload)
    status = exc.upstream_status
    if isinstance(exc, ERPConfigurationError):
        return HTTPException(status_code=503, detail=exc.message)
    if isinstance(exc, RateLimitExternalError):
        headers = (
            {"Retry-After": str(int(exc.retry_after_seconds))}
            if exc.retry_after_seconds
            else None
        )
        return HTTPException(
            status_code=429, detail=f"{name} is rate limiting", headers=headers
        )
    if isinstance(exc, AuthExternalError):
        if status == 403:
            return HTTPException(
                status_code=403,
                detail=detail
                or f"{name} does not permit this to the instance's account",
            )
        return HTTPException(
            status_code=502, detail=f"{name} refused the instance's credentials"
        )
    if isinstance(exc, InvalidInputExternalError):
        if status is None:
            return HTTPException(
                status_code=502, detail=f"{name} is not reachable at that address"
            )
        if status == 404:
            return HTTPException(
                status_code=404, detail=detail or f"Not found in {name}"
            )
        return HTTPException(
            status_code=409 if status == 409 else 422,
            detail=detail or f"{name} refused the request",
        )
    return HTTPException(status_code=502, detail=f"{name} is unavailable")


class ERPDocuments:
    """One instance's documents, for one request: what the ERP describes is
    kept for the request and no longer."""

    def __init__(self, instance: ERPInstance) -> None:
        self.instance = instance
        self._schemas: Dict[str, DocTypeSchema] = {}

    @classmethod
    def for_requester(
        cls, model_registry: Any, requester_id: str, provider_instance_id: Any
    ) -> "ERPDocuments":
        return cls(usable_instance(model_registry, requester_id, provider_instance_id))

    @property
    def provider(self) -> Type[AbstractERPProvider]:
        return self.instance.provider

    @property
    def account(self) -> ProviderInstanceModel:
        return self.instance.model

    async def _upstream(self, call: Awaitable[T]) -> T:
        try:
            return await call
        except BaseExternalError as exc:
            raise refusal(self.provider, exc) from exc

    def _document(self, doctype: str, document: Mapping[str, Any]) -> ERPDocument:
        modified = document.get("modified")
        docstatus = document.get("docstatus")
        return ERPDocument(
            provider_instance_id=self.instance.id,
            doctype=doctype,
            name=str(document.get("name") or ""),
            docstatus=docstatus if isinstance(docstatus, int) else 0,
            updated_at=str(modified) if modified else None,
            data=dict(document),
        )

    async def doctypes(self) -> List[ERPDocTypeInfo]:
        rows = await self._upstream(self.provider.doctypes(self.account))
        return [ERPDocTypeInfo.model_validate(row) for row in rows]

    async def schema(self, doctype: Any) -> DocTypeSchema:
        name = checked_doctype(doctype)
        if name not in self._schemas:
            bundle = await self._upstream(
                self.provider.doctype_bundle(self.account, name)
            )
            self._schemas[name] = lift_doctype(bundle)
        return self._schemas[name]

    async def _parent_schema(self, doctype: Any) -> DocTypeSchema:
        schema = await self.schema(doctype)
        if schema.istable:
            raise _refused(
                f"{schema.doctype} is a child table: its rows are written inside "
                f"their parent document",
                400,
            )
        return schema

    async def list(self, query: ListQuery) -> List[ERPDocument]:
        rows = await self._upstream(
            self.provider.list_documents(
                self.account,
                query.doctype,
                fields=query.fields,
                filters=query.filters,
                or_filters=query.or_filters,
                order_by=query.order_by,
                start=query.start,
                page_length=query.page_length,
            )
        )
        return [self._document(query.doctype, row) for row in rows]

    async def get(self, doctype: Any, name: Any) -> ERPDocument:
        doctype, name = checked_doctype(doctype), checked_name(name)
        document = await self._upstream(
            self.provider.get_document(self.account, doctype, name)
        )
        return self._document(doctype, document)

    async def create(self, doctype: Any, data: Any) -> ERPDocument:
        schema = await self._parent_schema(doctype)
        document = await self._upstream(
            self.provider.create_document(
                self.account, schema.doctype, schema.checked_write(data)
            )
        )
        return self._document(schema.doctype, document)

    async def _current(
        self, doctype: str, name: str, expected: Optional[IfMatch]
    ) -> ERPDocument:
        """The document now; 412 when it is not at the version expected."""
        current = await self.get(doctype, name)
        if expected is not None and not expected.matches(current):
            raise PreconditionFailed(current)
        return current

    async def _refused_save(
        self,
        doctype: str,
        name: str,
        expected: Optional[IfMatch],
        exc: BaseExternalError,
    ) -> NoReturn:
        """A save the ERP refused: 412 when the document has moved on from
        the version expected (the ERP's own timestamp check), else the
        ERP's refusal."""
        if (
            expected is not None
            and not expected.any_version
            and isinstance(exc, InvalidInputExternalError)
            and exc.upstream_status not in (None, 404)
        ):
            await self._current(doctype, name, expected)
        raise refusal(self.provider, exc) from exc

    async def update(
        self, doctype: Any, name: Any, data: Any, if_match: Optional[str]
    ) -> ERPDocument:
        schema = await self._parent_schema(doctype)
        name = checked_name(name)
        payload = schema.checked_write(data)
        return await self._save(schema, name, payload, save_expectation(if_match))

    async def update_expecting(
        self, doctype: Any, name: Any, data: Any, expected: Optional[IfMatch]
    ) -> ERPDocument:
        """:meth:`update`, for a caller that already read the version the
        save names (None: it names none, and need not)."""
        schema = await self._parent_schema(doctype)
        name = checked_name(name)
        return await self._save(schema, name, schema.checked_write(data), expected)

    async def _save(
        self,
        schema: DocTypeSchema,
        name: str,
        payload: Dict[str, Any],
        expected: Optional[IfMatch],
    ) -> ERPDocument:
        version: Optional[str] = None
        if expected is not None and not expected.any_version:
            if len(expected.versions) == 1:
                (version,) = expected.versions
            else:
                version = (
                    await self._current(schema.doctype, name, expected)
                ).updated_at
        try:
            document = await self.provider.update_document(
                self.account, schema.doctype, name, payload, version
            )
        except BaseExternalError as exc:
            await self._refused_save(schema.doctype, name, expected, exc)
        return self._document(schema.doctype, document)

    async def delete(self, doctype: Any, name: Any, if_match: Optional[str]) -> None:
        doctype, name = checked_doctype(doctype), checked_name(name)
        await self.delete_expecting(doctype, name, save_expectation(if_match))

    async def delete_expecting(
        self, doctype: Any, name: Any, expected: Optional[IfMatch]
    ) -> None:
        """:meth:`delete`, for a caller that already read the version the
        delete names."""
        doctype, name = checked_doctype(doctype), checked_name(name)
        if expected is not None:
            await self._current(doctype, name, expected)
        await self._upstream(self.provider.delete_document(self.account, doctype, name))

    async def _submittable(self, doctype: Any) -> DocTypeSchema:
        schema = await self._parent_schema(doctype)
        if not schema.is_submittable:
            raise _refused(f"{schema.doctype} is not submittable", 400)
        return schema

    async def submit(
        self, doctype: Any, name: Any, if_match: Optional[str]
    ) -> ERPDocument:
        schema = await self._submittable(doctype)
        name = checked_name(name)
        expected = save_expectation(if_match)
        current = await self._current(schema.doctype, name, expected)
        try:
            document = await self.provider.submit_document(self.account, current.data)
        except BaseExternalError as exc:
            await self._refused_save(schema.doctype, name, expected, exc)
        return self._document(schema.doctype, document)

    async def cancel(
        self, doctype: Any, name: Any, if_match: Optional[str]
    ) -> ERPDocument:
        schema = await self._submittable(doctype)
        name = checked_name(name)
        expected = save_expectation(if_match)
        await self._current(schema.doctype, name, expected)
        try:
            document = await self.provider.cancel_document(
                self.account, schema.doctype, name
            )
        except BaseExternalError as exc:
            await self._refused_save(schema.doctype, name, expected, exc)
        return self._document(schema.doctype, document)


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebhookEvent:
    event: str
    doctype: str
    name: str
    payload: Dict[str, Any]

    @classmethod
    def parsed(cls, body: bytes) -> "WebhookEvent":
        """The event a verified body reports: a JSON object naming its
        ``event`` (a Frappe document event), ``doctype`` and ``name``."""
        try:
            payload = json.loads(body)
        except ValueError:
            raise _refused("The body is a JSON object", 400) from None
        if not isinstance(payload, dict):
            raise _refused("The body is a JSON object", 400)
        event = payload.get("event")
        if event not in DOC_EVENTS:
            raise _refused(f"event is one of {', '.join(DOC_EVENTS)}", 400)
        try:
            doctype = checked_doctype(payload.get("doctype"))
            name = checked_name(payload.get("name"))
        except HTTPException as refused:
            raise _refused(refused.detail, 400) from None
        return cls(event=str(event), doctype=doctype, name=name, payload=payload)


def receive_webhook(
    model_registry: Any,
    provider_instance_id: str,
    headers: Mapping[str, str],
    body: bytes,
) -> ERPWebhookAccepted:
    """Pass a webhook the instance signed on to the subscribers who may use
    the instance. 401, whatever the reason, unless the instance is live and
    enabled, holds a webhook secret, signed this body, and has not had it
    delivered before."""
    from zephyrex.extensions.webhooks.BLL_WebhookDelivery import (
        dispatch_webhook_event,
    )

    found = live_instance(model_registry, provider_instance_id)
    if found is None or found.model.enabled is False:
        raise unsigned()
    signature = found.provider.verified_webhook(found.model, body, headers)
    if signature is None or not get_replay_cache().mark_if_unused(
        f"erp:webhook:{found.id}:{signature}", WEBHOOK_REPLAY_SECONDS
    ):
        raise unsigned()
    event = WebhookEvent.parsed(body)
    event_type = f"erp.{event.event}"
    queued = dispatch_webhook_event(
        model_registry,
        event_type,
        {
            "provider": found.provider.name,
            "provider_instance_id": found.id,
            "event": event.event,
            "doctype": event.doctype,
            "name": event.name,
            "data": event.payload,
        },
        may_see=lambda user_id: can_use(model_registry, user_id, found.model),
    )
    return ERPWebhookAccepted(event_type=event_type, queued=len(queued))


# ---------------------------------------------------------------------------
# Routes (REST and GraphQL)
# ---------------------------------------------------------------------------


def _if_match(request: Request, body: ERPActionInput) -> Optional[str]:
    """The version a save names: the If-Match header (REST), else the
    input's ``if_match`` (GraphQL)."""
    return request.headers.get(IF_MATCH_HEADER) or body.if_match


def _versioned(response: Response, document: ERPDocument) -> ERPDocument:
    response.headers.update(etag_headers(document))
    return document


class ERPManager(AbstractBLLManager, RouterMixin):
    """The documents of the instances the requester may use. Each operation
    is a POST naming the instance (a DocType or a document name is free
    text, so it travels in the body), on REST and as a GraphQL field."""

    prefix: ClassVar[Optional[str]] = ERP_PREFIX
    tags: ClassVar[Optional[List[str]]] = ["ERP"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    def _documents(self, provider_instance_id: str) -> ERPDocuments:
        return ERPDocuments.for_requester(
            self.model_registry, self.requester.id, provider_instance_id
        )

    @custom_route(
        method="POST",
        path="/doctypes",
        input_model=ERPInstanceRef,
        output_model=ERPDocTypeList,
        authentication_type="jwt",
        graphql_kind="query",
        openapi_tags=("ERP",),
        summary="Every DocType the instance's account is shown",
    )
    async def doctypes_route(self, body: ERPInstanceRef) -> ERPDocTypeList:
        documents = self._documents(body.provider_instance_id)
        return ERPDocTypeList(doctypes=await documents.doctypes())

    @custom_route(
        method="POST",
        path="/schema",
        input_model=ERPDocTypeRef,
        output_model=ERPDocTypeSchema,
        authentication_type="jwt",
        graphql_kind="query",
        openapi_tags=("ERP",),
        summary="A DocType's fields, child tables and JSON Schema",
    )
    async def schema_route(self, body: ERPDocTypeRef) -> ERPDocTypeSchema:
        schema = await self._documents(body.provider_instance_id).schema(body.doctype)
        return ERPDocTypeSchema.model_validate(schema.described())

    @custom_route(
        method="POST",
        path="/document/list",
        input_model=ERPListInput,
        output_model=ERPDocumentList,
        authentication_type="jwt",
        graphql_kind="query",
        openapi_tags=("ERP",),
        summary="A page of a DocType's documents",
    )
    async def list_documents_route(self, body: ERPListInput) -> ERPDocumentList:
        query = ListQuery.checked(
            body.doctype,
            body.fields,
            body.filters,
            body.or_filters,
            body.order_by,
            body.start,
            body.page_length,
        )
        found = await self._documents(body.provider_instance_id).list(query)
        return ERPDocumentList(
            documents=found, start=query.start, page_length=query.page_length
        )

    @custom_route(
        method="POST",
        path="/document/get",
        input_model=ERPDocumentRef,
        output_model=ERPDocument,
        authentication_type="jwt",
        graphql_kind="query",
        openapi_tags=("ERP",),
        summary="A document with its child tables; its ETag is its version",
    )
    async def get_document_route(
        self, body: ERPDocumentRef, response: Response
    ) -> ERPDocument:
        documents = self._documents(body.provider_instance_id)
        return _versioned(response, await documents.get(body.doctype, body.name))

    @custom_route(
        method="POST",
        path="/document/create",
        input_model=ERPCreateInput,
        output_model=ERPDocument,
        authentication_type="jwt",
        openapi_tags=("ERP",),
        summary="Create a document, as the ERP validates it",
    )
    async def create_document_route(
        self, body: ERPCreateInput, response: Response
    ) -> ERPDocument:
        documents = self._documents(body.provider_instance_id)
        return _versioned(response, await documents.create(body.doctype, body.data))

    @custom_route(
        method="POST",
        path="/document/update",
        input_model=ERPUpdateInput,
        output_model=ERPDocument,
        authentication_type="jwt",
        openapi_tags=("ERP",),
        summary="Save onto a document read at the If-Match version (412 if changed)",
    )
    async def update_document_route(
        self, body: ERPUpdateInput, request: Request, response: Response
    ) -> ERPDocument:
        documents = self._documents(body.provider_instance_id)
        saved = await documents.update(
            body.doctype, body.name, body.data, _if_match(request, body)
        )
        return _versioned(response, saved)

    @custom_route(
        method="POST",
        path="/document/delete",
        input_model=ERPActionInput,
        output_model=ERPDeleted,
        authentication_type="jwt",
        openapi_tags=("ERP",),
        summary="Delete a document read at the If-Match version (412 if changed)",
    )
    async def delete_document_route(
        self, body: ERPActionInput, request: Request
    ) -> ERPDeleted:
        documents = self._documents(body.provider_instance_id)
        await documents.delete(body.doctype, body.name, _if_match(request, body))
        return ERPDeleted(doctype=body.doctype, name=body.name, deleted=True)

    @custom_route(
        method="POST",
        path="/document/submit",
        input_model=ERPActionInput,
        output_model=ERPDocument,
        authentication_type="jwt",
        openapi_tags=("ERP",),
        summary="Submit a draft read at the If-Match version (412 if changed)",
    )
    async def submit_document_route(
        self, body: ERPActionInput, request: Request, response: Response
    ) -> ERPDocument:
        documents = self._documents(body.provider_instance_id)
        submitted = await documents.submit(
            body.doctype, body.name, _if_match(request, body)
        )
        return _versioned(response, submitted)

    @custom_route(
        method="POST",
        path="/document/cancel",
        input_model=ERPActionInput,
        output_model=ERPDocument,
        authentication_type="jwt",
        openapi_tags=("ERP",),
        summary="Cancel a submitted document read at the If-Match version",
    )
    async def cancel_document_route(
        self, body: ERPActionInput, request: Request, response: Response
    ) -> ERPDocument:
        documents = self._documents(body.provider_instance_id)
        cancelled = await documents.cancel(
            body.doctype, body.name, _if_match(request, body)
        )
        return _versioned(response, cancelled)


class ERPWebhookManager(AbstractBLLManager, RouterMixin):
    """Where an ERP posts its signed webhooks: no table of its own, and no
    session ever used."""

    prefix: ClassVar[Optional[str]] = WEBHOOK_PREFIX
    tags: ClassVar[Optional[List[str]]] = ["ERP Webhooks"]
    auth_type: ClassVar[AuthType] = AuthType.NONE
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    @custom_route(
        method="POST",
        path=WEBHOOK_PATH,
        output_model=ERPWebhookAccepted,
        authentication_type="none",
        raw_body=True,
        openapi_tags=("ERP Webhooks",),
        summary="Take a webhook the instance's ERP signed, for its subscribers",
        description=(
            "The body is the webhook's JSON object, naming its event (a "
            f"document event: {', '.join(DOC_EVENTS)}), doctype and name. "
            "X-Frappe-Webhook-Signature is the base64 HMAC-SHA256 of the body, "
            "keyed by the instance's webhook_secret. 401 for a bad or replayed "
            "signature; 400 for a signed body naming no event."
        ),
        expose_in=(ExposeIn.REST,),
    )
    @rate_limit(WEBHOOK_RATE_LIMIT, scope="(ip, endpoint)")
    async def deliver_route(
        self, provider_instance_id: str, request: Request
    ) -> ERPWebhookAccepted:
        body = await capped_body(
            request,
            MAX_WEBHOOK_BYTES,
            f"A webhook is at most {MAX_WEBHOOK_BYTES} bytes",
        )
        return receive_webhook(
            self.model_registry,
            provider_instance_id,
            lower_case_headers(request.headers.items()),
            body,
        )


# An ERP has no session here: a webhook is authenticated by its signature
# alone, and its body is the ERP's, not a negotiated format.
_WEBHOOK_PATHS = re.escape(WEBHOOK_PREFIX) + WEBHOOK_PATH.replace(
    "{provider_instance_id}", "[^/]+"
)
accept_cross_site_writes(_WEBHOOK_PATHS)
skip_negotiation(_WEBHOOK_PATHS)
