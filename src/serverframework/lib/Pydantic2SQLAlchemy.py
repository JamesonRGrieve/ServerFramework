import inspect
import re
import sys
import uuid
from datetime import datetime
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

import stringcase
from pydantic import BaseModel, Field
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
)
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import relationship

from serverframework.database.AbstractDatabaseEntity import BaseMixin, ImageMixin, UpdateMixin
from serverframework.lib.AbstractPydantic2 import default_name_processor
from serverframework.lib.Environment import inflection
from serverframework.lib.Logging import logger
from serverframework.lib.Pydantic import ModelRegistry

# Type variable for generic models
T = TypeVar("T", bound=BaseModel)
SQLAlchemyModelType = TypeVar("SQLAlchemyModelType")
DatabaseManagerType = TypeVar("DatabaseManagerType")

# Using shared inflection instance from Environment
inflect_engine: Any = inflection

# Map Pydantic types to SQLAlchemy types
TYPE_MAPPING: Dict[Type[Any], Type[Any]] = {
    str: String,
    int: Integer,
    bool: Boolean,
    datetime: DateTime,
    uuid.UUID: String,
    float: Float,
    dict: JSON,
    list: JSON,
    # Add more type mappings as needed
}

# Regex to extract tablename from a class name
TABLENAME_REGEX: re.Pattern[str] = re.compile(r"(?<!^)(?=[A-Z])")

# Reserved SQLAlchemy field names that need to be renamed
RESERVED_SQLALCHEMY_NAMES: FrozenSet[str] = frozenset(
    {
        "metadata",
        "registry",
        "query",
        "session",
        "bind",
        "mapper",
        "class_",
        "table",
        "columns",
        "primary_key",
        "foreign_keys",
        "constraints",
        "indexes",
        "info",
        "schema",
        "autoload",
        "autoload_with",
    }
)


def _sanitize_field_name(field_name: str) -> str:
    """
    Sanitize field names to avoid conflicts with SQLAlchemy reserved names.

    Args:
        field_name: Original field name

    Returns:
        Sanitized field name
    """
    from serverframework.lib.AbstractPydantic2 import NameProcessor

    return NameProcessor.sanitize_name(field_name, RESERVED_SQLALCHEMY_NAMES)


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
        import contextvars

        from starlette.requests import Request

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
            logger.debug(
                "DatabaseManager lookup: no current asyncio task: %s", e
            )

        # 2. Try to get from thread-local storage (not recommended but for compatibility)
        try:
            import threading

            local = threading.current_thread()
            if hasattr(local, "_db_manager"):
                return local._db_manager
        except (AttributeError, RuntimeError) as e:
            logger.debug(
                "DatabaseManager lookup: thread-local probe failed: %s", e
            )

        # 3. Try to get from global app state (last resort)
        try:
            # This is very fragile but might work in some cases
            import sys

            for module_name, module in sys.modules.items():
                if hasattr(module, "app") and hasattr(module.app, "state"):
                    if hasattr(module.app.state, "DB"):
                        return module.app.state.model_registry.database_manager
        except (AttributeError, RuntimeError, ImportError) as e:
            logger.debug(
                "DatabaseManager lookup: app-state probe failed: %s", e
            )

        return None

    except Exception as e:
        from serverframework.lib.Logging import logger

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
            from serverframework.database.DatabaseManager import get_database_manager_singleton

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

            context = starlette.concurrency.context.get()
            if context and hasattr(context, "state"):
                app_state = context.state
                if hasattr(app_state, "DB") and hasattr(
                    app_state.model_registry.database_manager, "Base"
                ):
                    base = app_state.model_registry.database_manager.Base
                    if hasattr(base, "_pydantic_models"):
                        base._pydantic_models.clear()
                        cleared_bases.append(
                            "serverframework.app.state.model_registry.database_manager.Base"
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
    calling_frame = inspect.currentframe().f_back
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
    if name == "id":
        params["primary_key"] = True
        params["nullable"] = False
        # Ensure proper type for primary key - always use String for IDs
        sa_type = String

    # Force String type for all ID fields (UUID pattern)
    if name.endswith("_id") or name == "id":
        sa_type = String

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
                    PydanticUndefined = None

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
            return sqlalchemy_model

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
            return model

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

        in_progress = getattr(model_registry, "_sqlalchemy_models_in_progress", set())
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
                    if field_name not in own_ref_fields and field_name.endswith("_id"):
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
                    if field_name not in own_ref_fields and field_name.endswith("_id"):
                        all_ref_fields[field_name] = field_type
                        all_optional_fields.add(field_name)

        elif hasattr(base, "__qualname__") and base.__qualname__.endswith(
            "ReferenceModel"
        ):
            try:
                ref_fields = get_type_hints(base)
                for field_name, field_type in ref_fields.items():
                    if field_name not in own_ref_fields and field_name.endswith("_id"):
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
        if not name.endswith("_id"):
            continue

        entity_name = name.removesuffix("_id")
        entity_class_name = stringcase.pascalcase(entity_name)
        entity_class_name_with_model = f"{entity_class_name}Model"
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
    from serverframework.lib.Logging import logger

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
            return existing_model

        in_progress_set = getattr(
            model_registry, "_sqlalchemy_models_in_progress", None
        )
        if in_progress_set is None:
            in_progress_set = set()
            setattr(model_registry, "_sqlalchemy_models_in_progress", in_progress_set)

        if pydantic_model in in_progress_set:
            existing = model_registry.db_models.get(pydantic_model)
            if existing is not None:
                return existing
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
        column = _create_column_from_field(field_name, field_type, field_info)
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

        model_class.dto = pydantic_model

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
            if column.name.endswith("_id") or column.name == "id":
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
                if name.endswith("_id"):
                    entity_name = name.removesuffix("_id")
                    entity_class_name = stringcase.pascalcase(entity_name)

                    # Look for the referenced model
                    ref_model_name = f"{entity_class_name}Model"
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


# Search model for string fields
class StringSearchModel(BaseModel):
    contains: Optional[str] | None = None
    equals: Optional[str] | None = None
    starts_with: Optional[str] | None = None
    ends_with: Optional[str] | None = None
    in_list: Optional[List[str]] | None = None


# Common Pydantic model mixins that match the SQLAlchemy mixins
class ApplicationModel(BaseModel):
    id: Optional[str] = Field(None, description="Unique identifier")
    created_at: Optional[datetime] = Field(None, description="Creation timestamp")
    created_by_user_id: Optional[str] = Field(
        None, description="ID of the user who created this record"
    )

    class Optional(BaseModel):
        id: Optional[str] | None = None
        created_at: Optional[datetime] | None = None
        created_by_user_id: Optional[str] | None = None

    class Search(BaseModel):
        id: Optional[StringSearchModel] | None = None
        created_at: Optional[datetime] | None = None
        created_by_user_id: Optional[StringSearchModel] | None = None


class UpdateMixinModel(BaseModel):
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")
    updated_by_user_id: Optional[str] = Field(
        None, description="ID of the user who last updated this record"
    )

    class Optional(BaseModel):
        updated_at: Optional[datetime] | None = None
        updated_by_user_id: Optional[str] | None = None

    class Search(BaseModel):
        updated_at: Optional[datetime] | None = None
        updated_by_user_id: Optional[StringSearchModel] | None = None


class ImageMixinModel(BaseModel):
    image_url: Optional[str] = Field(
        None, description="URL to the image for this record"
    )

    class Optional(BaseModel):
        image_url: Optional[str] = Field(None, description="Optional image URL")

    class Search(BaseModel):
        image_url: Optional[StringSearchModel] | None = None


class ParentMixinModel(BaseModel):
    parent_id: Optional[str] = Field(None, description="ID of the parent record")

    class Optional(BaseModel):
        parent_id: Optional[str] | None = None

    class Search(BaseModel):
        parent_id: Optional[StringSearchModel] | None = None


# Fix circular imports and handle parent relationship properly
class ParentRelationshipMixin:
    """Modified version of ParentMixin that sets up the self-reference correctly"""

    @declared_attr
    def parent_id(cls):
        return Column(
            String,
            ForeignKey(f"{cls.__tablename__}.id"),
            nullable=True,
            comment="ID of the parent record",
        )

    @declared_attr
    def parent(cls):
        return relationship(
            cls,
            remote_side=[cls.id],
            backref="children",
            primaryjoin=lambda: cls.id == cls.parent_id,
        )


# Legacy compatibility class
class ModelConverter:
    """
    Legacy utility class for backward compatibility.
    """

    @staticmethod
    def create_sqlalchemy_model(model, **kwargs):
        """Legacy method - use create_sqlalchemy_model function instead."""
        from serverframework.lib.Pydantic import ModelRegistry

        registry = ModelRegistry()
        return create_sqlalchemy_model(model, registry, **kwargs)

    @staticmethod
    def pydantic_to_dict(pydantic_obj: BaseModel) -> Dict[str, Any]:
        """
        Convert a Pydantic model instance to a dictionary suitable for SQLAlchemy.
        Removes any fields that don't belong in the SQLAlchemy model.

        Args:
            pydantic_obj: Pydantic model instance

        Returns:
            Dictionary with only the valid SQLAlchemy fields
        """
        # Handle both Pydantic v1 and v2
        try:
            if hasattr(pydantic_obj, "model_dump"):
                # Pydantic v2
                data = pydantic_obj.model_dump(exclude_unset=True)
            elif hasattr(pydantic_obj, "dict"):
                # Pydantic v1
                data = pydantic_obj.dict(exclude_unset=True)
            else:
                # Fallback
                data = {
                    k: v
                    for k, v in pydantic_obj.__dict__.items()
                    if not k.startswith("_")
                }
        except Exception as e:
            # Fallback if the methods fail
            data = {
                k: v for k, v in pydantic_obj.__dict__.items() if not k.startswith("_")
            }

        # Remove any fields that shouldn't be passed to SQLAlchemy
        # (like nested models or computed fields)
        keys_to_remove = []
        for key, value in data.items():
            if isinstance(value, BaseModel):
                keys_to_remove.append(key)
            elif isinstance(value, list) and value and isinstance(value[0], BaseModel):
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del data[key]

        # Filter out PydanticUndefined values
        try:
            from pydantic_core import PydanticUndefined
        except ImportError:
            try:
                from pydantic.fields import PydanticUndefined
            except ImportError:
                PydanticUndefined = None

        if PydanticUndefined is not None:
            keys_to_remove = []
            for key, value in data.items():
                if value is PydanticUndefined:
                    keys_to_remove.append(key)

            for key in keys_to_remove:
                del data[key]

        return data

    @staticmethod
    def sqlalchemy_to_pydantic(
        sa_obj: Any, pydantic_class: Type[BaseModel]
    ) -> BaseModel:
        """
        Convert a SQLAlchemy model instance to a Pydantic model instance.

        Args:
            sa_obj: SQLAlchemy model instance
            pydantic_class: Target Pydantic model class

        Returns:
            Pydantic model instance
        """
        # Convert SQLAlchemy model to dict
        if hasattr(sa_obj, "__dict__"):
            # Extract data from SQLAlchemy model
            data = {}
            for key, value in sa_obj.__dict__.items():
                if not key.startswith("_"):
                    data[key] = value

            # Get model fields and provide default values
            try:
                # Initialize missing optional fields with None
                for field_name, field_type in get_type_hints(pydantic_class).items():
                    if (
                        field_name not in data
                        and get_origin(field_type) is Union
                        and type(None) in get_args(field_type)
                    ):
                        data[field_name] = None

                # Create Pydantic model instance based on version
                try:
                    if hasattr(pydantic_class, "model_validate"):
                        # Pydantic v2
                        return pydantic_class.model_validate(data)
                    elif hasattr(pydantic_class, "parse_obj"):
                        # Pydantic v1
                        return pydantic_class.parse_obj(data)
                    else:
                        # Direct instantiation
                        return pydantic_class(**data)
                except Exception as e:
                    # If the above fails, try direct instantiation
                    return pydantic_class(**data)
            except Exception as e:
                raise ValueError(
                    f"Failed to convert SQLAlchemy object to Pydantic: {str(e)}"
                )
        else:
            # Handle the case where sa_obj is already a dict
            try:
                if hasattr(pydantic_class, "model_validate"):
                    # Pydantic v2
                    return pydantic_class.model_validate(sa_obj)
                elif hasattr(pydantic_class, "parse_obj"):
                    # Pydantic v1
                    return pydantic_class.parse_obj(sa_obj)
                else:
                    # Direct instantiation
                    return pydantic_class(**sa_obj)
            except Exception as e:
                # If the above fails, try direct instantiation
                try:
                    return pydantic_class(**sa_obj)
                except Exception as nested_e:
                    raise ValueError(
                        f"Failed to convert dict to Pydantic: {str(e)}, nested error: {str(nested_e)}"
                    )


class DatabaseMixin:
    """
    Mixin for Pydantic models that provides access to corresponding SQLAlchemy models.

    This mixin adds a `.DB(declarative_base)` method that returns the SQLAlchemy model class
    that corresponds to the Pydantic model for the given declarative base. The SQLAlchemy
    model is generated automatically and cached per declarative base.

    Example:
        class UserModel(BaseModel, DatabaseMixin):
            name: str = Field(..., description="User's name")
            email: str = Field(..., description="User's email")

        # Access the SQLAlchemy model for a specific declarative base
        # Note: In practice, get the db_manager from app.state.model_registry.database_manager or dependency injection
        db_manager = _get_db_manager_from_context()
        User = UserModel.DB(db_manager.Base)

        # Use it with SQLAlchemy
        with db_manager.get_session() as db:
            users = db.query(User).all()
    """

    @classmethod
    def DB(cls, declarative_base):
        """
        Get or create the SQLAlchemy model for this Pydantic model within the given declarative base.
        Automatically creates dependent models to ensure proper relationship resolution.

        Args:
            declarative_base: The SQLAlchemy declarative base to use

        Returns:
            The SQLAlchemy model class corresponding to this Pydantic model
        """
        if declarative_base is None:
            raise ValueError("declarative_base cannot be None")

        # Add debugging to help identify the issue
        logger.debug(
            f"DB method called for {cls.__name__} with declarative_base type: {type(declarative_base)}"
        )

        # Validate that declarative_base is a proper class/type, not a mock or proxy
        if not hasattr(declarative_base, "__name__") and not hasattr(
            declarative_base, "__class__"
        ):
            logger.error(
                f"Invalid declarative_base object: {declarative_base}, type: {type(declarative_base)}"
            )
            raise ValueError(
                f"declarative_base must be a valid SQLAlchemy declarative base class, got {type(declarative_base)}"
            )

        # Create a registry key based on the model and declarative base
        registry_key = f"{cls.__module__}.{cls.__name__}"

        # Check if we already have this model in the declarative base registry
        if hasattr(declarative_base, "_pydantic_models"):
            pydantic_models = getattr(declarative_base, "_pydantic_models", None)
            if isinstance(pydantic_models, dict) and registry_key in pydantic_models:
                return pydantic_models[registry_key]
            elif not isinstance(pydantic_models, dict):
                # Reset if it's not a proper dictionary
                declarative_base._pydantic_models = {}
        else:
            declarative_base._pydantic_models = {}

        # Get the model registry from the declarative base or database manager
        model_registry = None

        # First, try to get it from the declarative base if it has one attached
        if hasattr(declarative_base, "_model_registry"):
            model_registry = declarative_base._model_registry
        else:
            # Try to get it from the database manager
            try:
                # WARNING: This is deprecated singleton usage - use dependency injection in practice
                db_manager = _get_db_manager_from_context()
                if (
                    db_manager
                    and hasattr(db_manager, "Base")
                    and db_manager.Base == declarative_base
                ):
                    # Check if there's an app state with model registry
                    try:
                        import starlette.concurrency
                        from starlette.applications import Starlette

                        # This is a fallback - in practice we should have the registry attached to the base
                        pass
                    except ImportError as e:
                        logger.debug(
                            "starlette unavailable while resolving "
                            "model_registry: %s",
                            e,
                        )
            except Exception as e:
                logger.debug(
                    "model_registry resolution from db_manager context failed: %s",
                    e,
                )

        # If we still don't have a model registry, we need to create one for this declarative base
        if model_registry is None:
            from serverframework.lib.Pydantic import ModelRegistry

            model_registry = ModelRegistry()
            model_registry.declarative_base = declarative_base
            # Attach it to the declarative base for future use
            try:
                setattr(declarative_base, "_model_registry", model_registry)
            except (TypeError, AttributeError) as e:
                logger.warning(
                    f"Could not attach model registry to declarative_base: {e}"
                )
                # Continue without attaching - we still have the model_registry locally

        # Before creating this model, ensure all its dependencies are created first
        cls._ensure_dependencies_created(declarative_base)

        # Create the SQLAlchemy model using the proper model registry
        sqlalchemy_model = create_sqlalchemy_model(
            cls, model_registry, base_model=declarative_base
        )

        # Store it in the declarative base registry
        # Ensure _pydantic_models is a proper dictionary before storing
        try:
            if not hasattr(declarative_base, "_pydantic_models") or not isinstance(
                getattr(declarative_base, "_pydantic_models", None), dict
            ):
                setattr(declarative_base, "_pydantic_models", {})
            getattr(declarative_base, "_pydantic_models")[
                registry_key
            ] = sqlalchemy_model
        except (TypeError, AttributeError) as e:
            logger.warning(
                f"Could not store model in declarative_base registry due to {e}. Proceeding without caching."
            )
            # If we can't store in the registry, that's okay - we'll just return the model without caching

        return sqlalchemy_model

    @classmethod
    def _ensure_dependencies_created(cls, declarative_base):
        """
        Ensure all dependent models are created in the same declarative base.
        This prevents SQLAlchemy relationship resolution errors.
        """
        from typing import get_type_hints

        # Get all reference fields from the model's inheritance chain
        dependency_models = set()

        for base in cls.__bases__:
            # Skip basic types and our own mixins
            if (
                base.__name__ in ["BaseModel", "DatabaseMixin"]
                or "Mixin" in base.__name__
            ):
                continue

            # Check for ReferenceModel classes (like UserReferenceModel, TeamReferenceModel)
            if hasattr(base, "__qualname__") and base.__qualname__.endswith(
                "ReferenceModel"
            ):
                try:
                    ref_fields = get_type_hints(base)
                    for field_name, field_type in ref_fields.items():
                        if field_name.endswith("_id"):
                            entity_name = field_name.removesuffix("_id")
                            entity_class_name = f"{entity_name.title()}Model"

                            # Try to import and get the dependency model
                            try:
                                # Import from the same module as this model
                                module = __import__(
                                    cls.__module__, fromlist=[entity_class_name]
                                )
                                if hasattr(module, entity_class_name):
                                    dependency_model = getattr(
                                        module, entity_class_name
                                    )
                                    if hasattr(dependency_model, "DB") and hasattr(
                                        dependency_model, "model_fields"
                                    ):
                                        dependency_models.add(dependency_model)
                            except (ImportError, AttributeError):
                                # If we can't import the dependency, skip it
                                pass
                except Exception:
                    # If we can't get type hints, skip this base
                    pass

        # Create all dependency models first
        for dependency_model in dependency_models:
            try:
                dependency_model.DB(declarative_base)
            except Exception:
                # If creating a dependency fails, continue with others
                pass

    @classmethod
    def clear_db_cache(cls):
        """
        Clear any cached database models for this Pydantic model.
        Note: With the new approach, caching is per declarative base,
        so this method is mainly for compatibility.
        """
        # This method is now mainly for compatibility
        # The actual cache is stored in each declarative base
        pass

    @classmethod
    def get_db_model(cls, declarative_base) -> Type:
        """
        Alias for DB() method for backward compatibility.

        Args:
            declarative_base: The SQLAlchemy declarative base to use

        Returns:
            The SQLAlchemy model class
        """
        return cls.DB(declarative_base)


# =============================================================================
# MODEL EXTENSION SYSTEM
# =============================================================================

# Backward compatibility registry for tracking extensions
# In the new architecture, this is only used for test compatibility
_EXTENSION_REGISTRY_COMPAT = {}

# Snapshots of model state taken before extensions mutate them,
# keyed by (module, qualname). Used by reset_extension_system() to
# undo in-place field additions that _apply_model_extension makes.
_MODEL_SNAPSHOTS: Dict[str, dict] = {}


class RemoveField:
    """
    Marker class to indicate a field should be removed from the target model.

    Usage:
        @extension_model(UserModel)
        class MinimalAuth_UserModel:
            mfa_count: RemoveField | None = None
            timezone: RemoveField | None = None
    """

    pass


def extension_model(
    target_model: Type[BaseModel],
) -> Callable[[Type[BaseModel]], Type[BaseModel]]:
    """
    Decorator to mark a model as an extension of another model.
    The extension will be applied by ModelRegistry when binding models.

    This decorator only marks the extension class with metadata - it does NOT
    apply any global state changes. All extension tracking is handled by
    the instance-based ModelRegistry.

    Args:
        target_model: The Pydantic model class to extend

    Returns:
        Decorator function that marks the extension

    Usage:
        @extension_model(UserModel)
        class Payment_UserModel:
            some_extension_field: Optional[str] = Field(None, description="Example extension field")
    """

    def decorator(extension_class: Type[BaseModel]) -> Type[BaseModel]:
        # Store metadata for registry system - NO GLOBAL STATE
        extension_class._extension_target = target_model
        extension_class._is_extension_model = True

        # For backward compatibility, track in compatibility registry
        target_key = f"{target_model.__module__}.{target_model.__name__}"
        extension_key = f"{extension_class.__module__}.{extension_class.__name__}"

        if target_key not in _EXTENSION_REGISTRY_COMPAT:
            _EXTENSION_REGISTRY_COMPAT[target_key] = []

        if extension_key not in _EXTENSION_REGISTRY_COMPAT[target_key]:
            _EXTENSION_REGISTRY_COMPAT[target_key].append(extension_key)

        from serverframework.lib.Logging import logger

        logger.debug(
            f"Marked {extension_class.__module__}.{extension_class.__name__} as extension for {target_model.__module__}.{target_model.__name__}"
        )

        # Record each injected field so ``detect_extension_field_collisions``
        # at registry finalize time can surface conflicts where two
        # extensions inject the same (model, field) with non-equivalent
        # declarations. Source location lookup is best-effort — built-in
        # / dynamically-generated extension classes may have no source.
        try:
            import inspect as _inspect

            from serverframework.extensions.CollisionDetection import (
                register_extension_field,
            )

            try:
                source_file = _inspect.getsourcefile(extension_class) or "<unknown>"
            except (TypeError, OSError):
                source_file = "<unknown>"
            try:
                source_line = _inspect.getsourcelines(extension_class)[1]
            except (TypeError, OSError):
                source_line = 0
            extension_name = getattr(extension_class, "__module__", extension_key)
            for field_name, field_info in getattr(
                extension_class, "model_fields", {}
            ).items():
                register_extension_field(
                    extension_name=extension_name,
                    model_name=target_model.__name__,
                    field_name=field_name,
                    field_info=field_info,
                    source_file=source_file,
                    source_line=source_line,
                )
        except Exception as exc:
            # Registration is observability — never block decoration.
            logger.debug(
                f"register_extension_field failed for "
                f"{extension_class.__name__}: {exc}"
            )

        return extension_class

    return decorator


def _undo_model_extension(target_model: Type[BaseModel]) -> None:
    """Reverse every extension applied to ``target_model`` in place.

    The extension system mutates ``target_model.model_fields`` and
    ``target_model.__annotations__`` whenever a ``@extension_model``-decorated
    class is processed. That is global state — once payment loads in a
    test process, ``UserModel`` carries ``external_payment_id`` even
    though the next test's model registry doesn't include payment, and
    SQLAlchemy then SELECTs a column the test database doesn't have.

    The fix tracks applied fields on the target itself (``_applied_extension_fields``)
    so we can pop them back out before the next test's registry rebuilds
    its SQLAlchemy classes.
    """
    applied = getattr(target_model, "_applied_extension_fields", None)
    if not applied:
        return
    for field_name in list(applied):
        target_model.__annotations__.pop(field_name, None)
        if hasattr(target_model, "model_fields"):
            target_model.model_fields.pop(field_name, None)
    target_model._applied_extension_fields = set()


def _apply_model_extension(
    target_model: Type[BaseModel], extension_class: Type[BaseModel]
) -> None:
    """
    Apply fields and attributes from extension_class to target_model.

    This function modifies the target model's annotations, model_fields, and
    class attributes to add new fields or remove existing ones based on the
    extension class. It properly handles mixins with descriptors and navigation properties,
    and crucially rebuilds the Pydantic model to ensure runtime field access works.

    Args:
        target_model: The model to extend
        extension_class: The extension class containing field modifications
    """
    from serverframework.lib.Logging import logger

    # Snapshot the target model's original state before mutation so
    # reset_extension_system() can restore it for test isolation.
    snap_key = f"{target_model.__module__}.{target_model.__qualname__}"
    if snap_key not in _MODEL_SNAPSHOTS:
        _MODEL_SNAPSHOTS[snap_key] = {
            "annotations": dict(getattr(target_model, "__annotations__", {})),
            "model_fields": dict(getattr(target_model, "model_fields", {})),
            "model_cls": target_model,
        }

    # Get extension fields via reflection
    extension_annotations = getattr(extension_class, "__annotations__", {})
    extension_fields = getattr(extension_class, "model_fields", {})

    # Ensure target model has the required attributes
    if not hasattr(target_model, "__annotations__"):
        target_model.__annotations__ = {}
    if not hasattr(target_model, "model_fields"):
        target_model.model_fields = {}
    if not hasattr(target_model, "_applied_extension_fields"):
        target_model._applied_extension_fields = set()

    # Track changes for logging
    added_fields: List[str] = []
    removed_fields: List[str] = []
    added_attributes: List[str] = []

    # Process fields from extension class - check both annotations and model_fields
    all_extension_fields = set(extension_annotations.keys()) | set(
        extension_fields.keys()
    )

    for field_name in all_extension_fields:
        # Get field type from annotations if available, otherwise infer from model_fields
        if field_name in extension_annotations:
            field_type = extension_annotations[field_name]
        elif field_name in extension_fields:
            # Extract type from FieldInfo annotation
            field_info = extension_fields[field_name]
            field_type = (
                field_info.annotation if hasattr(field_info, "annotation") else str
            )
        else:
            continue

        if field_type is RemoveField or (
            hasattr(field_type, "__origin__") and field_type.__origin__ is RemoveField
        ):
            # Remove field from target model
            if field_name in target_model.__annotations__:
                del target_model.__annotations__[field_name]
                removed_fields.append(field_name)
            if field_name in target_model.model_fields:
                del target_model.model_fields[field_name]
        else:
            # Add field to target model
            target_model.__annotations__[field_name] = field_type
            added_fields.append(field_name)
            target_model._applied_extension_fields.add(field_name)

            # Copy field info if available from model_fields
            if field_name in extension_fields:
                target_model.model_fields[field_name] = extension_fields[field_name]
            else:
                # Look for Field instances in class attributes
                if hasattr(extension_class, field_name):
                    field_value = getattr(extension_class, field_name)
                    # Check if it's a Field instance
                    if (
                        hasattr(field_value, "__class__")
                        and field_value.__class__.__name__ == "FieldInfo"
                    ) or (
                        hasattr(field_value, "__class__")
                        and "Field" in str(type(field_value))
                    ):
                        target_model.model_fields[field_name] = field_value

    # Copy all class attributes from extension class (including descriptors, navigation properties, etc.)
    # This handles mixins properly by copying descriptors and other class-level attributes
    for attr_name in dir(extension_class):
        # Skip private attributes, methods, and standard class attributes
        if (
            attr_name.startswith("_")
            or attr_name
            in [
                "__annotations__",
                "__dict__",
                "__doc__",
                "__module__",
                "__qualname__",
                "__weakref__",
            ]
            or callable(getattr(extension_class, attr_name, None))
            or attr_name
            in [
                "model_fields",
                "model_config",
                "model_computed_fields",
                "model_extra",
                "model_fields_set",
            ]
        ):
            continue

        # Get the attribute from the extension class
        attr_value = getattr(extension_class, attr_name)

        # Copy the attribute to the target model if it's not already there
        # This includes descriptors like ExternalNavigationProperty
        if not hasattr(target_model, attr_name):
            setattr(target_model, attr_name, attr_value)
            added_attributes.append(attr_name)

    # CRITICAL: Properly integrate extension fields into Pydantic's model structure
    if added_fields or removed_fields:
        try:
            # Update the model's internal structures that Pydantic uses for field access

            # For Pydantic v2, we need to ensure the fields are added to the right places
            # and the model's validator and schema are updated

            # Create field defaults for new fields if they don't have values
            for field_name in added_fields:
                if field_name in extension_fields:
                    field_info = extension_fields[field_name]
                    # Ensure the field has a proper default if not specified
                    if not hasattr(target_model, field_name):
                        # Set the field as a class attribute with the default value
                        default_value = getattr(field_info, "default", None)
                        if default_value is not None:
                            setattr(target_model, field_name, default_value)
                        else:
                            # For Optional fields, default to None
                            setattr(target_model, field_name, None)

            # Force Pydantic to rebuild the model with the new fields
            target_model.model_rebuild(force=True)

            # Verify the rebuild worked by checking field accessibility
            rebuild_success = True
            for field_name in added_fields:
                if field_name not in target_model.model_fields:
                    rebuild_success = False
                    logger.warning(
                        f"Field {field_name} missing from model_fields after rebuild"
                    )

            if rebuild_success:
                logger.debug(
                    f"Successfully rebuilt Pydantic model {target_model.__name__} with extension fields"
                )
            else:
                logger.warning(
                    f"Pydantic model rebuild incomplete for {target_model.__name__}"
                )

        except Exception as e:
            logger.warning(
                f"Failed to rebuild Pydantic model {target_model.__name__}: {e}"
            )
            # Fallback: manually ensure field accessibility
            try:
                # For each added field, ensure it can be accessed
                for field_name in added_fields:
                    if field_name in extension_fields:
                        field_info = extension_fields[field_name]
                        # Set a property or default value to ensure field access works
                        if not hasattr(target_model, field_name):
                            default_value = getattr(field_info, "default", None)
                            setattr(target_model, field_name, default_value)
                            logger.debug(f"Set fallback field access for {field_name}")

            except Exception as fallback_e:
                logger.error(
                    f"Fallback model extension failed for {target_model.__name__}: {fallback_e}"
                )

    # Apply extensions to nested classes (Create, Update, Search, etc.)
    _apply_nested_model_extensions(target_model, extension_class)

    # Clear any cached SQLAlchemy models to force regeneration
    _clear_model_cache(target_model)

    # Log the changes
    if added_fields:
        logger.debug(f"Added fields to {target_model.__name__}: {added_fields}")
    if removed_fields:
        logger.debug(f"Removed fields from {target_model.__name__}: {removed_fields}")
    if added_attributes:
        logger.debug(f"Added attributes to {target_model.__name__}: {added_attributes}")


def _apply_nested_model_extensions(
    target_model: Type[BaseModel], extension_class: Type[BaseModel]
) -> None:
    from pydantic import BaseModel

    from serverframework.lib.Logging import logger

    nested_class_names = ["Create", "Update", "Search", "Reference", "Optional"]

    for nested_name in nested_class_names:
        if hasattr(extension_class, nested_name):
            extension_nested = getattr(extension_class, nested_name)

            if not hasattr(target_model, nested_name):
                setattr(target_model, nested_name, type(nested_name, (BaseModel,), {}))

            target_nested = getattr(target_model, nested_name)

            extension_annotations = getattr(extension_nested, "__annotations__", {})
            extension_fields = getattr(extension_nested, "model_fields", {})

            if not hasattr(target_nested, "__annotations__"):
                target_nested.__annotations__ = {}
            if not hasattr(target_nested, "model_fields"):
                target_nested.model_fields = {}

            fields_modified = False

            for field_name, field_type in extension_annotations.items():
                if field_type is RemoveField or (
                    hasattr(field_type, "__origin__")
                    and field_type.__origin__ is RemoveField
                ):
                    if field_name in target_nested.__annotations__:
                        del target_nested.__annotations__[field_name]
                    if field_name in target_nested.model_fields:
                        del target_nested.model_fields[field_name]
                    logger.debug(
                        f"Removed field {field_name} from {target_model.__name__}.{nested_name}"
                    )
                    fields_modified = True
                else:
                    target_nested.__annotations__[field_name] = field_type
                    if field_name in extension_fields:
                        target_nested.model_fields[field_name] = extension_fields[
                            field_name
                        ]
                    logger.debug(
                        f"Added field {field_name} to {target_model.__name__}.{nested_name}"
                    )
                    fields_modified = True

            if fields_modified and hasattr(target_nested, "model_rebuild"):
                target_nested.model_rebuild(force=True)


def _clear_model_cache(target_model: Type[BaseModel]) -> None:
    """
    Clear SQLAlchemy model cache to force regeneration with extended fields.

    Args:
        target_model: The model whose cache should be cleared
    """
    from serverframework.lib.Logging import logger

    # Clear DatabaseMixin cache if the model uses it
    if hasattr(target_model, "clear_db_cache"):
        target_model.clear_db_cache()
        logger.debug(f"Cleared DatabaseMixin cache for {target_model.__name__}")

    # Note: Global registries have been removed in favor of isolated ModelRegistry instances
    # The cache clearing is now handled primarily through the DatabaseMixin.clear_db_cache() method
    # and the ModelRegistry.clear_cache() method when available

    logger.debug(f"Cleared model cache for {target_model.__name__}")


def get_applied_extensions() -> Dict[str, List[str]]:
    """
    Get a dictionary of all applied extensions.

    Note: This function is deprecated. Extensions are now handled by instance-based
    ExtensionRegistry objects. This function returns the compatibility registry for backward compatibility.

    Returns:
        Dictionary mapping target model names to lists of applied extension names
    """
    from serverframework.lib.Logging import logger

    logger.debug(
        "get_applied_extensions() called - extensions are now instance-based in ExtensionRegistry"
    )

    # Return a copy of the compatibility registry
    return _EXTENSION_REGISTRY_COMPAT.copy()


def reset_extension_system() -> None:
    """
    Reset the extension system for testing purposes.

    Restores any model classes that were mutated by _apply_model_extension
    back to their pre-extension state, purges extension BLL modules from
    sys.modules so their @extension_model decorators re-register cleanly
    on next import, and clears the extension registry.

    WARNING: This should only be used in tests!
    """
    import sys

    from serverframework.lib.Logging import logger

    global _EXTENSION_REGISTRY_COMPAT, _MODEL_SNAPSHOTS

    # Restore mutated model classes to their pre-extension state
    for snap_key, snap in _MODEL_SNAPSHOTS.items():
        model_cls = snap["model_cls"]
        model_cls.__annotations__ = dict(snap["annotations"])
        model_cls.model_fields = dict(snap["model_fields"])
        # Remove class-level attributes that were added by extensions
        orig_attrs = set(snap["annotations"].keys())
        for attr in list(vars(model_cls)):
            if attr not in orig_attrs and not attr.startswith("_"):
                if attr in ("external_payment_id", "stripe_customer"):
                    try:
                        delattr(model_cls, attr)
                    except (AttributeError, TypeError):
                        pass
        try:
            model_cls.model_rebuild(force=True)
        except Exception:
            pass
        logger.debug(f"Restored model {snap_key} to pre-extension state")

    _MODEL_SNAPSHOTS.clear()
    _EXTENSION_REGISTRY_COMPAT.clear()


def prepare_test_registry() -> None:
    """Clear registry caches and extension model mutations before creating a test server.

    Every test server fixture must call this before ``instance()`` to prevent
    cross-test contamination (e.g. payment's @extension_model(UserModel)
    adding columns that other tests' isolated DBs don't have).
    """
    clear_registry_cache()
    reset_extension_system()
