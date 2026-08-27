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

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

import stringcase
from pydantic import BaseModel, Field

from zephyrex.lib.AbstractPydantic2 import (
    CacheManager,
    FieldProcessor,
    NameProcessor,
    ReferenceResolver,
    RelationshipAnalyzer,
    TypeIntrospector,
)
from zephyrex.lib.Environment import inflection
from zephyrex.lib.Logging import logger
from zephyrex.pydantic2.util import (
    is_reference_field_name,
    reference_relationship_name,
)


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
        return self.reference_resolver.resolve_string_reference(ref_str, module_context)  # type: ignore[no-any-return]

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

                        processed[field_name] = ListType[resolved]  # type: ignore[valid-type]
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
            return cache[cache_key]  # type: ignore[no-any-return]

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
        return fields  # type: ignore[no-any-return]

    def register_model(
        self, model: Type[BaseModel], name: Optional[str] | None = None
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
        return self.reference_resolver.find_model_by_name(name)  # type: ignore[no-any-return]

    def generate_unique_type_name(
        self, model_class: Type, unique_suffix: Optional[str] | None = None
    ) -> str:
        """
        Generates a unique and simplified GraphQL-friendly type name for a Pydantic model.
        Uses deterministic collision resolution instead of random UUIDs.
        """

        # --- Helper to get/resolve the base name for a model ---
        def _get_or_resolve_base_name(_model_full_path: str, _model_class: Type) -> str:
            if _model_full_path in self._type_name_mapping:
                return self._type_name_mapping[_model_full_path]  # type: ignore[no-any-return]

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
            return resolved_base  # type: ignore[no-any-return]

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

        return final_suffixed_name  # type: ignore[no-any-return]

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
        response_type: str | None = None,
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
                return response_json  # type: ignore[no-any-return]
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
    ) -> List[Tuple[Type[BaseModel], Type[BaseModel], Type]]:
        """
        Discover and map relationships between models.

        This function examines the provided BLL modules to find model relationships,
        including main models and reference models. Network models are no longer
        author-defined; they are emitted by the Pydantic2 generator from
        ``BaseNetworkModel`` and looked up via the registry, so they are not
        part of the discovery tuple.

        Args:
            bll_modules: Dictionary mapping module names to module objects

        Returns:
            List of tuples containing (model_class, ref_model_class, manager_class)
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

                    # Only process a model in the module where it is
                    # defined — not in modules that merely re-import it.
                    # Without this guard, a model re-exported by BLL_Providers
                    # gets claimed there (where its Manager doesn't exist)
                    # and is then skipped in its home module (BLL_Auth).
                    home_module = getattr(cls, "__module__", None)
                    if (
                        home_module
                        and home_module != module_name
                        and home_module in bll_modules
                    ):
                        continue

                    model_classes.append((name, cls))
                    processed_models.add(cls)

                    # Register model by normalized name for lookup
                    base_name = name.replace("Model", "").lower()
                    self.register_model(cls, base_name)  # type: ignore[arg-type]

            for model_name, model_class in model_classes:
                base_name = model_name.replace("Model", "")
                ref_model_name = f"{base_name}ReferenceModel"
                manager_name = f"{base_name}Manager"
                ref_model_class = next(
                    (cls for name, cls in module_members if name == ref_model_name),
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

                if manager_class:
                    relationships.append((model_class, ref_model_class, manager_class))
                    if not getattr(model_class, "Manager", None):
                        model_class.Manager = manager_class  # type: ignore[attr-defined]

        return relationships  # type: ignore[return-value]

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
        for model_class, ref_model_class, _ in model_relationships:
            model_fields_mapping[model_class] = self.get_model_fields(model_class)

        # Then collect fields for reference models
        for _, ref_model_class, _ in model_relationships:
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
        field_to_potential_model = {}  # type: ignore[var-annotated]

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
        model_class: Optional[Type[BaseModel]] | None = None,
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
            return relationship_cache[cache_key]  # type: ignore[no-any-return]

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
                return element_type  # type: ignore[return-value]

        # Handle Optional types (Union[type, None])
        if get_origin(field_type) is Union:
            args = get_args(field_type)
            for arg in args:
                if arg is not type(None) and arg in model_fields_cache:
                    relationship_cache[cache_key] = arg
                    return arg  # type: ignore[no-any-return]
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
                        return registered_model  # type: ignore[no-any-return]

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
                return registered_model  # type: ignore[no-any-return]

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
            return model_hierarchy_cache[model_class]  # type: ignore[no-any-return]

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
            if is_reference_field_name(field_name):
                relationship_name = reference_relationship_name(field_name)
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
            from zephyrex.lib.RequestContext import get_user_timezone

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
