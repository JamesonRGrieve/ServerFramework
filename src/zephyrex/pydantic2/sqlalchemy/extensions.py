from typing import Callable, Dict, List, Type

from pydantic import BaseModel

from zephyrex.pydantic2.sqlalchemy.builder import clear_registry_cache

# =============================================================================
# MODEL EXTENSION SYSTEM
# =============================================================================

# Backward compatibility registry for tracking extensions
# In the new architecture, this is only used for test compatibility
_EXTENSION_REGISTRY_COMPAT = {}  # type: ignore[var-annotated]

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
        extension_class._extension_target = target_model  # type: ignore[attr-defined]
        extension_class._is_extension_model = True  # type: ignore[attr-defined]

        # For backward compatibility, track in compatibility registry
        target_key = f"{target_model.__module__}.{target_model.__name__}"
        extension_key = f"{extension_class.__module__}.{extension_class.__name__}"

        if target_key not in _EXTENSION_REGISTRY_COMPAT:
            _EXTENSION_REGISTRY_COMPAT[target_key] = []

        if extension_key not in _EXTENSION_REGISTRY_COMPAT[target_key]:
            _EXTENSION_REGISTRY_COMPAT[target_key].append(extension_key)

        from zephyrex.lib.Logging import logger

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

            from zephyrex.extensions.CollisionDetection import (
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
    target_model._applied_extension_fields = set()  # type: ignore[attr-defined]


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
    from zephyrex.lib.Logging import logger

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
        target_model.model_fields = {}  # type: ignore[assignment]
    if not hasattr(target_model, "_applied_extension_fields"):
        target_model._applied_extension_fields = set()  # type: ignore[attr-defined]

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
            target_model._applied_extension_fields.add(field_name)  # type: ignore[attr-defined]

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

    from zephyrex.lib.Logging import logger

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
    from zephyrex.lib.Logging import logger

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
    from zephyrex.lib.Logging import logger

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
    from zephyrex.lib.Logging import logger

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
    # Also clear the process-wide GraphQL contribution registry: it accumulates
    # field/type/dataloader contributions as schemas are built, and a leftover
    # contribution from a prior test (e.g. an extension route like ``issue_route``)
    # poisons a later schema build with UnresolvedFieldTypeError under xdist.
    from zephyrex.pydantic2.strawberry import reset_gql_contribution_registry

    reset_gql_contribution_registry()
