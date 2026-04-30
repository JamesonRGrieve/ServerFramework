"""Field- and column-level attribute-based access control primitives.

Provides Pydantic-field-level ``requires=[<permission>]`` metadata plus a
serialization-time field filter. A field marked
``Field(..., json_schema_extra=requires("payment.invoice.read_lines"))`` is
omitted from serialized output for requesters lacking the permission.

Integration points (deferred):
- REST serialization integration lives in ``Pydantic2FastAPI.py`` and lands
  alongside Item 40 / Item 45 follow-up.
- GraphQL resolver integration lives in ``Pydantic2Strawberry.py`` and lands
  alongside Item 46.
"""

from typing import Any, Callable, FrozenSet, List, Optional, Type
from pydantic import BaseModel
from pydantic.fields import FieldInfo

_REQUIRES_KEY = "requires_permissions"


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
