import inspect
import json
from datetime import date, datetime, time
from enum import Enum
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
)

from lib.Pydantic2FastAPI import generate_routers_from_model_registry

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

import stringcase
from ordered_set import OrderedSet
from pydantic import BaseModel, Field
from sqlalchemy.orm import configure_mappers

from database.migrations.Migration import MigrationManager
from lib.AbstractPydantic2 import (
    CacheManager,
    FieldProcessor,
    NameProcessor,
    ReferenceResolver,
    RelationshipAnalyzer,
    TypeIntrospector,
)
from lib.Environment import AbstractRegistry, env, inflection
from lib.Logging import logger


class classproperty:
    def __init__(self, func):
        self.func = func

    def __get__(self, instance, owner):
        return self.func(owner)


class PydanticUtility:
    """
    Utility class for working with Pydantic models in GraphQL schemas.

    This class provides methods for introspecting Pydantic models, resolving type
    references, generating detailed schema representations, and converting
    string data to Pydantic model instances. It also handles model discovery
    and relationship mapping for GraphQL schema generation.
    """

    def __init__(self):
        # Use shared utility components
        self.cache_manager = CacheManager()
        self.name_processor = NameProcessor()
        self.type_introspector = TypeIntrospector(self.cache_manager)
        self.field_processor = FieldProcessor(self.cache_manager)
        self.reference_resolver = ReferenceResolver(self.cache_manager)
        self.relationship_analyzer = RelationshipAnalyzer(self.cache_manager)

        self._model_name_to_class = self.reference_resolver._model_registry

        self._processed_models = set()
        self._type_name_mapping = self.cache_manager.get_cache("type_name_mapping")
        self._generated_gql_names = self.cache_manager.get_cache("generated_gql_names")
        self._model_fingerprints = self.cache_manager.get_cache("model_fingerprints")
        self._processed_modules = set()
        self._model_fields_cache = self.cache_manager.get_cache("model_fields_cache")
        self._known_modules = set()
        self._relationship_cache = self.cache_manager.get_cache("relationship_cache")
        self._model_hierarchy_cache = self.cache_manager.get_cache(
            "model_hierarchy_cache"
        )

    def get_type_name(self, type_obj):
        """Get the string representation of a type."""
        return self.type_introspector.get_type_name(type_obj)

    def _is_scalar_type(self, type_obj):
        """Check if a type is a scalar type."""
        return self.type_introspector.is_scalar_type(type_obj)

    def resolve_string_reference(
        self, ref_str: str, module_context=None
    ) -> Optional[Type]:
        """
        Resolves a string forward reference to its actual class.

        This function is crucial for handling forward references in Pydantic models
        where types are referenced by string names rather than actual classes.
        It attempts to find the referenced class by name in the provided module
        context or in the registered model dictionary.

        Args:
            ref_str: String representation of a class
            module_context: Optional module context to search first

        Returns:
            The actual class object or None if not found
        """
        return self.reference_resolver.resolve_string_reference(ref_str, module_context)

    def process_annotations_with_forward_refs(
        self, annotations: Dict, module_context=None
    ) -> Dict:
        """
        Process annotations dictionary to resolve forward references.

        Args:
            annotations: Dictionary of field annotations
            module_context: Optional module context

        Returns:
            Processed annotations with resolved forward references
        """
        processed = {}

        for field_name, field_type in annotations.items():
            if isinstance(field_type, str):
                # This is a string forward reference
                resolved_type = self.reference_resolver.resolve_string_reference(
                    field_type, module_context
                )
                if resolved_type:
                    processed[field_name] = resolved_type
                else:
                    processed[field_name] = field_type  # Keep as is if can't resolve
            elif get_origin(field_type) is Union:
                # Handle Optional[...] which is Union[..., None]
                args = get_args(field_type)
                new_args = [
                    (
                        (
                            self.reference_resolver.resolve_string_reference(
                                arg, module_context
                            )
                            or arg
                        )
                        if isinstance(arg, str)
                        else arg
                    )
                    for arg in args
                ]

                # Recreate the Union with resolved types
                if all(not isinstance(arg, str) for arg in new_args):
                    processed[field_name] = Union[tuple(new_args)]
                else:
                    processed[field_name] = field_type
            elif get_origin(field_type) is list or get_origin(field_type) is List:
                # Handle List[...] with a string type
                args = get_args(field_type)
                if args and isinstance(args[0], str):
                    resolved = self.reference_resolver.resolve_string_reference(
                        args[0], module_context
                    )
                    if resolved:
                        from typing import List as ListType

                        processed[field_name] = ListType[resolved]
                    else:
                        processed[field_name] = field_type
                else:
                    processed[field_name] = field_type
            else:
                processed[field_name] = field_type

        return processed

    def get_model_fields(
        self, model: Type[BaseModel], process_refs: bool = True
    ) -> Dict[str, Any]:
        cache_key = f"{model.__module__}.{model.__name__}_{process_refs}"
        cache = self.cache_manager.get_cache("model_fields_with_refs")

        if cache_key in cache:
            return cache[cache_key]

        fields = self.field_processor.get_model_fields(
            model, include_inherited=True, process_refs=False
        )

        if process_refs:
            try:
                fields = self.process_annotations_with_forward_refs(
                    fields, inspect.getmodule(model)
                )
            except Exception:
                pass

        cache[cache_key] = fields
        self._model_fields_cache[cache_key] = fields
        return fields

    def register_model(
        self, model: Type[BaseModel], name: Optional[str] = None
    ) -> None:
        """
        Register a model for name-based lookups.

        This method adds a Pydantic model to an internal registry that maps normalized
        model names to their class definitions. This enables finding models by name
        when resolving relationships between models.

        The method also registers shortened versions of model names to support more
        flexible matching when searching for models by field names.

        Args:
            model: The model class to register
            name: Optional name to register it under (defaults to normalized class name)
        """
        self.reference_resolver.register_model(model, name)

    def register_models(self, models: List[Type[BaseModel]]) -> None:
        """
        Register multiple models at once.

        Args:
            models: List of model classes to register
        """
        for model in models:
            self.reference_resolver.register_model(model)

    def find_model_by_name(self, name: str) -> Optional[Type[BaseModel]]:
        """
        Find a model class by name.

        This method attempts to find a registered model using various matching strategies:
        1. Direct match with the normalized name
        2. Match with the singular form of the name (for plural field names)
        3. Partial matches where either the model name contains the search term or vice versa

        Args:
            name: Name to search for

        Returns:
            The model class if found, None otherwise
        """
        return self.reference_resolver.find_model_by_name(name)

    def generate_unique_type_name(
        self, model_class: Type, unique_suffix: Optional[str] = None
    ) -> str:
        """
        Generates a unique and simplified GraphQL-friendly type name for a Pydantic model.
        Uses deterministic collision resolution instead of random UUIDs.
        """

        # --- Helper to get/resolve the base name for a model ---
        def _get_or_resolve_base_name(_model_full_path: str, _model_class: Type) -> str:
            if _model_full_path in self._type_name_mapping:
                return self._type_name_mapping[_model_full_path]

            # Base name not cached, derive and resolve it.
            ideal_base_name = _model_class.__name__

            # Handle nested classes like TeamModel.Create -> TeamCreate
            if (
                hasattr(_model_class, "__qualname__")
                and "." in _model_class.__qualname__
            ):
                ideal_base_name = self.name_processor.handle_nested_class_name(
                    _model_class.__qualname__
                )

            # Handle locally defined classes in the base name itself
            if "<locals>" in ideal_base_name:
                # Extract just the class name, removing function scope info
                parts = ideal_base_name.split(".")
                ideal_base_name = parts[-1]  # Get the last part (actual class name)

            # Apply standard transformations using name processor
            ideal_base_name = self.name_processor.extract_base_name(
                ideal_base_name, ("ReferenceModel", "Model")
            )

            # For ReferenceModel, add "Ref" suffix
            if _model_class.__name__.endswith("ReferenceModel"):
                ideal_base_name += "Ref"

            # Sanitize the name to ensure it's GraphQL-compatible
            ideal_base_name = self.name_processor.sanitize_name(ideal_base_name)

            if not ideal_base_name[0].isupper():
                ideal_base_name = stringcase.pascalcase(ideal_base_name)

            # Use shared name processor for collision resolution
            existing_names = set(self._generated_gql_names.keys())
            resolved_base = self.name_processor.generate_unique_name(
                ideal_base_name, existing_names, _model_full_path
            )

            # Update the tracking dictionary
            self._generated_gql_names[resolved_base] = _model_full_path

            self._type_name_mapping[_model_full_path] = resolved_base
            return resolved_base

        # --- Main logic for generate_unique_type_name ---
        resolved_base_name = _get_or_resolve_base_name(
            f"{model_class.__module__}.{model_class.__qualname__}", model_class
        )

        if not unique_suffix:
            return resolved_base_name

        # Use shared name processor for suffixed name collision resolution
        existing_names = set(self._generated_gql_names.keys())
        final_suffixed_name = self.name_processor.generate_unique_name(
            f"{resolved_base_name}{unique_suffix}",
            existing_names,
            f"{model_class.__module__}.{model_class.__qualname__}",
        )

        # Update the tracking dictionary
        self._generated_gql_names[final_suffixed_name] = (
            f"{model_class.__module__}.{model_class.__qualname__}"
        )

        return final_suffixed_name

    def generate_detailed_schema(
        self, model: Type[BaseModel], max_depth: int = 3, depth: int = 0
    ) -> str:
        """
        Recursively generates a detailed schema representation of a Pydantic model.

        This function traverses through the fields of a Pydantic model and creates a
        string representation of its schema, including nested models and complex types.
        It handles various type constructs such as Lists, Dictionaries, Unions, and Enums.

        The max_depth parameter controls how deep the recursion goes, which is important
        to prevent infinite recursion with circular model references.

        Args:
            model (Type[BaseModel]): The Pydantic model to generate a schema for.
            max_depth (int, optional): Maximum recursion depth. Defaults to 3.
            depth (int, optional): The current depth level for indentation. Defaults to 0.

        Returns:
            str: A string representation of the model's schema with proper indentation.
        """
        # Get model fields
        fields = self.get_model_fields(model)
        field_descriptions = []
        indent = "  " * depth

        # Stop recursion if we've reached max depth to prevent infinite recursion
        if depth >= max_depth:
            return f"{indent}(max depth reached)"

        for field, field_type in fields.items():
            description = f"{indent}{field}: "
            origin_type = get_origin(field_type)
            if origin_type is None:
                origin_type = field_type

            # Handle nested Pydantic models
            if inspect.isclass(origin_type) and issubclass(origin_type, BaseModel):
                description += f"Nested Model:\n{self.generate_detailed_schema(origin_type, max_depth, depth + 1)}"
            # Handle lists, which could contain primitive types or nested models
            elif origin_type == list:
                if inspect.isclass(get_args(field_type)[0]) and issubclass(
                    get_args(field_type)[0], BaseModel
                ):
                    description += f"List of Nested Model:\n{self.generate_detailed_schema(get_args(field_type)[0], max_depth, depth + 1)}"
                elif get_origin(get_args(field_type)[0]) == Union:
                    description += f"List of Union:\n"
                    for union_type in get_args(get_args(field_type)[0]):
                        if inspect.isclass(union_type) and issubclass(
                            union_type, BaseModel
                        ):
                            description += f"{indent}  - Nested Model:\n{self.generate_detailed_schema(union_type, max_depth, depth + 2)}"
                        else:
                            description += f"{indent}  - {self.type_introspector.get_type_name(union_type)}\n"
                else:
                    description += f"List[{self.type_introspector.get_type_name(get_args(field_type)[0])}]"
            # Handle dictionaries with key and value types
            elif origin_type == dict:
                key_type, value_type = get_args(field_type)
                description += f"Dict[{self.type_introspector.get_type_name(key_type)}, {self.type_introspector.get_type_name(value_type)}]"
            # Handle union types (including Optional)
            elif origin_type == Union:
                union_types = get_args(field_type)

                for union_type in union_types:
                    if inspect.isclass(union_type) and issubclass(
                        union_type, BaseModel
                    ):
                        description += f"{indent}  - Nested Model:\n{self.generate_detailed_schema(union_type, max_depth, depth + 2)}"
                    else:
                        type_name = self.type_introspector.get_type_name(union_type)
                        if (
                            type_name != "NoneType"
                        ):  # Skip None type for Optional fields
                            description += (
                                f"{self.type_introspector.get_type_name(union_type)}\n"
                            )
            # Handle Enum types with their possible values
            elif inspect.isclass(origin_type) and issubclass(origin_type, Enum):
                enum_values = ", ".join([f"{e.name} = {e.value}" for e in origin_type])
                enum_name = origin_type.__name__

                # Special case for test enums
                if enum_name == "EnumForTest":
                    enum_name = "TestEnum"

                description += f"{enum_name} (Enum values: {enum_values})"
            # Handle scalar types and everything else
            else:
                description += self.type_introspector.get_type_name(origin_type)
            field_descriptions.append(description)
        return "\n".join(field_descriptions)

    # TODO Move this to the AI extension
    async def convert_to_model(
        self,
        input_string: str,
        model: Type[BaseModel],
        max_failures: int = 3,
        response_type: str = None,
        inference_function=None,
        **kwargs,
    ) -> Union[dict, BaseModel, str]:
        """
        Convert a string to a Pydantic model using an inference function.

        This function takes a string input and attempts to convert it to a specified
        Pydantic model by generating a schema and using an inference agent. It includes
        retry logic for handling conversion failures.

        The function works with external inference systems (like LLMs) to structure
        unstructured text into a properly formatted object that matches the Pydantic model.
        It can handle extraction of JSON from code blocks and includes retry logic to
        handle potential parsing failures.

        Args:
            input_string (str): The string to convert to a model.
            model (Type[BaseModel]): The Pydantic model to convert the string to.
            max_failures (int, optional): Maximum number of retry attempts. Defaults to 3.
            response_type (str, optional): The type of response to return ('json' or None).
                If 'json', returns the raw dictionary; otherwise returns the model instance.
            inference_function: The function to use for inference. Should take a schema and input string.
            **kwargs: Additional arguments to pass to the inference function.

        Returns:
            Union[dict, BaseModel, str]:
                - If response_type is 'json': Returns the parsed JSON dictionary.
                - If response_type is None and successful: Returns the instantiated model.
                - If all retries fail: Returns either the raw response or an error message.

        Raises:
            ValueError: If no inference function is provided.
        """
        input_string = str(input_string)
        # Generate a detailed schema representation of the model for the inference function
        schema = self.generate_detailed_schema(model)

        # Remove potentially conflicting kwargs
        if "user_input" in kwargs:
            del kwargs["user_input"]
        if "schema" in kwargs:
            del kwargs["schema"]

        # If no inference function is provided, we can't proceed
        if inference_function is None:
            raise ValueError("An inference function must be provided")

        # Call the inference function with our schema and input
        response = await inference_function(
            user_input=input_string, schema=schema, **kwargs
        )

        # Extract JSON from markdown code blocks if present
        if "```json" in response:
            response = response.split("```json")[1].split("```")[0].strip()
        elif "```" in response:
            response = response.split("```")[1].strip()

        try:
            # Parse the JSON response
            response_json = json.loads(response)

            # Return based on desired response type
            if response_type == "json":
                return response_json
            else:
                # Instantiate the Pydantic model with the parsed JSON data
                return model(**response_json)
        except Exception as e:
            # Implement retry logic for handling errors
            if "failures" in kwargs:
                failures = int(kwargs["failures"]) + 1
                if failures > max_failures:
                    logger.error(
                        f"Error: {e}. Failed to convert the response to the model after {max_failures} attempts. "
                        f"Response: {response}"
                    )
                    return (
                        response
                        if response
                        else "Failed to convert the response to the model."
                    )
            else:
                failures = 1

            logger.warning(
                f"Error: {e}. Failed to convert the response to the model, trying again. "
                f"{failures}/{max_failures} failures. Response: {response}"
            )

            # Retry with incremented failure count
            return await self.convert_to_model(
                input_string=input_string,
                model=model,
                max_failures=max_failures,
                response_type=response_type,
                inference_function=inference_function,
                failures=failures,
                **kwargs,
            )

    def discover_model_relationships(
        self, bll_modules: Dict
    ) -> List[Tuple[Type[BaseModel], Type[BaseModel], Type[BaseModel], Type]]:
        """
        Discover and map relationships between models.

        This function examines the provided BLL modules to find model relationships,
        including main models, reference models, and network models.

        Args:
            bll_modules: Dictionary mapping module names to module objects

        Returns:
            List of tuples containing (model_class, ref_model_class, network_model_class, manager_class)
        """
        relationships = []
        processed_models = set()

        for module_name, module in bll_modules.items():
            self._processed_modules.add(module_name)

            module_members = inspect.getmembers(module, inspect.isclass)

            model_classes = []
            for name, cls in module_members:
                if name.endswith("Model") and cls not in processed_models:
                    # Skip extension models - they enhance existing types
                    if hasattr(cls, "_is_extension_model") and cls._is_extension_model:
                        logger.debug(
                            f"Skipping extension model {name} in relationship discovery"
                        )
                        continue

                    # Skip abstract base classes
                    if inspect.isabstract(cls):
                        logger.debug(
                            f"Skipping abstract base class {name} in relationship discovery"
                        )
                        continue

                    model_classes.append((name, cls))
                    processed_models.add(cls)

                    # Register model by normalized name for lookup
                    base_name = name.replace("Model", "").lower()
                    self.register_model(cls, base_name)

            for model_name, model_class in model_classes:
                base_name = model_name.replace("Model", "")
                ref_model_name = f"{base_name}ReferenceModel"
                network_model_name = f"{base_name}NetworkModel"
                manager_name = f"{base_name}Manager"
                # TODO @Kristy NetworkModel doesn't exist anymore
                ref_model_class = next(
                    (cls for name, cls in module_members if name == ref_model_name),
                    None,
                )
                network_model_class = next(
                    (cls for name, cls in module_members if name == network_model_name),
                    None,
                )
                manager_class = next(
                    (cls for name, cls in module_members if name == manager_name), None
                )

                if not ref_model_class:
                    ref_model_class = type(
                        ref_model_name,
                        (BaseModel,),
                        {
                            "__annotations__": {"id": str},
                            "__module__": model_class.__module__,
                        },
                    )

                if not network_model_class:
                    network_model_class = type(
                        network_model_name,
                        (BaseModel,),
                        {
                            "__annotations__": {"id": str},
                            "__module__": model_class.__module__,
                        },
                    )

                if manager_class:
                    relationships.append(
                        (
                            model_class,
                            ref_model_class,
                            network_model_class,
                            manager_class,
                        )
                    )

        return relationships

    def collect_model_fields(
        self, model_relationships: List[Tuple]
    ) -> Dict[Type[BaseModel], Dict[str, Any]]:
        """
        Collect fields for all models and reference models.

        Args:
            model_relationships: List of model relationship tuples

        Returns:
            Dictionary mapping model classes to their field definitions
        """
        model_fields_mapping = {}

        # First collect all main model fields
        for model_class, ref_model_class, _, _ in model_relationships:
            model_fields_mapping[model_class] = self.get_model_fields(model_class)

        # Then collect fields for reference models
        for _, ref_model_class, _, _ in model_relationships:
            if ref_model_class not in model_fields_mapping:
                model_fields_mapping[ref_model_class] = self.get_model_fields(
                    ref_model_class
                )

        return model_fields_mapping

    def enhance_model_discovery(
        self, model_fields_mapping: Dict[Type[BaseModel], Dict[str, Any]]
    ) -> None:
        """
        Enhance model discovery by analyzing field relationships.

        This method scans models and their fields to discover relationships
        based on field names that could link to other models.

        Args:
            model_fields_mapping: Dictionary mapping models to their fields
        """
        # Create a temporary lookup based on field names
        field_to_potential_model = {}

        # Scan all models and their fields
        for model_class, fields in model_fields_mapping.items():
            for field_name, field_type in fields.items():
                # Process field type to extract potential model references
                if isinstance(field_type, str):
                    # Handle string references
                    clean_name = field_type.strip("'\"")
                    if clean_name.endswith("Model"):
                        base_name = clean_name.replace("Model", "").lower()
                        if base_name not in field_to_potential_model:
                            field_to_potential_model[base_name] = []
                        if model_class not in field_to_potential_model[base_name]:
                            field_to_potential_model[base_name].append(model_class)

                # Index the field name for potential model matching

                # Use inflect engine
                singular_name = (
                    inflection.singular_noun(field_name.lower()) or field_name.lower()
                )
                if singular_name not in field_to_potential_model:
                    field_to_potential_model[singular_name] = []
                if model_class not in field_to_potential_model[singular_name]:
                    field_to_potential_model[singular_name].append(model_class)

        # Update model registry with additional mappings
        for field_name, potential_models in field_to_potential_model.items():
            if field_name not in self._model_name_to_class and potential_models:
                # Find the most likely model match based on name similarity
                for model_class in potential_models:
                    model_name = stringcase.snakecase(
                        model_class.__name__.replace("Model", "")
                    )
                    if field_name in model_name or model_name in field_name:
                        self.register_model(model_class, field_name)
                        break

                # If no match found by name similarity, use the first candidate
                if field_name not in self._model_name_to_class and potential_models:
                    self.register_model(potential_models[0], field_name)

    def get_model_for_field(
        self,
        field_name: str,
        field_type: Any,
        model_class: Optional[Type[BaseModel]] = None,
    ) -> Optional[Type[BaseModel]]:
        """
        Get the model class for a field based on its name and type.

        This method tries to resolve the model that a field refers to,
        using various heuristics like field name matching, type resolution, etc.

        Args:
            field_name: The name of the field
            field_type: The type of the field
            model_class: Optional parent model class for context

        Returns:
            The model class if found, None otherwise
        """
        # Cache key for performance
        cache_key = f"{field_name}:{str(field_type)}:{model_class.__name__ if model_class else 'None'}"

        relationship_cache = self.cache_manager.get_cache("relationships")
        model_fields_cache = self.cache_manager.get_cache("model_fields")

        if cache_key in relationship_cache:
            return relationship_cache[cache_key]

        # Handle string forward references directly
        if isinstance(field_type, str):
            module_context = inspect.getmodule(model_class) if model_class else None
            resolved = self.resolve_string_reference(field_type, module_context)
            if resolved:
                relationship_cache[cache_key] = resolved
                return resolved

        # Handle list types directly
        if get_origin(field_type) is list or get_origin(field_type) is List:
            element_type = get_args(field_type)[0] if get_args(field_type) else Any

            # Handle string reference in list
            if isinstance(element_type, str):
                module_context = inspect.getmodule(model_class) if model_class else None
                resolved = self.resolve_string_reference(element_type, module_context)
                if resolved:
                    relationship_cache[cache_key] = resolved
                    return resolved

            # Check if the element type is in our model fields
            if element_type in model_fields_cache:
                relationship_cache[cache_key] = element_type
                return element_type

        # Handle Optional types (Union[type, None])
        if get_origin(field_type) is Union:
            args = get_args(field_type)
            for arg in args:
                if arg is not type(None) and arg in model_fields_cache:
                    relationship_cache[cache_key] = arg
                    return arg
                elif isinstance(arg, str):
                    module_context = (
                        inspect.getmodule(model_class) if model_class else None
                    )
                    resolved = self.resolve_string_reference(arg, module_context)
                    if resolved:
                        relationship_cache[cache_key] = resolved
                        return resolved

        # Try to find by matching field name to model names
        model = self.find_model_by_name(field_name)
        if model:
            relationship_cache[cache_key] = model
            return model

        # If we have a model class, check its module for related models first
        if model_class:
            module = inspect.getmodule(model_class)

            # Check all models registered from this module
            for registered_model in self._model_name_to_class.values():
                if inspect.getmodule(registered_model) == module:
                    registered_name = stringcase.snakecase(
                        registered_model.__name__.replace("Model", "")
                    )
                    # Use inflect engine
                    field_singular = (
                        inflection.singular_noun(field_name.lower())
                        or field_name.lower()
                    )
                    if (
                        field_singular == registered_name
                        or field_singular in registered_name
                        or registered_name.endswith(field_singular)
                    ):
                        relationship_cache[cache_key] = registered_model
                        return registered_model

        # Then try the general approach with all models

        for registered_model in self._model_name_to_class.values():
            registered_name = stringcase.snakecase(
                registered_model.__name__.replace("Model", "")
            )
            # Use inflect engine
            field_singular = (
                inflection.singular_noun(field_name.lower()) or field_name.lower()
            )
            if (
                field_singular == registered_name
                or field_singular in registered_name
                or registered_name.endswith(field_singular)
            ):
                relationship_cache[cache_key] = registered_model
                return registered_model

        # No match found
        relationship_cache[cache_key] = None
        return None

    def get_model_hierarchy(
        self, model_class: Type[BaseModel]
    ) -> List[Type[BaseModel]]:
        """
        Get the hierarchy of parent models for a given model.

        This method returns a list of all parent classes of a model
        that are subclasses of BaseModel, useful for inheritance mapping.

        Args:
            model_class: The model class to get the hierarchy for

        Returns:
            List of parent model classes
        """
        model_hierarchy_cache = self.cache_manager.get_cache("model_hierarchy")
        if model_class in model_hierarchy_cache:
            return model_hierarchy_cache[model_class]

        hierarchy = []
        for parent_class in model_class.__mro__[1:]:  # Skip the class itself
            if inspect.isclass(parent_class) and issubclass(parent_class, BaseModel):
                hierarchy.append(parent_class)

        model_hierarchy_cache[model_class] = hierarchy
        return hierarchy

    def clear_caches(self) -> None:
        """Clear all internal caches."""
        self.cache_manager.clear_all_caches()
        self.reference_resolver._model_registry.clear()

        # Clear specialized caches
        self._processed_models.clear()
        self._known_modules.clear()
        self._model_fields_cache.clear()
        self._type_name_mapping.clear()
        self._relationship_cache.clear()
        self._model_hierarchy_cache.clear()
        logger.debug("PydanticUtility caches cleared.")

    def is_model_processed(self, model_class: Type) -> bool:
        """
        Check if a model has already been processed during schema generation.

        Args:
            model_class: The model class to check

        Returns:
            True if the model has been processed, False otherwise
        """
        return model_class in self._processed_models

    def mark_model_processed(self, model_class: Type) -> None:
        """
        Mark a model as processed during schema generation.

        Args:
            model_class: The model class to mark as processed
        """
        self._processed_models.add(model_class)

    def process_model_relationships(
        self,
        model_class: Type[BaseModel],
        processed_models: Set[Type[BaseModel]],
        max_recursion_depth: int = 2,
        recursion_depth: int = 0,
    ) -> Dict[str, Any]:
        """
        Process a model's relationships recursively up to a maximum depth.

        This method traverses through a model's fields and identifies relationships
        to other models, processing them recursively up to the specified maximum depth.

        Args:
            model_class: The model class to process
            processed_models: Set of models already processed to avoid cycles
            max_recursion_depth: Maximum recursion depth for nested models
            recursion_depth: Current recursion depth

        Returns:
            Dictionary of field name to related model mappings
        """
        if recursion_depth > max_recursion_depth or model_class in processed_models:
            return {}

        # Mark as processed to prevent cycles
        processed_models.add(model_class)

        # Use relationship analyzer for comprehensive analysis
        analysis = self.relationship_analyzer.analyze_model_relationships(model_class)
        relationships = {}

        # Process references and collections from the analysis
        for ref in analysis.get("references", []):
            target_model = ref["model_class"]
            relationships[ref["field_name"]] = target_model

            # Process nested model relationships if not at max depth
            if recursion_depth < max_recursion_depth:
                self.process_model_relationships(
                    target_model,
                    processed_models,
                    max_recursion_depth,
                    recursion_depth + 1,
                )

        for coll in analysis.get("collections", []):
            target_model = coll["item_model"]
            relationships[coll["field_name"]] = target_model

            # Process nested model relationships if not at max depth
            if recursion_depth < max_recursion_depth:
                self.process_model_relationships(
                    target_model,
                    processed_models,
                    max_recursion_depth,
                    recursion_depth + 1,
                )

        # Also check for string references that need resolution
        fields = self.get_model_fields(model_class)
        for field_name, field_type in fields.items():
            if (
                self.field_processor.should_skip_field(field_name)
                or field_name in relationships
            ):
                continue

            # Extract inner type for Optional fields
            inner_type = self.type_introspector.extract_optional_inner_type(field_type)

            # Handle string references
            if isinstance(inner_type, str):
                module_context = inspect.getmodule(model_class)
                resolved_type = self.resolve_string_reference(
                    inner_type, module_context
                )

                if resolved_type and self.type_introspector.is_pydantic_model(
                    resolved_type
                ):
                    relationships[field_name] = resolved_type

                    if recursion_depth < max_recursion_depth:
                        self.process_model_relationships(
                            resolved_type,
                            processed_models,
                            max_recursion_depth,
                            recursion_depth + 1,
                        )

        return relationships


def validate_entity_fields(
    model_class: Type[BaseModel], fields: Optional[List[str]]
) -> None:
    """
    Validate that requested fields exist on the entity model.

    Args:
        model_class: The entity model class to validate against
        fields: List of field names to validate

    Raises:
        ValueError: If any field doesn't exist on the model
    """
    if not fields:
        return

    if not hasattr(model_class, "model_fields"):
        return  # Skip validation if model doesn't have fields

    valid_fields = set(model_class.model_fields.keys())
    invalid_fields = set(fields) - valid_fields

    if invalid_fields:
        raise ValueError(
            f"Invalid fields: {', '.join(sorted(invalid_fields))}. "
            f"Valid fields are: {', '.join(sorted(valid_fields))}"
        )


def validate_entity_includes(
    model_class: Type[BaseModel], includes: Optional[List[str]]
) -> None:
    """
    Validate that requested includes are valid relationships.

    Args:
        model_class: The entity model class to validate against
        includes: List of relationship names to validate

    Raises:
        ValueError: If any include doesn't exist as a relationship
    """
    if not includes:
        return

    # Check for Reference classes which indicate relationships
    valid_includes = set()

    # Look for Reference classes in the model
    if hasattr(model_class, "Reference"):
        reference_class = getattr(model_class, "Reference")
        # Get all attributes of the Reference class that might be relationships
        for attr_name in dir(reference_class):
            if not attr_name.startswith("_"):
                attr = getattr(reference_class, attr_name)
                if hasattr(attr, "__name__") and attr.__name__.endswith("Model"):
                    # Convert ModelName to snake_case for include validation
                    include_name = stringcase.snakecase(
                        attr.__name__.replace("Model", "")
                    )
                    valid_includes.add(include_name)
                    # Also allow the original name
                    valid_includes.add(attr_name.lower())

    # Also check model fields for relationship hints
    if hasattr(model_class, "model_fields"):
        for field_name, field_info in model_class.model_fields.items():
            # If field name ends with _id, the relationship might be the name without _id
            if field_name.endswith("_id"):
                relationship_name = field_name[:-3]  # Remove '_id'
                valid_includes.add(relationship_name)
                # Also add plural form
                valid_includes.add(inflection.plural(relationship_name))

    # If we couldn't determine valid includes, allow all (let BLL handle validation)
    if not valid_includes:
        return

    invalid_includes = set(includes) - valid_includes
    if invalid_includes:
        raise ValueError(
            f"Invalid includes: {', '.join(sorted(invalid_includes))}. "
            f"Valid includes are: {', '.join(sorted(valid_includes))}"
        )


class BaseNetworkModel(BaseModel):
    """
    Base model for all network operations that includes common query parameters.

    This model provides include and fields parameters that can be used across
    all REST operations (GET, LIST, POST, PUT, PATCH, SEARCH) to control
    the response data structure.
    """

    model_config = {"extra": "forbid"}

    include: Optional[Union[List[str], str]] = Field(
        None,
        description="List of related entities to include in the response, or CSV string of entity names",
    )
    fields: Optional[Union[List[str], str]] = Field(
        None,
        description="List of specific fields to include in the response, or CSV string of field names",
    )


class ModelRegistry(AbstractRegistry):
    """
    Registry for managing Pydantic models and their database/API bindings.

    This class provides encapsulated model registration that allows for:
    - Per-application model sets (avoiding global state contamination)
    - Deferred processing of model extensions and dependencies
    - Isolated testing environments with different model configurations
    - Clean separation between model binding and schema generation

    The registry operates in two phases:
    1. Bind phase: Models are registered but not processed
    2. Commit phase: All models are processed, dependencies resolved, and schemas generated
    """

    def __init__(
        self,
        app_instance=None,
        database_manager=None,
        extension_registry=None,
        auto_bind_models=False,
        extensions_str="",
    ):
        """Initialize a new model registry.

        Args:
            app_instance: Optional FastAPI app instance to associate with this registry
            database_manager: Optional DatabaseManager instance to use for this registry
            extension_registry: Optional ExtensionRegistry instance containing extension models
            auto_bind_models: If True, automatically import and bind core and extension models
            extensions_str: Comma-separated list of extensions to load (used with auto_bind_models)
        """
        self.app = app_instance
        self.database_manager = database_manager
        self.extension_registry = extension_registry
        self.utility = PydanticUtility()

        # Model storage
        self.bound_models: OrderedSet[Type[BaseModel]] = OrderedSet()
        self.extension_models: Dict[Type[BaseModel], List[Type]] = (
            {}
        )  # target -> [extensions]
        self.model_metadata: Dict[Type[BaseModel], Dict[str, Any]] = {}
        self._model_dependencies: Dict[Type[BaseModel], Set[Type[BaseModel]]] = (
            {}
        )  # model -> set of models it depends on

        # Initialize cache using CacheManager
        self._cache_manager = CacheManager()
        self._import_cache = self._cache_manager.get_cache("imports")

        # Committed state
        self._locked = False
        self.db_models: Dict[Type[BaseModel], Type] = {}
        self.model_relationships: List[Tuple] = []
        self.dependency_order: List[Type[BaseModel]] = []
        self.declarative_base = None  # Will be set during commit()

        # Router and schema storage
        self.ep_routers = []
        self.gql = None

        logger.debug("Initialized new ModelRegistry")

        # Auto-bind models if requested
        if auto_bind_models:
            self._auto_bind_models(extensions_str)

    def apply(self, type: Type) -> Type:
        if type is None:
            raise TypeError(f"Cannot apply registry to None type")
        # print(f"BEFORE APPLY: {list(type.model_fields.keys())}")
        new_type = next(
            (
                possible_type
                for possible_type in self.bound_models
                if possible_type.__name__ == type.__name__
            ),
            None,
        )
        # print(f"AFTER APPLY: {list(new_type.model_fields.keys())}")
        if not new_type:
            raise TypeError(f"No matching type found in registry for {type.__name__}!")
        return new_type

    def _generate_network_class(self, model):
        """
        Generate the static Network class for this Pydantic model.

        The Network class contains inner classes for different REST operations:
        - POST: For creating new entities (with include/fields support)
        - PUT: For updating existing entities (with include/fields support)
        - PATCH: For partial updates (with include/fields support, if model has Patch class)
        - SEARCH: For search/filter operations (with include/fields support)
        - GET: For single entity query parameters (include/fields validation)
        - LIST: For list/pagination query parameters (include/fields validation)
        - ResponseSingle: For single entity responses
        - ResponsePlural: For list responses
        """
        from typing import List, Optional, Union, get_origin

        import stringcase
        from pydantic import BaseModel, Field

        from lib.Environment import inflection
        from lib.Logging import logger

        # Get the model name for the network fields (snake_case)
        model_name = model.__name__
        if model_name.endswith("Model"):
            model_name = model_name[:-5]  # Remove 'Model' suffix
        field_name = stringcase.snakecase(model_name)
        print(f"DEBUG _generate_network_class: model={model}, field_name={field_name}")

        # Create the Network class statically
        network_model_name = f"{model.__name__.replace('Model', '')}Network"
        network_attrs = {}

        create_model = (
            getattr(model, "Create", model) if hasattr(model, "Create") else model
        )
        network_attrs["POST"] = type(
            "POST",
            (BaseNetworkModel,),
            {
                "__annotations__": {field_name: create_model},
                "__module__": model.__module__,
            },
        )

        update_model = (
            getattr(model, "Update", model) if hasattr(model, "Update") else model
        )
        network_attrs["PUT"] = type(
            "PUT",
            (BaseNetworkModel,),
            {
                "__annotations__": {field_name: update_model},
                "__module__": model.__module__,
            },
        )

        if hasattr(model, "Patch"):
            patch_model = getattr(model, "Patch")
            network_attrs["PATCH"] = type(
                "PATCH",
                (BaseNetworkModel,),
                {
                    "__annotations__": {field_name: patch_model},
                    "__module__": model.__module__,
                },
            )

        # SEARCH class - uses Search if available, otherwise create a basic search
        if hasattr(model, "Search"):
            search_model = getattr(model, "Search")
        else:
            # Create a basic search model with optional versions of main fields
            search_annotations = {}
            if hasattr(model, "model_fields"):
                for field_name_inner, field_info in model.model_fields.items():
                    # Make all search fields optional
                    field_type = field_info.annotation
                    # Handle Union types (Optional fields)
                    if get_origin(field_type) is Union:
                        search_annotations[field_name_inner] = field_type
                    else:
                        search_annotations[field_name_inner] = Optional[field_type]

            search_model = type(
                "Search",
                (BaseModel,),
                {
                    "__annotations__": search_annotations,
                    "__module__": model.__module__,
                },
            )

        network_attrs["SEARCH"] = type(
            "SEARCH",
            (BaseNetworkModel,),
            {
                "__annotations__": {field_name: search_model},
                "__module__": model.__module__,
            },
        )

        # GET class - for single entity query parameters (no id field - that's a path param)
        network_attrs["GET"] = type(
            "GET",
            (BaseNetworkModel,),
            {
                "__annotations__": {},  # Only include/fields from BaseNetworkModel
                "__module__": model.__module__,
            },
        )

        # LIST class - for list/pagination query parameters
        # Build annotations and class attributes for LIST
        list_annotations = {
            "offset": int,
            "limit": int,
            "sort_by": Optional[str],
            "sort_order": Optional[str],
        }
        list_attrs = {
            "__module__": model.__module__,
            "offset": Field(
                0, ge=0, description="Number of items to skip for pagination"
            ),
            "limit": Field(
                1000, ge=1, le=1000, description="Maximum number of items to return"
            ),
            "sort_by": Field(None, description="Field to sort results by"),
            "sort_order": Field(
                "asc",
                pattern="^(asc|desc)$",
                description="Sort direction (asc or desc)",
            ),
        }

        # Add model fields as optional filter parameters
        # This allows filtering like GET /entity?id=xxx&name=yyy
        model_fields = getattr(model, "model_fields", {})

        # Define types that are safe for query parameter filtering
        # These are simple scalar types that can be easily parsed from query strings
        def is_filterable_type(field_type) -> bool:
            """Check if a field type is suitable for query parameter filtering."""
            # Get the origin type for generic types (e.g., Optional[str] -> Union)
            origin = get_origin(field_type)

            # Handle Optional types (Union[X, None])
            if origin is Union:
                args = get_args(field_type)
                # Check if it's Optional (Union with None)
                non_none_args = [arg for arg in args if arg is not type(None)]
                if len(non_none_args) == 1:
                    # It's Optional[X], check the inner type
                    return is_filterable_type(non_none_args[0])
                return False

            # Handle List types - not filterable via simple query params
            if origin in (list, List):
                return False

            # Handle Dict types - not filterable via simple query params
            if origin in (dict, Dict):
                return False

            # Simple scalar types that are safe for filtering
            filterable_types = (str, int, float, bool)

            # Check if it's a simple filterable type
            if field_type in filterable_types:
                return True

            # Check for string subclasses (like UUID which is often str)
            try:
                if isinstance(field_type, type) and issubclass(field_type, str):
                    return True
            except TypeError:
                pass

            return False

        for field_name_inner, field_info in model_fields.items():
            # Skip fields that are already defined (like offset, limit, etc.)
            if field_name_inner in list_annotations:
                continue

            field_type = field_info.annotation

            # Only add filterable types
            if is_filterable_type(field_type):
                # Make the field Optional for filtering
                if get_origin(field_type) is Union:
                    # Already Optional, use as-is
                    list_annotations[field_name_inner] = field_type
                else:
                    # Wrap in Optional
                    list_annotations[field_name_inner] = Optional[field_type]

                # Add Field with description
                field_description = (
                    field_info.description
                    if field_info.description
                    else f"Filter by {field_name_inner}"
                )
                list_attrs[field_name_inner] = Field(
                    None, description=field_description
                )

        list_attrs["__annotations__"] = list_annotations
        network_attrs["LIST"] = type(
            "LIST",
            (BaseNetworkModel,),
            list_attrs,
        )

        # ResponseSingle class - wraps the base model
        print(f"DEBUG ResponseSingle creation: field_name={field_name}, model={model}")
        network_attrs["ResponseSingle"] = type(
            "ResponseSingle",
            (BaseModel,),
            {
                "__annotations__": {field_name: model},
                "__module__": model.__module__,
            },
        )
        print(
            f"DEBUG ResponseSingle created: {network_attrs['ResponseSingle'].model_fields}"
        )

        # ResponsePlural class - wraps a list of the base model
        # Use inflection for proper pluralization
        plural_field_name = inflection.plural(field_name)
        network_attrs["ResponsePlural"] = type(
            "ResponsePlural",
            (BaseModel,),
            {
                "__annotations__": {plural_field_name: List[model]},
                "__module__": model.__module__,
            },
        )

        # Create the main Network class and attach it to the model
        network_class = type(
            network_model_name,
            (),
            {
                **network_attrs,
                "__module__": model.__module__,
            },
        )

        # Attach the Network class to the model
        model.Network = network_class

        logger.debug(
            f"Generated Network class for {model.__name__}: {network_model_name}"
        )

    def _auto_bind_models(self, extensions_str: str = ""):
        """Automatically import and bind core and extension models.

        Args:
            extensions_str: Comma-separated list of extensions to load
        """
        import sys

        # Prepare scopes - always include core logic
        scopes = ["logic"]

        # Add extension scopes if extensions are configured
        if extensions_str:
            extension_names = [
                name.strip() for name in extensions_str.split(",") if name.strip()
            ]
            if extension_names:
                extension_scopes = [
                    f"extensions.{ext_name}" for ext_name in extension_names
                ]
                scopes.extend(extension_scopes)
                logger.debug(f"Auto-binding models for scopes: {scopes}")

        # Use _scoped_import to import BLL modules
        try:
            imported_modules, import_errors = self._scoped_import(
                file_type="BLL", scopes=scopes
            )

            if import_errors:
                logger.warning(f"Errors importing BLL modules: {import_errors}")

            # Process imported modules and bind models
            for module_name in imported_modules:
                try:
                    module = sys.modules.get(module_name)
                    if module:
                        # Determine if this is an extension module
                        is_extension_module = "extensions." in module_name

                        # Look for Pydantic models and manager classes in the module
                        for attr_name in dir(module):
                            attr = getattr(module, attr_name)

                            # Check for domain models - must inherit from ApplicationModel and have ModelMeta metaclass
                            # but exclude ApplicationModel itself and other mixin base classes
                            if (
                                hasattr(attr, "__bases__")
                                and any(
                                    base.__name__ == "ApplicationModel"
                                    for base in attr.__mro__
                                )
                                and hasattr(attr, "__annotations__")
                                and hasattr(attr, "__class__")
                                and hasattr(attr.__class__, "__name__")
                                and attr.__class__.__name__ == "ModelMeta"
                                and attr.__name__ != "ApplicationModel"
                                and not attr.__name__.endswith("MixinModel")
                            ):
                                # Check if this is an extension model (for extension modules)
                                if (
                                    is_extension_module
                                    and hasattr(attr, "_is_extension_model")
                                    and hasattr(attr, "_extension_target")
                                ):
                                    # This is an extension model - bind it as an extension
                                    try:
                                        target_model = attr._extension_target
                                        self.bind_extension(target_model, attr)
                                        logger.debug(
                                            f"Bound extension {attr.__name__} to {target_model.__name__} from {module_name}"
                                        )
                                    except Exception as e:
                                        logger.debug(
                                            f"Could not bind extension {attr.__name__}: {e}"
                                        )
                                else:
                                    # This is a regular Pydantic model - bind it normally
                                    try:
                                        print(
                                            f"DEBUG: Attempting to bind {attr.__name__} from {module_name}"
                                        )
                                        self.bind(attr)
                                        logger.debug(
                                            f"Bound {'extension' if is_extension_module else 'core'} model {attr.__name__} from {module_name}"
                                        )
                                    except Exception as e:
                                        logger.debug(
                                            f"Could not bind model {attr.__name__}: {e}"
                                        )

                            # Check for manager classes that have Model attributes to bind
                            elif (
                                inspect.isclass(attr)
                                and attr_name.endswith("Manager")
                                and hasattr(attr, "BaseModel")
                                and hasattr(attr.BaseModel, "__bases__")
                                and any(
                                    base.__name__ == "BaseModel"
                                    for base in attr.BaseModel.__mro__
                                )
                            ):
                                # This is a manager class with a Pydantic model - bind the model
                                try:
                                    self.bind(attr.BaseModel)
                                    logger.debug(
                                        f"Bound model {attr.BaseModel.__name__} from manager {attr.__name__} in {module_name}"
                                    )
                                except Exception as e:
                                    logger.debug(
                                        f"Could not bind model {attr.BaseModel.__name__} from manager {attr.__name__}: {e}"
                                    )
                except Exception as e:
                    logger.error(f"Error processing module {module_name}: {e}")

            logger.debug(
                f"Successfully auto-bound models for scopes: {scopes} (total models: {len(self.bound_models)})"
            )
        except Exception as e:
            logger.error(f"Error auto-binding models: {e}")

    @property
    def DB(self):
        """Provide direct access to database operations via the attached DatabaseManager.

        This allows any method that receives a model_registry to access database functionality
        without requiring separate db_manager and db parameters.

        Returns:
            DatabaseManager instance with convenient session access
        """
        if not self.database_manager:
            raise RuntimeError("No DatabaseManager attached to this ModelRegistry")

        # Return a wrapper that provides convenient session access
        class DatabaseProxy:
            def __init__(self, db_manager):
                self._db_manager = db_manager

            def session(self):
                """Get a new database session."""
                return self._db_manager.get_session()

            def get_session(self):
                """Get a new database session (alias for session())."""
                return self._db_manager.get_session()

            @property
            def manager(self):
                """Direct access to the underlying DatabaseManager."""
                return self._db_manager

            def __getattr__(self, name):
                """Delegate all other attributes to the DatabaseManager."""
                return getattr(self._db_manager, name)

        return DatabaseProxy(self.database_manager)

    def bind(self, model: Type[BaseModel], **metadata) -> None:
        """
        Bind a model to this registry with dependency analysis.

        Args:
            model: The Pydantic model to bind
            **metadata: Additional metadata for the model
        """
        if self._locked:
            raise RuntimeError("Cannot bind models after registry has been committed")

        if not (inspect.isclass(model) and issubclass(model, BaseModel)):
            raise ValueError(f"Model must be a Pydantic BaseModel subclass: {model}")

        # Skip binding extension models directly - they only extend existing models
        if hasattr(model, "_is_extension_model"):
            return

        # Analyze dependencies before adding
        dependencies = self._analyze_model_dependencies(model)
        self._model_dependencies[model] = dependencies

        # Add the model using topological ordering
        self._add_model_with_dependencies(model, metadata)

        # Extensions are no longer tracked globally - they will be discovered
        # and bound when extension modules are imported via scoped_import
        # or explicitly bound via bind_extension() method

        logger.debug(f"Bound model {model.__name__} to registry")

        # Mark registry as needing commit
        self._locked = False

    def _analyze_model_dependencies(
        self, model: Type[BaseModel]
    ) -> Set[Type[BaseModel]]:
        """
        Analyze a model's fields to find dependencies on other models.

        Args:
            model: The model to analyze

        Returns:
            Set of models that this model depends on
        """
        dependencies = set()

        # Get model fields
        model_fields = self.utility.get_model_fields(model)

        for field_name, field_type in model_fields.items():
            # Skip internal fields
            if self.utility.field_processor.should_skip_field(field_name):
                continue

            # Check if field references another model
            referenced_model = self.utility.get_model_for_field(
                field_name, field_type, model
            )

            if referenced_model and referenced_model != model:
                # Check if it's a model we manage (BaseModel subclass)
                if inspect.isclass(referenced_model) and issubclass(
                    referenced_model, BaseModel
                ):
                    dependencies.add(referenced_model)
                    logger.debug(
                        f"Model {model.__name__} depends on {referenced_model.__name__} via field {field_name}"
                    )

        return dependencies

    def _add_model_with_dependencies(
        self, model: Type[BaseModel], metadata: Dict[str, Any]
    ) -> None:
        """
        Add a model to bound_models ensuring all its dependencies are added first.

        This maintains topological ordering in the OrderedSet.

        Args:
            model: The model to add
            metadata: Model metadata
        """
        # If model is already bound, nothing to do
        if model in self.bound_models:
            print(f"DEBUG: Model {model.__name__} already bound, skipping")
            return

        # Check for duplicate model names with different class objects (this should never happen)
        for existing_model in self.bound_models:
            if (
                existing_model.__name__ == model.__name__
                and existing_model is not model
            ):
                raise RuntimeError(
                    f"CRITICAL ERROR: Duplicate model class detected! "
                    f"Model '{model.__name__}' exists with different class objects:\n"
                    f"  Existing: {existing_model} (ID: {id(existing_model)}) from {existing_model.__module__}\n"
                    f"  New: {model} (ID: {id(model)}) from {model.__module__}\n"
                    f"This indicates a module import or class loading issue that must be fixed."
                )

        # First, ensure all dependencies are added
        dependencies = self._model_dependencies.get(model, set())
        for dep_model in dependencies:
            if dep_model not in self.bound_models:
                # Check if we have metadata for the dependency
                dep_metadata = self.model_metadata.get(dep_model, {})
                # Recursively add the dependency
                self._add_model_with_dependencies(dep_model, dep_metadata)

        # Now add this model
        # Check for name duplicates before adding - this should never happen
        for existing_model in self.bound_models:
            if existing_model.__name__ == model.__name__:
                raise RuntimeError(
                    f"CRITICAL ERROR: Duplicate model name detected!\n"
                    f"  Existing: {existing_model.__name__} (ID: {id(existing_model)}) from {existing_model.__module__}\n"
                    f"  New: {model.__name__} (ID: {id(model)}) from {model.__module__}\n"
                    f"This indicates a module import or class loading issue that must be fixed."
                )

        self.bound_models.add(model)
        self.model_metadata[model] = metadata

        logger.debug(
            f"Added model {model.__name__} to bound_models at position {len(self.bound_models)}"
        )

    def bind_extension(
        self, target_model: Type[BaseModel], extension_model: Type
    ) -> None:
        """
        Bind an extension model to its target.

        Args:
            target_model: The model being extended
            extension_model: The extension model
        """
        if not hasattr(extension_model, "_is_extension_model"):
            raise ValueError(
                f"Model {extension_model} is not marked as an extension model"
            )

        if not hasattr(extension_model, "_extension_target"):
            raise ValueError(f"Extension model {extension_model} has no target model")

        # Store the extension for processing during commit
        if target_model not in self.extension_models:
            self.extension_models[target_model] = []
        self.extension_models[target_model].append(extension_model)

        # Mark registry as needing commit
        self._locked = False

    def commit(self, extensions=None, database_manager=None) -> None:
        """Process all bound models and generate schemas.

        This method:
        1. Resolves model dependencies and extension relationships
        2. Creates a database
        3. Runs migrations
        4. Creates SQLAlchemy table objects and metadata
        5. Generates FastAPI routers
        6. Creates GraphQL schema
        7. Locks the registry against further changes

        Args:
            database_manager: Optional DatabaseManager instance for SQLAlchemy integration
        """
        logger.debug(
            f"ModelRegistry.commit() called with extensions={extensions}, database_manager={database_manager}"
        )

        if self._locked:
            logger.warning("Registry already committed, skipping")
            return

        logger.debug(f"Committing registry with {len(self.bound_models)} models")

        # Use provided database manager or the one attached to the registry
        if database_manager:
            self.database_manager = database_manager
        elif not self.database_manager:
            # Create a default instance if none provided
            # Use test prefix to avoid touching production database
            # (production code always provides a database_manager via app.instance())
            from database.DatabaseManager import DatabaseManager

            # DatabaseManager.__init__ already calls init_engine_config(db_prefix)
            # so we don't need to call it again here
            self.database_manager = DatabaseManager("test.registry_fallback")

        # Phase 1: Process extensions
        self._process_extensions()

        # Phase 1.5: Generate Network classes for all bound models
        logger.debug(
            f"Generating Network classes for {len(self.bound_models)} bound models"
        )
        for model in self.bound_models:
            if not hasattr(model, "Network"):
                try:
                    self._generate_network_class(model)
                    logger.debug(f"Generated Network class for {model.__name__}")
                except Exception as e:
                    logger.warning(
                        f"Failed to generate Network class for {model.__name__}: {e}"
                    )

        # Phase 2: Resolve dependencies
        self._resolve_dependencies()

        # Database migration
        if hasattr(self.database_manager, "Base") and self.database_manager.Base:
            self.database_manager.Base._model_registry = self
            logger.debug(
                "Attached ModelRegistry to DatabaseManager.Base for migration access"
            )

        engine = self.database_manager.get_setup_engine()
        db_type = self.database_manager.DATABASE_TYPE
        db_name = self.database_manager.DATABASE_NAME

        if db_type != "sqlite":
            logger.info("Connecting to database...")
            for retry_count in range(5):
                try:
                    connection = engine.connect()
                    connection.close()
                    break
                except Exception as e:
                    logger.error(
                        f"Error connecting to database (attempt {retry_count+1}/5)",
                        exc_info=True,
                    )
                    if retry_count == 4:
                        raise Exception(
                            "Failed to connect to database after maximum retries"
                        )
                    time.sleep(5)

        custom_db_info = {
            "type": self.database_manager.DATABASE_TYPE,
            "name": self.database_manager.DATABASE_NAME,
            "url": self.database_manager.DATABASE_URI,
            "file_path": getattr(self.database_manager, "_database_file_path", None),
        }
        logger.info(f"Migration target database: {custom_db_info}")

        migration_manager = MigrationManager(custom_db_info=custom_db_info)
        extensions_csv = self.extension_registry.csv if self.extension_registry else ""

        result = migration_manager.run_all_migrations(
            "upgrade",
            "head",
            extensions=extensions_csv.split(",") if extensions_csv else [],
        )
        if not result:
            import os

            worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
            db_info_str = (
                f"db_name={custom_db_info['name']}, db_type={custom_db_info['type']}"
            )
            logger.error(
                f"Failed to apply migrations. Worker={worker_id}, {db_info_str}, Result={result}"
            )
            raise Exception(
                f"Failed to apply migrations. Worker={worker_id}, {db_info_str}"
            )
        logger.info(f"Successfully verified database migrations for {db_name}")

        configure_mappers()

        # Phase 3: Create SQLAlchemy models
        self._create_sqlalchemy_models()

        # Phase 4: Generate routers
        self._generate_routers()

        # Lock the registry before creating schema (required for schema generation)
        self._locked = True

        from lib.Pydantic2Strawberry import GraphQLManager

        # Create schema using the new instance-based GraphQLManager
        self.graphql_manager = GraphQLManager(self)
        self.gql = self.graphql_manager.create_schema()

        # Phase 5: Seed the database with initial data
        seed_db_value = env("SEED_DATA")
        logger.info(f"SEED_DATA env var value: '{seed_db_value}'")
        if seed_db_value.lower() == "true":
            logger.info("Calling _seed() method...")
            self._seed()
        else:
            logger.info(f"Skipping seeding because SEED_DATA='{seed_db_value}'")

        logger.debug("Registry committed successfully")
        return self

    def _process_extensions(self) -> None:
        """Process all registered model extensions."""
        from lib.Logging import logger
        from lib.Pydantic2SQLAlchemy import _apply_model_extension

        logger.debug(
            f"_process_extensions called with extension_registry: {self.extension_registry}"
        )

        # First, discover extension models from BLL and PRV files
        if self.extension_registry:
            extension_names = [ext.name for ext in self.extension_registry.extensions]
            logger.debug(
                f"Discovering extension models for extensions: {extension_names}"
            )
            self.extension_registry.discover_extension_models(extension_names)
            logger.debug(
                f"Discovered extension models: {list(self.extension_registry.extension_models.keys())}"
            )
        else:
            logger.debug("No ExtensionRegistry available for model discovery")

        # Register external models from PRV files
        if not self.extension_registry:
            logger.debug(
                "No ExtensionRegistry available for external model registration"
            )
        else:
            logger.debug("Registering external models from PRV files")
            external_model_count = 0
            for key, models in self.extension_registry.extension_models.items():
                if key.startswith("external."):
                    for model in models:
                        try:
                            model_name = model.__name__
                            base_name = (
                                model_name[:-5].lower()
                                if model_name.endswith("Model")
                                else model_name.lower()
                            )
                            self.utility.register_model(model, base_name)
                            fields = self.utility.get_model_fields(model)
                            self.utility._model_fields_cache[model] = fields
                            logger.debug(
                                f"Registered external model {model_name} as '{base_name}'"
                            )
                            external_model_count += 1
                        except Exception as e:
                            logger.error(
                                f"Failed to register external model {model}: {e}"
                            )
            logger.debug(f"Registered {external_model_count} external models")

        # Process extensions from the extension registry if available
        if self.extension_registry:
            logger.debug(
                f"Processing extensions from ExtensionRegistry with {len(self.bound_models)} bound models"
            )
            logger.debug(
                f"ExtensionRegistry has extensions: {list(self.extension_registry.extension_models.keys())}"
            )

            for target_model in self.bound_models:
                target_key = f"{target_model.__module__}.{target_model.__name__}"
                logger.debug(
                    f"Processing bound model: {target_model.__name__} with key: {target_key}"
                )
                if target_model.__name__ == "UserModel":
                    logger.debug(
                        f"Found UserModel - checking for extensions with key {target_key}"
                    )

                extension_models = (
                    self.extension_registry.get_extension_models_for_target(
                        target_model
                    )
                )
                logger.debug(
                    f"Found {len(extension_models)} extensions for {target_model.__name__}"
                )
                if target_model.__name__ == "UserModel":
                    logger.debug(
                        f"UserModel extensions: {[ext.__name__ for ext in extension_models]}"
                    )
                    logger.debug(
                        f"Found {len(extension_models)} extensions for UserModel"
                    )
                    if extension_models:
                        for ext in extension_models:
                            logger.debug(f"UserModel extension: {ext.__name__}")

                for extension_model in extension_models:
                    try:
                        # Apply the extension
                        logger.debug(
                            f"Applying extension {extension_model.__name__} to {target_model.__name__}"
                        )
                        if target_model.__name__ == "UserModel":
                            logger.debug(
                                f"UserModel fields before extension: {list(target_model.model_fields.keys())}"
                            )
                            logger.debug(f"UserModel ID before: {id(target_model)}")
                        _apply_model_extension(target_model, extension_model)
                        if target_model.__name__ == "UserModel":
                            logger.debug(
                                f"UserModel fields after extension: {list(target_model.model_fields.keys())}"
                            )
                            logger.debug(f"UserModel ID after: {id(target_model)}")
                        logger.debug(
                            f"Successfully applied extension {extension_model.__name__} to {target_model.__name__}"
                        )
                        # Also store in local extension_models for backward compatibility
                        if target_model not in self.extension_models:
                            self.extension_models[target_model] = []
                        self.extension_models[target_model].append(extension_model)
                    except Exception as e:
                        logger.debug(
                            f"Failed to apply extension {extension_model.__name__} to {target_model.__name__}: {e}"
                        )
                        logger.error(
                            f"Failed to apply extension {extension_model.__name__} to {target_model.__name__}: {e}"
                        )
                        raise
        else:
            logger.warning("No ExtensionRegistry available for processing extensions")

        # Also process any manually registered extensions
        for target_model, extensions in self.extension_models.items():
            for extension_model in extensions:
                try:
                    # Apply the extension
                    _apply_model_extension(target_model, extension_model)
                    logger.debug(
                        f"Applied extension {extension_model.__name__} to {target_model.__name__}"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to apply extension {extension_model.__name__} to {target_model.__name__}: {e}"
                    )
                    raise

    def _resolve_dependencies(self) -> None:
        """Resolve model dependencies and determine processing order."""
        # Discover relationships between models
        bll_modules = {}

        # Group models by module to match existing discovery pattern
        for model in self.bound_models:
            module_name = model.__module__
            if module_name not in bll_modules:
                import sys

                module = sys.modules.get(module_name)
                if module:
                    bll_modules[module_name] = module
                    logger.debug(
                        f"Added BLL module for bound model {model.__name__}: {module_name}"
                    )

        logger.debug(f"BLL modules for discovery: {list(bll_modules.keys())}")

        # Use utility to discover relationships
        self.model_relationships = self.utility.discover_model_relationships(
            bll_modules
        )

        logger.debug(f"Discovered {len(self.model_relationships)} model relationships")

        # Collect all model fields for relationship processing
        model_fields_mapping = self.utility.collect_model_fields(
            self.model_relationships
        )

        # Enhance model discovery
        self.utility.enhance_model_discovery(model_fields_mapping)

        # Build dependency order from our already-sorted OrderedSet
        # Since we've been adding models in dependency order during bind(),
        # the OrderedSet already maintains the correct order
        self.dependency_order = list(self.bound_models)

        logger.debug(f"Resolved dependencies for {len(self.dependency_order)} models")
        logger.debug(f"Dependency order: {[m.__name__ for m in self.dependency_order]}")

    def _seed(self) -> None:
        """
        Seed the database with initial data from all models.
        Uses the dependency order already established in bound_models.
        """
        from database.StaticSeeder import seed_model

        logger.log("SQL", "Starting database seeding process...")
        logger.log("SQL", f"Total bound models: {len(self.bound_models)}")

        session = self.database_manager.get_session()
        try:
            # Use the already sorted models from our OrderedSet
            models_to_seed = []

            # Process models in dependency order
            for pydantic_model in self.bound_models:
                logger.log(
                    "SQL", f"Checking model {pydantic_model.__name__} for seed_data..."
                )
                # Check if model has seed data
                if hasattr(pydantic_model, "seed_data"):
                    logger.log(
                        "SQL",
                        f"Model {pydantic_model.__name__} HAS seed_data attribute",
                    )
                    # Get the SQLAlchemy model
                    if hasattr(pydantic_model, "DB") and callable(pydantic_model.DB):
                        db_model = pydantic_model.DB(self.database_manager.Base)
                        models_to_seed.append(db_model)
                        logger.log(
                            "SQL",
                            f"Found model with seed_data: {pydantic_model.__name__}",
                        )
                else:
                    logger.log(
                        "SQL",
                        f"Model {pydantic_model.__name__} does NOT have seed_data",
                    )

            logger.log("SQL", f"Found {len(models_to_seed)} models to seed")
            logger.log(
                "SQL",
                f"Models in dependency order: {[model.__name__ for model in models_to_seed]}",
            )

            # Seed all models in dependency order
            for model in models_to_seed:
                seed_model(model, session, self.database_manager, self)

            session.commit()
            logger.log("SQL", "Database seeding completed successfully")

        except Exception as e:
            logger.error(f"Error during database seeding: {e}")
            session.rollback()
            raise
        finally:
            session.close()

    def _create_sqlalchemy_models(self):
        """
        Create SQLAlchemy models for all bound Pydantic models.

        This method generates SQLAlchemy models using the declarative base
        and stores them in the registry for later access.
        """
        logger.debug(
            f"Creating SQLAlchemy models for {len(self.bound_models)} bound models"
        )

        # Use the DatabaseManager's declarative base if available, otherwise create one
        if self.database_manager and hasattr(self.database_manager, "Base"):
            self.declarative_base = self.database_manager.Base
            logger.debug(
                f"Using DatabaseManager's declarative_base: {self.declarative_base}"
            )
        elif self.declarative_base is None:
            from sqlalchemy.ext.declarative import declarative_base

            self.declarative_base = declarative_base()
            logger.debug(f"Created isolated declarative_base: {self.declarative_base}")

        # Attach the registry to the declarative base for model creation
        self.declarative_base._model_registry = self

        for model_class in self.bound_models:
            try:
                logger.debug(f"Processing model: {model_class.__name__}")

                # Use the new .DB(declarative_base) method to get/create the SQLAlchemy model
                if hasattr(model_class, "DB"):
                    sqlalchemy_model = model_class.DB(self.declarative_base)

                    if sqlalchemy_model is not None:
                        # Store in our registry for quick access
                        self.db_models[model_class] = sqlalchemy_model
                        logger.debug(
                            f"✓ SQLAlchemy model created and stored for {model_class.__name__}"
                        )
                    else:
                        logger.warning(
                            f"⚠ No SQLAlchemy model returned for {model_class.__name__}"
                        )
                else:
                    logger.debug(
                        f"⚠ Model {model_class.__name__} does not have .DB method, skipping"
                    )

            except Exception as e:
                logger.error(
                    f"✗ Failed to create SQLAlchemy model for {model_class.__name__}: {e}"
                )
                logger.debug(f"Model class: {model_class}")
                logger.debug(f"Error details: {e}", exc_info=True)

        logger.debug(
            f"SQLAlchemy model creation complete. Created {len(self.db_models)} models"
        )
        logger.debug(f"Models in registry: {list(self.db_models.keys())}")

    def _generate_routers(self) -> None:
        """Generate FastAPI routers for bound models."""
        from lib.Environment import env

        if env("REST").strip().lower() != "true":
            logger.debug("REST endpoints disabled, skipping router generation")
            return

        self.ep_routers = generate_routers_from_model_registry(self)

    def _scoped_import(
        self, file_type="DB", scopes=["database", "extensions"], clean=False
    ):
        """
        Private method to safely import models with automatic dependency resolution.
        This functionality was moved from lib.Import to be contained within ModelRegistry.

        Args:
            file_type: Prefix of the file name (e.g., "DB" for files starting with "DB_")
            scopes: List of relative module paths to search for files
            clean: If True, bypass cache and force fresh import

        Returns:
            tuple: (imported_modules, import_errors)
        """
        import ast
        import glob
        import importlib
        import os
        import sys
        from functools import lru_cache

        import networkx as nx
        from sqlalchemy.orm import configure_mappers

        from lib.Environment import env

        # Create cache key based on file_type and scopes
        scoped_import_cache_key = f"{file_type}_{'+'.join(sorted(scopes))}"

        # Return cached result if available and not forcing clean
        if (
            not clean
            and hasattr(self, "_import_cache")
            and scoped_import_cache_key in self._import_cache
        ):
            logger.debug(
                f"Using cached scoped_import result for {scoped_import_cache_key}"
            )
            return self._import_cache[scoped_import_cache_key]

        # Cache is already initialized in __init__ via CacheManager

        # Get the source directory
        src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # Dictionary to store files by scope
        files_by_scope = {}

        # Track already imported modules to prevent duplicates
        already_imported_modules = set()
        for module_name in sys.modules:
            if module_name.startswith("database.DB_") or module_name.startswith(
                "extensions."
            ):
                already_imported_modules.add(module_name)
                logger.debug(f"Module already imported: {module_name}")

        # Expand "extensions" scope to include only enabled extension subdirectories
        expanded_scopes = []
        for scope in scopes:
            if scope == "extensions":
                # Get enabled extensions from APP_EXTENSIONS environment variable
                app_extensions = env("APP_EXTENSIONS")
                if app_extensions:
                    enabled_extensions = [
                        ext.strip() for ext in app_extensions.split(",") if ext.strip()
                    ]
                    logger.debug(
                        f"Only importing from enabled extensions: {enabled_extensions}"
                    )

                    # Only include enabled extension directories
                    extensions_dir = os.path.join(src_dir, "extensions")
                    if os.path.exists(extensions_dir) and os.path.isdir(extensions_dir):
                        for ext_name in enabled_extensions:
                            ext_path = os.path.join(extensions_dir, ext_name)
                            if os.path.isdir(ext_path):
                                expanded_scopes.append(f"extensions.{ext_name}")
                                logger.debug(
                                    f"Added enabled extension to scope: extensions.{ext_name}"
                                )
                            else:
                                logger.warning(
                                    f"Enabled extension directory not found: {ext_path}"
                                )
                else:
                    logger.debug(
                        "No APP_EXTENSIONS configured, skipping extension imports"
                    )
            else:
                expanded_scopes.append(scope)

        # Always ensure core database models are imported first when dealing with extensions
        contains_extensions = any(
            scope.startswith("extensions") for scope in expanded_scopes
        )
        if contains_extensions and "database" not in expanded_scopes:
            logger.debug(
                "Adding database scope to ensure core models are imported for extensions"
            )
            expanded_scopes.insert(0, "database")

        # Find all Python files with the specified prefix in all scopes
        for scope in expanded_scopes:
            # Convert module path to directory path
            scope_dir = os.path.join(src_dir, *scope.split("."))

            # Create the pattern for files with the specified prefix
            files_pattern = os.path.join(scope_dir, f"{file_type}_*.py")

            # Get all matching files
            all_files = glob.glob(files_pattern)

            # Filter out test files
            matching_files = [
                f for f in all_files if not os.path.basename(f).endswith("_test.py")
            ]

            if matching_files:
                files_by_scope[scope] = matching_files
                logger.debug(
                    f"Found {len(matching_files)} {file_type} files in {scope}"
                )

        if not files_by_scope:
            logger.debug(
                f"No {file_type} files found in any of the specified scopes: {scopes}"
            )
            return [], []

        # Build dependency graph and get ordered file list
        ordered_files, dependency_graph, module_to_file = self._build_dependency_graph(
            files_by_scope
        )

        # Track imported modules and errors
        imported_modules = []
        import_errors = []
        imported_file_paths = set()

        # Import modules in the determined order
        for file_path in ordered_files:
            # Skip if already imported
            if file_path in imported_file_paths:
                continue

            # Determine the module name and scope based on the file path
            module_name = None
            for scope, files in files_by_scope.items():
                if file_path in files:
                    module_name = f"{scope}.{os.path.basename(file_path)[:-3]}"
                    break

            if module_name is None:
                module_name = f"unknown.{os.path.basename(file_path)[:-3]}"
                logger.warning(f"Could not determine module name for {file_path}")
                continue

            # Skip this module if it's already imported
            if module_name in already_imported_modules:
                logger.debug(f"Skipping already imported module: {module_name}")
                imported_modules.append(module_name)
                imported_file_paths.add(file_path)
                continue

            try:
                logger.debug(f"Importing {module_name}")

                # Check if core module is attempting to be re-imported
                if "database.DB_" in module_name and module_name in sys.modules:
                    logger.debug(f"Reusing already imported core module: {module_name}")
                    module = sys.modules[module_name]
                else:
                    # Import the module
                    spec = importlib.util.spec_from_file_location(
                        module_name, file_path
                    )
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    spec.loader.exec_module(module)

                # Register this module as imported
                already_imported_modules.add(module_name)

                # Tag tables with their module path for filtering in migrations
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    # Look for SQLAlchemy Table or declarative model classes
                    if hasattr(attr, "__tablename__") and hasattr(attr, "__table__"):
                        # Add module_path to table.info
                        table = getattr(attr, "__table__")
                        if "info" not in table.__dict__:
                            table.info = {}
                        table.info["module_path"] = module_name

                        # Ensure bidirectional relationship between table and class
                        if not hasattr(table, "class_") or table.class_ is None:
                            table.class_ = attr
                            logger.debug(
                                f"Set class_ attribute for table {attr.__tablename__} to {attr}"
                            )

                imported_modules.append(module_name)
                imported_file_paths.add(file_path)
                logger.debug(f"Successfully imported {module_name}")

            except Exception as e:
                import_errors.append((file_path, str(e)))
                logger.error(f"Error importing {file_path}: {e}")

        # Log summary
        if import_errors:
            error_details = []
            for file_path, error in import_errors:
                error_details.append(f"  {file_path}: {error}")

            error_message = (
                f"Failed to import {len(import_errors)} model files:\n"
                + "\n".join(error_details)
            )
            logger.error(error_message)
            raise ImportError(error_message)

        # Configure mappers to resolve any remaining issues
        try:
            configure_mappers()
        except Exception as e:
            logger.error(f"Error configuring mappers: {e}")

        # Cache the result for future use
        result = (imported_modules, import_errors)
        self._import_cache[scoped_import_cache_key] = result
        logger.debug(f"Cached scoped_import result for {scoped_import_cache_key}")

        return result

    def _build_dependency_graph(self, files_by_scope):
        """
        Private method to build a dependency graph of modules and determine import order.

        Args:
            files_by_scope: Dict mapping scope names to lists of file paths to analyze

        Returns:
            tuple: (ordered_files, module_graph, module_to_file)
        """
        import ast

        import networkx as nx

        # Create mapping from module name to file path
        module_to_file = {}
        module_classes = {}
        module_imports = {}
        dependencies = {}
        all_defined_classes = set()
        parsing_errors = []

        # First pass: collect all modules and their defined classes from all scopes
        for scope, file_paths in files_by_scope.items():
            for file_path in file_paths:
                try:
                    module_name, deps, classes, imports = (
                        self._parse_imports_and_dependencies(file_path, scope)
                    )

                    module_to_file[module_name] = file_path
                    module_classes[module_name] = classes
                    module_imports[module_name] = imports
                    dependencies[module_name] = deps
                    all_defined_classes.update(classes)
                except Exception as e:
                    parsing_errors.append((file_path, str(e)))
                    logger.error(f"Skipping {file_path} due to parsing error: {e}")
                    continue

        if parsing_errors:
            logger.warning(f"Encountered {len(parsing_errors)} parsing errors")
            for file_path, error in parsing_errors:
                logger.warning(f"  {file_path}: {error}")

        # Build a directed graph for module dependencies
        G = nx.DiGraph()

        # Add all modules as nodes
        for module_name in module_to_file.keys():
            G.add_node(module_name)

        # Add edges for dependencies
        for module_name, deps in dependencies.items():
            for dep in deps:
                # Find which module defines this dependency
                for other_module, classes in module_classes.items():
                    if dep in classes:
                        if other_module != module_name:  # Avoid self-dependencies
                            G.add_edge(module_name, other_module)
                            break

        # Check for cycles and resolve them
        try:
            cycles = list(nx.simple_cycles(G))
            if cycles:
                logger.warning(f"Detected {len(cycles)} circular dependencies")
                for cycle in cycles:
                    logger.warning(f"Circular dependency: {' -> '.join(cycle)}")

                # Break cycles by removing edges
                while cycles:
                    edge_cycle_count = {}
                    for cycle in cycles:
                        for i in range(len(cycle)):
                            edge = (cycle[i], cycle[(i + 1) % len(cycle)])
                            edge_cycle_count[edge] = edge_cycle_count.get(edge, 0) + 1

                    if not edge_cycle_count:
                        break

                    max_edge = max(edge_cycle_count.items(), key=lambda x: x[1])[0]
                    G.remove_edge(*max_edge)
                    logger.warning(
                        f"Breaking cycle by removing dependency: {max_edge[0]} -> {max_edge[1]}"
                    )
                    cycles = list(nx.simple_cycles(G))
        except nx.NetworkXNoCycle:
            pass

        # Get topological sort order
        try:
            ordered_modules = list(nx.topological_sort(G))
        except nx.NetworkXUnfeasible:
            logger.warning("Graph still contains cycles, using fallback ordering")
            ordered_modules = list(module_to_file.keys())

        # Convert ordered modules to file paths
        ordered_files = [
            module_to_file[m] for m in ordered_modules if m in module_to_file
        ]

        # Create module graph dict for backward compatibility
        module_graph = {node: set(G.successors(node)) for node in G.nodes()}

        return ordered_files, module_graph, module_to_file

    def _parse_imports_and_dependencies(self, file_path, scope="database"):
        """
        Private method to parse a Python file to identify its imports and class dependencies.

        Args:
            file_path: Path to the Python file
            scope: The module scope (e.g., "database" or "extensions.prompts")

        Returns:
            tuple: (module_name, dependencies, defined_classes, imports)
        """
        import ast
        import os

        current_module = f"{scope}.{os.path.basename(file_path)[:-3]}"

        try:
            # Use AST to parse the file
            imports, import_froms, classes = self._parse_module_ast(file_path)

            # Process the results
            all_imports = set(imports)
            dependencies = set(import_froms)
            defined_classes = {f"{current_module}.{cls}" for cls in classes}

            return current_module, dependencies, defined_classes, all_imports
        except Exception as e:
            logger.error(f"Error parsing {file_path}: {e}")
            return current_module, set(), set(), set()

    def _parse_module_ast(self, file_path):
        """
        Private method to parse a Python file using the AST module to extract imports and classes.

        Args:
            file_path: Path to the Python file

        Returns:
            Tuple of (imports, import_froms, classes)
        """
        import ast

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            tree = ast.parse(content)
            imports = [
                name.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for name in node.names
            ]
            import_froms = [
                f"{node.module}.{name.name}"
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
                for name in node.names
            ]
            classes = [
                node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
            ]

            return imports, import_froms, classes
        except UnicodeDecodeError:
            # Try with latin-1 encoding if UTF fails
            try:
                with open(file_path, "r", encoding="latin-1") as f:
                    content = f.read()
                logger.warning(f"File {file_path} required latin-1 encoding")
                tree = ast.parse(content)

                imports = []
                import_froms = []
                classes = []

                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for name in node.names:
                            imports.append(name.name)
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            module_name = node.module
                            for name in node.names:
                                import_froms.append(f"{module_name}.{name.name}")
                    elif isinstance(node, ast.ClassDef):
                        classes.append(node.name)

                return imports, import_froms, classes
            except Exception as e:
                logger.error(f"Error parsing {file_path} with alternate encoding: {e}")
                raise ValueError(
                    f"Failed to parse dependencies in {file_path}: {str(e)}"
                )
        except Exception as e:
            logger.error(f"Error parsing {file_path}: {e}")
            raise ValueError(f"Failed to parse dependencies in {file_path}: {str(e)}")

    def build_routers(self):
        """
        Build FastAPI routers using RouterMixin from BLL managers.

        This method uses the RouterMixin approach exclusively - NO EP files are used.
        We are now completely decoupled from EP_Auth, EP_Extensions, and EP_Providers.

        Returns:
            List of router information dictionaries
        """
        if not self._locked:
            raise RuntimeError("Registry must be committed before building routers")

        logger.info("Building routers using RouterMixin approach - NO EP files")

        # Use the RouterMixin approach exclusively
        router_instances = self.build_all_routers_from_managers()

        # Add special root authorization verification endpoint
        root_auth_router = self._create_root_auth_router()
        if root_auth_router:
            router_instances.append(root_auth_router)

        # Convert to the format expected by the application
        routers = []
        for i, router in enumerate(router_instances):
            # Extract router information from the router instance
            router_prefix = getattr(router, "prefix", f"/unknown_{i}")
            router_name = router_prefix.replace("/v1/", "").replace("/", "_")
            if router_name.startswith("_"):
                router_name = router_name[1:]

            routers.append(
                {
                    "router": router,
                    "model_name": router_name,
                    "module_name": f"RouterMixin_{router_name}",
                }
            )

            # Include nested routers if they exist
            if hasattr(router, "nested_routers") and router.nested_routers:
                logger.info(
                    f"Found {len(router.nested_routers)} nested routers for {router_name}"
                )
                for j, nested_router in enumerate(router.nested_routers):
                    nested_prefix = getattr(nested_router, "prefix", f"/nested_{i}_{j}")
                    nested_name = (
                        nested_prefix.replace("/v1/", "")
                        .replace("/", "_")
                        .replace("{", "")
                        .replace("}", "")
                    )
                    if nested_name.startswith("_"):
                        nested_name = nested_name[1:]

                    routers.append(
                        {
                            "router": nested_router,
                            "model_name": nested_name,
                            "module_name": f"RouterMixin_nested_{nested_name}",
                        }
                    )
                    logger.info(f"Added nested router: {nested_prefix}")
            else:
                logger.debug(f"No nested routers found for {router_name}")

        # Add static routes from extensions
        extension_static_routes = [
            item for item in self.extension_registry.extensions_static_routes.values()
        ]
        if extension_static_routes:
            routers.extend(extension_static_routes)
            logger.info(
                f"Added {len(extension_static_routes)} extension static route routers"
            )

        logger.info(
            f"Built {len(routers)} total routers (including nested) using RouterMixin approach"
        )
        return routers

    def build_all_routers_from_managers(self):
        """
        Build FastAPI routers from all managers using RouterMixin.

        This is the new approach that generates routers directly from BLL managers
        without requiring separate EP_ files.

        Returns:
            List of APIRouter instances
        """
        if not self._locked:
            raise RuntimeError("Registry must be committed before building routers")

        routers = []

        try:
            # Import BLL managers using scoped import
            imported_modules, _ = self._scoped_import(
                file_type="BLL", scopes=["logic", "extensions"]
            )

            import sys

            from lib.Pydantic2FastAPI import RouterMixin

            # Find all BLL manager classes with RouterMixin
            for module_name in imported_modules:
                try:
                    module = sys.modules.get(module_name)
                    if not module:
                        continue

                    # Only process modules that are actually BLL files (not imported dependencies)
                    if ".BLL_" not in module_name:
                        # Skip modules that don't match the BLL_ pattern
                        logger.debug(f"Skipping non-BLL module: {module_name}")
                        continue

                    # Look for manager classes in the module
                    for attr_name in dir(module):
                        if attr_name.endswith("Manager"):
                            attr = getattr(module, attr_name)

                            # Check if it's a class that inherits RouterMixin
                            if (
                                inspect.isclass(attr)
                                and issubclass(attr, RouterMixin)
                                and attr != RouterMixin
                                and hasattr(attr, "Router")
                            ):

                                # Additional check: make sure this class is actually defined in this module
                                if attr.__module__ == module_name:
                                    try:
                                        # Get the model used by this manager
                                        base_model = getattr(attr, "BaseModel", None)
                                        if base_model is None:
                                            logger.debug(
                                                f"Skipping {attr_name} - no BaseModel attribute"
                                            )
                                            continue
                                        model = self.apply(base_model)
                                        if model and hasattr(model, "model_fields"):
                                            logger.debug(
                                                f"{attr.__name__} router uses model {model.__name__} with fields: {list(model.model_fields.keys())}"
                                            )
                                            # Generate router using RouterMixin
                                        router = attr.Router(model_registry=self)
                                        routers.append(router)
                                        logger.debug(
                                            f"Generated router for {attr_name} from {module_name}"
                                        )
                                    except Exception as e:
                                        import traceback

                                        logger.error(
                                            f"Failed to generate router for {attr_name}: {traceback.print_exc(e)}"
                                        )
                                        raise (e)

                                else:
                                    logger.debug(
                                        f"Skipping {attr_name} as it's defined in {attr.__module__}, not {module_name}"
                                    )

                except Exception as e:
                    logger.error(f"Failed to process module {module_name}: {e}")
                    raise (e)

        except Exception as e:
            logger.error(f"Error building routers from managers: {e}")
            raise (e)

        logger.debug(f"Generated {len(routers)} routers from managers")
        return routers

    def _create_root_auth_router(self):
        """Create the root authorization verification router (/v1)."""
        try:
            from typing import Optional

            from fastapi import (
                APIRouter,
                Depends,
                Header,
                HTTPException,
                Request,
                Response,
                status,
            )

            router = APIRouter(prefix="/v1", tags=["Authentication"])

            # Define dependency for model registry
            def get_model_registry(request: Request):
                """Get the model registry from app state."""
                model_registry = getattr(request.app.state, "model_registry", None)
                if model_registry is None:
                    raise HTTPException(
                        status_code=500, detail="Model registry not available"
                    )
                return model_registry

            @router.get(
                "",
                summary="Verify authorization",
                description="Verifies if the provided JWT token or API Key is valid.",
                status_code=status.HTTP_204_NO_CONTENT,
                responses={
                    status.HTTP_204_NO_CONTENT: {
                        "description": "Authorization is valid"
                    },
                    status.HTTP_401_UNAUTHORIZED: {
                        "description": "Invalid authorization"
                    },
                },
            )
            async def verify_authorization(
                authorization: Optional[str] = Header(
                    None,
                    description="Authorization header with Bearer token or API Key",
                ),
                model_registry=Depends(get_model_registry),
            ):
                if not authorization:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Authorization header is missing",
                    )
                token = authorization.replace("Bearer ", "").strip()
                if not token:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Token is missing or empty",
                    )

                # Import UserManager and verify token
                try:
                    from logic.BLL_Auth import UserManager

                    UserManager.verify_token(token=token, model_registry=model_registry)
                    return Response(status_code=status.HTTP_204_NO_CONTENT)
                except ImportError:
                    logger.error("Could not import UserManager for token verification")
                    raise HTTPException(
                        status_code=500, detail="Authentication service unavailable"
                    )
                except Exception as e:
                    logger.debug(f"Token verification failed: {e}")
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
                    )

            logger.debug("Created root authorization verification router at /v1")
            return router

        except Exception as e:
            logger.error(f"Failed to create root auth router: {e}")
            return None

    def _load_extension_ep_files(self, extension_names):
        """Load EP (endpoint) files for specified extensions."""
        import glob
        import importlib.util
        import os
        import sys

        # Get the source directory
        src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        imported_modules = []

        # Load EP files for each extension
        for ext_name in extension_names:
            scope_dir = os.path.join(src_dir, "extensions", ext_name)
            files_pattern = os.path.join(scope_dir, "EP_*.py")
            matching_files = glob.glob(files_pattern)

            # Filter out test files
            ep_files = [
                f
                for f in matching_files
                if not os.path.basename(f).endswith("_test.py")
            ]

            for file_path in ep_files:
                module_name = (
                    f"extensions.{ext_name}.{os.path.basename(file_path)[:-3]}"
                )

                # Skip if already imported
                if module_name in sys.modules:
                    logger.debug(f"EP module already imported: {module_name}")
                    imported_modules.append(module_name)
                    continue

                try:
                    logger.debug(f"Importing extension EP module: {module_name}")
                    # Use importlib.util for proper module loading
                    spec = importlib.util.spec_from_file_location(
                        module_name, file_path
                    )
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[module_name] = module
                        spec.loader.exec_module(module)
                        imported_modules.append(module_name)
                        logger.debug(f"Successfully imported EP module: {module_name}")
                    else:
                        logger.warning(
                            f"Could not create spec for EP module {module_name}"
                        )
                except Exception as e:
                    logger.error(f"Failed to import EP module {module_name}: {e}")

        return imported_modules

    def get_sqlalchemy_model(
        self, pydantic_model: Type[BaseModel], for_generation: bool = False
    ) -> Optional[Type]:
        """Get the SQLAlchemy model for a given Pydantic model.

        Args:
            pydantic_model: The Pydantic model class
            for_generation: True if checking before generation (INFO log), False if expecting existing model (WARNING log)

        Returns:
            The corresponding SQLAlchemy model class, or None if not found
        """
        logger.debug(f"Looking for SQLAlchemy model for {pydantic_model.__name__}")
        logger.debug(
            f"Available models in registry: {[model.__name__ for model in self.db_models.keys()]}"
        )
        result = self.db_models.get(pydantic_model)
        if result is None:
            if for_generation:
                logger.debug(
                    f"Generating SQLAlchemy model for {pydantic_model.__name__} (first time)"
                )
            else:
                logger.warning(
                    f"No SQLAlchemy model found for {pydantic_model.__name__}"
                )
        return result

    def get_bound_models(self) -> Set[Type[BaseModel]]:
        """Get all models bound to this registry."""
        return self.bound_models.copy()

    def is_model_bound(self, model: Type[BaseModel]) -> bool:
        """Check if a model is bound to this registry."""
        return model in self.bound_models

    def is_committed(self) -> bool:
        """Check if this registry has been committed (schema generated)."""
        return self._locked

    def clear(self) -> None:
        """Clear the registry (for testing purposes)."""
        self.bound_models.clear()
        self.extension_models.clear()
        self.model_metadata.clear()
        self.db_models.clear()
        self.model_relationships.clear()
        self.dependency_order.clear()
        self.ep_routers.clear()
        self.gql = None
        self._locked = False

        # Clear utility caches
        self.utility.clear_caches()

        logger.debug("Cleared model registry")

    def attach_to_app(self, app) -> None:
        """Attach this registry to a FastAPI app instance.

        Args:
            app: FastAPI application instance
        """
        self.app = app
        app.state.model_registry = self

        # Include any generated routers
        for router in self.ep_routers:
            app.include_router(router)

        # Add GraphQL if available
        if self.gql:
            from strawberry.fastapi import GraphQLRouter

            graphql_app = GraphQLRouter(schema=self.gql)
            app.include_router(graphql_app, prefix="/graphql")

        logger.debug("Attached registry to FastAPI app")

    @classmethod
    def from_scoped_import(
        cls, file_type="BLL", scopes=None, app_instance=None
    ) -> "ModelRegistry":
        """Create a ModelRegistry by importing models using scoped_import.

        This provides backward compatibility with the existing scoped_import system
        while providing the benefits of the registry pattern.

        Args:
            file_type: Type of files to import (e.g., "BLL")
            scopes: List of scopes to search (e.g., ["logic", "extensions.payment"])
            app_instance: Optional FastAPI app to associate with

        Returns:
            Configured ModelRegistry instance
        """
        import sys

        if scopes is None:
            scopes = ["logic"]

        registry = cls(app_instance)

        # Import modules using our private scoped_import method
        imported_modules, import_errors = registry._scoped_import(
            file_type=file_type, scopes=scopes
        )

        if import_errors:
            logger.warning(f"Import errors during registry creation: {import_errors}")

        # Discover and bind models from imported modules
        for module_name in imported_modules:
            module = sys.modules.get(module_name)
            if not module:
                continue

            # Look for BaseModel subclasses in the module
            for attr_name in dir(module):
                attr = getattr(module, attr_name)

                if (
                    inspect.isclass(attr)
                    and issubclass(attr, BaseModel)
                    and attr.__module__ == module_name
                ):

                    # Check if this is an extension model
                    if hasattr(attr, "_is_extension_model") and hasattr(
                        attr, "_extension_target"
                    ):
                        # This is an extension model - bind it to its target
                        target_model = attr._extension_target
                        registry.bind_extension(target_model, attr)
                        logger.debug(
                            f"Discovered and bound extension {attr.__name__} to {target_model.__name__}"
                        )
                    else:
                        # This is a regular model - bind it normally
                        # Extract metadata if available
                        metadata = {}
                        if hasattr(attr, "table_comment"):
                            metadata["table_comment"] = attr.table_comment

                        registry.bind(attr, **metadata)

        logger.debug(
            f"Created registry from scoped import with {len(registry.bound_models)} models"
        )
        return registry


def obj_to_dict(obj, _visited=None):
    """
    Convert an entity to a dictionary, handling both DB entities and regular objects.
    Recursively converts nested objects and handles circular references.

    Args:
        obj: The object to convert to a dictionary
        _visited: Set of already visited object IDs to prevent infinite recursion

    Returns:
        Dictionary representation of the object with nested objects converted
    """
    # Initialize visited set for circular reference detection
    if _visited is None:
        _visited = set()

    # Handle None values
    if obj is None:
        return None

    # Handle primitive types that don't need conversion
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj

    # Handle dates and times
    if isinstance(obj, datetime):
        # Convert datetime to user's timezone before serialization
        try:
            from lib.RequestContext import get_user_timezone

            user_timezone = get_user_timezone()
            if user_timezone != "UTC" and obj.tzinfo is not None:
                # Convert from UTC to user's timezone
                user_tz = ZoneInfo(user_timezone)
                obj_in_user_tz = obj.astimezone(user_tz)
                return obj_in_user_tz.isoformat()
        except Exception:
            # If any error occurs, fall back to default behavior
            pass
        return obj.isoformat()

    if isinstance(obj, (date, time)):
        return obj.isoformat()

    # Handle already converted dictionaries
    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
            if not key.startswith("_"):
                result[key] = obj_to_dict(value, _visited)
        return result

    # Handle lists and tuples
    if isinstance(obj, (list, tuple)):
        return [obj_to_dict(item, _visited) for item in obj]

    # Handle sets
    if isinstance(obj, set):
        return [obj_to_dict(item, _visited) for item in obj]

    # Handle enums
    if hasattr(obj, "__class__") and issubclass(obj.__class__, Enum):
        return obj.value

    # Prevent infinite recursion by checking if we've already visited this object
    obj_id = id(obj)
    if obj_id in _visited:
        # Return a reference representation for circular references
        if hasattr(obj, "id"):
            return {"__circular_ref__": True, "id": getattr(obj, "id", str(obj_id))}
        else:
            return {"__circular_ref__": True, "ref_id": str(obj_id)}

    # Add current object to visited set
    _visited.add(obj_id)

    try:
        # Handle objects with __dict__ (SQLAlchemy entities, Pydantic models, etc.)
        if hasattr(obj, "__dict__"):
            result = {}
            for key, value in obj.__dict__.items():
                if not key.startswith("_"):
                    result[key] = obj_to_dict(value, _visited)
            return result

        # Handle objects without __dict__ by using dir() and getattr()
        else:
            result = {}
            for attr_name in dir(obj):
                if not attr_name.startswith("_") and not callable(
                    getattr(obj, attr_name, None)
                ):
                    try:
                        attr_value = getattr(obj, attr_name)
                        result[attr_name] = obj_to_dict(attr_value, _visited)
                    except (AttributeError, TypeError):
                        # Skip attributes that can't be accessed
                        continue
            return result

    finally:
        # Remove from visited set when done processing this object
        _visited.discard(obj_id)
