"""
StaticSeeder.py - Helper functions for database seeding.

This module provides helper functions used by ModelRegistry during the seeding process.
The main seeding logic has been moved to ModelRegistry._seed() for better integration
with the dependency ordering system.
"""

import inspect
from typing import Any, Dict, List
from sqlalchemy import select
from database.DatabaseManager import DatabaseManager
from lib.Environment import env
from lib.Logging import logger


# Helper lookup functions


def get_provider_by_name(session, provider_name, db_manager: DatabaseManager):
    """
    Helper function to look up a provider by name.
    """
    try:
        from logic.BLL_Providers import ProviderModel

        Provider = ProviderModel.DB(db_manager.Base)
        stmt = select(Provider).where(Provider.name == provider_name)
        provider = session.execute(stmt).scalar_one_or_none()

        if provider:
            logger.log("SQL", f"Found provider {provider_name} with ID {provider.id}")
            return provider
        else:
            logger.warning(f"Provider {provider_name} not found")
            return None
    except Exception as e:
        logger.error(f"Error looking up provider {provider_name}: {str(e)}")
        return None


def get_extension_by_name(session, extension_name, db_manager: DatabaseManager):
    """Helper function to look up an extension by name."""
    try:
        from logic.BLL_Extensions import ExtensionModel

        Extension = ExtensionModel.DB(db_manager.Base)
        stmt = select(Extension).where(Extension.name == extension_name)
        extension = session.execute(stmt).scalar_one_or_none()

        if extension:
            logger.log(
                "SQL", f"Found extension {extension_name} with ID {extension.id}"
            )
            return extension
        else:
            logger.warning(f"Extension {extension_name} not found")
            return None
    except Exception as e:
        logger.error(f"Error looking up extension {extension_name}: {str(e)}")
        return None


def get_rotation_by_name(session, rotation_name, db_manager: DatabaseManager):
    """Helper function to look up a rotation by name."""
    try:
        from logic.BLL_Providers import RotationModel

        Rotation = RotationModel.DB(db_manager.Base)
        stmt = select(Rotation).where(Rotation.name == rotation_name)
        rotation = session.execute(stmt).scalar_one_or_none()

        if rotation:
            logger.log("SQL", f"Found rotation {rotation_name} with ID {rotation.id}")
            return rotation
        else:
            logger.warning(f"Rotation {rotation_name} not found")
            return None
    except Exception as e:
        logger.error(f"Error looking up rotation {rotation_name}: {str(e)}")
        return None


def get_provider_instance_by_name(session, instance_name, db_manager: DatabaseManager):
    """Helper function to look up a provider instance by name."""
    try:
        from logic.BLL_Providers import ProviderInstanceModel

        ProviderInstance = ProviderInstanceModel.DB(db_manager.Base)
        stmt = select(ProviderInstance).where(ProviderInstance.name == instance_name)
        instance = session.execute(stmt).scalar_one_or_none()

        if instance:
            logger.log(
                "SQL",
                f"Found provider instance {instance_name} with ID {instance.id}",
            )
            return instance
        else:
            logger.warning(f"Provider instance {instance_name} not found")
            return None
    except Exception as e:
        logger.error(f"Error looking up provider instance {instance_name}: {str(e)}")
        return None


def _resolve_placeholder_fields(item, session, class_name, db_manager):
    """Resolve placeholder fields like _provider_name, _extension_name, etc. to actual IDs."""
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
                logger.warning(
                    f"Extension '{ext_name}' not found for {class_name} (legacy format)"
                )
                return None

        # Resolve _provider_name to provider_id
        if "_provider_name" in resolved_item:
            provider_name = resolved_item.pop("_provider_name")
            provider = get_provider_by_name(session, provider_name, db_manager)
            if provider:
                resolved_item["provider_id"] = str(provider.id)
            else:
                logger.warning(f"Provider '{provider_name}' not found for {class_name}")
                return None

        # Resolve _extension_name to extension_id
        if "_extension_name" in resolved_item:
            extension_name = resolved_item.pop("_extension_name")
            extension = get_extension_by_name(session, extension_name, db_manager)
            if extension:
                resolved_item["extension_id"] = str(extension.id)
            else:
                logger.warning(
                    f"Extension '{extension_name}' not found for {class_name}"
                )
                return None

        # Resolve _rotation_name to rotation_id
        if "_rotation_name" in resolved_item:
            rotation_name = resolved_item.pop("_rotation_name")
            rotation = get_rotation_by_name(session, rotation_name, db_manager)
            if rotation:
                resolved_item["rotation_id"] = str(rotation.id)
            else:
                logger.warning(f"Rotation '{rotation_name}' not found for {class_name}")
                return None

        # Resolve _provider_instance_name to provider_instance_id
        if "_provider_instance_name" in resolved_item:
            instance_name = resolved_item.pop("_provider_instance_name")
            instance = get_provider_instance_by_name(session, instance_name, db_manager)
            if instance:
                resolved_item["provider_instance_id"] = str(instance.id)
            else:
                logger.warning(
                    f"Provider instance '{instance_name}' not found for {class_name}"
                )
                return None

        return resolved_item

    except Exception as e:
        logger.error(f"Error resolving placeholders for {class_name}: {e}")
        return None


def seed_model(model_class, session, db_manager, model_registry=None):
    """Helper function to seed a specific model class."""
    class_name = model_class.__name__
    logger.log("SQL", f"Processing seeding for {class_name}...")

    # Trigger before_seed_model hook to allow extensions to prepare or modify the model
    try:
        from extensions.AbstractExtensionProvider import AbstractStaticExtension

        AbstractStaticExtension.trigger_hook(
            "DB",
            "Seed",
            model_class.__name__,
            "before_seed_model",
            "before",
            model_class,
            session,
        )
    except Exception as e:
        logger.log("SQL", f"No extensions loaded or hook trigger failed: {e}")

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
                return
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
                    return

    # If still no seed list found, start with empty list for extensions to inject into
    if not seed_list:
        seed_list = []
        logger.log(
            "SQL",
            f"Model {class_name} has no seed data, starting with empty list for extensions",
        )

    # Trigger before_seed_list hook to allow extensions to inject seed data
    try:
        from extensions.AbstractExtensionProvider import AbstractStaticExtension

        hook_results = AbstractStaticExtension.trigger_hook(
            "DB",
            "Seed",
            model_class.__name__,
            "inject_seed_data",
            "before",
            seed_list,
            model_class,
            session,
        )

        # Collect any seed data returned by hooks
        for result in hook_results:
            if isinstance(result, list):
                seed_list.extend(result)
                logger.log(
                    "SQL",
                    f"Extension injected {len(result)} seed items for {class_name}",
                )
            elif isinstance(result, dict):
                seed_list.append(result)
                logger.log("SQL", f"Extension injected 1 seed item for {class_name}")
    except Exception as e:
        logger.log("SQL", f"No extensions loaded or seed injection hook failed: {e}")

    if not seed_list:
        logger.log("SQL", f"No seed items for {class_name}")
        return

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

    # Process each seed item
    items_created = 0
    for item in seed_list:
        # Resolve placeholder fields
        item = _resolve_placeholder_fields(item, session, class_name, db_manager)

        if item is None:
            # Item couldn't be resolved, skip it
            continue

        # Check if the item already exists using the 'exists' method
        exists = False
        try:
            if hasattr(model_class, "exists"):
                # Determine the field to check for existence, prioritizing 'id'
                if "id" in item:
                    check_field = "id"
                else:
                    check_field = next(
                        (k for k in ["name", "email"] if k in item),
                        None,  # Check name/email if no id
                    )

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

    # Trigger after_seed_model hook to allow extensions to perform post-seeding actions
    try:
        from extensions.AbstractExtensionProvider import AbstractStaticExtension

        AbstractStaticExtension.trigger_hook(
            "DB",
            "Seed",
            model_class.__name__,
            "after_seed_model",
            "after",
            model_class,
            session,
            items_created,
        )
    except Exception as e:
        logger.log("SQL", f"No extensions loaded or hook trigger failed: {e}")

    logger.log("SQL", f"Created {items_created} items for {class_name}")
