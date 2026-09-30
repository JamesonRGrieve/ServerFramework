# SPDX-License-Identifier: AGPL-3.0-or-later
"""
StaticSeeder.py - Helper functions for database seeding.

This module provides helper functions used by ModelRegistry during the seeding process.
A seed item may name its parent (``_provider_name``, ``_extension_name``, ...)
instead of carrying its id; an item whose parent is not seeded yet is deferred
and retried by ``ModelRegistry._seed`` until nothing more resolves.
"""

import inspect
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from zephyrex.database.DatabaseManager import DatabaseManager
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger


class ParentNotSeeded(LookupError):
    """A seed item names a parent row that does not exist (yet)."""

    def __init__(self, entity_label: str, name: str) -> None:
        super().__init__(f"{entity_label} {name!r} is not seeded")


@dataclass
class SeedBatch:
    """Seed items of one model still waiting for their parents."""

    model_class: Any
    pydantic_model: Any
    items: List[Dict[str, Any]] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)


# Helper lookup functions


def _get_entity_by_name(
    session,
    name: str,
    pydantic_model,
    entity_label: str,
    db_manager: DatabaseManager,
):
    """Look up any entity by its ``name`` column.

    Args:
        session: SQLAlchemy session.
        name: The value to match against ``Model.name``.
        pydantic_model: Pydantic model class exposing ``.DB(base)``.
        entity_label: Human-readable label for log messages (e.g. "provider").
        db_manager: ``DatabaseManager`` providing the declarative base.

    Returns:
        The entity row, or ``None`` if not found / on error.
    """
    try:
        db_cls = pydantic_model.DB(db_manager.Base)
        stmt = select(db_cls).where(db_cls.name == name)
        entity = session.execute(stmt).scalar_one_or_none()

        if entity:
            logger.log("SQL", f"Found {entity_label} {name} with ID {entity.id}")
            return entity
        logger.log("SQL", f"{entity_label.capitalize()} {name} not found")
        return None
    except Exception as e:
        logger.error(f"Error looking up {entity_label} {name}: {str(e)}")
        return None


def get_provider_by_name(session, provider_name, db_manager: DatabaseManager):
    """Helper function to look up a provider by name."""
    from zephyrex.logic.BLL_Providers import ProviderModel

    return _get_entity_by_name(
        session, provider_name, ProviderModel, "provider", db_manager
    )


def get_extension_by_name(session, extension_name, db_manager: DatabaseManager):
    """Helper function to look up an extension by name."""
    from zephyrex.logic.BLL_Extensions import ExtensionModel

    return _get_entity_by_name(
        session, extension_name, ExtensionModel, "extension", db_manager
    )


def get_rotation_by_name(session, rotation_name, db_manager: DatabaseManager):
    """Helper function to look up a rotation by name."""
    from zephyrex.logic.BLL_Providers import RotationModel

    return _get_entity_by_name(
        session, rotation_name, RotationModel, "rotation", db_manager
    )


def get_provider_instance_by_name(session, instance_name, db_manager: DatabaseManager):
    """Helper function to look up a provider instance by name."""
    from zephyrex.logic.BLL_Providers import ProviderInstanceModel

    return _get_entity_by_name(
        session, instance_name, ProviderInstanceModel, "provider instance", db_manager
    )


def _resolve_placeholder_fields(item, session, class_name, db_manager):
    """Resolve placeholder fields like _provider_name, _extension_name, etc. to
    actual IDs. Raises ParentNotSeeded when a named parent does not exist, so
    the caller can retry the item once more rows are seeded; returns None when
    the item cannot be resolved at all."""
    if not isinstance(item, dict):
        return item

    # Make a copy to avoid modifying the original
    resolved_item = item.copy()

    try:
        # Handle legacy "EXT:name" format for backward compatibility
        if (
            "extension_id" in resolved_item
            and isinstance(resolved_item["extension_id"], str)
            and resolved_item["extension_id"].startswith("EXT:")
        ):
            ext_name = resolved_item["extension_id"][4:]  # Remove "EXT:" prefix
            extension = get_extension_by_name(session, ext_name, db_manager)
            if extension:
                resolved_item["extension_id"] = str(extension.id)
                logger.debug(
                    f"Resolved legacy EXT:{ext_name} to extension_id {extension.id}"
                )
            else:
                raise ParentNotSeeded("extension", ext_name)

        # Resolve _provider_name to provider_id
        if "_provider_name" in resolved_item:
            provider_name = resolved_item.pop("_provider_name")
            provider = get_provider_by_name(session, provider_name, db_manager)
            if provider:
                resolved_item["provider_id"] = str(provider.id)
            else:
                raise ParentNotSeeded("provider", provider_name)

        # Resolve _extension_name to extension_id
        if "_extension_name" in resolved_item:
            extension_name = resolved_item.pop("_extension_name")
            extension = get_extension_by_name(session, extension_name, db_manager)
            if extension:
                resolved_item["extension_id"] = str(extension.id)
            else:
                raise ParentNotSeeded("extension", extension_name)

        # Resolve _rotation_name to rotation_id
        if "_rotation_name" in resolved_item:
            rotation_name = resolved_item.pop("_rotation_name")
            rotation = get_rotation_by_name(session, rotation_name, db_manager)
            if rotation:
                resolved_item["rotation_id"] = str(rotation.id)
            else:
                raise ParentNotSeeded("rotation", rotation_name)

        # Resolve _provider_instance_name to provider_instance_id
        if "_provider_instance_name" in resolved_item:
            instance_name = resolved_item.pop("_provider_instance_name")
            instance = get_provider_instance_by_name(session, instance_name, db_manager)
            if instance:
                resolved_item["provider_instance_id"] = str(instance.id)
            else:
                raise ParentNotSeeded("provider instance", instance_name)

        return resolved_item

    except ParentNotSeeded:
        raise
    except Exception as e:
        logger.error(f"Error resolving placeholders for {class_name}: {e}")
        return None


def seed_model(
    model_class, session, db_manager, model_registry=None
) -> Optional[SeedBatch]:
    """Seed one model class; returns the items deferred until their
    parents exist (see seed_deferred), or None when it has no seed data."""
    class_name = model_class.__name__
    logger.log("SQL", f"Processing seeding for {class_name}...")

    # Find the corresponding Pydantic model to get seed_data
    seed_list = []
    pydantic_model = None

    # For new Pydantic2SQLAlchemy models, find the Pydantic model that has this SQLAlchemy model as its .DB property
    try:
        # Get pydantic models from the model registry if available
        if model_registry and hasattr(model_registry, "bound_models"):
            pydantic_models = list(model_registry.bound_models)
        else:
            pydantic_models = []

        # Find the Pydantic model that corresponds to this SQLAlchemy model
        for pmodel in pydantic_models:
            if hasattr(pmodel, "DB") and pmodel.DB(db_manager.Base) == model_class:
                pydantic_model = pmodel
                break

        if pydantic_model and hasattr(pydantic_model, "seed_data"):
            # Check if seed_data is a method or property
            seed_data_attr = getattr(pydantic_model, "seed_data")
            if callable(seed_data_attr):
                # It's a method, call it to get the data with model_registry if available
                try:
                    # Try calling with model_registry parameter (for Provider/Extension models)
                    seed_list = seed_data_attr(model_registry=model_registry)
                except TypeError:
                    # Fallback to calling without parameters for older models
                    seed_list = seed_data_attr()
            else:
                # It's a static list
                seed_list = seed_data_attr
            logger.log(
                "SQL",
                f"Found seed_data with {len(seed_list)} items for {class_name} from Pydantic model {pydantic_model.__name__}",
            )
        else:
            logger.log("SQL", f"No seed_data found for {class_name}")

    except Exception as e:
        logger.log("SQL", f"Error finding Pydantic model for {class_name}: {e}")

    # Fallback: Check the old way for legacy models
    if not seed_list:
        # First check if the class has a get_seed_list method (dynamic)
        if hasattr(model_class, "get_seed_list") and callable(
            model_class.get_seed_list
        ):
            try:
                seed_list = model_class.get_seed_list()
                logger.log(
                    "SQL",
                    f"Retrieved dynamic seed list with {len(seed_list)} items for {class_name}",
                )
            except Exception as e:
                logger.error(
                    f"Error calling get_seed_list method for {class_name}: {str(e)}"
                )
                return None
        # Otherwise check for the static seed_list attribute
        elif hasattr(model_class, "seed_list"):
            # Handle seed_list that is a callable
            seed_list = model_class.seed_list
            if callable(seed_list) and not inspect.isclass(seed_list):
                try:
                    seed_list = seed_list()
                    logger.log(
                        "SQL",
                        f"Called seed_list function for {class_name}, got {len(seed_list)} items",
                    )
                except Exception as e:
                    logger.error(
                        f"Error calling seed_list function for {class_name}: {str(e)}"
                    )
                    return None

    if not seed_list:
        logger.log("SQL", f"No seed items for {class_name}")
        return None

    # Item 38 — accept typed Pydantic instances in addition to raw dicts.
    # A seed list declared as `List[ModelClass]` is normalized to dicts
    # here so the rest of the pipeline (placeholder resolution, exists
    # check, create call) keeps its existing shape. This means a typo in
    # a field name now fails at *import* time (Pydantic instantiation
    # raises) rather than when the seeder runs against a live DB —
    # closing the type-safety gap Item 38 surfaced.
    from pydantic import BaseModel as _PydanticBaseModel

    normalized: List[Dict[str, Any]] = []
    for item in seed_list:
        if isinstance(item, _PydanticBaseModel):
            normalized.append(item.model_dump(exclude_unset=True))
        elif isinstance(item, dict):
            normalized.append(item)
        else:
            logger.warning(
                f"Seed item for {class_name} is neither dict nor Pydantic model "
                f"(got {type(item).__name__}); skipping."
            )
    seed_list = normalized

    logger.log("SQL", f"Seeding {class_name} table with {len(seed_list)} items...")

    items_created, deferred = _seed_items(
        model_class, pydantic_model, seed_list, session, db_manager, model_registry
    )

    logger.log("SQL", f"Created {items_created} items for {class_name}")
    return deferred


def _seed_items(
    model_class, pydantic_model, seed_list, session, db_manager, model_registry
) -> "tuple[int, SeedBatch]":
    """Create each item that does not exist yet; returns how many were
    created and the items deferred because a named parent is missing."""
    class_name = model_class.__name__
    items_created = 0
    deferred = SeedBatch(model_class=model_class, pydantic_model=pydantic_model)
    for item in seed_list:
        # Resolve placeholder fields; an item whose parent is not seeded yet
        # waits for a later pass.
        try:
            resolved = _resolve_placeholder_fields(
                item, session, class_name, db_manager
            )
        except ParentNotSeeded as missing:
            deferred.items.append(item)
            deferred.reasons.append(str(missing))
            continue
        if resolved is None:
            continue
        item = resolved

        # Check if the item already exists using the 'exists' method
        exists = False
        # The field that identifies an existing row: id, else name, else email.
        check_field: Optional[str] = next(
            (k for k in ("id", "name", "email") if k in item), None
        )
        try:
            if hasattr(model_class, "exists"):

                if check_field:
                    exists = model_class.exists(
                        env("ROOT_ID"),
                        model_registry,
                        **{check_field: item[check_field]},
                    )
            else:
                logger.warning(
                    f"Model {class_name} does not have an 'exists' method. Skipping existence check."
                )

        except Exception as e:
            # Handle schema mismatches gracefully (e.g., missing columns from disabled extensions)
            if "no such column" in str(e).lower():
                logger.log(
                    "SQL",
                    f"Schema mismatch for {class_name} - assuming item doesn't exist and proceeding with creation: {e}",
                )
                exists = False
            else:
                logger.error(
                    f"Error checking existence for {class_name} with {check_field}={item.get(check_field)}: {str(e)}"
                )
                continue

        if not exists:
            try:
                # Create the item
                if hasattr(model_class, "create"):
                    # Use the model's seed_id if available, otherwise fall back to SYSTEM_ID
                    creator_id = env("SYSTEM_ID")

                    # Check if the pydantic model has seed_creator_id
                    if pydantic_model and hasattr(pydantic_model, "seed_creator_id"):
                        creator_id = pydantic_model.seed_creator_id
                        logger.log(
                            "SQL",
                            f"Using seed_creator_id ({creator_id}) as creator for {class_name}",
                        )

                    model_class.create(
                        creator_id, model_registry, return_type="db", **item
                    )
                    logger.log(
                        "SQL",
                        f"Created {class_name} item: {item.get('name', str(item))}",
                    )
                    items_created += 1
                else:
                    # Fallback to direct SQLAlchemy creation
                    new_instance = model_class(**item)
                    session.add(new_instance)
                    session.flush()
                    logger.log(
                        "SQL",
                        f"Created {class_name} item: {item.get('name', str(item))}",
                    )
                    items_created += 1
            except Exception as e:
                logger.error(f"Error creating {class_name} item: {str(e)}")
                continue

    return items_created, deferred


def seed_deferred(batch: SeedBatch, session, db_manager, model_registry) -> SeedBatch:
    """Retry a batch's deferred items; returns those still waiting."""
    _, remaining = _seed_items(
        batch.model_class,
        batch.pydantic_model,
        batch.items,
        session,
        db_manager,
        model_registry,
    )
    return remaining
