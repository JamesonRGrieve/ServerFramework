# SPDX-License-Identifier: AGPL-3.0-or-later
"""Language-agnostic SDK intermediate representation.

The ``meta_sdk_<lang>`` extensions (``meta_sdk_py``, ``meta_sdk_ts``,
``meta_sdk_rs``) all emit a typed client SDK for the same committed registry.
What differs between them is *syntax*, not *shape*: every generated client
exposes the identical REST surface that :class:`sdk.AbstractSDKHandler` serves
(``create``/``get``/``list``/``update``/``delete``/``search`` plus the three
batch variants), against the identical endpoint convention and — critically —
the identical request bodies.

This module is that shared shape. It walks the registry once into a list of
:class:`ResourceDescriptor` records and pins the canonical
:data:`STANDARD_OPERATIONS` set. Each operation carries its full request
contract — the HTTP method, the path suffix, the typed argument list, and how
those arguments assemble into the JSON body — so a per-language emitter is a
pure, mechanical render over data it never has to re-derive. Keeping the whole
contract here (rather than re-encoding the batch body keys in three emitters) is
what keeps the language SDKs from silently drifting off the server's wire
format.

The request contract mirrors ``sdk/AbstractSDKHandler.py`` exactly:
``batch_create`` posts ``{<name_plural>: items}``, ``batch_update`` puts
``{<name>: updates, "target_ids": ids}``, ``batch_delete`` deletes
``{"target_ids": ids}``. The resource-name / endpoint derivation is reused
verbatim from :mod:`sdk.SDKGenerator` so the Python emitter and the IR agree by
construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from zephyrex.sdk.SDKGenerator import (
    _endpoint_for,
    _iter_manager_classes,
    _resource_name_for,
    _resource_name_plural_for,
)

# Abstract argument/return types, mapped to concrete syntax by each emitter.
TYPE_OBJECT = "object"  # a single resource payload (dict / Record / serde_json::Value)
TYPE_STRING = "string"  # an identifier
TYPE_OBJECT_LIST = "object_list"  # a list of resource payloads
TYPE_STRING_LIST = "string_list"  # a list of identifiers
TYPE_QUERY = "query_map"  # an optional map of query parameters


@dataclass(frozen=True)
class Arg:
    """One method argument: its name and its abstract type token."""

    name: str
    type_token: str


@dataclass(frozen=True)
class BodyField:
    """One entry in the JSON request body.

    ``key_token`` selects the JSON key:
      * ``""``              -> the argument *is* the whole body (no wrapper),
      * ``"name"``          -> keyed by the resource singular name,
      * ``"name_plural"``   -> keyed by the resource plural name,
      * ``"literal:<key>"`` -> keyed by the literal ``<key>``.
    ``arg`` names the method argument supplying the value.
    """

    key_token: str
    arg: str


@dataclass(frozen=True)
class Operation:
    """One REST operation, fully specified and language-independent.

    ``path_suffix`` is appended to the resource ``endpoint`` with ``{id}`` as the
    single interpolation point (present iff ``needs_id``). ``body`` is empty for
    GET/DELETE-without-payload; a single ``BodyField`` with an empty key token is
    a raw (unwrapped) body; multiple entries assemble an object literal.
    """

    name: str
    http_method: str
    path_suffix: str
    needs_id: bool
    args: Tuple[Arg, ...]
    body: Tuple[BodyField, ...]
    has_query: bool
    returns_list: bool


# The canonical operation set, byte-for-byte aligned with the URL/method/body
# choices in ``sdk/AbstractSDKHandler.py``. Core owns this contract so the three
# language SDKs cannot diverge from the server or from each other.
STANDARD_OPERATIONS: Tuple[Operation, ...] = (
    Operation(
        "create",
        "POST",
        "",
        False,
        (Arg("data", TYPE_OBJECT),),
        (BodyField("name", "data"),),
        False,
        False,
    ),
    Operation(
        "get",
        "GET",
        "/{id}",
        True,
        (Arg("id", TYPE_STRING),),
        (),
        False,
        False,
    ),
    Operation(
        "list",
        "GET",
        "",
        False,
        (Arg("query", TYPE_QUERY),),
        (),
        True,
        True,
    ),
    Operation(
        "update",
        "PUT",
        "/{id}",
        True,
        (Arg("id", TYPE_STRING), Arg("data", TYPE_OBJECT)),
        (BodyField("name", "data"),),
        False,
        False,
    ),
    Operation(
        "delete",
        "DELETE",
        "/{id}",
        True,
        (Arg("id", TYPE_STRING),),
        (),
        False,
        False,
    ),
    Operation(
        "search",
        "GET",
        "/search",
        False,
        (Arg("query", TYPE_QUERY),),
        (),
        True,
        True,
    ),
    Operation(
        "batch_create",
        "POST",
        "",
        False,
        (Arg("items", TYPE_OBJECT_LIST),),
        (BodyField("name_plural", "items"),),
        False,
        True,
    ),
    Operation(
        "batch_update",
        "PUT",
        "",
        False,
        (Arg("updates", TYPE_OBJECT), Arg("ids", TYPE_STRING_LIST)),
        (BodyField("name", "updates"), BodyField("literal:target_ids", "ids")),
        False,
        True,
    ),
    Operation(
        "batch_delete",
        "DELETE",
        "",
        False,
        (Arg("ids", TYPE_STRING_LIST),),
        (BodyField("literal:target_ids", "ids"),),
        False,
        True,
    ),
)


def resolve_body_key(field: BodyField, resource: "ResourceDescriptor") -> Optional[str]:
    """Resolve a ``BodyField.key_token`` to its JSON key for ``resource``.

    Returns ``None`` for a raw (unwrapped) body — the argument is the whole body.
    """
    token = field.key_token
    if token == "":
        return None
    if token == "name":
        return resource.name
    if token == "name_plural":
        return resource.name_plural
    if token.startswith("literal:"):
        return token[len("literal:") :]
    raise ValueError(f"Unknown body key token: {token!r}")


@dataclass(frozen=True)
class ResourceDescriptor:
    """A single RouterMixin-tagged resource, reduced to what an emitter needs."""

    name: str  # snake_case singular, e.g. "user"
    name_plural: str  # e.g. "users"
    endpoint: str  # base path, e.g. "/v1/user"
    version: str  # e.g. "v1"
    deprecated_in: Optional[str]
    sunset_in: Optional[str]
    manager_qualname: str  # module.Qualname of the source manager

    @property
    def operations(self) -> Tuple[Operation, ...]:
        """The REST operations this resource exposes (the standard set)."""
        return STANDARD_OPERATIONS


def extract_resources(registry: Any) -> List[ResourceDescriptor]:
    """Reduce a committed registry to an ordered list of resource descriptors.

    Accepts every registry shape :func:`sdk.SDKGenerator._iter_manager_classes`
    does. Order is deterministic (by manager module then qualname), so repeated
    generation over the same registry is byte-stable.
    """
    resources: List[ResourceDescriptor] = []
    for manager_cls in _iter_manager_classes(registry):
        resource_name = _resource_name_for(manager_cls)
        resources.append(
            ResourceDescriptor(
                name=resource_name,
                name_plural=_resource_name_plural_for(resource_name),
                endpoint=_endpoint_for(manager_cls, resource_name),
                version=getattr(manager_cls, "version", "v1") or "v1",
                deprecated_in=getattr(manager_cls, "deprecated_in", None),
                sunset_in=getattr(manager_cls, "sunset_in", None),
                manager_qualname=(
                    f"{manager_cls.__module__}.{manager_cls.__qualname__}"
                ),
            )
        )
    return resources
