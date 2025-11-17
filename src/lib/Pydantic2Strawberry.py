import json
import sys
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum, IntEnum
from typing import (
    Any,
    AsyncGenerator,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    Union,
    get_args,
    get_origin,
)

from types import ModuleType

import strawberry
import stringcase
from broadcaster import Broadcast
from pydantic import BaseModel
from strawberry.types import Info

from lib.AbstractPydantic2 import (
    TypeIntrospector,
    CacheManager,
    RelationshipAnalyzer,
    ErrorHandlerMixin,
)
from lib.Environment import inflection
from lib.Logging import logger
from lib.Pydantic import ModelRegistry
from logic.AbstractLogicManager import AbstractBLLManager


def enum_serializer(value: Any) -> str:
    """Serialize enum values to their string representation"""
    if hasattr(value, "name"):
        return value.name
    elif hasattr(value, "value"):
        return value.value
    return str(value)


# Configure GraphQL scalar types
@strawberry.scalar(
    description="DateTime scalar",
    serialize=lambda v: v.isoformat() if v else None,
    parse_value=lambda v: datetime.fromisoformat(v) if v else None,
)
class DateTimeScalar:
    pass


@strawberry.scalar(
    description="Date scalar",
    serialize=lambda v: v.isoformat() if v else None,
    parse_value=lambda v: date.fromisoformat(v) if v else None,
)
class DateScalar:
    pass


# Define scalar types for complex data
@strawberry.scalar(
    description="Any JSON-serializable value",
    serialize=lambda v: (
        v
        if isinstance(v, str)
        else (
            enum_serializer(v)
            if hasattr(v, "name") or hasattr(v, "value")
            else json.dumps(v) if v is not None else None
        )
    ),
    parse_value=lambda v: (
        v if isinstance(v, str) else json.loads(v) if v is not None else None
    ),
)
class AnyScalar:
    pass


ANY_SCALAR = AnyScalar


@strawberry.scalar(
    description="JSON object",
    serialize=lambda v: json.dumps(v) if v is not None else None,
    parse_value=lambda v: json.loads(v) if v is not None else None,
)
class DictScalar:
    pass


DICT_SCALAR = DictScalar


@strawberry.scalar(
    description="JSON array",
    serialize=lambda v: json.dumps(v) if v is not None else None,
    parse_value=lambda v: json.loads(v) if v is not None else None,
)
class ListScalar:
    pass


LIST_SCALAR = ListScalar

# Remove generic type - not needed

# Map Python types to GraphQL scalar types
TYPE_MAPPING = {
    str: strawberry.scalar(
        str,
        description="String value",
        serialize=lambda v: v if v is not None else None,
        parse_value=lambda v: v if v is not None else None,
    ),
    int: strawberry.scalar(int, description="Integer value"),
    float: strawberry.scalar(float, description="Float value"),
    bool: strawberry.scalar(bool, description="Boolean value"),
    datetime: DateTimeScalar,
    date: DateScalar,
    dict: DICT_SCALAR,
    list: LIST_SCALAR,
    Any: ANY_SCALAR,
}


# Create a shared type introspector instance
_type_introspector: TypeIntrospector = TypeIntrospector()


def convert_field_name(
    field_name: Optional[str], use_camelcase: bool = True
) -> Optional[str]:
    """Convert field names to camelCase."""
    if field_name is None:
        return None
    if field_name in ["id", "__typename"]:
        return field_name
    return stringcase.camelcase(field_name)


@dataclass
class ModelInfo:
    """Information about a model and its relationships"""

    model_class: Type[BaseModel]
    ref_model_class: Type[BaseModel]
    network_model_class: Type[BaseModel]
    manager_class: Type[AbstractBLLManager]
    gql_type: Optional[Type] = None
    plural_name: str = ""
    singular_name: str = ""


# Removed FilterTypeGenerator - functionality moved to GraphQLManager


# Removed TypeGenerator - functionality moved to GraphQLManager


# Removed BatchResultGenerator - functionality moved to GraphQLManager


# Removed ResolverGenerator - functionality moved to GraphQLManager


class GraphQLManager(ErrorHandlerMixin):
    """Main GraphQL schema manager that generates schemas from ModelRegistry"""

    def __init__(self, model_registry: ModelRegistry) -> None:
        """Initialize SchemaManager with ModelRegistry"""
        if not model_registry:
            raise ValueError("ModelRegistry instance is required")

        self.model_registry = model_registry
        self.broadcast = Broadcast("memory://")

        # Legacy generator references for backward compatibility with tests
        class MockGenerator:
            def _convert_filter_to_search_params(
                self, filter_obj: Optional[Any]
            ) -> Dict[str, Any]:
                return {} if filter_obj is None else {}

            def _extract_nested_data(self, data_dict: Dict[str, Any]) -> Dict[str, Any]:
                if not data_dict:
                    return {}
                # Extract nested fields (fields with dict/list values)
                nested_data: Dict[str, Any] = {}
                keys_to_remove: List[str] = []
                for key, value in data_dict.items():
                    if isinstance(value, (dict, list)):
                        nested_data[key] = value
                        keys_to_remove.append(key)
                # Remove nested fields from original dict
                for key in keys_to_remove:
                    del data_dict[key]
                return nested_data

        self.filter_generator = MockGenerator()
        self.type_generator = MockGenerator()
        self.resolver_generator = MockGenerator()
        self.batch_result_generator = MockGenerator()

        # Schema caches
        self._query_fields: Dict[str, Any] = {}
        self._mutation_fields: Dict[str, Any] = {}
        self._subscription_fields: Dict[str, Any] = {}

        # Type registry to ensure types are created only once
        self._type_registry: Dict[Type[BaseModel], Type] = (
            {}
        )  # model_class -> GraphQL type
        self._input_type_registry: Dict[Tuple[Type[BaseModel], str], Type] = (
            {}
        )  # (model_class, suffix) -> GraphQL input type

        # Add relationship tracking
        self._forward_relationships: Dict[
            Type[BaseModel], Dict[str, Type[BaseModel]]
        ] = {}  # model -> {field: target_model}
        self._reverse_relationships: Dict[
            Type[BaseModel], Dict[str, Tuple[Type[BaseModel], str]]
        ] = {}  # model -> {field: (source_model, source_field)}
        self._analyzed_models: Set[Type[BaseModel]] = set()

        # Track types currently being created to prevent infinite recursion
        self._types_being_created: Set[Type[BaseModel]] = set()

    def create_schema(self) -> strawberry.Schema:
        """Create complete GraphQL schema from ModelRegistry"""
        # Generate all types, queries, mutations, and subscriptions
        self._generate_all_components()

        # Create Query, Mutation, and Subscription types
        query_type = self._create_query_type()
        mutation_type = self._create_mutation_type()
        subscription_type = self._create_subscription_type()

        # Create schema
        schema = strawberry.Schema(
            query=query_type, mutation=mutation_type, subscription=subscription_type
        )

        return schema

    def _generate_all_components(self) -> None:
        """Generate all GraphQL components from models"""
        # Use model_relationships from the registry
        if (
            not hasattr(self.model_registry, "model_relationships")
            or not self.model_registry.model_relationships
        ):
            logger.warning("No model relationships found in registry")
            return

        successful_models: List[str] = []
        failed_models: List[Tuple[str, str, str]] = []

        for relationship in self.model_registry.model_relationships:
            # Each relationship is a tuple: (model_class, ref_model_class, network_model_class, manager_class)
            if len(relationship) >= 4:
                model_class, ref_model_class, network_model_class, manager_class = (
                    relationship[:4]
                )
                model_name = model_class.__name__ if model_class else "Unknown"
                try:
                    self._generate_components_for_model(model_class, manager_class)
                    successful_models.append(model_name)
                except Exception as e:
                    module_name = model_class.__module__ if model_class else "unknown"
                    failed_models.append((model_name, module_name, str(e)))
                    logger.error(
                        f"Failed to generate GraphQL components for {model_name} "
                        f"(module: {module_name}): {str(e)}. "
                        f"Continuing with other models..."
                    )
                    # Continue processing other models instead of failing completely

        # Log summary
        logger.info(
            f"GraphQL generation complete. "
            f"Successful: {len(successful_models)} models, "
            f"Failed: {len(failed_models)} models"
        )

        if failed_models:
            logger.error("Failed models:")
            for model_name, module_name, error in failed_models:
                logger.error(f"  - {model_name} ({module_name}): {error}")

    def _generate_components_for_model(
        self, model_class: Type[BaseModel], manager_class: Type[AbstractBLLManager]
    ) -> None:
        """Generate all components for a specific model class"""
        if not model_class or not manager_class:
            return

        # Skip extension models - they enhance existing types, don't create new ones
        if (
            hasattr(model_class, "_is_extension_model")
            and model_class._is_extension_model
        ):
            logger.debug(
                f"Skipping GraphQL generation for extension model {model_class.__name__} "
                f"(extends {getattr(model_class, '_extension_target', 'Unknown').__name__})"
            )
            return

        model_name = model_class.__name__
        logger.debug(f"Generating GraphQL components for {model_name}")

        # Strip "Model" suffix for GraphQL field names and convert to camelCase
        base_name = model_name.removesuffix("Model")
        model_name_camel = convert_field_name(base_name)
        model_name_plural = inflection.plural(model_name_camel)

        try:
            # Apply model registry to get extension-enhanced version
            # Handle both core managers (BaseModel) and extension managers (Model)
            base_model: Optional[Type[BaseModel]] = None
            if hasattr(manager_class, "BaseModel"):
                base_model = manager_class.BaseModel
            elif hasattr(manager_class, "Model"):
                base_model = manager_class.Model
            else:
                logger.error(
                    f"Manager {manager_class.__name__} has neither BaseModel nor Model attribute"
                )
                return

            try:
                registry_model = self.model_registry.apply(base_model)
            except Exception as e:
                logger.warning(
                    f"Model {base_model.__name__} not found in registry during GraphQL generation "
                    f"({e.__class__.__name__}: {e})"
                )
                logger.debug(
                    f"Available models in registry: {[m.__name__ for m in self.model_registry.bound_models]}"
                )

                # Fallback to the original model to ensure GraphQL fields are generated
                registry_model = base_model

            # Generate GraphQL types using standardized error handling
            type_operations: Dict[str, Callable[[], Type]] = {
                "graphql_type": lambda: self._create_gql_type_from_model(
                    registry_model
                ),
                "create_input": lambda: self._create_input_type_from_model(
                    registry_model, "Create"
                ),
                "update_input": lambda: self._create_input_type_from_model(
                    registry_model, "Update"
                ),
                "filter_input": lambda: self._create_filter_type_from_model(
                    registry_model
                ),
            }

            type_results: Dict[str, Type] = self.batch_safe_operation(
                type_operations, strict=True
            )
            gql_type: Type = type_results["graphql_type"]
            create_input: Type = type_results["create_input"]
            update_input: Type = type_results["update_input"]
            filter_input: Type = type_results["filter_input"]

            # Generate resolvers using safe operations
            self.safe_operation(
                lambda: (
                    self._add_query_resolver(model_name_camel, gql_type, manager_class),
                    self._add_list_query_resolver(
                        model_name_plural, gql_type, manager_class, filter_input
                    ),
                ),
                f"query resolvers for {model_name}",
                strict=True,
            )

            self.safe_operation(
                lambda: (
                    self._add_create_mutation_resolver(
                        f"create{base_name}", gql_type, manager_class, create_input
                    ),
                    self._add_update_mutation_resolver(
                        f"update{base_name}", gql_type, manager_class, update_input
                    ),
                    self._add_delete_mutation_resolver(
                        f"delete{base_name}", manager_class
                    ),
                ),
                f"mutation resolvers for {model_name}",
                strict=True,
            )

            self.safe_operation(
                lambda: (
                    self._add_subscription_resolver(
                        f"{model_name_camel}Created", model_name
                    ),
                    self._add_subscription_resolver(
                        f"{model_name_camel}Updated", model_name
                    ),
                    self._add_subscription_resolver(
                        f"{model_name_camel}Deleted", model_name
                    ),
                ),
                f"subscription resolvers for {model_name}",
                strict=True,
            )

            logger.log("SQL", f"Generated GraphQL components for {model_name}")

        except Exception as e:
            # Include module information for better debugging
            module_info = f" (module: {model_class.__module__})" if model_class else ""
            logger.error(
                f"Failed to generate components for {model_name}{module_info}: {e}"
            )
            # Re-raise to be caught by the outer try-catch for proper error isolation
            raise

    def _create_gql_type_from_model(self, model_class: Type[BaseModel]) -> Type:
        """Create a GraphQL type from a Pydantic model"""
        # Ensure we are using the registry-applied model (with extensions) when available
        try:
            if hasattr(self, "model_registry") and self.model_registry:
                apply_fn = getattr(self.model_registry, "apply", None)
                if callable(apply_fn):
                    try:
                        applied_model = apply_fn(model_class)
                        # Validate the returned object before using it
                        if isinstance(applied_model, type) and hasattr(
                            applied_model, "model_fields"
                        ):
                            model_class = applied_model
                    except Exception:
                        # If apply fails for any reason, continue with the original model
                        pass
        except Exception:
            # Defensive: if anything unexpected happens, ignore and proceed
            pass

        # Check if type already exists in registry
        if model_class in self._type_registry:
            return self._type_registry[model_class]

        # Check if we're already creating this type (prevent infinite recursion)
        if model_class in self._types_being_created:
            # Return a lazy type reference to break circular dependencies
            type_name = self._get_type_name_for_model(model_class)

            # Use strawberry.lazy to create a forward reference
            def get_type():
                # By the time this is called, the type should be in the registry
                if model_class in self._type_registry:
                    return self._type_registry[model_class]
                else:
                    # Fallback - create a minimal type
                    @strawberry.type
                    class MinimalType:
                        id: Optional[str] = None

                    MinimalType.__name__ = type_name
                    return MinimalType

            # Return a reference that will be resolved later
            # Instead of strawberry.lazy, return the actual type from registry
            return self._type_registry.get(model_class, ANY_SCALAR)

        # Mark this type as being created
        self._types_being_created.add(model_class)

        try:
            # Analyze relationships first
            self._analyze_model_relationships(model_class)

            # Debug log relationships for this model
            if model_class in self._reverse_relationships:
                logger.debug(
                    f"Reverse relationships for {model_class.__name__}: {list(self._reverse_relationships[model_class].keys())}"
                )

            base_name = model_class.__name__.removesuffix("Model")

            module_name = model_class.__module__ or __name__

            if module_name not in sys.modules:
                placeholder_module = ModuleType(module_name)
                sys.modules[module_name] = placeholder_module

            # Check if this is from an extension and add prefix to avoid collisions
            module_parts = module_name.split(".")
            if len(module_parts) > 1 and module_parts[0] == "extensions":
                # For extension models, prefix with the extension name
                extension_name = module_parts[1]
                type_name = f"{extension_name.title()}{base_name}Type"
            else:
                type_name = f"{base_name}Type"

            # Create field annotations for the class
            annotations: Dict[str, Type] = {}
            # Track field name mappings for resolvers (camelCase -> snake_case)
            field_name_mappings: Dict[str, str] = {}

            for field_name, field_info in model_class.model_fields.items():
                field_type = field_info.annotation

                # Debug logging for ActivityState field
                if "state" in field_name and "Activity" in model_class.__name__:
                    logger.debug(
                        f"Processing field {field_name} in {model_class.__name__}: "
                        f"field_type={field_type}, type={type(field_type)}, "
                        f"is_dict={isinstance(field_type, dict)}"
                    )

                gql_field_type = self._convert_python_type_to_gql(field_type)
                # Convert snake_case field names to camelCase for GraphQL (GraphQL convention)
                gql_field_name = convert_field_name(field_name, use_camelcase=True)
                annotations[gql_field_name] = gql_field_type
                # Store mapping for resolver if names differ
                if gql_field_name != field_name:
                    field_name_mappings[gql_field_name] = field_name

            # Add reverse navigation properties
            if model_class in self._reverse_relationships:
                for reverse_field_name, (
                    source_model,
                    source_field,
                ) in self._reverse_relationships[model_class].items():
                    # Add annotation for the reverse field using the lazy type
                    source_gql_type: Type = self._get_or_create_type(source_model)
                    annotations[reverse_field_name] = List[source_gql_type]

            # Always add at least one field to avoid empty type error
            if not annotations:
                annotations["_dummy"] = Optional[str]

            # Check if a type with this name already exists
            # This is a global registry to track all type names
            if not hasattr(self, "_global_type_names"):
                self._global_type_names: Dict[str, str] = {}

            if type_name in self._global_type_names:
                existing_model = self._global_type_names[type_name]
                current_model = f"{model_class.__module__}.{model_class.__name__}"

                if existing_model == current_model:
                    logger.debug(
                        f"Type '{type_name}' already created for model {existing_model}, returning existing type"
                    )
                    existing_type = self._type_registry.get(model_class)
                    if existing_type is None:
                        for registered_model, gql_type in self._type_registry.items():
                            if (
                                registered_model.__module__ == model_class.__module__
                                and registered_model.__name__ == model_class.__name__
                            ):
                                existing_type = gql_type
                                self._type_registry[model_class] = gql_type
                                break
                    if existing_type is not None:
                        return existing_type
                    logger.warning(
                        "Stale GraphQL type registry entry detected for %s; regenerating",
                        current_model,
                    )
                    del self._global_type_names[type_name]
                else:
                    # Different models with same name - need to make it unique
                    logger.warning(
                        f"Type name collision: '{type_name}' already exists for {existing_model}. "
                        f"Current model: {current_model}"
                    )

                    # Generate a unique name by including module information
                    module_parts = model_class.__module__.split(".")
                    if len(module_parts) > 1:
                        if module_parts[0] == "extensions":
                            # For extensions, use extension name as prefix
                            unique_prefix = module_parts[1].title()
                        elif module_parts[0] == "logic":
                            # For logic models, use "Core" as prefix
                            unique_prefix = "Core"
                        else:
                            # For other modules, use the first part
                            unique_prefix = module_parts[0].title()

                        original_type_name = type_name
                        type_name = f"{unique_prefix}{type_name}"

                        # Check if the prefixed name also collides
                        counter: int = 1
                        while type_name in self._global_type_names:
                            existing = self._global_type_names[type_name]
                            if existing == current_model:
                                # Found our own type with this name, return it
                                logger.debug(
                                    f"Found existing type '{type_name}' for model {current_model}"
                                )
                                return self._type_registry[model_class]
                            # Still colliding, add a counter
                            type_name = f"{unique_prefix}{original_type_name}{counter}"
                            counter += 1

                        logger.info(f"Using unique type name: {type_name}")

            # Register the type name
            self._global_type_names[type_name] = (
                f"{model_class.__module__}.{model_class.__name__}"
            )

            # Create fields dict to hold strawberry fields with resolvers
            fields_dict: Dict[str, Any] = {"__annotations__": annotations}

            # Add field resolvers for camelCase -> snake_case mapping
            # Strawberry needs explicit resolvers when GraphQL field names differ from Python attribute names
            for gql_field_name, pydantic_field_name in field_name_mappings.items():
                # Create a resolver that maps the camelCase GraphQL field to the snake_case Pydantic attribute
                def make_resolver(py_field_name: str):
                    def resolver(root) -> Any:
                        # root is the Pydantic model instance
                        return getattr(root, py_field_name, None)

                    return resolver

                # Use strawberry.field with a resolver to map GraphQL field name to Python attribute
                fields_dict[gql_field_name] = strawberry.field(
                    resolver=make_resolver(pydantic_field_name)
                )

            # Add navigation resolver methods for reverse relationships
            if model_class in self._reverse_relationships:
                for reverse_field_name, (
                    source_model,
                    source_field,
                ) in self._reverse_relationships[model_class].items():
                    # Create a resolver method for this reverse relationship
                    resolver = self._create_reverse_navigation_resolver(
                        model_class, source_model, source_field, reverse_field_name
                    )
                    # Add the resolver as a method on the type using strawberry.field
                    # This creates a proper GraphQL field with the resolver
                    fields_dict[reverse_field_name] = strawberry.field(
                        resolver=resolver,
                        description=f"List of related {source_model.__name__} objects",
                    )

            # Create the type class with proper annotations and methods
            type_class = type(type_name, (), fields_dict)

            # Add module information to avoid name collisions
            # Store the module path in the type for debugging
            type_class.__module__ = module_name

            gql_type = strawberry.type(type_class)

            # Register the type
            self._type_registry[model_class] = gql_type

            logger.debug(
                f"Created GraphQL type '{type_name}' for model {model_class.__module__}.{model_class.__name__}"
            )

            return gql_type
        finally:
            # Remove from types being created
            self._types_being_created.discard(model_class)

    def _create_input_type_from_model(
        self, model_class: Type[BaseModel], suffix: str
    ) -> Type:
        """Create a GraphQL input type from a Pydantic model"""
        # Check if input type already exists in registry
        registry_key = (model_class, suffix)
        if registry_key in self._input_type_registry:
            return self._input_type_registry[registry_key]

        base_name = model_class.__name__.removesuffix("Model")

        # Check if this is from an extension and add prefix to avoid collisions
        module_parts = model_class.__module__.split(".")
        if len(module_parts) > 1 and module_parts[0] == "extensions":
            # For extension models, prefix with the extension name
            extension_name = module_parts[1]
            input_name = f"{extension_name.title()}{base_name}{suffix}Input"
        else:
            input_name = f"{base_name}{suffix}Input"

        # Check if model has nested Create/Update classes
        if suffix == "Create" and hasattr(model_class, "Create"):
            base_model = model_class.Create
        elif suffix == "Update" and hasattr(model_class, "Update"):
            base_model = model_class.Update
        else:
            base_model = model_class

        # Extract fields from model_fields (includes inherited fields from mixins)
        annotations: Dict[str, Type] = {}
        for field_name, field_info in base_model.model_fields.items():
            field_type = field_info.annotation

            # Debug logging for ActivityState field
            if "state" in field_name and "Activity" in base_model.__name__:
                logger.debug(
                    f"Processing input field {field_name} in {base_model.__name__}: "
                    f"field_type={field_type}, type={type(field_type)}, "
                    f"is_dict={isinstance(field_type, dict)}"
                )

            # Make ALL fields optional for GraphQL input types to avoid validation errors
            # This is a common pattern in GraphQL where mutations are more permissive
            if not self._is_already_optional(field_type):
                field_type = Optional[field_type]

            # Convert snake_case field names to camelCase for GraphQL input types
            gql_field_name = convert_field_name(field_name, use_camelcase=True)

            gql_field_type = self._convert_python_type_to_gql(field_type)
            annotations[gql_field_name] = gql_field_type

        # Always add at least one field to avoid empty input type error
        if not annotations:
            annotations["_dummy"] = Optional[str]

        # Create the input type class with proper annotations
        # Use strawberry.field with default=None to make all fields truly optional
        input_fields = {}
        for field_name, field_type in annotations.items():
            input_fields[field_name] = strawberry.field(
                default=None, description=f"Optional {field_name}"
            )

        # Create input class dynamically with all fields defaulting to None
        input_class = type(input_name, (), input_fields)
        input_class.__annotations__ = annotations

        input_type = strawberry.input(input_class)

        # Store in registry for future use
        self._input_type_registry[registry_key] = input_type

        return input_type

    def _create_filter_type_from_model(self, model_class: Type[BaseModel]) -> Type:
        """Create a filter input type for a model"""
        base_name = model_class.__name__.removesuffix("Model")

        # Check if this is from an extension and add prefix to avoid collisions
        module_parts = model_class.__module__.split(".")
        if len(module_parts) > 1 and module_parts[0] == "extensions":
            # For extension models, prefix with the extension name
            extension_name = module_parts[1]
            filter_name = f"{extension_name.title()}{base_name}FilterInput"
        else:
            filter_name = f"{base_name}FilterInput"

        # Create basic filter fields for string/numeric fields
        annotations: Dict[str, Type] = {}
        for field_name, field_info in model_class.model_fields.items():
            field_type = field_info.annotation
            if field_type == str:
                annotations[f"{field_name}_contains"] = Optional[str]
                annotations[f"{field_name}_equals"] = Optional[str]
            elif field_type in [int, float]:
                annotations[f"{field_name}_equals"] = Optional[field_type]
                annotations[f"{field_name}_gt"] = Optional[field_type]
                annotations[f"{field_name}_lt"] = Optional[field_type]

        # Always add at least one field to avoid empty input type error
        if not annotations:
            annotations["_dummy"] = Optional[str]

        filter_class = type(filter_name, (), {"__annotations__": annotations})
        return strawberry.input(filter_class)

    def _is_already_optional(self, python_type: Type) -> bool:
        """Check if a type is already Optional (Union with None)"""
        return _type_introspector.is_optional_type(python_type)

    def _convert_python_type_to_gql(self, python_type: Type) -> Type:
        """Convert Python type to GraphQL type"""
        try:
            # Handle Optional types
            if get_origin(python_type) is Union:
                args = get_args(python_type)
                if len(args) == 2 and type(None) in args:
                    inner_type = next(arg for arg in args if arg is not type(None))
                    return Optional[self._convert_python_type_to_gql(inner_type)]

            # Handle List types
            if get_origin(python_type) is list:
                args = get_args(python_type)
                return (
                    List[self._convert_python_type_to_gql(args[0])]
                    if args
                    else List[str]
                )

            # Handle Dict types
            if get_origin(python_type) is dict:
                return DICT_SCALAR

            # Use type mapping for basic types
            if python_type in TYPE_MAPPING:
                return TYPE_MAPPING[python_type]

            # Handle non-type objects
            if not isinstance(python_type, type):
                return TYPE_MAPPING[str]

            # Handle Enum types
            if _type_introspector.is_enum_type(python_type):
                try:
                    # Check if it's an IntEnum
                    if (
                        hasattr(python_type, "__mro__")
                        and IntEnum in python_type.__mro__
                    ):
                        return TYPE_MAPPING[int]

                    # Handle string-based enums
                    if str in python_type.__bases__:
                        enum_values: Dict[str, str] = {
                            member.name: member.name
                            for member in python_type.__members__.values()
                        }
                        enum_name = python_type.__name__

                        # Add prefix for extension enums
                        if hasattr(python_type, "__module__") and isinstance(
                            python_type.__module__, str
                        ):
                            module_parts = python_type.__module__.split(".")
                            if (
                                len(module_parts) > 1
                                and module_parts[0] == "extensions"
                            ):
                                enum_name = f"{module_parts[1].title()}{enum_name}"

                        new_enum = type(enum_name, (Enum,), enum_values)
                        new_enum.__module__ = python_type.__module__
                        return strawberry.enum(new_enum)

                    # Try direct strawberry conversion
                    return strawberry.enum(python_type)
                except Exception:
                    return TYPE_MAPPING[str]

            # Handle constants classes and ProviderType classes
            if (
                hasattr(python_type, "values")
                and callable(getattr(python_type, "values"))
            ) or python_type.__name__.endswith("ProviderType"):
                return TYPE_MAPPING[str]

            # Handle Pydantic models (nested types)
            if hasattr(python_type, "__bases__") and any(
                base.__name__ == "BaseModel" for base in python_type.__mro__
            ):
                # Skip extension models
                if (
                    hasattr(python_type, "_is_extension_model")
                    and python_type._is_extension_model
                ):
                    return ANY_SCALAR

                # For nested Pydantic models, create a GraphQL type
                try:
                    return self._get_or_create_type(python_type)
                except Exception:
                    # Return fallback instead of strawberry.lazy
                    return self._type_registry.get(python_type, ANY_SCALAR)

            # Handle extension Type classes
            if (
                hasattr(python_type, "__module__")
                and isinstance(python_type.__module__, str)
                and python_type.__module__.startswith("extensions.")
                and python_type.__name__.endswith("Type")
            ):
                return TYPE_MAPPING[str]

            # Default fallback
            return ANY_SCALAR

        except Exception as e:
            type_name = getattr(python_type, "__name__", str(python_type))
            module_name = getattr(python_type, "__module__", "unknown")
            logger.warning(
                f"Error converting type {module_name}.{type_name}: {str(e)}. Using ANY_SCALAR."
            )
            return ANY_SCALAR

    def _add_query_resolver(
        self,
        field_name: str,
        return_type: Type,
        manager_class: Type[AbstractBLLManager],
    ) -> None:
        """Add a query resolver for getting a single item"""
        # Special handling for user queries - users can only query themselves
        if "User" in manager_class.__name__:

            async def user_resolver(info: Info, **kwargs: Optional[str]) -> return_type:
                try:
                    context = self._get_context_from_info(info)
                    requester_id = context.get("requester_id")
                    if not requester_id:
                        raise Exception(
                            "Unable to authenticate user for GraphQL query - no requester_id found in context"
                        )
                    manager = manager_class(
                        model_registry=self.model_registry, requester_id=requester_id
                    )

                    # For users, always query the requester (no ID parameter allowed)
                    result = manager.get(id=requester_id, include=None, fields=None)
                    return result
                except Exception as e:
                    logger.error(f"Error in {field_name} resolver: {e}")
                    raise

            self._query_fields[field_name] = strawberry.field(user_resolver)
        else:
            # Create resolver with only ID parameter to avoid unknown argument errors
            async def resolver(id: str, info: Info) -> return_type:
                try:
                    context = self._get_context_from_info(info)
                    requester_id = context.get("requester_id")
                    if not requester_id:
                        raise Exception(
                            "Unable to authenticate user for GraphQL query - no requester_id found in context"
                        )
                    manager = manager_class(
                        model_registry=self.model_registry, requester_id=requester_id
                    )

                    # Call manager.get with just the ID
                    result = manager.get(id=id, include=None, fields=None)
                    return result
                except Exception as e:
                    logger.error(f"Error in {field_name} resolver: {e}")
                    raise

            self._query_fields[field_name] = strawberry.field(resolver)

    def _add_list_query_resolver(
        self,
        field_name: str,
        return_type: Type,
        manager_class: Type[AbstractBLLManager],
        filter_type: Type,
    ) -> None:
        """Add a query resolver for listing items with filtering and pagination"""
        # Special handling for user list queries
        if "User" in manager_class.__name__:

            async def user_list_resolver(
                teamId: Optional[str] = None,
                limit: Optional[int] = 100,
                offset: Optional[int] = 0,
                info: Info = None,
                **kwargs: Optional[str],
            ) -> List[return_type]:
                try:
                    context = self._get_context_from_info(info)
                    requester_id = context.get("requester_id")
                    if not requester_id:
                        raise Exception(
                            "Unable to authenticate user for GraphQL query - no requester_id found in context"
                        )
                    manager = manager_class(
                        model_registry=self.model_registry, requester_id=requester_id
                    )

                    # If teamId is provided, return users in that team
                    if teamId:
                        # Convert camelCase teamId to snake_case team_id
                        filter_params = {"team_id": teamId}
                        for key, value in kwargs.items():
                            snake_key = stringcase.snakecase(key)
                            filter_params[snake_key] = value

                        result = manager.list(
                            offset=offset or 0,
                            limit=limit or 100,
                            include=None,
                            fields=None,
                            **filter_params,
                        )
                        return result
                    else:
                        # No teamId provided - return only the requester
                        user = manager.get(id=requester_id, include=None, fields=None)
                        return [user] if user else []
                except Exception as e:
                    logger.error(f"Error in {field_name} resolver: {e}")
                    return []

            self._query_fields[field_name] = strawberry.field(user_list_resolver)
        else:

            async def resolver(
                filter: Optional[filter_type] = None,
                limit: Optional[int] = 100,
                offset: Optional[int] = 0,
                info: Info = None,
            ) -> List[return_type]:
                try:
                    context = self._get_context_from_info(info)
                    requester_id = context.get("requester_id")
                    if not requester_id:
                        raise Exception(
                            "Unable to authenticate user for GraphQL query - no requester_id found in context"
                        )
                    manager = manager_class(
                        model_registry=self.model_registry, requester_id=requester_id
                    )

                    # Call manager.list with pagination support
                    result = manager.list(
                        offset=offset or 0,
                        limit=limit or 100,
                        include=None,
                        fields=None,
                    )
                    return result
                except Exception as e:
                    logger.error(f"Error in {field_name} resolver: {e}")
                    return []

            self._query_fields[field_name] = strawberry.field(resolver)

    def _add_create_mutation_resolver(
        self,
        field_name: str,
        return_type: Type,
        manager_class: Type[AbstractBLLManager],
        input_type: Type,
    ) -> None:
        """Add a mutation resolver for creating items"""

        async def resolver(input: input_type, info: Info) -> return_type:
            try:
                context = self._get_context_from_info(info)
                requester_id = context.get("requester_id")
                if not requester_id:
                    raise Exception(
                        "Unable to authenticate user for GraphQL query - no requester_id found in context"
                    )
                manager = manager_class(
                    model_registry=self.model_registry, requester_id=requester_id
                )
                data = self._convert_input_to_dict(input)

                # Call manager.create with same signature as REST API
                # Special-case User creation which uses a register flow
                if "User" in manager_class.__name__:
                    # Use the static register method on the manager class to perform registration
                    try:
                        result = manager_class.register(data, model_registry=self.model_registry)
                    except TypeError:
                        # Fallback to pass kwargs style if the register signature expects named args
                        result = manager_class.register(registration_data=data, model_registry=self.model_registry)
                else:
                    result = manager.create(**data)

                # Broadcast subscription (convert to dict for JSON serialization)
                try:
                    if hasattr(result, "model_dump"):
                        result_data = result.model_dump()
                    elif hasattr(result, "dict"):
                        result_data = result.dict()
                    else:
                        result_data = str(result)

                    await self.broadcast.publish(
                        channel=f"{return_type.__name__.lower()}_created",
                        message=json.dumps({"action": "created", "data": result_data}),
                    )
                except Exception as e:
                    logger.log("SQL", f"Failed to broadcast create event: {e}")

                return result
            except Exception as e:
                logger.error(f"Error in {field_name} resolver: {e}")
                raise

        self._mutation_fields[field_name] = strawberry.field(resolver)

    def _add_update_mutation_resolver(
        self,
        field_name: str,
        return_type: Type,
        manager_class: Type[AbstractBLLManager],
        input_type: Type,
    ) -> None:
        """Add a mutation resolver for updating items"""
        # Special handling for user update mutations - users can only update themselves
        if "User" in manager_class.__name__:

            async def user_update_resolver(
                input: input_type, info: Info
            ) -> return_type:
                try:
                    context = self._get_context_from_info(info)
                    requester_id = context.get("requester_id")
                    if not requester_id:
                        raise Exception(
                            "Unable to authenticate user for GraphQL query - no requester_id found in context"
                        )
                    manager = manager_class(
                        model_registry=self.model_registry, requester_id=requester_id
                    )
                    logger.info(f"GraphQL update input: {input}")
                    logger.info(f"GraphQL update input type: {type(input)}")
                    logger.info(
                        f"GraphQL update input dict: {input.__dict__ if hasattr(input, '__dict__') else 'No __dict__'}"
                    )
                    data = self._convert_input_to_dict(input)

                    logger.info(f"GraphQL update data: {data}")

                    # For users, always update the requester (no ID parameter allowed)
                    result = manager.update(requester_id, **data)

                    # Broadcast subscription (convert to dict for JSON serialization)
                    try:
                        if hasattr(result, "model_dump"):
                            result_data = result.model_dump()
                        elif hasattr(result, "dict"):
                            result_data = result.dict()
                        else:
                            result_data = str(result)

                        await self.broadcast.publish(
                            channel=f"{return_type.__name__.lower()}_updated",
                            message=json.dumps(
                                {"action": "updated", "data": result_data}
                            ),
                        )
                    except Exception as e:
                        logger.log("SQL", f"Failed to broadcast update event: {e}")

                    return result
                except Exception as e:
                    logger.error(f"Error in {field_name} resolver: {e}")
                    raise

            self._mutation_fields[field_name] = strawberry.field(user_update_resolver)
        else:

            async def resolver(id: str, input: input_type, info: Info) -> return_type:
                try:
                    context = self._get_context_from_info(info)
                    requester_id = context.get("requester_id")
                    if not requester_id:
                        raise Exception(
                            "Unable to authenticate user for GraphQL query - no requester_id found in context"
                        )
                    manager = manager_class(
                        model_registry=self.model_registry, requester_id=requester_id
                    )
                    data = self._convert_input_to_dict(input)

                    # Call manager.update with same signature as REST API
                    result = manager.update(id, **data)

                    # Broadcast subscription (convert to dict for JSON serialization)
                    try:
                        if hasattr(result, "model_dump"):
                            result_data = result.model_dump()
                        elif hasattr(result, "dict"):
                            result_data = result.dict()
                        else:
                            result_data = str(result)

                        await self.broadcast.publish(
                            channel=f"{return_type.__name__.lower()}_updated",
                            message=json.dumps(
                                {"action": "updated", "data": result_data}
                            ),
                        )
                    except Exception as e:
                        logger.log("SQL", f"Failed to broadcast update event: {e}")

                    return result
                except Exception as e:
                    logger.error(f"Error in {field_name} resolver: {e}")
                    raise

            self._mutation_fields[field_name] = strawberry.field(resolver)

    def _add_delete_mutation_resolver(
        self, field_name: str, manager_class: Type[AbstractBLLManager]
    ) -> None:
        """Add a mutation resolver for deleting items"""
        # Special handling for user delete mutations - users can only delete themselves
        if "User" in manager_class.__name__:

            async def user_delete_resolver(info: Info) -> bool:
                try:
                    context = self._get_context_from_info(info)
                    requester_id = context.get("requester_id")
                    if not requester_id:
                        raise Exception(
                            "Unable to authenticate user for GraphQL query - no requester_id found in context"
                        )
                    manager = manager_class(
                        model_registry=self.model_registry, requester_id=requester_id
                    )

                    # For users, always delete the requester (no ID parameter allowed)
                    result = manager.delete(id=requester_id)

                    # Broadcast subscription (convert to dict for JSON serialization)
                    try:
                        await self.broadcast.publish(
                            channel=f"{manager_class.__name__.lower()}_deleted",
                            message=json.dumps(
                                {"action": "deleted", "id": requester_id}
                            ),
                        )
                    except Exception as e:
                        logger.log("SQL", f"Failed to broadcast delete event: {e}")

                    return True
                except Exception as e:
                    logger.error(f"Error in {field_name} resolver: {e}")
                    return False

            self._mutation_fields[field_name] = strawberry.field(user_delete_resolver)
        else:

            async def resolver(id: str, info: Info) -> bool:
                try:
                    context = self._get_context_from_info(info)
                    requester_id = context.get("requester_id")
                    if not requester_id:
                        raise Exception(
                            "Unable to authenticate user for GraphQL query - no requester_id found in context"
                        )
                    manager = manager_class(
                        model_registry=self.model_registry, requester_id=requester_id
                    )

                    # Call manager.delete with same signature as REST API
                    result = manager.delete(id=id)

                    # Broadcast subscription (convert to dict for JSON serialization)
                    try:
                        await self.broadcast.publish(
                            channel=f"{manager_class.__name__.lower()}_deleted",
                            message=json.dumps({"action": "deleted", "id": id}),
                        )
                    except Exception as e:
                        logger.log("SQL", f"Failed to broadcast delete event: {e}")

                    return True
                except Exception as e:
                    logger.error(f"Error in {field_name} resolver: {e}")
                    return False

            self._mutation_fields[field_name] = strawberry.field(resolver)

    def _add_subscription_resolver(self, field_name: str, model_name: str) -> None:
        """Add a subscription resolver for model events"""

        async def resolver() -> AsyncGenerator[str, None]:
            channel = f"{model_name.lower()}_created"  # Simplified for now
            async with self.broadcast.subscribe(channel=channel) as subscriber:
                async for event in subscriber:
                    yield event.message

        self._subscription_fields[field_name] = strawberry.subscription(resolver)

    def _convert_filter_to_params(self, filter_obj: Optional[Any]) -> Dict[str, Any]:
        """Convert filter object to search parameters"""
        if not filter_obj:
            return {}

        params: Dict[str, Any] = {}
        for attr_name in dir(filter_obj):
            if not attr_name.startswith("_"):
                value = getattr(filter_obj, attr_name)
                if value is not None:
                    params[attr_name] = value

        return params

    def _get_context_from_info(self, info: Info) -> Dict[str, Any]:
        """Extract context from GraphQL Info object"""
        context: Dict[str, Any] = {}
        requester_id: Optional[str] = None

        if info and hasattr(info, "context"):
            ctx = info.context

            # Only extract clean context data, avoid FastAPI internals
            if isinstance(ctx, dict):
                # Extract only safe context fields
                for key, value in ctx.items():
                    if key not in ["request", "background_tasks", "response"]:
                        context[key] = value
                        if key == "requester_id":
                            requester_id = value

            # Try to get requester_id from FastAPI request if not found
            if not requester_id:
                request: Optional[Any] = None

                # Try different ways to get the request
                if hasattr(ctx, "request"):
                    request = ctx.request
                elif isinstance(ctx, dict) and "request" in ctx:
                    request = ctx["request"]

                if request:
                    # Check if request has user information
                    if hasattr(request, "state") and hasattr(request.state, "user"):
                        user = request.state.user
                        if hasattr(user, "id"):
                            requester_id = user.id

                    # Fallback: try to authenticate from Authorization header or API key
                    elif hasattr(request, "headers"):
                        auth_header = request.headers.get("authorization")
                        api_key = request.headers.get("x-api-key")

                        # Check for API key first (for system entities)
                        if api_key:
                            from lib.Environment import env

                            if api_key == env("ROOT_API_KEY"):
                                # For system operations with API key, use system ID
                                requester_id = env("SYSTEM_ID")
                        # Fall back to JWT authentication
                        elif auth_header:
                            try:
                                from logic.BLL_Auth import UserManager

                                if self.model_registry:
                                    # Use the static auth method with model_registry parameter
                                    user = UserManager.auth(
                                        model_registry=self.model_registry,
                                        authorization=auth_header,
                                        request=request,
                                    )
                                    if user and hasattr(user, "id"):
                                        requester_id = user.id
                            except Exception as e:
                                logger.log(
                                    "SQL",
                                    f"Failed to authenticate user from GraphQL context: {e}",
                                )

        # Set requester_id in context if found
        if requester_id:
            context["requester_id"] = requester_id

        return context

    def _convert_input_to_dict(self, input_obj: Any) -> Dict[str, Any]:
        """Convert input object to dictionary, converting camelCase keys to snake_case for Python/Pydantic compatibility"""
        if hasattr(input_obj, "model_dump"):
            data = input_obj.model_dump(exclude_none=True)
        elif hasattr(input_obj, "__dict__"):
            # Convert from input object, excluding None values
            data: Dict[str, Any] = {}
            for k, v in input_obj.__dict__.items():
                if v is not None and not k.startswith("_"):
                    data[k] = v
        else:
            return {}

        # Convert camelCase keys to snake_case for Python/Pydantic compatibility
        result: Dict[str, Any] = {}
        for key, value in data.items():
            # Convert camelCase to snake_case
            snake_key = stringcase.snakecase(key)
            result[snake_key] = value

        return result

    def _get_create_input_type(self, model_class: Type[BaseModel]) -> Type:
        """Get or create the Create input type for a model"""
        if hasattr(model_class, "Create"):
            return self._convert_pydantic_to_input(model_class.Create)
        # For now, return a simple placeholder to avoid complex dependencies
        return self._create_simple_input_type(model_class, "Create")

    def _get_update_input_type(self, model_class: Type[BaseModel]) -> Type:
        """Get or create the Update input type for a model"""
        if hasattr(model_class, "Update"):
            return self._convert_pydantic_to_input(model_class.Update)
        # For now, return a simple placeholder to avoid complex dependencies
        return self._create_simple_input_type(model_class, "Update")

    def _convert_pydantic_to_input(self, pydantic_model: Type[BaseModel]) -> Type:
        """Convert Pydantic model to Strawberry input type"""
        # For now, return a simple placeholder
        return self._create_simple_input_type(pydantic_model, "Input")

    def _create_simple_input_type(
        self, model_class: Type[BaseModel], suffix: str = "Input"
    ) -> Type:
        """Create a simple input type to avoid complex dependencies"""
        input_name = f"{model_class.__name__}{suffix}"

        # Get fields from the model
        annotations: Dict[str, Type] = {}
        for field_name, field_info in model_class.model_fields.items():
            field_type = field_info.annotation
            # Skip read-only fields for input types
            if field_name in [
                "id",
                "created_at",
                "updated_at",
                "created_by_user_id",
                "updated_by_user_id",
                "deleted_at",
                "deleted_by_user_id",
            ]:
                continue

            # Convert field type to optional GraphQL type
            gql_type = self._convert_python_type_to_gql(field_type)
            if not self._is_already_optional(gql_type):
                gql_type = Optional[gql_type]
            # Convert snake_case field names to camelCase for GraphQL input types
            gql_field_name = convert_field_name(field_name, use_camelcase=True)
            annotations[gql_field_name] = gql_type

        # Always add at least one field to avoid empty input type error
        if not annotations:
            annotations["_dummy"] = Optional[str]

        # Create the input type class with proper annotations
        input_fields: Dict[str, Any] = {}
        for field_name, field_type in annotations.items():
            input_fields[field_name] = strawberry.field(
                default=None, description=f"Optional {field_name}"
            )

        # Create input class dynamically with all fields defaulting to None
        input_class = type(input_name, (), input_fields)
        input_class.__annotations__ = annotations

        return strawberry.input(input_class)

    def _create_query_type(self) -> Type:
        """Create Query type with all query fields"""
        fields: Dict[str, Any] = self._query_fields.copy()

        # Add a default field if no fields were generated
        if not fields:

            @strawberry.field
            def hello() -> str:
                return "Hello from GraphQL!"

            fields["hello"] = hello

        # Create the Query class dynamically
        Query = type("Query", (), fields)
        return strawberry.type(Query)

    def _create_mutation_type(self) -> Type:
        """Create Mutation type with all mutation fields"""
        fields: Dict[str, Any] = self._mutation_fields.copy()

        # Add a default field if no fields were generated
        if not fields:

            @strawberry.field
            def noop() -> str:
                return "No mutations available"

            fields["noop"] = noop

        # Create the Mutation class dynamically
        Mutation = type("Mutation", (), fields)
        return strawberry.type(Mutation)

    def _create_subscription_type(self) -> Type:
        """Create Subscription type with all subscription fields"""
        fields: Dict[str, Any] = self._subscription_fields.copy()

        # Add a default field if no fields were generated
        if not fields:

            @strawberry.subscription
            async def noop() -> AsyncGenerator[str, None]:
                yield "No subscriptions available"

            fields["noop"] = noop

        # Create the Subscription class dynamically
        Subscription = type("Subscription", (), fields)
        return strawberry.type(Subscription)

    def _analyze_model_relationships(self, model_class: Type[BaseModel]) -> None:
        """Analyze a model's relationships and register them."""
        if model_class in self._analyzed_models:
            return

        self._analyzed_models.add(model_class)

        # Analyze fields for references
        for field_name, field_info in model_class.model_fields.items():
            field_type = field_info.annotation

            # Handle Optional types
            if get_origin(field_type) is Union:
                args = get_args(field_type)
                if len(args) == 2 and type(None) in args:
                    field_type = next(arg for arg in args if arg is not type(None))

            # Check for foreign key relationships (fields ending with _id)
            if field_name.endswith("_id") and field_name != "id":
                # Try to find the corresponding model
                base_field_name = field_name[:-3]  # Remove '_id'

                # Debug logging for UserTeamModel
                if "UserTeam" in model_class.__name__:
                    logger.debug(
                        f"UserTeamModel field: {field_name} -> base_field_name: {base_field_name}"
                    )
                    logger.debug(
                        f"Available fields: {list(model_class.model_fields.keys())}"
                    )

                # Look for a corresponding object field
                if base_field_name in model_class.model_fields:
                    object_field_type: Type = model_class.model_fields[
                        base_field_name
                    ].annotation

                    # Extract the actual model type
                    if get_origin(object_field_type) is Union:
                        args = get_args(object_field_type)
                        if len(args) == 2 and type(None) in args:
                            object_field_type = next(
                                arg for arg in args if arg is not type(None)
                            )

                    if self._is_pydantic_model(object_field_type):
                        # Register forward relationship
                        if model_class not in self._forward_relationships:
                            self._forward_relationships[model_class] = {}
                        self._forward_relationships[model_class][
                            base_field_name
                        ] = object_field_type

                        # Register reverse relationship
                        if object_field_type not in self._reverse_relationships:
                            self._reverse_relationships[object_field_type] = {}

                        # Generate plural field name for reverse relationship
                        model_name = model_class.__name__.removesuffix("Model")
                        reverse_field_name = inflection.plural(
                            stringcase.snakecase(model_name)
                        )

                        self._reverse_relationships[object_field_type][
                            reverse_field_name
                        ] = (model_class, base_field_name)
                        logger.debug(
                            f"Registered reverse relationship: {object_field_type.__name__}.{reverse_field_name} "
                            f"-> List[{model_class.__name__}] (via {base_field_name}_id)"
                        )

    def _is_pydantic_model(self, field_type: Any) -> bool:
        """Check if a type is a Pydantic model."""
        return _type_introspector.is_pydantic_model(field_type)

    def _get_type_name_for_model(self, model_class: Type[BaseModel]) -> str:
        """Get the GraphQL type name for a model."""
        base_name = model_class.__name__.removesuffix("Model")

        # Check if this is from an extension and add prefix to avoid collisions
        module_parts = model_class.__module__.split(".")
        if len(module_parts) > 1 and module_parts[0] == "extensions":
            # For extension models, prefix with the extension name
            extension_name = module_parts[1]
            return f"{extension_name.title()}{base_name}Type"
        else:
            return f"{base_name}Type"

    def _get_or_create_type(self, model_class: Type[BaseModel]) -> Type:
        """Get or create a GraphQL type with lazy resolution for circular dependencies."""
        if model_class in self._type_registry:
            return self._type_registry[model_class]

        # For circular dependencies, we need to create the type immediately
        # This will recursively create any dependent types
        return self._create_gql_type_from_model(model_class)

    def _get_manager_for_model(self, model_class: Type[BaseModel]) -> Optional[Type]:
        """Find the manager class for a given model."""
        # Look through model relationships
        for relationship in self.model_registry.model_relationships:
            if len(relationship) >= 4:
                rel_model, _, _, manager_class = relationship[:4]
                if rel_model == model_class:
                    return manager_class

        return None

    def _create_navigation_resolver(
        self,
        source_model: Type[BaseModel],
        target_model: Type[BaseModel],
        field_name: str,
        is_reverse: bool = False,
    ) -> Callable:
        """Create a resolver for navigation properties."""

        if is_reverse:
            # Reverse navigation (one-to-many)
            async def reverse_resolver(
                self, info: Info, limit: Optional[int] = 100, offset: Optional[int] = 0
            ) -> List[target_model]:
                try:
                    context = self._get_context_from_info(info)
                    requester_id = context.get("requester_id")

                    if not requester_id:
                        raise Exception("Authentication required")

                    # Get the manager for the target model
                    manager_class = self._get_manager_for_model(target_model)
                    if not manager_class:
                        return []

                    manager = manager_class(
                        model_registry=self.model_registry, requester_id=requester_id
                    )

                    # Build filter based on the foreign key
                    foreign_key_field = f"{field_name}_id"
                    filter_params = {foreign_key_field: self.id}

                    # Get the related items
                    results = manager.list(limit=limit, offset=offset, **filter_params)

                    return results

                except Exception as e:
                    logger.error(f"Error in reverse navigation resolver: {e}")
                    return []

            return reverse_resolver
        else:
            # Forward navigation (many-to-one)
            async def forward_resolver(self, info: Info) -> Optional[target_model]:
                try:
                    # Check if we already have the object loaded
                    if (
                        hasattr(self, field_name)
                        and getattr(self, field_name) is not None
                    ):
                        return getattr(self, field_name)

                    # Get the foreign key value
                    foreign_key = getattr(self, f"{field_name}_id", None)
                    if not foreign_key:
                        return None

                    context = self._get_context_from_info(info)
                    requester_id = context.get("requester_id")

                    if not requester_id:
                        raise Exception("Authentication required")

                    # Get the manager for the target model
                    manager_class = self._get_manager_for_model(target_model)
                    if not manager_class:
                        return None

                    manager = manager_class(
                        requester_id=requester_id, model_registry=self.model_registry
                    )

                    # Get the related item
                    result = manager.get(id=foreign_key)

                    # Cache it on the object for future access
                    setattr(self, field_name, result)

                    return result

                except Exception as e:
                    logger.error(f"Error in forward navigation resolver: {e}")
                    return None

            return forward_resolver

    def _create_reverse_navigation_resolver(
        self,
        target_model: Type[BaseModel],
        source_model: Type[BaseModel],
        source_field: str,
        reverse_field_name: str,
    ) -> Callable:
        """Create a resolver for reverse navigation properties."""
        # Store the manager reference for use in the resolver
        manager_ref: "GraphQLManager" = self

        async def resolver(
            self, info: Info, limit: Optional[int] = 100, offset: Optional[int] = 0
        ):
            try:
                context = manager_ref._get_context_from_info(info)
                requester_id = context.get("requester_id")

                if not requester_id:
                    logger.error("No requester_id found in GraphQL context")
                    return []

                # Get the manager for the source model
                manager_class = manager_ref._get_manager_for_model(source_model)
                if not manager_class:
                    logger.error(f"No manager found for model {source_model}")
                    return []

                manager = manager_class(
                    model_registry=manager_ref.model_registry, requester_id=requester_id
                )

                # Build filter based on the foreign key
                foreign_key_field = f"{source_field}_id"
                filter_params = {foreign_key_field: self.id}

                # Get the related items
                results = manager.list(limit=limit, offset=offset, **filter_params)

                return results

            except Exception as e:
                logger.error(
                    f"Error in reverse navigation resolver for {reverse_field_name}: {e}"
                )
                return []

        # Set the return type annotation dynamically
        source_gql_type: Type = manager_ref._get_or_create_type(source_model)
        resolver.__annotations__["return"] = List[source_gql_type]

        return resolver
