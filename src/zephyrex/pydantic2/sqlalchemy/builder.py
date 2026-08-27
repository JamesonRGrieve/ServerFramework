import inspect
import sys
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

import stringcase
from pydantic import BaseModel
from sqlalchemy import JSON, Column, ForeignKey, ForeignKeyConstraint, String
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import relationship

from zephyrex.database.AbstractDatabaseEntity import BaseMixin, ImageMixin, UpdateMixin
from zephyrex.lib.AbstractPydantic2 import default_name_processor
from zephyrex.lib.Logging import logger
from zephyrex.pydantic2.util import (
    PRIMARY_KEY_FIELD,
    is_reference_field_name,
    reference_relationship_name,
    reference_target_model_name,
)
from zephyrex.pydantic2.registry import ModelRegistry
from zephyrex.pydantic2.sqlalchemy._const import RESERVED_SQLALCHEMY_NAMES, TYPE_MAPPING
from zephyrex.pydantic2.sqlalchemy.mixins import DatabaseMixin, ParentRelationshipMixin


def _sanitize_field_name(field_name: str) -> str:
    """
    Sanitize field names to avoid conflicts with SQLAlchemy reserved names.

    Args:
        field_name: Original field name

    Returns:
        Sanitized field name
    """
    from zephyrex.lib.AbstractPydantic2 import NameProcessor

    return NameProcessor.sanitize_name(field_name, RESERVED_SQLALCHEMY_NAMES)  # type: ignore[no-any-return]


# Note: Removed singleton _CURRENT_BASE - use base_model parameter instead


def _get_db_manager_from_context() -> Optional[Any]:
    """
    DEPRECATED: Try to get DatabaseManager from context for legacy compatibility.

    This function provides fallback access to DatabaseManager for legacy code
    that hasn't been updated to use dependency injection. It should not be used
    in new code.

    Returns:
        DatabaseManager instance if found, None otherwise
    """
    import warnings

    warnings.warn(
        "_get_db_manager_from_context is deprecated - use dependency injection instead",
        DeprecationWarning,
        stacklevel=2,
    )

    try:
        # Try to get from current context (e.g., FastAPI request context)
        # This is a fallback approach - in practice, the DatabaseManager should be
        # passed explicitly or accessed through app.state.model_registry.database_manager
        # Try to access through various context mechanisms
        # 1. Try to get from current asyncio task context
        try:
            import asyncio

            task = asyncio.current_task()
            if task and hasattr(task, "_db_manager"):
                return task._db_manager
        except RuntimeError as e:
            # current_task() raises RuntimeError outside an event loop.
            logger.debug("DatabaseManager lookup: no current asyncio task: %s", e)

        # 2. Try to get from thread-local storage (not recommended but for compatibility)
        try:
            import threading

            local = threading.current_thread()
            if hasattr(local, "_db_manager"):
                return local._db_manager
        except (AttributeError, RuntimeError) as e:
            logger.debug("DatabaseManager lookup: thread-local probe failed: %s", e)

        # 3. Try to get from global app state (last resort)
        try:
            # This is very fragile but might work in some cases
            import sys

            for module_name, module in sys.modules.items():
                if hasattr(module, "app") and hasattr(module.app, "state"):
                    if hasattr(module.app.state, "DB"):
                        return module.app.state.model_registry.database_manager
        except (AttributeError, RuntimeError, ImportError) as e:
            logger.debug("DatabaseManager lookup: app-state probe failed: %s", e)

        return None

    except Exception as e:
        from zephyrex.lib.Logging import logger

        logger.warning(f"Could not get DatabaseManager from context: {e}")
        return None


def clear_registry_cache() -> None:
    """
    Clear all cached mapper configurations to allow reconfiguration.
    Use this when there are mapper initialization issues or for testing isolation.

    Note: With the new architecture, caching is done per declarative base via _pydantic_models.
    This function clears those caches without needing to iterate through all system modules.

    Also reverses every extension that has been applied to a Pydantic
    target model (e.g. payment's ``external_payment_id`` on
    ``UserModel``). The extension system mutates target ``model_fields``
    in place, so without this reset a test that loads payment leaves
    the column on ``UserModel`` for the next test running in the same
    worker process.
    """
    from zephyrex.pydantic2.sqlalchemy.extensions import (
        _EXTENSION_REGISTRY_COMPAT,
        _undo_model_extension,
    )

    # Clear DatabaseMixin cache (legacy, mostly for compatibility)
    if hasattr(DatabaseMixin, "_db_cache"):
        DatabaseMixin._db_cache.clear()

    # Walk the compat registry and undo every applied extension on its
    # target model. The compat registry tracks extension classes by
    # qualified name; we resolve the target on the extension class
    # itself (``_extension_target``) since the decorator stores it.
    import importlib

    targets_to_reset = set()
    for target_key in list(_EXTENSION_REGISTRY_COMPAT.keys()):
        try:
            module_name, class_name = target_key.rsplit(".", 1)
            module = importlib.import_module(module_name)
            target_model = getattr(module, class_name, None)
            if target_model is not None:
                targets_to_reset.add(target_model)
        except (ImportError, ValueError, AttributeError):
            continue
    for target_model in targets_to_reset:
        _undo_model_extension(target_model)

    # Clear cached models from all known declarative bases
    # This approach avoids iterating through all system modules and accessing deprecated typing modules
    cleared_bases = []

    try:
        # Look for database managers in likely locations
        try:
            from zephyrex.database.DatabaseManager import get_database_manager_singleton

            db_manager = get_database_manager_singleton()
            if (
                db_manager
                and hasattr(db_manager, "Base")
                and hasattr(db_manager.Base, "_pydantic_models")
            ):
                db_manager.Base._pydantic_models.clear()
                cleared_bases.append("DatabaseManager.Base")
        except (ImportError, AttributeError):
            pass

        # Check app state if available
        try:
            import starlette.concurrency

            context = starlette.concurrency.context.get()  # type: ignore[attr-defined]
            if context and hasattr(context, "state"):
                app_state = context.state
                if hasattr(app_state, "DB") and hasattr(
                    app_state.model_registry.database_manager, "Base"
                ):
                    base = app_state.model_registry.database_manager.Base
                    if hasattr(base, "_pydantic_models"):
                        base._pydantic_models.clear()
                        cleared_bases.append(
                            "zephyrex.app.state.model_registry.database_manager.Base"
                        )
        except (ImportError, AttributeError, LookupError):
            pass

    except Exception as e:
        logger.debug(f"Some caches could not be cleared: {e}")

    logger.debug(
        f"Cleared SQLAlchemy model caches from: {cleared_bases if cleared_bases else 'no active declarative bases found'}"
    )


# Note: set_base_model function removed - was deprecated singleton pattern
# Pass base_model parameter directly to create_sqlalchemy_model instead

# Note: register_model function removed - models now use isolated ModelRegistry from app state


def _is_database_model(obj: Any) -> bool:
    """Check if obj is a database model without triggering DatabaseDescriptor."""
    return isinstance(obj, type) and issubclass(obj, BaseModel)


def get_entity_module_class(
    entity_class_name: str,
) -> Tuple[Optional[str], Optional[Type[BaseModel]]]:
    """
    Get entity class from modules or calling frame.
    Returns a tuple of (module_name, class).

    Note: Global registries removed - this now searches modules directly.
    """

    # Try to find in calling frame first
    calling_frame = inspect.currentframe().f_back  # type: ignore[union-attr]
    if calling_frame:
        # Check the caller's globals first
        caller_globals = calling_frame.f_globals
        if entity_class_name in caller_globals:
            obj = caller_globals[entity_class_name]
            if _is_database_model(obj):
                return obj.__module__, obj

    # Check all loaded modules as a fallback
    for module_name, module in sys.modules.items():
        if module and hasattr(module, entity_class_name):
            obj = getattr(module, entity_class_name)
            if _is_database_model(obj):
                return module_name, obj

        # Also check for variations like UserModel when looking for User
        model_variant = f"{entity_class_name}Model"
        if module and hasattr(module, model_variant):
            obj = getattr(module, model_variant)
            if _is_database_model(obj):
                return module_name, obj

    return None, None


def get_relationship_target(entity_class_name: str) -> str:
    """
    Get the appropriate target string for a relationship.

    Args:
        entity_class_name: The name of the target entity class

    Returns:
        A string suitable for use as the target in a relationship

    Note: Global registries removed - this now always returns the simple class name.
    SQLAlchemy will resolve it at runtime.
    """
    # Without global registries, we just return the class name
    # SQLAlchemy will resolve it at runtime
    return entity_class_name


def _extract_mixin_classes(pydantic_model: Type[BaseModel]) -> List[Type[Any]]:
    """
    Extract SQLAlchemy mixin classes from Pydantic model inheritance.

    Args:
        pydantic_model: The Pydantic model class

    Returns:
        List of SQLAlchemy mixin classes
    """
    base_classes = []

    # Always include BaseMixin as it provides essential CRUD methods
    base_classes.append(BaseMixin)

    if hasattr(pydantic_model, "__bases__"):
        for base in pydantic_model.__bases__:
            base_name = base.__name__

            # Handle both direct and .Optional variants
            if base_name == "ApplicationModel" or (
                hasattr(base, "__qualname__")
                and base.__qualname__.startswith("ApplicationModel")
            ):
                # BaseMixin is already included above
                pass
            elif base_name == "UpdateMixinModel" or (
                hasattr(base, "__qualname__")
                and base.__qualname__.startswith("UpdateMixinModel")
            ):
                base_classes.append(UpdateMixin)
            elif base_name == "ImageMixinModel" or (
                hasattr(base, "__qualname__")
                and base.__qualname__.startswith("ImageMixinModel")
            ):
                base_classes.append(ImageMixin)
            elif base_name == "ParentMixinModel" or (
                hasattr(base, "__qualname__")
                and base.__qualname__.startswith("ParentMixinModel")
            ):
                # Use our fixed ParentRelationshipMixin instead of ParentMixin
                base_classes.append(ParentRelationshipMixin)
            elif (
                base_name == "Optional"
                and hasattr(base, "__module__")
                and "ImageMixinModel" in base.__module__
            ):
                # Handle ImageMixinModel.Optional case
                base_classes.append(ImageMixin)

    return base_classes


def _get_existing_columns(
    base_classes: List[Type[Any]], base_model: Type[Any]
) -> Set[str]:
    """
    Get columns that are already defined by base classes.

    Args:
        base_classes: List of SQLAlchemy base classes
        base_model: The SQLAlchemy declarative base being used

    Returns:
        Set of column names that already exist
    """
    existing_columns: Set[str] = set()

    for base_class in base_classes:
        if hasattr(base_class, "__table__") and not isinstance(
            base_class, type(base_model)
        ):
            # Get columns from an already mapped class
            existing_columns.update(col.name for col in base_class.__table__.columns)
        elif hasattr(base_class, "__dict__"):
            # Get columns from a mixin class
            for key, value in base_class.__dict__.items():
                if isinstance(value, Column):
                    existing_columns.add(key)
                elif isinstance(value, declared_attr) and key != "__tablename__":
                    # For declared_attr, assume it returns a Column unless it's __tablename__
                    # We don't need to evaluate it - just trust that it's a column
                    if key not in [
                        "__tablename__",
                        "__table_args__",
                        "__mapper_args__",
                    ]:
                        existing_columns.add(key)
                # Check for declared_attr methods that return columns
                elif callable(value) and hasattr(value, "__get__"):
                    # This is likely a descriptor that returns a column
                    existing_columns.add(key)

    return existing_columns


def _create_column_from_field(
    name: str, field_type: Type[Any], field_info: Optional[Any] | None = None
) -> Optional[Column]:
    """
    Create a SQLAlchemy Column from a Pydantic field.

    Args:
        name: Field name
        field_type: Field type (from type annotations)
        field_info: Optional Pydantic field info object

    Returns:
        SQLAlchemy Column or None if it should be skipped
    """
    # Handle Optional types to get the actual type
    actual_field_type: Type[Any] = field_type
    is_optional: bool = False
    if get_origin(field_type) is Union:
        args = get_args(field_type)
        if type(None) in args:
            is_optional = True
            # Extract the actual type from Optional
            non_none_args = [arg for arg in args if arg is not type(None)]
            if non_none_args:
                actual_field_type = non_none_args[0]

    # Skip relationship fields (fields whose type is another Pydantic model)
    if inspect.isclass(actual_field_type) and issubclass(actual_field_type, BaseModel):
        return None

    # Handle List types - check if they contain Pydantic models (navigation properties)
    if get_origin(actual_field_type) in (list, List):
        list_args = get_args(actual_field_type)
        if list_args:
            list_item_type = list_args[0]
            # If the list contains Pydantic models, this is a navigation property, skip it
            if inspect.isclass(list_item_type) and issubclass(
                list_item_type, BaseModel
            ):
                return None
            # If the list contains string references to models (forward references), also skip
            if isinstance(list_item_type, str):
                return None
        # Check for common navigation property names that should be skipped
        if name in ["children", "parent", "items", "records"]:
            return None
        # Otherwise, treat as JSON column
        sa_type = JSON
    elif get_origin(actual_field_type) in (dict, Dict):
        # For Dict types, use JSON type
        sa_type = JSON
    else:
        # Get the SQLAlchemy type for regular types
        sa_type = TYPE_MAPPING.get(actual_field_type, String)

    # Extract field parameters
    params: Dict[str, Any] = {}

    # Default nullable based on Optional status
    params["nullable"] = is_optional

    # Add primary key for id columns
    if name == PRIMARY_KEY_FIELD:
        params["primary_key"] = True
        params["nullable"] = False
        # Ensure proper type for primary key - always use String for IDs
        sa_type = String  # type: ignore[assignment]

    # Force String type for all ID fields (UUID pattern)
    if is_reference_field_name(name) or name == PRIMARY_KEY_FIELD:
        sa_type = String  # type: ignore[assignment]

    if field_info:
        # Extract description/comment from various possible locations
        comment: Optional[str] | None = None

        # Try different ways Pydantic might store the description
        if hasattr(field_info, "description") and field_info.description:
            comment = field_info.description
        elif hasattr(field_info, "json_schema_extra") and field_info.json_schema_extra:
            # Pydantic v2 might store it here
            if (
                isinstance(field_info.json_schema_extra, dict)
                and "description" in field_info.json_schema_extra
            ):
                comment = field_info.json_schema_extra["description"]
        elif hasattr(field_info, "schema") and callable(field_info.schema):
            # Try to get from schema (Pydantic v1)
            try:
                schema = field_info.schema()
                if isinstance(schema, dict) and "description" in schema:
                    comment = schema["description"]
            except (AttributeError, TypeError, ValueError) as e:
                logger.debug(
                    "field schema introspection failed for %r: %s",
                    getattr(field_info, "alias", field_info),
                    e,
                )

        if comment:
            params["comment"] = comment

        # Get default value (compatible with both Pydantic v1 and v2)
        default_value: Optional[Any] | None = None

        # Look for default in various places
        if hasattr(field_info, "default") and field_info.default is not ...:
            default_value = field_info.default
        elif (
            hasattr(field_info, "default_factory")
            and field_info.default_factory is not None
            and field_info.default_factory is not ...
        ):
            try:
                default_value = field_info.default_factory()
            except Exception as e:
                logger.debug(
                    "default_factory invocation failed for %r: %s",
                    getattr(field_info, "alias", field_info),
                    e,
                )

        # Filter out PydanticUndefined values before setting as SQLAlchemy default
        if default_value is not None and default_value is not ...:
            # Import PydanticUndefined if available
            try:
                from pydantic_core import PydanticUndefined
            except ImportError:
                try:
                    from pydantic.fields import PydanticUndefined
                except ImportError:
                    PydanticUndefined = None  # type: ignore[assignment]

            # Only set default if it's not PydanticUndefined
            if PydanticUndefined is None or default_value is not PydanticUndefined:
                params["default"] = default_value

        # Extract constraints
        if hasattr(field_info, "unique") and field_info.unique:
            params["unique"] = True

        # Look for uniqueness in schema extras
        if hasattr(field_info, "json_schema_extra") and isinstance(
            field_info.json_schema_extra, dict
        ):
            if field_info.json_schema_extra.get("unique") is True:
                params["unique"] = True

    # Create the column
    return Column(sa_type, **params)


def _resolve_sqlalchemy_model(
    model_registry: Optional["ModelRegistry"], candidate_names: List[str]
) -> Optional[Type[Any]]:
    if model_registry is None:
        return None

    normalized_candidates = {name for name in candidate_names}
    normalized_lower = {name.lower() for name in candidate_names}

    for sqlalchemy_model in model_registry.db_models.values():
        model_name = sqlalchemy_model.__name__
        if (
            model_name in normalized_candidates
            or model_name.lower() in normalized_lower
        ):
            return sqlalchemy_model  # type: ignore[no-any-return]

    return None


def _queue_pending_relationship(
    model_registry: Optional["ModelRegistry"],
    source_model_name: str,
    relationship_name: str,
    target_candidate_names: List[str],
    relationship_kwargs: Dict[str, Any],
) -> None:
    if model_registry is None:
        return

    pending = getattr(model_registry, "_pending_sqlalchemy_relationships", None)
    if pending is None:
        pending = []
        setattr(model_registry, "_pending_sqlalchemy_relationships", pending)

    pending.append(
        {
            "source_name": source_model_name,
            "attr_name": relationship_name,
            "target_candidates": target_candidate_names,
            "relationship_kwargs": relationship_kwargs,
        }
    )


def _apply_pending_relationships(model_registry: Optional["ModelRegistry"]) -> None:
    if model_registry is None:
        return

    pending = getattr(model_registry, "_pending_sqlalchemy_relationships", None)
    if not pending:
        return

    resolved_indexes: List[int] = []

    for idx, entry in enumerate(pending):
        source_model = _resolve_sqlalchemy_model(model_registry, [entry["source_name"]])
        target_model = _resolve_sqlalchemy_model(
            model_registry, entry["target_candidates"]
        )

        if source_model is None or target_model is None:
            continue

        existing_attr = getattr(source_model, entry["attr_name"], None)
        if existing_attr is not None and hasattr(existing_attr, "property"):
            rel_prop = existing_attr.property
            rel_prop.argument = target_model
            if hasattr(rel_prop, "_setup_entity"):
                try:
                    rel_prop._setup_entity()
                except Exception:
                    pass
        else:
            setattr(
                source_model,
                entry["attr_name"],
                relationship(target_model, **entry["relationship_kwargs"]),
            )

        resolved_indexes.append(idx)

    for idx in reversed(resolved_indexes):
        pending.pop(idx)


def _find_pydantic_model_by_name(
    model_registry: Optional["ModelRegistry"], candidate_names: List[str]
) -> Optional[Type[BaseModel]]:
    if model_registry is None:
        return None

    normalized = set(candidate_names)
    normalized_lower = {name.lower() for name in candidate_names}
    for model in getattr(model_registry, "bound_models", []):
        model_name = model.__name__
        if model_name in normalized or model_name.lower() in normalized_lower:
            return model  # type: ignore[no-any-return]

    return None


def _ensure_pending_relationship_targets(
    model_registry: Optional["ModelRegistry"],
    base_model: Optional[Type[Any]],
) -> None:
    if model_registry is None or base_model is None:
        return

    pending = getattr(model_registry, "_pending_sqlalchemy_relationships", None)
    if not pending:
        return

    for entry in list(pending):
        target_model = _resolve_sqlalchemy_model(
            model_registry, entry["target_candidates"]
        )
        if target_model is not None:
            continue

        pydantic_target = _find_pydantic_model_by_name(
            model_registry, entry["target_candidates"]
        )
        if pydantic_target is None:
            continue

        if pydantic_target in model_registry.db_models:
            continue

        in_progress = getattr(model_registry, "_sqlalchemy_models_in_progress", set())  # type: ignore[var-annotated]
        if pydantic_target in in_progress:
            continue

        create_sqlalchemy_model(
            pydantic_target,
            model_registry,
            base_model=base_model,
        )


def _process_reference_fields(
    pydantic_model: Type[BaseModel],
    class_dict: Dict[str, Any],
    existing_columns: Set[str],
    tablename: str,
) -> List[Dict[str, Any]]:
    """Collect reference metadata and inject placeholder columns."""

    all_ref_fields: Dict[str, Type[Any]] = {}
    all_optional_fields: Set[str] = set()

    own_ref_fields: Set[str] = set()
    if hasattr(pydantic_model, "Reference") and hasattr(pydantic_model.Reference, "ID"):
        own_ref_fields = set(get_type_hints(pydantic_model.Reference.ID).keys())

    for base in pydantic_model.__bases__:
        if base.__name__ in {"BaseModel", "DatabaseMixin"} or "Mixin" in base.__name__:
            continue

        if hasattr(base, "__name__") and base.__name__ == "ID":
            ref_fields = get_type_hints(base)
            for field_name, field_type in ref_fields.items():
                if field_name not in own_ref_fields:
                    all_ref_fields[field_name] = field_type

            if hasattr(base, "Optional"):
                optional_fields = set(get_type_hints(base.Optional).keys())
                for field_name in optional_fields:
                    if field_name not in own_ref_fields:
                        all_optional_fields.add(field_name)

        elif hasattr(base, "__qualname__") and (
            "ReferenceModel.Optional" in base.__qualname__
            or base.__name__.endswith("Optional")
        ):
            try:
                ref_fields = get_type_hints(base)
                for field_name, field_type in ref_fields.items():
                    if field_name not in own_ref_fields and is_reference_field_name(
                        field_name
                    ):
                        all_ref_fields[field_name] = field_type
                        all_optional_fields.add(field_name)
            except Exception:
                pass

            for parent in getattr(base, "__mro__", ())[1:]:
                if parent is object:
                    continue
                try:
                    parent_fields = get_type_hints(parent)
                except Exception:
                    continue

                for field_name, field_type in parent_fields.items():
                    if field_name not in own_ref_fields and is_reference_field_name(
                        field_name
                    ):
                        all_ref_fields[field_name] = field_type
                        all_optional_fields.add(field_name)

        elif hasattr(base, "__qualname__") and base.__qualname__.endswith(
            "ReferenceModel"
        ):
            try:
                ref_fields = get_type_hints(base)
                for field_name, field_type in ref_fields.items():
                    if field_name not in own_ref_fields and is_reference_field_name(
                        field_name
                    ):
                        all_ref_fields[field_name] = field_type
                        if hasattr(base, "Optional"):
                            optional_fields = set(get_type_hints(base.Optional).keys())
                            if field_name in optional_fields:
                                all_optional_fields.add(field_name)
            except Exception:
                pass

    reference_configs: List[Dict[str, Any]] = []

    logger.debug(
        "Collected reference fields for {}: {}",
        pydantic_model.__name__,
        list(all_ref_fields.keys()),
    )

    for name, field_type in all_ref_fields.items():
        if not is_reference_field_name(name):
            continue

        entity_name = reference_relationship_name(name)
        entity_class_name = stringcase.pascalcase(entity_name)
        entity_class_name_with_model = reference_target_model_name(name)
        is_optional = name in all_optional_fields

        entity_module, entity_class = get_entity_module_class(
            entity_class_name_with_model
        )

        if entity_class is None:
            entity_module, entity_class = get_entity_module_class(entity_class_name)

        if entity_class is None:
            variations = [
                entity_class_name,
                entity_class_name_with_model,
                f"{entity_class_name}Entity",
                f"{entity_class_name}Table",
            ]

            for variation in variations:
                entity_module, entity_class = get_entity_module_class(variation)
                if entity_class is not None:
                    entity_class_name = variation
                    break

        if entity_class is None and "_" in entity_name:
            segments = entity_name.split("_")
            for index in range(1, len(segments)):
                candidate = stringcase.pascalcase("_".join(segments[index:]))
                entity_module, entity_class = get_entity_module_class(
                    f"{candidate}Model"
                )
                if entity_class is not None:
                    entity_class_name = f"{candidate}Model"
                    break

                entity_module, entity_class = get_entity_module_class(candidate)
                if entity_class is not None:
                    entity_class_name = candidate
                    break

        if name in existing_columns:
            logger.debug(
                "Overriding reference column {} to attach foreign key for {}",
                name,
                entity_class_name_with_model,
            )

        if entity_class and _is_database_model(entity_class):
            ref_table_name = default_name_processor.generate_resource_name(
                entity_class.__name__, use_plural=True
            )
            entity_comment_name = entity_class.__name__
        else:
            ref_table_name = default_name_processor.generate_resource_name(
                entity_class_name, use_plural=True
            )
            entity_comment_name = entity_class_name

        if entity_name == "parent":
            ref_table_name = tablename
            entity_comment_name = pydantic_model.__name__

        fk_comment_prefix = "Optional foreign key" if is_optional else "Foreign key"

        reference_configs.append(
            {
                "column_name": name,
                "ref_table_name": ref_table_name,
                "is_optional": is_optional,
                "entity_comment_name": entity_comment_name,
            }
        )

        logger.debug(
            "Queued reference column {} on {} referencing {}",
            name,
            pydantic_model.__name__,
            ref_table_name,
        )

        if ref_table_name:
            try:
                class_dict[name] = Column(
                    String,
                    ForeignKey(f"{ref_table_name}.id"),
                    nullable=is_optional,
                    comment=f"{fk_comment_prefix} to {entity_comment_name}",
                )
            except Exception as e:
                class_dict[name] = Column(
                    String,
                    nullable=is_optional,
                    comment=f"{fk_comment_prefix} to {entity_comment_name} (FK error: {str(e)})",
                )
        else:
            class_dict[name] = Column(
                String,
                nullable=is_optional,
                comment=f"{fk_comment_prefix} to {entity_class_name} (target unresolved)",
            )

    return reference_configs


def create_sqlalchemy_model(
    pydantic_model: Type[BaseModel],
    model_registry: ModelRegistry,
    tablename: Optional[str] | None = None,
    table_comment: Optional[str] | None = None,
    base_model: Optional[Type[Any]] | None = None,
) -> Type[Any]:
    """
    Create a SQLAlchemy model class from a Pydantic model.

    Args:
        pydantic_model: The Pydantic model to convert
        tablename: Custom table name (optional)
        table_comment: Custom table comment (optional)
        base_model: Custom SQLAlchemy Base class (optional)
        model_registry: Optional ModelRegistry for isolated model tracking (optional)

    Returns:
        SQLAlchemy model class
    """
    from zephyrex.lib.Logging import logger

    # Use provided base model - base_model is required, no singleton fallback
    if base_model is None:
        raise ValueError(
            "base_model parameter is required - no singleton fallback available"
        )

        # Generate table name if not provided
    if not tablename:
        # Use shared name processor for consistent table name generation
        tablename = default_name_processor.generate_resource_name(
            pydantic_model.__name__, use_plural=True
        )

    # Generate table comment if not provided
    if not table_comment:
        table_comment = getattr(pydantic_model, "table_comment", None)
        if not table_comment:
            table_comment = f"Table for {pydantic_model.__name__}"

    # Create the model name
    model_name: str = pydantic_model.__name__

    # Track models currently being generated to avoid recursive loops
    in_progress_set: Optional[Set[Type[BaseModel]]] | None = None
    if model_registry is not None:
        # Use ModelRegistry for isolated tracking
        existing_model = model_registry.get_sqlalchemy_model(
            pydantic_model, for_generation=True
        )
        if existing_model:
            return existing_model  # type: ignore[no-any-return]

        in_progress_set = getattr(
            model_registry, "_sqlalchemy_models_in_progress", None
        )
        if in_progress_set is None:
            in_progress_set = set()
            setattr(model_registry, "_sqlalchemy_models_in_progress", in_progress_set)

        if pydantic_model in in_progress_set:
            existing = model_registry.db_models.get(pydantic_model)
            if existing is not None:
                return existing  # type: ignore[no-any-return]
        else:
            in_progress_set.add(pydantic_model)
    # Note: Global registry fallback removed - all models must use isolated ModelRegistry

    # Extract mixin classes from the Pydantic model
    mixin_classes = _extract_mixin_classes(pydantic_model)

    # Get existing columns from base classes to avoid conflicts
    existing_columns = _get_existing_columns(mixin_classes, base_model)

    # Start building the class dictionary
    class_dict: Dict[str, Any] = {
        "__tablename__": tablename,
        "__table_args__": {"comment": table_comment},
        "__module__": pydantic_model.__module__,
    }

    # Process fields from the Pydantic model
    for field_name, field_info in pydantic_model.model_fields.items():
        # Skip if column already exists in base classes
        if field_name in existing_columns:
            continue

        # Get the field type from the model annotation
        field_type = pydantic_model.model_fields[field_name].annotation

        # Create SQLAlchemy column from field
        column = _create_column_from_field(field_name, field_type, field_info)  # type: ignore[arg-type]
        if column is not None:
            class_dict[_sanitize_field_name(field_name)] = column

    reference_configs = _process_reference_fields(
        pydantic_model, class_dict, existing_columns, tablename
    )

    try:
        model_class = type(model_name, (base_model, *mixin_classes), class_dict)

        _fix_null_type_columns(model_class)
        _ensure_reference_foreign_keys(model_class, reference_configs)

        model_registry.db_models[pydantic_model] = model_class
        logger.debug(f"Registered {model_name} in isolated ModelRegistry")

        model_class.dto = pydantic_model  # type: ignore[attr-defined]

        return model_class
    finally:
        if model_registry is not None and in_progress_set is not None:
            in_progress_set.discard(pydantic_model)


def _fix_null_type_columns(model_class: Type[Any]) -> None:
    """
    Fix any columns in the model that have NullType by replacing with appropriate types.

    This is needed because declared_attr properties from mixins might return NullType()
    when DatabaseManager isn't properly initialized.
    """
    if not hasattr(model_class, "__table__"):
        return

    from sqlalchemy.sql.sqltypes import NullType

    # Check each column in the table
    for column in model_class.__table__.columns:
        if isinstance(column.type, NullType):
            # Replace NullType with appropriate type based on column name
            if is_reference_field_name(column.name) or column.name == PRIMARY_KEY_FIELD:
                # ID fields should be String (UUID)
                column.type = String()
            elif column.name.endswith("_at"):
                # Timestamp fields should be DateTime
                from sqlalchemy import DateTime

                column.type = DateTime()
            else:
                # Default to String for unknown fields
                column.type = String()


def _ensure_reference_foreign_keys(
    model_class: Type[Any], reference_configs: List[Dict[str, Any]]
) -> None:
    if not reference_configs or not hasattr(model_class, "__table__"):
        return

    table = getattr(model_class, "__table__", None)
    if table is None:
        return

    for config in reference_configs:
        column_name = config["column_name"]
        column = table.columns.get(column_name)
        if column is None:
            logger.debug(
                "Reference column {} missing on {} during FK enforcement",
                column_name,
                model_class.__name__,
            )
            continue

        ref_table_name = config.get("ref_table_name")
        if ref_table_name and not column.foreign_keys:
            fk = ForeignKey(f"{ref_table_name}.id")
            column.append_foreign_key(fk)
            if fk.constraint is None:
                constraint = ForeignKeyConstraint(
                    [table.c[column_name]],
                    [f"{ref_table_name}.id"],
                    name=f"fk_{table.name}_{column_name}_{ref_table_name}",
                )
                table.append_constraint(constraint)
            column.nullable = config["is_optional"]
            fk_comment_prefix = (
                "Optional foreign key" if config["is_optional"] else "Foreign key"
            )
            column.comment = (
                column.comment
                or f"{fk_comment_prefix} to {config['entity_comment_name']}"
            )


def _analyze_model_dependencies(bll_models: Dict[str, Type[BaseModel]]) -> List[str]:
    """
    Analyze dependencies between BLL models to determine creation order.

    Args:
        bll_models: Dictionary of BLL models

    Returns:
        List of model names in dependency order (dependencies first)
    """
    dependencies: Dict[str, Set[str]] = {}

    for model_name, pydantic_model in bll_models.items():
        if (
            model_name.endswith("ReferenceModel")
            or model_name.endswith("NetworkModel")
            or "." in model_name
        ):
            continue

        deps: Set[str] = set()

        # Check for Reference.ID dependencies
        if hasattr(pydantic_model, "Reference") and hasattr(
            pydantic_model.Reference, "ID"
        ):
            ref_class = pydantic_model.Reference.ID
            ref_fields = get_type_hints(ref_class)

            for name, field_type in ref_fields.items():
                if is_reference_field_name(name):
                    entity_name = reference_relationship_name(name)
                    entity_class_name = stringcase.pascalcase(entity_name)

                    # Look for the referenced model
                    ref_model_name = reference_target_model_name(name)
                    if ref_model_name in bll_models and ref_model_name != model_name:
                        deps.add(ref_model_name)

        dependencies[model_name] = deps

    # Topological sort to get creation order
    ordered: List[str] = []
    visited: Set[str] = set()
    temp_visited: Set[str] = set()

    def visit(model_name):
        if model_name in temp_visited:
            # Circular dependency - skip this dependency
            return
        if model_name in visited:
            return

        temp_visited.add(model_name)

        # Visit dependencies first
        for dep in dependencies.get(model_name, set()):
            if dep in dependencies:  # Only visit if it's in our model list
                visit(dep)

        temp_visited.remove(model_name)
        visited.add(model_name)
        ordered.append(model_name)

    # Visit all models
    for model_name in dependencies:
        if model_name not in visited:
            visit(model_name)

    return ordered


def get_scaffolded_model(model_name: str) -> Optional[Type[Any]]:
    """
    Get a scaffolded SQLAlchemy model by name.

    Note: Global registry removed - this function is deprecated.
    Use the ModelRegistry from app state instead.

    Args:
        model_name: Name of the model

    Returns:
        None (function deprecated)
    """
    logger.warning(
        "get_scaffolded_model is deprecated - use ModelRegistry from app state"
    )
    return None


def list_scaffolded_models() -> List[str]:
    """
    List all scaffolded SQLAlchemy model names.

    Note: Global registry removed - this function is deprecated.
    Use the ModelRegistry from app state instead.

    Returns:
        Empty list (function deprecated)
    """
    logger.warning(
        "list_scaffolded_models is deprecated - use ModelRegistry from app state"
    )
    return []
