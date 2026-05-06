"""Field- and column-level attribute-based access control primitives.

Provides Pydantic-field-level ``requires=[<permission>]`` metadata plus the
serialization-time field filter, the response-payload integrator, and the
search/order-by validator.

A field marked
``Field(..., json_schema_extra=requires("payment.invoice.read_lines"))`` is
omitted from serialized output for requesters lacking the permission.
``Sensitive[T]`` is a typed alias for the same construct on common cases.

Integration:
- REST serialization integrates via :func:`apply_field_acl_to_response` —
  ``Pydantic2FastAPI.serialize_for_response`` calls into this helper before
  returning.
- GraphQL resolver integration uses the same helper through
  :func:`apply_field_acl_to_response`. The permission cache is per-request
  per-(manager, requester) per the Item 45 acceptance criteria.
- Search and order-by gates use :func:`validate_query_field_access`: the
  endpoint layer rejects requests using restricted fields with a typed 403
  rather than leaking values through inference attacks.
"""

from threading import RLock
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Optional,
    Tuple,
    Type,
    TypeVar,
)

from pydantic import BaseModel
from pydantic.fields import FieldInfo

_REQUIRES_KEY = "requires_permissions"

# Sentinel mode for fields the requester cannot see. Per Item 45 the choice
# between OMIT (drop the key entirely) and MASK (replace with "***") is
# operator-configurable. The default is OMIT because it survives JSON-schema
# validators that reject unknown sentinel strings.
SENTINEL_OMIT = "omit"
SENTINEL_MASK = "mask"
DEFAULT_SENTINEL_MODE = SENTINEL_OMIT
MASK_PLACEHOLDER = "***"

T = TypeVar("T")


def Sensitive(  # noqa: N802 — naming mirrors typing.Annotated style
    permission: str,
    *,
    additional: Iterable[str] = (),
) -> Dict[str, Any]:
    """Concise alias for ``json_schema_extra=requires(permission, *additional)``.

    Intended as the common-case ergonomic shorthand:

    ::

        ssn: str = Field(..., **Sensitive("auth.user.read_ssn"))

    For the multi-permission case, pass extras via ``additional``.
    """
    perms = (permission, *tuple(additional))
    return {"json_schema_extra": requires(*perms)}


def requires(*permissions: str):
    """Pydantic field metadata helper. Use as:

        class Foo(BaseModel):
            ssn: str = Field(..., json_schema_extra=requires("auth.user.read_ssn"))
    """
    return {_REQUIRES_KEY: tuple(permissions)}


def get_required_permissions(field_info: FieldInfo) -> FrozenSet[str]:
    """Return the frozenset of permissions a requester must hold to see
    this field. Empty frozenset means "no restriction"."""
    extra = getattr(field_info, "json_schema_extra", None) or {}
    if not isinstance(extra, dict):
        return frozenset()
    perms = extra.get(_REQUIRES_KEY, ())
    return frozenset(perms)


def filter_response_dict(
    model: BaseModel,
    has_permission: Callable[[str], bool],
) -> dict:
    """Return ``model.model_dump()`` with disallowed fields omitted.

    For each field whose ``requires`` set is non-empty, the field is
    INCLUDED only if ``has_permission(p)`` returns True for EVERY
    permission ``p`` in the set. AND-semantics, not OR.

    Disallowed fields are dropped from the dict. (Sentinel-replacement
    is configurable in a future enhancement; for v1, omit.)
    """
    data = model.model_dump()
    cls_fields = type(model).model_fields
    for name, field_info in cls_fields.items():
        required = get_required_permissions(field_info)
        if required and not all(has_permission(p) for p in required):
            data.pop(name, None)
    return data


def collect_restricted_fields(
    model_cls: Type[BaseModel],
) -> List[tuple[str, FrozenSet[str]]]:
    """Walk a model class for ``requires``-tagged fields. Returns
    [(field_name, frozenset_of_permissions), ...]."""
    results = []
    for name, info in model_cls.model_fields.items():
        required = get_required_permissions(info)
        if required:
            results.append((name, required))
    return sorted(results, key=lambda x: x[0])


def restricted_for_filter_or_order(model_cls: Type[BaseModel]) -> FrozenSet[str]:
    """The set of field names a requester without grants cannot use
    in ``ORDER BY`` or filter clauses (Item 45 inference-attack
    mitigation). Endpoints validate against this set at request
    time."""
    return frozenset(name for name, _ in collect_restricted_fields(model_cls))


# ---------------------------------------------------------------------------
# Per-request allowed-field-set cache (Item 45 performance contract)
# ---------------------------------------------------------------------------
#
# The grant check on a 10k-row list response is O(rows × restricted_fields)
# without caching. The framework computes the allowed-field set once per
# (manager, requester) at request bind and reuses it for every row in the
# response. The cache is bounded by the request's lifetime — callers
# instantiate a `FieldACLCache` per request and discard it on response.

_FieldKey = Tuple[Type[BaseModel], int]


class FieldACLCache:
    """Per-request cache of (model, requester) → allowed-field-set lookups.

    Multiple resolvers / serializers in the same request share one cache so
    a list of 10k rows pays one permission resolution per restricted field,
    not one per row. The cache is locked because GraphQL resolvers under
    Strawberry can fan out concurrently within the same request.
    """

    def __init__(self) -> None:
        self._cache: Dict[_FieldKey, FrozenSet[str]] = {}
        self._lock = RLock()

    def allowed_fields(
        self,
        model_cls: Type[BaseModel],
        requester_id: int,
        has_permission: Callable[[str], bool],
    ) -> FrozenSet[str]:
        """Return the set of field names allowed for ``requester_id``.

        Cached on (model_cls, requester_id). The first call materializes
        the set by walking restricted fields and evaluating the permission
        function; subsequent calls reuse the cached frozenset.
        """
        key = (model_cls, requester_id)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached

            allowed: List[str] = []
            for name, field_info in model_cls.model_fields.items():
                required = get_required_permissions(field_info)
                if not required or all(has_permission(p) for p in required):
                    allowed.append(name)
            result = frozenset(allowed)
            self._cache[key] = result
            return result


# ---------------------------------------------------------------------------
# Response-payload integration (REST + GraphQL)
# ---------------------------------------------------------------------------


def apply_field_acl_to_response(
    payload: Any,
    model_cls: Optional[Type[BaseModel]],
    has_permission: Optional[Callable[[str], bool]],
    *,
    sentinel_mode: str = DEFAULT_SENTINEL_MODE,
    cache: Optional[FieldACLCache] = None,
    requester_id: Optional[int] = None,
) -> Any:
    """Apply Item 45 field ACL to a serialized REST/GraphQL payload.

    ``payload`` may be a single dict, a list of dicts, or arbitrary other
    data (returned unchanged so the function is safe at every serialization
    seam). Restricted fields are either omitted (``sentinel_mode=OMIT``) or
    replaced with the framework's mask placeholder
    (``sentinel_mode=MASK``) — operators choose per deployment.

    ``has_permission`` is the requester's permission resolver (typically
    ``requester.has_permission``). When ``None`` the function is a no-op so
    framework-internal calls (system-key audit jobs, etc.) bypass the gate.

    ``cache`` lets callers share the per-request allowed-field memoization
    across rows in a list response. When omitted a temporary cache is used
    so a single payload still benefits.
    """
    if payload is None or model_cls is None or has_permission is None:
        return payload

    restricted = collect_restricted_fields(model_cls)
    if not restricted:
        return payload

    cache = cache or FieldACLCache()
    rid = requester_id if requester_id is not None else id(has_permission)
    allowed = cache.allowed_fields(model_cls, rid, has_permission)

    return _apply_to_value(payload, model_cls, allowed, sentinel_mode)


def _apply_to_value(
    value: Any,
    model_cls: Type[BaseModel],
    allowed: FrozenSet[str],
    sentinel_mode: str,
) -> Any:
    if value is None:
        return value
    if isinstance(value, list):
        return [_apply_to_value(item, model_cls, allowed, sentinel_mode) for item in value]
    if isinstance(value, dict):
        return _apply_to_dict(value, model_cls, allowed, sentinel_mode)
    if isinstance(value, BaseModel):
        return _apply_to_dict(value.model_dump(), model_cls, allowed, sentinel_mode)
    return value


def _apply_to_dict(
    data: Dict[str, Any],
    model_cls: Type[BaseModel],
    allowed: FrozenSet[str],
    sentinel_mode: str,
) -> Dict[str, Any]:
    """Filter a serialized dict by the allowed-field set."""
    filtered: Dict[str, Any] = {}
    for key, value in data.items():
        if key in model_cls.model_fields and key not in allowed:
            if sentinel_mode == SENTINEL_MASK:
                filtered[key] = MASK_PLACEHOLDER
            # SENTINEL_OMIT — drop entirely.
            continue
        filtered[key] = value
    return filtered


# ---------------------------------------------------------------------------
# Search / order-by inference-attack mitigation
# ---------------------------------------------------------------------------


class FieldACLViolation(Exception):
    """Raised when a query references a field the requester cannot access.

    Endpoints catch this and return a typed 403 / GraphQL error rather than
    silently dropping the field — silent drops mask the policy and confuse
    operators trying to debug failed clients.
    """

    def __init__(self, field_names: Iterable[str], context: str) -> None:
        self.field_names = tuple(field_names)
        self.context = context
        super().__init__(
            f"Restricted fields rejected for {context}: {sorted(self.field_names)}"
        )


def validate_query_field_access(
    model_cls: Type[BaseModel],
    requested_fields: Iterable[str],
    has_permission: Optional[Callable[[str], bool]],
    *,
    context: str = "query",
) -> List[str]:
    """Reject ``requested_fields`` that reference restricted fields the
    requester cannot access.

    Returns the list of disallowed field names. Callers either raise
    :class:`FieldACLViolation` themselves or convert the list into the
    appropriate transport-layer error (HTTPException(403) for REST, typed
    GraphQL error for resolvers). When ``has_permission`` is ``None`` no
    fields are restricted (system-internal callers).

    ``context`` is folded into the error message — set it to ``"order_by"``
    or ``"filter"`` so operators can distinguish inference-attack vectors
    in audit logs.
    """
    if has_permission is None:
        return []

    restricted_map = dict(collect_restricted_fields(model_cls))
    if not restricted_map:
        return []

    disallowed: List[str] = []
    for field in requested_fields:
        if field not in restricted_map:
            continue
        required = restricted_map[field]
        if not all(has_permission(p) for p in required):
            disallowed.append(field)
    return disallowed
