"""Shared utilities for Pydantic model processing across different target systems."""

import hashlib
import re
from abc import ABC, abstractmethod
from enum import Enum
from functools import lru_cache
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Type,
    Union,
    get_args,
    get_origin,
)

import stringcase
from pydantic import BaseModel, ConfigDict

from serverframework.lib.Environment import inflection
from serverframework.lib.Logging import logger


class CacheManager:
    """Manages caching with consistent patterns across all Pydantic utilities."""

    def __init__(self):
        self._caches: Dict[str, Dict] = {}

    def get_cache(self, cache_name: str) -> Dict:
        return self._caches.setdefault(cache_name, {})

    def clear_cache(self, cache_name: str) -> None:
        self._caches.pop(cache_name, None)

    def clear_all_caches(self) -> None:
        self._caches.clear()


class NameProcessor:
    """Handles all name processing, sanitization, and generation operations."""

    @staticmethod
    @lru_cache(maxsize=1024)
    def sanitize_name(name: str, reserved_names: Optional[frozenset] | None = None) -> str:
        if not name:
            return "UnnamedType"

        # Remove angle brackets and replace non-alphanumeric with underscore
        sanitized = re.sub(r"[<>]", "", name)
        sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", sanitized)

        # Ensure starts with letter or underscore
        if sanitized and not (sanitized[0].isalpha() or sanitized[0] == "_"):
            sanitized = f"_{sanitized}"

        # Handle reserved names
        if reserved_names and sanitized.lower() in reserved_names:
            sanitized = f"custom_{sanitized}"

        return sanitized or "UnnamedType"

    @staticmethod
    @lru_cache(maxsize=1024)
    def extract_base_name(
        class_name: str, remove_suffixes: Optional[tuple] | None = None
    ) -> str:
        if not remove_suffixes:
            remove_suffixes = ("Manager", "Model")
        for suffix in remove_suffixes:
            if class_name.endswith(suffix):
                return class_name[: -len(suffix)] or class_name
        return class_name

    @staticmethod
    @lru_cache(maxsize=1024)
    def generate_resource_name(class_name: str, use_plural: bool = True) -> str:
        base_name = NameProcessor.extract_base_name(class_name)
        snake_case_name = stringcase.snakecase(base_name)
        return inflection.plural(snake_case_name) if use_plural else snake_case_name

    @staticmethod
    def generate_unique_name(
        base_name: str,
        existing_names: Set[str],
        context: Optional[str] | None = None,
        use_hash: bool = True,
    ) -> str:
        if base_name not in existing_names:
            return base_name

        # Try context-based naming
        if context:
            # Extract clean context name
            context_parts = context.split(".")
            last_part = context_parts[-1]

            # Remove common prefixes
            for prefix in ["BLL_", "DB_", "EP_", "EXT_"]:
                if last_part.startswith(prefix):
                    last_part = last_part[len(prefix) :]
                    break

            # Try module-based prefix for nested classes
            if len(context_parts) > 1 and "<locals>" not in context:
                module_prefix = context_parts[-2].replace("Model", "")
                prefixed_name = f"{stringcase.pascalcase(module_prefix)}{base_name}"
                if prefixed_name not in existing_names:
                    return prefixed_name

            # Try simple context prefix
            context_name = f"{stringcase.pascalcase(last_part)}{base_name}"
            if context_name not in existing_names:
                return context_name

        # Try hash-based naming if enabled
        if use_hash:
            context_hash = (
                hashlib.md5((context or base_name).encode()).hexdigest()[:4].upper()
            )
            hash_name = f"{base_name}{context_hash}"
            if hash_name not in existing_names:
                return hash_name

        # Fall back to numeric suffix
        counter = 1
        while f"{base_name}{counter}" in existing_names:
            counter += 1
        return f"{base_name}{counter}"

    @staticmethod
    @lru_cache(maxsize=512)
    def handle_nested_class_name(qualname: str) -> str:
        if "." not in qualname:
            return qualname

        parts = qualname.split(".")
        if len(parts) < 2:
            return qualname

        parent_name, child_name = parts[-2], parts[-1]

        if "<locals>" in parent_name:
            parent_name = (
                parent_name.split(".")[-1] if "." in parent_name else child_name
            )
            child_name = "" if parent_name == child_name else child_name

        parent_name = NameProcessor.extract_base_name(parent_name)
        return f"{parent_name}{child_name}" if child_name else parent_name


class TypeIntrospector:
    """Handles type introspection and analysis operations."""

    SCALAR_TYPES = frozenset({str, int, float, bool, dict, list})

    def __init__(self, cache_manager: Optional[CacheManager] | None = None):
        self.cache_manager = cache_manager or CacheManager()
        self._cache = self.cache_manager.get_cache("type_introspection")

    @lru_cache(maxsize=1024)
    def is_scalar_type(self, field_type: Any) -> bool:
        if field_type in self.SCALAR_TYPES:
            return True
        if get_origin(field_type) is Union:
            args = get_args(field_type)
            return (
                len(args) == 2
                and type(None) in args
                and self.is_scalar_type(
                    next(arg for arg in args if arg is not type(None))
                )
            )
        return False

    @lru_cache(maxsize=1024)
    def is_optional_type(self, field_type: Any) -> bool:
        return get_origin(field_type) is Union and type(None) in get_args(field_type)

    def extract_optional_inner_type(self, field_type: Any) -> Any:
        return (
            next(
                (arg for arg in get_args(field_type) if arg is not type(None)),
                field_type,
            )
            if self.is_optional_type(field_type)
            else field_type
        )

    @lru_cache(maxsize=1024)
    def is_list_type(self, field_type: Any) -> bool:
        return get_origin(field_type) in (list, List)

    def extract_list_inner_type(self, field_type: Any) -> Optional[Any]:
        return (
            get_args(field_type)[0]
            if self.is_list_type(field_type) and get_args(field_type)
            else None
        )

    @lru_cache(maxsize=1024)
    def is_dict_type(self, field_type: Any) -> bool:
        return get_origin(field_type) in (dict, Dict)

    @lru_cache(maxsize=1024)
    def is_union_type(self, field_type: Any) -> bool:
        return get_origin(field_type) is Union

    @lru_cache(maxsize=1024)
    def is_enum_type(self, field_type: Any) -> bool:
        try:
            return isinstance(field_type, type) and issubclass(field_type, Enum)
        except (TypeError, AttributeError):
            return False

    def is_pydantic_model(self, field_type: Any) -> bool:
        try:
            return (
                isinstance(field_type, type)
                and issubclass(field_type, BaseModel)
                and field_type is not BaseModel
            )
        except (TypeError, AttributeError):
            return False

    @staticmethod
    def get_type_name(type_: Any) -> str:
        return getattr(type_, "__name__", str(type_).replace("typing.", ""))


class FieldProcessor:
    """Handles field processing operations for Pydantic models."""

    READONLY_FIELDS = frozenset(
        {
            "id",
            "created_at",
            "updated_at",
            "created_by_user_id",
            "updated_by_user_id",
            "deleted_at",
            "deleted_by_user_id",
        }
    )

    def __init__(self, cache_manager: Optional[CacheManager] | None = None):
        self.cache_manager = cache_manager or CacheManager()
        self.type_introspector = TypeIntrospector(cache_manager)
        self._cache = self.cache_manager.get_cache("model_fields")

    def get_model_fields(
        self,
        model: Type[BaseModel],
        include_inherited: bool = True,
        process_refs: bool = False,
    ) -> Dict[str, Any]:
        model_fields_cache_key = (
            f"{model.__module__}.{model.__name__}_{include_inherited}_{process_refs}"
        )
        if model_fields_cache_key in self._cache:
            return self._cache[model_fields_cache_key]

        fields = {}
        if hasattr(model, "model_fields"):
            fields = {
                name: info.annotation
                for name, info in model.model_fields.items()
                if not name.startswith("_")
            }
        elif include_inherited:
            for cls in reversed(model.__mro__):
                if hasattr(cls, "__annotations__"):
                    fields.update(
                        {
                            name: type_
                            for name, type_ in cls.__annotations__.items()
                            if not name.startswith("_")
                        }
                    )

        self._cache[model_fields_cache_key] = fields
        return fields

    def extract_field_info(
        self, field_name: str, field_type: Any, field_info: Any | None = None
    ) -> Dict[str, Any]:
        info = {
            "name": field_name,
            "type": field_type,
            "is_optional": self.type_introspector.is_optional_type(field_type),
            "is_scalar": self.type_introspector.is_scalar_type(field_type),
            "is_list": self.type_introspector.is_list_type(field_type),
            "is_pydantic_model": self.type_introspector.is_pydantic_model(field_type),
            "inner_type": (
                self.type_introspector.extract_optional_inner_type(field_type)
                if self.type_introspector.is_optional_type(field_type)
                else field_type
            ),
        }

        if info["is_list"]:
            info["list_inner_type"] = self.type_introspector.extract_list_inner_type(
                field_type
            )

        if field_info:
            metadata = {}

            if hasattr(field_info, "description") and field_info.description:
                metadata["description"] = field_info.description
            elif hasattr(field_info, "json_schema_extra") and isinstance(
                field_info.json_schema_extra, dict
            ):
                metadata["description"] = field_info.json_schema_extra.get(
                    "description"
                )

            if hasattr(field_info, "default") and field_info.default is not ...:
                metadata["default"] = field_info.default
            elif hasattr(field_info, "default_factory") and field_info.default_factory:
                try:
                    metadata["default"] = field_info.default_factory()
                except Exception:
                    pass

            if getattr(field_info, "unique", False) or (
                hasattr(field_info, "json_schema_extra")
                and isinstance(field_info.json_schema_extra, dict)
                and field_info.json_schema_extra.get("unique")
            ):
                metadata["unique"] = True

            info.update(metadata)

        return info

    def should_skip_field(
        self, field_name: str, field_type: Any | None = None, context: str | None = None
    ) -> bool:
        return (
            field_name.startswith("_")
            or (
                context in ("input", "create", "update")
                and field_name in self.READONLY_FIELDS
            )
            or (
                context == "db"
                and field_type
                and (
                    self.type_introspector.is_pydantic_model(field_type)
                    or (
                        self.type_introspector.is_list_type(field_type)
                        and self.type_introspector.is_pydantic_model(
                            self.type_introspector.extract_list_inner_type(field_type)
                        )
                    )
                )
            )
        )

    def filter_fields_for_context(
        self, fields: Dict[str, Any], context: str, exclude_readonly: bool = False
    ) -> Dict[str, Any]:
        """Filter fields based on context using centralized skip logic."""
        return {
            k: v
            for k, v in fields.items()
            if not self.should_skip_field(k, v, context if exclude_readonly else None)
        }

    def clean_model_for_fastapi(self, model_class: Type[BaseModel]) -> Type[BaseModel]:
        if not hasattr(model_class, "model_fields"):
            return model_class

        clean_fields = {}
        undefined_field_names = []

        for field_name, field_info in model_class.model_fields.items():
            try:
                annotation_str = str(field_info.annotation)
                if (
                    "PydanticUndefinedType" in annotation_str
                    or str(type(field_info.annotation))
                    == "<class 'pydantic_core._pydantic_core.PydanticUndefinedType'>"
                ):
                    undefined_field_names.append(field_name)
                    logger.warning(
                        f"FOUND UNDEFINED FIELD: {field_name} in {model_class.__name__} -> {annotation_str}"
                    )
                else:
                    clean_fields[field_name] = field_info
            except Exception as e:
                logger.warning(
                    f"Skipping problematic field {field_name} in {model_class.__name__}: {e}"
                )
                undefined_field_names.append(field_name)

        if not undefined_field_names:
            return model_class

        logger.warning(
            f"MODEL HAS UNDEFINED FIELDS: {model_class.__name__} has {len(undefined_field_names)} undefined fields: {undefined_field_names}. "
            f"These fields will be removed from the FastAPI schema."
        )

        clean_class = type(
            f"Clean{model_class.__name__}",
            (BaseModel,),
            {
                "__annotations__": {
                    name: info.annotation for name, info in clean_fields.items()
                },
                "__module__": model_class.__module__,
                "model_config": getattr(model_class, "model_config", ConfigDict()),
                **{
                    attr: getattr(model_class, attr)
                    for attr in dir(model_class)
                    if not attr.startswith("_")
                    and attr not in ["model_fields", "model_config"]
                    and (
                        hasattr(getattr(model_class, attr), "__annotations__")
                        or callable(getattr(model_class, attr))
                    )
                },
            },
        )

        return clean_class


class ReferenceResolver:
    """Handles forward reference resolution and model lookups."""

    def __init__(self, cache_manager: Optional[CacheManager] | None = None):
        self.cache_manager = cache_manager or CacheManager()
        self._model_registry: Dict[str, Type[BaseModel]] = {}
        self._cache = self.cache_manager.get_cache("string_refs")

    def register_model(
        self, model: Type[BaseModel], name: Optional[str] | None = None
    ) -> None:
        if name:
            model_name = name.lower()
        else:
            class_name = model.__name__
            model_name = stringcase.snakecase(
                class_name.split("For")[1].lower()
                if class_name.startswith("Model") and "For" in class_name
                else NameProcessor.extract_base_name(class_name)
            )

        self._model_registry[model_name] = model

        while "_" in model_name:
            model_name = model_name.split("_", 1)[1]
            if model_name and model_name not in self._model_registry:
                self._model_registry[model_name] = model

    def find_model_by_name(self, name: str) -> Optional[Type[BaseModel]]:
        normalized = name.lower()

        if normalized in self._model_registry:
            return self._model_registry[normalized]

        singular = inflection.singular_noun(normalized) or normalized
        if singular in self._model_registry:
            return self._model_registry[singular]

        return next(
            (
                model
                for model_name, model in self._model_registry.items()
                if model_name in normalized or normalized in model_name
            ),
            None,
        )

    def resolve_string_reference(
        self, ref_str: str, module_context=None
    ) -> Optional[Type]:
        string_ref_cache_key = (
            f"{ref_str}:{module_context.__name__ if module_context else 'None'}"
        )
        if string_ref_cache_key in self._cache:
            return self._cache[string_ref_cache_key]

        clean_ref = ref_str.strip("\"'")
        lower_name = stringcase.snakecase(NameProcessor.extract_base_name(clean_ref))

        if lower_name in self._model_registry:
            result = self._model_registry[lower_name]
        elif module_context:
            if hasattr(module_context, clean_ref):
                result = getattr(module_context, clean_ref)
            else:
                base_name = NameProcessor.extract_base_name(clean_ref)
                for variant in (
                    [base_name, f"{base_name}Model"]
                    if clean_ref.endswith("Model")
                    else [f"{clean_ref}Model", clean_ref]
                ):
                    if hasattr(module_context, variant):
                        result = getattr(module_context, variant)
                        break
                else:
                    result = None
        else:
            result = None

        self._cache[string_ref_cache_key] = result
        return result


# Shared instances
default_cache_manager = CacheManager()
default_name_processor = NameProcessor()
default_type_introspector = TypeIntrospector(default_cache_manager)
default_field_processor = FieldProcessor(default_cache_manager)
default_reference_resolver = ReferenceResolver(default_cache_manager)


def clear_all_caches():
    """Clear all shared caches."""
    default_cache_manager.clear_all_caches()
    # Clear lru_cache decorators
    NameProcessor.sanitize_name.cache_clear()
    NameProcessor.extract_base_name.cache_clear()
    NameProcessor.generate_resource_name.cache_clear()
    NameProcessor.handle_nested_class_name.cache_clear()
    TypeIntrospector.is_scalar_type.cache_clear()
    TypeIntrospector.is_optional_type.cache_clear()
    TypeIntrospector.is_list_type.cache_clear()


# ===== ABSTRACT BASE CLASSES AND MIXINS =====


class ModelDiscoveryMixin:
    """Provides model discovery functionality for converters."""

    def discover_models_in_modules(
        self, modules: List[str], file_type: str = "BLL"
    ) -> Dict[str, Type]:
        """
        Discover models in the specified modules.

        Args:
            modules: List of module names to search
            file_type: Type of files to search (BLL, DB, EP, etc.)

        Returns:
            Dict mapping model names to model classes
        """
        discovered = {}

        # Use model_registry if available for scoped import
        if hasattr(self, "model_registry") and hasattr(
            self.model_registry, "_scoped_import"
        ):
            imported_modules, import_errors = self.model_registry._scoped_import(
                file_type=file_type, scopes=modules
            )

            if import_errors:
                logger.debug(f"Import errors during model discovery: {import_errors}")

            # Process imported modules
            for module_name in imported_modules:
                try:
                    import sys

                    module = sys.modules[module_name]
                    discovered.update(
                        self._extract_models_from_module(module, module_name)
                    )
                except Exception as e:
                    logger.debug(f"Error processing module {module_name}: {e}")

        return discovered

    def _extract_models_from_module(self, module, module_name: str) -> Dict[str, Type]:
        """Extract model classes from a module."""
        models = {}

        for attr_name in dir(module):
            if attr_name.startswith("_"):
                continue

            attr = getattr(module, attr_name)
            if self._is_valid_model_class(attr, attr_name):
                models[attr_name] = attr
                # Also store with base name
                base_name = NameProcessor.extract_base_name(attr_name)
                if base_name != attr_name:
                    models[base_name] = attr

        return models

    def _is_valid_model_class(self, attr: Any, attr_name: str) -> bool:
        """Check if an attribute is a valid model class."""
        import inspect

        return (
            inspect.isclass(attr)
            and attr_name.endswith("Model")
            and issubclass(attr, BaseModel)
            and attr != BaseModel
        )


class ErrorHandlerMixin:
    """Provides consistent error handling across converters."""

    def safe_operation(
        self,
        operation: Callable,
        item_name: str,
        fallback: Any | None = None,
        strict: bool = False,
        log_success: bool = False,
    ) -> Any:
        """
        Execute an operation with consistent error handling.

        Args:
            operation: Function to execute
            item_name: Name of the item being processed (for logging)
            fallback: Value to return on error (if not strict)
            strict: Whether to raise exceptions or return fallback
            log_success: Whether to log successful operations

        Returns:
            Operation result or fallback value
        """
        try:
            result = operation()
            if log_success:
                logger.debug(f"Successfully processed {item_name}")
            return result
        except Exception as e:
            logger.error(f"Failed to process {item_name}: {e}")
            if strict:
                raise
            return fallback

    def batch_safe_operation(
        self, operations: Dict[str, Callable], strict: bool = False
    ) -> Dict[str, Any]:
        """
        Execute multiple operations with error handling.

        Args:
            operations: Dict mapping operation names to callables
            strict: Whether to stop on first error

        Returns:
            Dict mapping operation names to results
        """
        results = {}

        for name, operation in operations.items():
            try:
                results[name] = operation()
                logger.debug(f"Successfully completed {name}")
            except Exception as e:
                logger.error(f"Failed to complete {name}: {e}")
                if strict:
                    raise
                results[name] = None

        return results


class RelationshipAnalyzer:
    """Analyzes relationships between Pydantic models."""

    def __init__(self, cache_manager: CacheManager):
        self.cache_manager = cache_manager
        self._relationship_cache = cache_manager.get_cache("relationships")

    def analyze_model_relationships(self, model: Type[BaseModel]) -> Dict[str, Any]:
        """
        Analyze relationships for a model.

        Args:
            model: The Pydantic model to analyze

        Returns:
            Dict containing relationship information
        """
        model_relationships_cache_key = f"{model.__module__}.{model.__name__}"

        if model_relationships_cache_key in self._relationship_cache:
            return self._relationship_cache[model_relationships_cache_key]

        relationships = {
            "foreign_keys": self._find_foreign_keys(model),
            "references": self._find_model_references(model),
            "collections": self._find_collection_fields(model),
            "reverse_relationships": self._find_reverse_relationships(model),
        }

        self._relationship_cache[model_relationships_cache_key] = relationships
        return relationships

    def _find_foreign_keys(self, model: Type[BaseModel]) -> List[Dict[str, str]]:
        """Find foreign key fields (fields ending with _id)."""
        foreign_keys = []

        for field_name, field_info in getattr(model, "model_fields", {}).items():
            if field_name.endswith("_id") and field_name != "id":
                related_name = field_name.removesuffix("_id")
                foreign_keys.append(
                    {
                        "field_name": field_name,
                        "related_name": related_name,
                        "field_type": str(field_info.annotation),
                    }
                )

        return foreign_keys

    def _find_model_references(self, model: Type[BaseModel]) -> List[Dict[str, Any]]:
        """Find direct references to other Pydantic models."""
        references = []

        for field_name, field_info in getattr(model, "model_fields", {}).items():
            field_type = field_info.annotation

            # Handle Optional[SomeModel]
            if get_origin(field_type) is Union:
                args = get_args(field_type)
                if len(args) == 2 and type(None) in args:
                    field_type = next(arg for arg in args if arg is not type(None))

            # Check if it's a Pydantic model
            if (
                isinstance(field_type, type)
                and issubclass(field_type, BaseModel)
                and field_type != model
            ):
                references.append(
                    {
                        "field_name": field_name,
                        "model_class": field_type,
                        "is_optional": get_origin(field_info.annotation) is Union,
                    }
                )

        return references

    def _find_collection_fields(self, model: Type[BaseModel]) -> List[Dict[str, Any]]:
        """Find fields that contain collections of other models."""
        collections = []

        for field_name, field_info in getattr(model, "model_fields", {}).items():
            field_type = field_info.annotation

            # Check for List[SomeModel]
            if get_origin(field_type) in (list, List):
                args = get_args(field_type)
                if (
                    args
                    and isinstance(args[0], type)
                    and issubclass(args[0], BaseModel)
                ):
                    collections.append(
                        {
                            "field_name": field_name,
                            "item_model": args[0],
                            "is_list": True,
                        }
                    )

        return collections

    def _find_reverse_relationships(self, model: Type[BaseModel]) -> List[str]:
        """Find potential reverse relationships (fields that might reference this model)."""
        # This would require analyzing other models in the registry
        # For now, return empty list - could be enhanced later
        return []

    def extract_relationship_info(
        self, field_name: str, field_type: Any, model: Type[BaseModel] | None = None
    ) -> Dict[str, Any]:
        """Extract comprehensive relationship information from a field."""
        info = {
            "field_name": field_name,
            "field_type": field_type,
            "relationship_type": None,
            "target_model": None,
            "is_optional": False,
            "is_collection": False,
            "foreign_key_field": None,
        }

        # Check if it's an Optional type
        if get_origin(field_type) is Union:
            args = get_args(field_type)
            if len(args) == 2 and type(None) in args:
                info["is_optional"] = True
                field_type = next(arg for arg in args if arg is not type(None))

        # Check if it's a List type
        if get_origin(field_type) in (list, List):
            info["is_collection"] = True
            info["relationship_type"] = "one_to_many"
            args = get_args(field_type)
            if args:
                field_type = args[0]

        # Check if it's a foreign key field
        if field_name.endswith("_id") and field_name != "id":
            info["relationship_type"] = "foreign_key"
            info["foreign_key_field"] = field_name
            # Try to find corresponding object field
            base_name = field_name.removesuffix("_id")
            if (
                model
                and hasattr(model, "model_fields")
                and base_name in model.model_fields
            ):
                obj_field_type = model.model_fields[base_name].annotation
                if get_origin(obj_field_type) is Union:
                    args = get_args(obj_field_type)
                    if len(args) == 2 and type(None) in args:
                        obj_field_type = next(
                            arg for arg in args if arg is not type(None)
                        )
                if isinstance(obj_field_type, type) and issubclass(
                    obj_field_type, BaseModel
                ):
                    info["target_model"] = obj_field_type

        # Check if it's a direct model reference
        elif isinstance(field_type, type) and issubclass(field_type, BaseModel):
            info["relationship_type"] = (
                "many_to_one" if not info["is_collection"] else "one_to_many"
            )
            info["target_model"] = field_type

        return info


class TemplateGenerator:
    """Generates code templates for different target systems."""

    def __init__(self, name_processor: NameProcessor | None = None):
        self.name_processor = name_processor or default_name_processor

    def generate_class_template(
        self,
        model: Type[BaseModel],
        template_type: str,
        base_classes: List[str] | None = None,
        additional_imports: List[str] | None = None,
    ) -> str:
        """
        Generate a class template for a model.

        Args:
            model: The Pydantic model
            template_type: Type of template (manager, handler, etc.)
            base_classes: List of base class names
            additional_imports: Additional import statements

        Returns:
            Generated template string
        """
        model_name = model.__name__
        base_name = self.name_processor.extract_base_name(model_name)
        class_name = f"{base_name}{template_type.title()}"

        # Generate imports
        imports = self._generate_imports(model, additional_imports or [])

        # Generate class definition
        bases = ", ".join(base_classes or ["object"])
        class_def = f"class {class_name}({bases}):"

        # Generate docstring
        docstring = f'    """{class_name} for {model_name} operations."""'

        # Generate basic attributes
        attributes = self._generate_class_attributes(model, template_type)

        # Generate __init__ method
        init_method = self._generate_init_method(template_type)

        # Combine all parts
        template_parts = [
            imports,
            "",
            class_def,
            docstring,
            "",
            attributes,
            "",
            init_method,
        ]

        return "\n".join(template_parts)

    def _generate_imports(self, model: Type[BaseModel], additional: List[str]) -> str:
        """Generate import statements."""
        base_imports = [
            "from typing import Any, Dict, List, Optional",
            f"from {model.__module__} import {model.__name__}",
        ]

        all_imports = base_imports + additional
        return "\n".join(all_imports)

    def _generate_class_attributes(
        self, model: Type[BaseModel], template_type: str
    ) -> str:
        """Generate class attributes."""
        model_name = model.__name__
        base_name = self.name_processor.extract_base_name(model_name)

        attributes = [f"    Model = {model_name}"]

        # NetworkModel is resolved at runtime from ``Model.NetworkModel`` via the
        # registry (see ``AbstractBLLManager.__init__``); it is not a module-level
        # class, so we don't emit a static binding for it here.
        if template_type == "manager":
            attributes.append(f"    ReferenceModel = {base_name}ReferenceModel")

        return "\n".join(attributes)

    def _generate_init_method(self, template_type: str) -> str:
        """Generate __init__ method."""
        if template_type == "manager":
            return '''    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] | None = None,
        target_team_id: Optional[str] | None = None,
        model_registry: Optional[Any] | None = None,
    ):
        """Initialize the manager."""
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )'''
        else:
            return '''    def __init__(self):
        """Initialize the class."""
        super().__init__()'''


class AbstractPydanticConverter(ABC):
    """
    Abstract base class for all Pydantic model converters.

    This class provides a common interface and shared functionality for
    converting Pydantic models to different target representations
    (SQLAlchemy, FastAPI, GraphQL, etc.).
    """

    def __init__(self, model_registry=None):
        """Initialize the converter with shared utilities."""
        # Import here to avoid circular imports
        from serverframework.lib.Pydantic import ModelRegistry

        self.model_registry = model_registry or ModelRegistry()
        self.cache_manager = getattr(
            model_registry, "cache_manager", default_cache_manager
        )
        self.name_processor = default_name_processor
        self.type_introspector = default_type_introspector
        self.field_processor = default_field_processor
        self.reference_resolver = default_reference_resolver
        self.relationship_analyzer = RelationshipAnalyzer(self.cache_manager)
        self.template_generator = TemplateGenerator(self.name_processor)

        # Converter-specific cache
        self._conversion_cache = self.cache_manager.get_cache(
            f"{self.__class__.__name__.lower()}_conversions"
        )

    @abstractmethod
    def convert_model(self, model: Type[BaseModel], **options) -> Any:
        """
        Convert a Pydantic model to the target representation.

        Args:
            model: The Pydantic model to convert
            **options: Converter-specific options

        Returns:
            The converted representation
        """
        pass

    @abstractmethod
    def get_target_type_mapping(self) -> Dict[Type, Any]:
        """
        Get mapping from Python types to target system types.

        Returns:
            Dict mapping Python types to target types
        """
        pass

    def extract_model_metadata(self, model: Type[BaseModel]) -> Dict[str, Any]:
        """
        Extract common metadata from a Pydantic model.

        Args:
            model: The Pydantic model

        Returns:
            Dict containing model metadata
        """
        return {
            "name": model.__name__,
            "base_name": self.name_processor.extract_base_name(model.__name__),
            "module": model.__module__,
            "fields": self.field_processor.get_model_fields(model),
            "relationships": self.relationship_analyzer.analyze_model_relationships(
                model
            ),
            "docstring": model.__doc__,
            "config": getattr(model, "model_config", None),
        }

    def cached_conversion(
        self, model: Type[BaseModel], conversion_func: Callable, **options
    ) -> Any:
        """
        Perform a cached conversion operation.

        Args:
            model: The model to convert
            conversion_func: The conversion function
            **options: Additional options for caching key

        Returns:
            Conversion result
        """
        # Create cache key from model and options
        model_conversion_cache_key = (
            f"{model.__module__}.{model.__name__}:{hash(frozenset(options.items()))}"
        )

        if model_conversion_cache_key in self._conversion_cache:
            return self._conversion_cache[model_conversion_cache_key]

        result = conversion_func()
        self._conversion_cache[model_conversion_cache_key] = result
        return result

    def validate_model_compatibility(self, model: Type[BaseModel]) -> List[str]:
        """
        Validate that a model is compatible with this converter.

        Args:
            model: The model to validate

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        if not issubclass(model, BaseModel):
            errors.append(f"{model.__name__} is not a Pydantic BaseModel")

        # Check for required fields/attributes based on converter type
        required_attributes = self._get_required_model_attributes()
        for attr in required_attributes:
            if not hasattr(model, attr):
                errors.append(f"Missing required attribute: {attr}")

        return errors

    def _get_required_model_attributes(self) -> List[str]:
        """Get list of required model attributes. Override in subclasses."""
        return []


# Enhanced mixin versions that inherit from the abstract base
class CacheManagerMixin:
    """Provides consistent caching functionality."""

    def get_cache(self, cache_name: str) -> Dict:
        """Get a named cache."""
        if hasattr(self, "cache_manager"):
            return self.cache_manager.get_cache(cache_name)
        elif hasattr(self, "model_registry") and hasattr(
            self.model_registry, "cache_manager"
        ):
            return self.model_registry.cache_manager.get_cache(cache_name)
        else:
            return default_cache_manager.get_cache(cache_name)

    def cached_operation(self, cache_name: str, key: str, operation: Callable) -> Any:
        """Perform a cached operation."""
        cache = self.get_cache(cache_name)
        if key not in cache:
            cache[key] = operation()
        return cache[key]
