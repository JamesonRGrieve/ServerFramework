import json
import re
import uuid
from datetime import date, datetime
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ClassVar,
    Dict,
    Set,
    Type,
    Union,
    get_args,
    get_origin,
)

import stringcase
from faker import Faker
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

# Sentinel import for Pydantic's undefined values
try:
    from pydantic_core import PydanticUndefined
except ImportError:  # pragma: no cover - fallback for alternate Pydantic packages
    try:
        from pydantic.fields import PydanticUndefined  # type: ignore
    except ImportError:  # pragma: no cover - ultimate fallback

        class _UndefinedSentinel:
            pass

        PydanticUndefined = _UndefinedSentinel()  # type: ignore

from zephyrex.lib.Environment import inflection
from zephyrex.lib.Logging import logger

if TYPE_CHECKING:
    from .types import NetworkModelProtocol as NetworkModelProtocol


class ExampleGenerator:
    """
    Utility class to generate example data for Pydantic models for OpenAPI documentation.

    This class analyzes Pydantic models and generates realistic example data based on
    field types, names, and patterns. It supports nested models, lists, and optional fields.
    Uses Faker library to generate realistic fake data with a dictionary-based pattern matching system.
    """

    # Cache for generated examples to avoid redundant work
    _example_cache: Dict[str, Dict[str, Any]] = {}

    # Initialize faker instance
    _faker = Faker()

    # Dictionary mapping field name patterns to Faker callables
    # Patterns are checked in order, so more specific patterns should come first
    _field_generators: Dict[str, Callable[[], Any]] = {
        # ID patterns
        r"^.*_?id$": lambda: str(uuid.uuid4()),
        r"^id$": lambda: str(uuid.uuid4()),
        # Name patterns
        r"^.*first_?name.*$": lambda: ExampleGenerator._faker.first_name(),
        r"^.*last_?name.*$": lambda: ExampleGenerator._faker.last_name(),
        r"^.*user_?name.*$": lambda: ExampleGenerator._faker.user_name(),
        r"^.*display_?name.*$": lambda: ExampleGenerator._faker.name(),
        r"^.*company_?name.*$": lambda: ExampleGenerator._faker.company(),
        r"^.*full_?name.*$": lambda: ExampleGenerator._faker.name(),
        r"^.*name.*$": lambda: ExampleGenerator._faker.name(),
        # Email patterns
        r"^.*email.*$": lambda: ExampleGenerator._faker.email(),
        # Phone patterns
        r"^.*phone.*$": lambda: ExampleGenerator._faker.phone_number(),
        # Address patterns
        r"^.*address.*$": lambda: ExampleGenerator._faker.address(),
        r"^.*street.*$": lambda: ExampleGenerator._faker.street_address(),
        r"^.*city.*$": lambda: ExampleGenerator._faker.city(),
        r"^.*state.*$": lambda: ExampleGenerator._faker.state(),
        r"^.*country.*$": lambda: ExampleGenerator._faker.country(),
        r"^.*(zip|postal).*$": lambda: ExampleGenerator._faker.postcode(),
        # URL and path patterns
        r"^.*hosted.*path.*$": lambda: ExampleGenerator._faker.url().replace(
            "http://", "https://"
        ),
        r"^.*url.*$": lambda: ExampleGenerator._faker.url().replace(
            "http://", "https://"
        ),
        r"^.*relative.*path.*$": lambda: "path/to/file.txt",
        r"^.*path.*$": lambda: "/path/to/file.txt",
        # Date and time patterns
        r"^.*birth.*date.*$": lambda: ExampleGenerator._faker.date_of_birth().isoformat(),
        r"^.*created.*date.*$": lambda: ExampleGenerator._faker.date_this_decade().isoformat(),
        r"^.*updated.*date.*$": lambda: ExampleGenerator._faker.date_this_decade().isoformat(),
        r"^.*date.*$": lambda: ExampleGenerator._faker.date_this_decade().isoformat(),
        r"^.*created.*at.*$": lambda: ExampleGenerator._faker.date_time_this_decade().isoformat(),
        r"^.*updated.*at.*$": lambda: ExampleGenerator._faker.date_time_this_decade().isoformat(),
        r"^.*timestamp.*$": lambda: ExampleGenerator._faker.date_time_this_decade().isoformat(),
        # Description and content patterns
        r"^.*description.*$": lambda: ExampleGenerator._faker.paragraph(nb_sentences=2),
        r"^.*content.*$": lambda: ExampleGenerator._faker.paragraph(nb_sentences=3),
        r"^.*summary.*$": lambda: ExampleGenerator._faker.sentence(),
        r"^.*comment.*$": lambda: ExampleGenerator._faker.sentence(),
        r"^.*note.*$": lambda: ExampleGenerator._faker.sentence(),
        r"^.*bio.*$": lambda: ExampleGenerator._faker.paragraph(nb_sentences=1),
        # Token and code patterns
        r"^.*token.*$": lambda: f"tk-{ExampleGenerator._faker.lexify('????????')}",
        r"^.*api.*key.*$": lambda: f"ak-{ExampleGenerator._faker.lexify('????????????????')}",
        r"^.*secret.*$": lambda: ExampleGenerator._faker.password(length=32),
        r"^.*code.*$": lambda: ExampleGenerator._faker.lexify("???###"),
        r"^.*uuid.*$": lambda: str(uuid.uuid4()),
        # Status and type patterns
        r"^.*status.*$": lambda: ExampleGenerator._faker.random_element(
            ["active", "inactive", "pending", "completed"]
        ),
        r"^.*type.*$": lambda: ExampleGenerator._faker.random_element(
            ["standard", "premium", "basic", "advanced"]
        ),
        r"^.*category.*$": lambda: ExampleGenerator._faker.random_element(
            ["general", "specific", "important", "urgent"]
        ),
        r"^.*priority.*$": lambda: ExampleGenerator._faker.random_element(
            ["low", "medium", "high", "critical"]
        ),
        # Role and permission patterns
        r"^.*admin.*role.*$": lambda: "admin",
        r"^.*owner.*role.*$": lambda: "owner",
        r"^.*role.*$": lambda: ExampleGenerator._faker.random_element(
            ["admin", "user", "owner", "editor", "viewer"]
        ),
        r"^.*permission.*$": lambda: ExampleGenerator._faker.random_element(
            ["read", "write", "admin", "none"]
        ),
        # Business patterns
        r"^.*company.*$": lambda: ExampleGenerator._faker.company(),
        r"^.*job.*title.*$": lambda: ExampleGenerator._faker.job(),
        r"^.*department.*$": lambda: ExampleGenerator._faker.random_element(
            ["Engineering", "Marketing", "Sales", "HR"]
        ),
        r"^.*salary.*$": lambda: ExampleGenerator._faker.random_int(
            min=30000, max=200000
        ),
        # Technical patterns
        r"^.*version.*$": lambda: f"{ExampleGenerator._faker.random_int(1, 5)}.{ExampleGenerator._faker.random_int(0, 9)}.{ExampleGenerator._faker.random_int(0, 9)}",
        r"^.*hash.*$": lambda: ExampleGenerator._faker.sha256(),
        r"^.*ip.*address.*$": lambda: ExampleGenerator._faker.ipv4(),
        r"^.*mac.*address.*$": lambda: ExampleGenerator._faker.mac_address(),
        r"^.*domain.*$": lambda: ExampleGenerator._faker.domain_name(),
        r"^.*hostname.*$": lambda: ExampleGenerator._faker.hostname(),
        # File patterns
        r"^.*filename.*$": lambda: ExampleGenerator._faker.file_name(),
        r"^.*file.*extension.*$": lambda: ExampleGenerator._faker.file_extension(),
        r"^.*mime.*type.*$": lambda: ExampleGenerator._faker.mime_type(),
        # Financial patterns
        r"^.*price.*$": lambda: round(
            ExampleGenerator._faker.random.uniform(1.99, 999.99), 2
        ),
        r"^.*amount.*$": lambda: round(
            ExampleGenerator._faker.random.uniform(10.00, 10000.00), 2
        ),
        r"^.*currency.*$": lambda: ExampleGenerator._faker.currency_code(),
        # Location patterns
        r"^.*latitude.*$": lambda: float(ExampleGenerator._faker.latitude()),
        r"^.*longitude.*$": lambda: float(ExampleGenerator._faker.longitude()),
        r"^.*timezone.*$": lambda: ExampleGenerator._faker.timezone(),
        # Color patterns
        r"^.*color.*$": lambda: ExampleGenerator._faker.color_name(),
        r"^.*hex.*color.*$": lambda: ExampleGenerator._faker.hex_color(),
    }

    # Boolean field patterns
    _boolean_generators: Dict[str, Callable[[], bool]] = {
        r"^.*is_.*$": lambda: True,
        r"^.*has_.*$": lambda: True,
        r"^.*enabled.*$": lambda: True,
        r"^.*active.*$": lambda: True,
        r"^.*favourite.*$": lambda: True,
        r"^.*favorite.*$": lambda: True,
        r"^.*verified.*$": lambda: True,
        r"^.*confirmed.*$": lambda: True,
        r"^.*approved.*$": lambda: True,
        r"^.*visible.*$": lambda: True,
        r"^.*public.*$": lambda: True,
        # Default for other boolean fields
        r".*": lambda: ExampleGenerator._faker.boolean(),
    }

    @staticmethod
    def generate_uuid() -> str:
        """Generate a random UUID string."""
        return str(uuid.uuid4())

    @staticmethod
    def get_example_value(field_type: Type, field_name: str) -> Any:
        """
        Generate an appropriate example value based on field type and name.

        Args:
            field_type: The type of the field
            field_name: The name of the field

        Returns:
            An appropriate example value
        """
        # Check for Optional types
        origin = get_origin(field_type)
        if origin is Union:
            args = get_args(field_type)
            if type(None) in args:  # This is an Optional type
                for arg in args:
                    if arg is not type(None):
                        field_type = arg
                        break

        # Check for List types
        if origin is list:
            inner_type = get_args(field_type)[0]
            # Return a list with a single example item of the inner type
            return [ExampleGenerator.get_example_value(inner_type, field_name)]

        # Check for Dict types
        if origin is dict or field_type is dict or field_type is Dict:
            # For dictionaries, provide a simple key-value example
            return {"key": "value"}

        # Generate example based on field type
        faker = ExampleGenerator._faker

        # Generate example based on field type and field name
        if field_type is str:
            return ExampleGenerator._generate_string_example(field_name)
        elif field_type is int:
            # Check for specific integer patterns
            field_lower = field_name.lower()
            if "age" in field_lower:
                return faker.random_int(min=18, max=80)
            elif "count" in field_lower or "number" in field_lower:
                return faker.random_int(min=1, max=1000)
            elif "port" in field_lower:
                return faker.random_int(min=1024, max=65535)
            else:
                return 42
        elif field_type is float:
            field_lower = field_name.lower()
            if "price" in field_lower or "amount" in field_lower:
                return round(faker.random.uniform(1.99, 999.99), 2)
            elif "rate" in field_lower or "percentage" in field_lower:
                return round(faker.random.uniform(0.0, 100.0), 2)
            else:
                return 42.5
        elif field_type is bool:
            return ExampleGenerator._generate_bool_example(field_name)
        elif field_type is datetime:
            return faker.date_time_this_decade().isoformat()
        elif field_type is date:
            return faker.date_this_decade().isoformat()
        else:
            return None

    @staticmethod
    def field_name_to_example(field_name: str) -> str:
        """
        Convert a field name to a human-readable example string.

        Args:
            field_name: The field name to convert

        Returns:
            A human-readable example string
        """
        # Remove common suffixes that don't add meaning
        clean_field = field_name
        if clean_field.endswith("_name") or clean_field.endswith("_id"):
            clean_field = "_".join(clean_field.split("_")[:-1])

        # Use stringcase to convert to title case
        human_readable = stringcase.titlecase(clean_field)
        return f"Example {human_readable}"

    @staticmethod
    def _generate_string_example(field_name: str) -> str:
        """Generate string examples using pattern matching with Faker callables."""
        field_lower = field_name.lower()

        # Check patterns in the field generators dictionary
        for pattern, generator in ExampleGenerator._field_generators.items():
            if re.match(pattern, field_lower):
                try:
                    return generator()  # type: ignore[no-any-return]
                except Exception as e:
                    logger.warning(
                        f"Failed to generate example for pattern {pattern}: {e}"
                    )
                    continue

        # Fallback to field name conversion if no pattern matches
        return ExampleGenerator.field_name_to_example(field_name)

    _unsafe_default_type_names: ClassVar[Set[str]] = {"ModelFieldAccessor"}

    @staticmethod
    def _is_serializable_default(value: Any) -> bool:
        """Return True if the default can be safely used in an OpenAPI example."""

        if value is None:
            return True

        value_type = value.__class__.__name__
        if value is PydanticUndefined or value_type in {
            "PydanticUndefinedType",
            "UndefinedType",
        }:
            return False

        if value_type in ExampleGenerator._unsafe_default_type_names:
            return False

        try:
            json.dumps(jsonable_encoder(value))
        except (TypeError, ValueError):
            return False
        return True

    @staticmethod
    def _generate_bool_example(field_name: str) -> bool:
        """Generate boolean examples using pattern matching."""
        field_lower = field_name.lower()

        # Check patterns in the boolean generators dictionary
        for pattern, generator in ExampleGenerator._boolean_generators.items():
            if re.match(pattern, field_lower):
                try:
                    return generator()
                except Exception as e:
                    logger.warning(
                        f"Failed to generate boolean example for pattern {pattern}: {e}"
                    )
                    continue

        # Default fallback
        return False

    @staticmethod
    def generate_example_for_model(model_cls: Type[BaseModel]) -> Dict[str, Any]:
        """
        Generate a complete example object for a Pydantic model.

        Args:
            model_cls: The Pydantic model class

        Returns:
            Dictionary with example values for all fields
        """
        # Check cache first
        cache_key = f"{model_cls.__module__}.{model_cls.__name__}"
        if cache_key in ExampleGenerator._example_cache:
            logger.debug(f"Using cached example for {cache_key}")
            return ExampleGenerator._example_cache[cache_key].copy()

        logger.debug(f"Generating example for model: {model_cls.__name__}")
        example = {}
        try:
            # Process fields from model
            for field_name, field in model_cls.model_fields.items():
                field_info = field
                field_type = field_info.annotation

                # Check if field has a default value
                if not field_info.is_required():
                    default_value = field_info.default
                    if (
                        default_value is not None
                        and ExampleGenerator._is_serializable_default(default_value)
                    ):
                        example[field_name] = default_value
                        continue
                    elif field_info.default_factory is not None:
                        try:
                            generated_default = field_info.default_factory()  # type: ignore[call-arg]
                        except Exception as exc:  # pragma: no cover - defensive guard
                            logger.debug(
                                "Default factory for %s on %s raised %s",
                                field_name,
                                model_cls.__name__,
                                exc,
                            )
                            generated_default = None
                        if ExampleGenerator._is_serializable_default(generated_default):
                            example[field_name] = generated_default
                            continue

                # Check for example in field metadata
                if (
                    hasattr(field_info, "json_schema_extra")
                    and field_info.json_schema_extra
                ):
                    schema_extra = field_info.json_schema_extra
                    if isinstance(schema_extra, dict) and "example" in schema_extra:
                        example[field_name] = schema_extra["example"]
                        continue

                # Generate example value based on field type and name
                example[field_name] = ExampleGenerator.get_example_value(
                    field_type, field_name  # type: ignore[arg-type]
                )
        except AttributeError as e:
            raise e

        # Cache the result for future use
        ExampleGenerator._example_cache[cache_key] = example.copy()

        return example

    @staticmethod
    def generate_operation_examples(
        network_model_cls: "NetworkModelProtocol", resource_name: str
    ) -> Dict[str, Dict]:
        """
        Generate examples for all operation types (create, update, get, search).

        Args:
            network_model_cls: The Network model class
            resource_name: The name of the resource

        Returns:
            Dictionary with examples for each operation type
        """
        logger.debug(f"Generating operation examples for {resource_name}")
        examples = {}
        resource_name_plural = inflection.plural(resource_name)

        # Get model classes using introspection
        response_single_cls = getattr(network_model_cls, "ResponseSingle", None)
        response_plural_cls = getattr(network_model_cls, "ResponsePlural", None)
        post_cls = getattr(network_model_cls, "POST", None)
        put_cls = getattr(network_model_cls, "PUT", None)
        search_cls = getattr(network_model_cls, "SEARCH", None)

        # Generate resource example
        resource_cls = None
        if response_single_cls:
            for field_name, field in response_single_cls.model_fields.items():
                if field_name == resource_name:
                    resource_cls = field.annotation
                    break

        if resource_cls:
            # Generate single resource example
            resource_example = ExampleGenerator.generate_example_for_model(resource_cls)

            # Get example
            examples["get"] = {resource_name: resource_example}

            # List example
            examples["list"] = {resource_name_plural: [resource_example]}  # type: ignore[dict-item]

        # Generate create example
        if post_cls:
            create_field = None
            for field_name, field in post_cls.model_fields.items():
                if field_name == resource_name:
                    create_field = field
                    break

            if create_field:
                create_cls = create_field.annotation
                create_example = ExampleGenerator.generate_example_for_model(create_cls)
                examples["create"] = {resource_name: create_example}

        # Generate update example
        if put_cls:
            update_field = None
            for field_name, field in put_cls.model_fields.items():
                if field_name == resource_name:
                    update_field = field
                    break

            if update_field:
                update_cls = update_field.annotation
                update_example = ExampleGenerator.generate_example_for_model(update_cls)
                examples["update"] = {resource_name: update_example}

                # Also generate batch update example
                examples["batch_update"] = {
                    resource_name: update_example,
                    "target_ids": [  # type: ignore[dict-item]
                        ExampleGenerator.generate_uuid(),
                        ExampleGenerator.generate_uuid(),
                    ],
                }

        # Generate search example
        if search_cls:
            search_field = None
            for field_name, field in search_cls.model_fields.items():
                if field_name == resource_name:
                    search_field = field
                    break

            if search_field:
                search_cls = search_field.annotation
                search_example = ExampleGenerator.generate_example_for_model(search_cls)

                # Make search examples more realistic for search operations
                # Only include a subset of fields that would commonly be used for filtering
                search_example_refined = {}

                for key, value in search_example.items():
                    # Keep ID fields, name fields, status fields, type fields, date fields
                    if (
                        "id" in key.lower()
                        or "name" in key.lower()
                        or "status" in key.lower()
                        or "type" in key.lower()
                        or "date" in key.lower()
                        or "created" in key.lower()
                        or "updated" in key.lower()
                    ):
                        search_example_refined[key] = value

                # If we filtered out everything, use original example
                if not search_example_refined:
                    search_example_refined = search_example

                examples["search"] = {resource_name: search_example_refined}

        # Generate batch delete example
        examples["batch_delete"] = {
            "target_ids": [  # type: ignore[dict-item]
                ExampleGenerator.generate_uuid(),
                ExampleGenerator.generate_uuid(),
            ]
        }

        return examples

    @staticmethod
    def clear_cache():
        """Clear the example cache."""
        ExampleGenerator._example_cache.clear()

    @staticmethod
    def customize_example(
        example: Dict[str, Any], customizations: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Apply customizations to an example.

        Args:
            example: The original example dictionary
            customizations: Dict of paths to values to customize
                           (e.g., {"name": "Custom Name", "settings.theme": "dark"})

        Returns:
            Customized example dictionary
        """
        result = example.copy()

        for path, value in customizations.items():
            if "." in path:
                # Handle nested paths
                parts = path.split(".")
                current = result
                for part in parts[:-1]:
                    if part not in current:
                        current[part] = {}
                    current = current[part]
                current[parts[-1]] = value
            else:
                # Handle top-level paths
                result[path] = value

        return result

    @staticmethod
    def add_field_generator(pattern: str, generator: Callable[[], Any]) -> None:
        """
        Add a custom field generator pattern.

        Args:
            pattern: Regex pattern to match field names
            generator: Callable that returns the example value
        """
        ExampleGenerator._field_generators[pattern] = generator

    @staticmethod
    def add_boolean_generator(pattern: str, generator: Callable[[], bool]) -> None:
        """
        Add a custom boolean field generator pattern.

        Args:
            pattern: Regex pattern to match field names
            generator: Callable that returns the boolean value
        """
        # Create a new dictionary with the custom pattern first to ensure it's checked before catch-all patterns
        new_generators = {pattern: generator}
        new_generators.update(ExampleGenerator._boolean_generators)
        ExampleGenerator._boolean_generators = new_generators

    @staticmethod
    def remove_field_generator(pattern: str) -> None:
        """
        Remove a field generator pattern.

        Args:
            pattern: Regex pattern to remove
        """
        ExampleGenerator._field_generators.pop(pattern, None)

    @staticmethod
    def remove_boolean_generator(pattern: str) -> None:
        """
        Remove a boolean field generator pattern.

        Args:
            pattern: Regex pattern to remove
        """
        ExampleGenerator._boolean_generators.pop(pattern, None)

    @staticmethod
    def get_field_patterns() -> Dict[str, Callable[[], Any]]:
        """
        Get a copy of the current field generator patterns.

        Returns:
            Dictionary of field generator patterns
        """
        return ExampleGenerator._field_generators.copy()

    @staticmethod
    def get_boolean_patterns() -> Dict[str, Callable[[], bool]]:
        """
        Get a copy of the current boolean generator patterns.

        Returns:
            Dictionary of boolean generator patterns
        """
        return ExampleGenerator._boolean_generators.copy()
