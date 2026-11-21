# SPDX-License-Identifier: AGPL-3.0-or-later
"""A record named by type and id (``target_entity_type`` + ``target_entity_id``), as polymorphic
rows (attachments, per-record metadata) point at any record, and the access that follows it.

The type is the snake-case name of the record's model without ``Model`` (``location`` for
``LocationModel``), among the managers the app has loaded; each check is the record manager's own
(``get`` for VIEW, the permission layer's EDIT check), never another extension's internals.
"""

import re
from typing import Any, Type, cast

from fastapi import HTTPException

TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def record_type_of(model_class: type) -> str:
    """The record type that names ``model_class``'s records (``location`` for
    ``LocationModel``), as ``record_manager`` resolves it."""
    return _snake(model_class.__name__.removesuffix("Model"))


def record_manager(model_registry: Any, target_entity_type: str) -> Type[Any]:
    """The manager of the records ``target_entity_type`` names. 422 when no loaded one does."""
    if not TYPE_PATTERN.match(target_entity_type or ""):
        raise HTTPException(
            status_code=422, detail="target_entity_type is not a record type"
        )
    named = (
        lambda model: _snake(model.__name__.removesuffix("Model")) == target_entity_type
    )  # noqa: E731
    # A bound model that names its manager (core's TeamModel.Manager, UserModel.Manager, ...).
    for model in model_registry.get_bound_models():
        manager = getattr(model, "Manager", None)
        if named(model) and manager is not None:
            return cast(Type[Any], manager)
    # Otherwise the router managers of the loaded extensions' BLL modules.
    for manager in model_registry.router_managers():
        model = getattr(manager, "Model", None)
        if model is not None and named(model):
            return cast(Type[Any], manager)
    raise HTTPException(
        status_code=422, detail=f"No record type {target_entity_type!r} is loaded"
    )


def can_view(
    model_registry: Any,
    requester_id: str,
    target_entity_type: str,
    target_entity_id: str,
) -> bool:
    """Whether the requester may read the record (its manager's ``get`` answers)."""
    manager = record_manager(model_registry, target_entity_type)
    try:
        manager(requester_id=requester_id, model_registry=model_registry).get(
            id=target_entity_id
        )
    except HTTPException as error:
        if error.status_code in (403, 404):
            return False
        raise
    return True


def can_edit(
    model_registry: Any,
    requester_id: str,
    target_entity_type: str,
    target_entity_id: str,
) -> bool:
    """Whether the requester may change the record: they can read it, and the permission layer
    grants EDIT on it."""
    from zephyrex.database.StaticPermissions import user_can_edit

    if not can_view(model_registry, requester_id, target_entity_type, target_entity_id):
        return False
    manager = record_manager(model_registry, target_entity_type)
    db_cls = manager(requester_id=requester_id, model_registry=model_registry).DB
    session = model_registry.DB.session()
    try:
        return bool(
            user_can_edit(
                requester_id,
                db_cls,
                target_entity_id,
                session,
                declarative_base=model_registry.DB.manager.Base,
            )
        )
    finally:
        session.close()
