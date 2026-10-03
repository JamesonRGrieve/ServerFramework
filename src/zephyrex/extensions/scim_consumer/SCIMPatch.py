# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIM 2.0 PATCH (RFC 7644 §3.5.2) applied to a resource as this server
renders it. The patched resource is then read back exactly as a PUT body
is, so PATCH and PUT share one validation.

``add``, ``replace`` and ``remove`` (any case: Azure AD sends
``Replace``) take a path of ``attribute``, ``attribute.sub``,
``attribute[filter]`` or ``attribute[filter].sub``, or no path with an
object of such paths. A value filter that matches nothing and pins values
by equality (``emails[type eq "work"].value``) adds the element it
describes; a ``remove`` of a multi-valued attribute with a value removes
the values listed (``members`` with ``[{"value": id}]``)."""

import copy
import json
from typing import Any, Dict, FrozenSet, List, Mapping, MutableMapping, Optional

from zephyrex.extensions.scim_consumer.SCIMErrors import (
    INVALID_SYNTAX,
    INVALID_VALUE,
    MUTABILITY,
    NO_TARGET,
    bad_request,
)
from zephyrex.extensions.scim_consumer.SCIMFilter import (
    CORE_URNS,
    Filter,
    PatchPath,
    equality_seed,
    lookup,
    matches,
    parse_patch_path,
)

PATCH_OP_URN = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
PATCH_OPERATIONS = frozenset({"add", "remove", "replace"})
MAX_OPERATIONS = 1000


def _identity(value: Any) -> str:
    """What makes two values of a multi-valued attribute the same one."""
    if isinstance(value, Mapping):
        key = lookup(value, "value")
        if key is not None:
            return json.dumps(value[key], sort_keys=True, default=str)
    return json.dumps(value, sort_keys=True, default=str)


def _merge(target: MutableMapping[str, Any], value: Mapping[str, Any]) -> None:
    for name, item in value.items():
        target[lookup(target, name) or name] = item


def _container(
    resource: Dict[str, Any], patch_path: PatchPath, read_only: FrozenSet[str]
) -> MutableMapping[str, Any]:
    urn = patch_path.path.urn
    if urn is None or urn.lower() in CORE_URNS:
        if patch_path.path.attribute.lower() in read_only:
            raise bad_request(MUTABILITY, f"{patch_path.path.attribute} is read-only")
        return resource
    key = lookup(resource, urn) or urn
    extension = resource.get(key)
    if not isinstance(extension, dict):
        extension = resource[key] = {}
    return extension


def _require(value: Any, operation: str) -> Any:
    if value is None:
        raise bad_request(INVALID_VALUE, f"{operation} needs a value")
    return value


def _apply_without_filter(
    container: MutableMapping[str, Any],
    operation: str,
    patch_path: PatchPath,
    value: Any,
) -> None:
    name = patch_path.path.attribute
    key = lookup(container, name) or name
    current = container.get(key)
    sub = patch_path.path.sub_attribute
    if sub is not None:
        targets = current if isinstance(current, list) else [current]
        if operation == "remove":
            for item in targets:
                if isinstance(item, dict):
                    item.pop(lookup(item, sub) or sub, None)
            return
        if isinstance(current, list):
            if not current:
                current.append({})
            elements = [item for item in current if isinstance(item, dict)]
        else:
            if not isinstance(current, dict):
                current = container[key] = {}
            elements = [current]
        for element in elements:
            element[lookup(element, sub) or sub] = _require(value, operation)
        return
    if operation == "remove":
        if value is not None and isinstance(current, list):
            drop = {
                _identity(v) for v in (value if isinstance(value, list) else [value])
            }
            container[key] = [item for item in current if _identity(item) not in drop]
        else:
            container.pop(key, None)
        return
    _require(value, operation)
    if operation == "add" and (isinstance(current, list) or isinstance(value, list)):
        merged: List[Any] = list(current) if isinstance(current, list) else []
        held = {_identity(item) for item in merged}
        for item in value if isinstance(value, list) else [value]:
            if _identity(item) not in held:
                merged.append(item)
                held.add(_identity(item))
        container[key] = merged
    elif isinstance(current, dict) and isinstance(value, Mapping):
        _merge(current, value)
    else:
        container[key] = copy.deepcopy(value)


def _apply_with_filter(
    container: MutableMapping[str, Any],
    operation: str,
    patch_path: PatchPath,
    value_filter: Filter,
    value: Any,
) -> None:
    name = patch_path.path.attribute
    key = lookup(container, name) or name
    current = container.get(key)
    if current is not None and not isinstance(current, list):
        raise bad_request(
            INVALID_VALUE, f"{name} is not multi-valued; it takes no value filter"
        )
    elements: List[Any] = current if isinstance(current, list) else []
    matched: List[Dict[str, Any]] = [
        element
        for element in elements
        if isinstance(element, dict) and matches(value_filter, element, name.lower())
    ]
    sub = patch_path.sub_attribute
    if operation == "remove":
        if sub is not None:
            for element in matched:
                element.pop(lookup(element, sub) or sub, None)
        else:
            chosen = {id(element) for element in matched}
            container[key] = [e for e in elements if id(e) not in chosen]
        return
    _require(value, operation)
    if not matched:
        seed = equality_seed(value_filter)
        if seed is None:
            raise bad_request(NO_TARGET, f"no value of {name} matches the filter")
        added: Dict[str, Any] = dict(seed)
        if sub is not None:
            added[sub] = value
        elif isinstance(value, Mapping):
            _merge(added, value)
        else:
            raise bad_request(INVALID_VALUE, f"a value of {name} is an object")
        container[key] = elements + [added]
        return
    for element in matched:
        if sub is not None:
            element[lookup(element, sub) or sub] = value
        elif not isinstance(value, Mapping):
            raise bad_request(INVALID_VALUE, f"a value of {name} is an object")
        elif operation == "replace":
            element.clear()
            element.update(value)
        else:
            _merge(element, value)


def _apply_path(
    resource: Dict[str, Any],
    operation: str,
    patch_path: PatchPath,
    value: Any,
    read_only: FrozenSet[str],
) -> None:
    container = _container(resource, patch_path, read_only)
    if patch_path.value_filter is None:
        _apply_without_filter(container, operation, patch_path, value)
    else:
        _apply_with_filter(
            container, operation, patch_path, patch_path.value_filter, value
        )


def _apply_operation(
    resource: Dict[str, Any], raw: Any, read_only: FrozenSet[str]
) -> None:
    if not isinstance(raw, Mapping):
        raise bad_request(INVALID_SYNTAX, "each operation is an object")
    op_key = lookup(raw, "op")
    operation = str(raw[op_key]).lower() if op_key else ""
    if operation not in PATCH_OPERATIONS:
        raise bad_request(INVALID_SYNTAX, f"op is one of {sorted(PATCH_OPERATIONS)}")
    path_key, value_key = lookup(raw, "path"), lookup(raw, "value")
    path: Optional[str] = raw[path_key] if path_key else None
    value = raw[value_key] if value_key else None
    if path:
        if not isinstance(path, str):
            raise bad_request(INVALID_SYNTAX, "path is a string")
        _apply_path(resource, operation, parse_patch_path(path), value, read_only)
        return
    if operation == "remove":
        raise bad_request(NO_TARGET, "remove names a path")
    if not isinstance(value, Mapping):
        raise bad_request(INVALID_VALUE, f"{operation} without a path takes an object")
    for name, item in value.items():
        if name.lower() == "schemas":
            continue
        if name.lower().startswith("urn:") and isinstance(item, Mapping):
            for sub_name, sub_item in item.items():
                _apply_path(
                    resource,
                    operation,
                    parse_patch_path(f"{name}:{sub_name}"),
                    sub_item,
                    read_only,
                )
            continue
        _apply_path(resource, operation, parse_patch_path(name), item, read_only)


def apply_patch(
    resource: Mapping[str, Any], body: Mapping[str, Any], read_only: FrozenSet[str]
) -> Dict[str, Any]:
    """``resource`` with the PatchOp ``body`` applied, all or nothing
    (``resource`` itself is left unchanged). ``read_only`` names the
    attributes no operation may touch."""
    schemas = body.get("schemas")
    if not isinstance(schemas, list) or PATCH_OP_URN not in schemas:
        raise bad_request(INVALID_SYNTAX, f"schemas lists {PATCH_OP_URN}")
    operations = body.get(lookup(body, "Operations") or "Operations")
    if not isinstance(operations, list) or not operations:
        raise bad_request(INVALID_SYNTAX, "Operations lists at least one operation")
    if len(operations) > MAX_OPERATIONS:
        raise bad_request(INVALID_SYNTAX, f"at most {MAX_OPERATIONS} operations")
    patched: Dict[str, Any] = copy.deepcopy(dict(resource))
    for raw in operations:
        _apply_operation(patched, raw, read_only)
    return patched
