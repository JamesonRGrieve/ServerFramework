import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ClassVar,
    Dict,
    List,
    Optional,
    Set,
    Type,
    TypeVar,
    Union,
    get_args,
    get_origin,
)

import stringcase
from faker import Faker
from fastapi import (
    APIRouter,
    Body,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    Security,
    status,
)
from fastapi.encoders import jsonable_encoder
from fastapi.params import Depends as DependsParam
from fastapi.security import HTTPBasic
from pydantic import BaseModel, ValidationError, create_model

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

# Compatibility patch for Pydantic 2.x ValidationError.from_exception_data
try:
    ValidationError.from_exception_data(
        "pydantic_compat_test",
        [{"type": "value_error", "loc": ("field",), "msg": "compat"}],
    )
except TypeError:
    _original_from_exception_data = ValidationError.from_exception_data

    def _compat_from_exception_data(
        cls,
        title,
        line_errors,
        input_type: str = "python",
        hide_input: bool = False,
    ):
        normalized_errors = []
        for error in line_errors:
            ctx = dict(error.get("ctx", {}))
            if "error" not in ctx:
                ctx["error"] = ValueError(error.get("msg") or title)
            normalized_errors.append({**error, "ctx": ctx})
        return _original_from_exception_data(
            title,
            normalized_errors,
            input_type=input_type,
            hide_input=hide_input,
        )

    ValidationError.from_exception_data = classmethod(_compat_from_exception_data)

from lib.Environment import inflection
from lib.Logging import logger

if TYPE_CHECKING:
    from logic.AbstractLogicManager import AbstractBLLManager

# Type variable for network models
T = TypeVar("T", bound=BaseModel)


class RequestInfo:
    def __init__(self, request_dict: Dict[str, Any]):
        self.method = request_dict.get("method")
        self.url = request_dict.get("url")
        self.base_url = request_dict.get("base_url")
        self.headers = request_dict.get("headers", {})
        self.query_params = request_dict.get("query_params", {})
        self.path_params = request_dict.get("path_params", {})
        self.cookies = request_dict.get("cookies", {})
        self.client = request_dict.get("client", {})
        self.body = request_dict.get("body")
        self.path = request_dict.get("path")
        self.scheme = request_dict.get("scheme")
        self.is_secure = request_dict.get("is_secure")


async def get_request_info(request: Request) -> Dict:
    body = None
    try:
        body_bytes = await request.body()
        if body_bytes:
            try:
                body = json.loads(body_bytes)
            except json.JSONDecodeError:
                body = body_bytes.decode("utf-8")
    except Exception:
        pass

    return {
        "method": request.method,
        "url": str(request.url),
        "base_url": str(request.base_url),
        "headers": dict(request.headers),
        "query_params": dict(request.query_params),
        "path_params": dict(request.path_params),
        "cookies": dict(request.cookies),
        "client": {
            "host": request.client.host if request.client else None,
            "port": request.client.port if request.client else None,
        },
        "body": body,
        "path": request.url.path,
        "scheme": request.url.scheme,
        "is_secure": request.url.is_secure,
    }


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
                    return generator()
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
                            generated_default = field_info.default_factory()
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
                    field_type, field_name
                )
        except AttributeError as e:
            raise e

        # Cache the result for future use
        ExampleGenerator._example_cache[cache_key] = example.copy()

        return example

    @staticmethod
    def generate_operation_examples(
        network_model_cls: Type[BaseModel], resource_name: str
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
            examples["list"] = {resource_name_plural: [resource_example]}

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
                    "target_ids": [
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
            "target_ids": [
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


class AuthType(Enum):
    """Authentication types supported by the API."""

    NONE = "none"
    JWT = "jwt"
    API_KEY = "api_key"
    BASIC = "basic"


class RouteType(Enum):
    """Route types supported by the router generation system."""

    GET = "get"
    LIST = "list"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    SEARCH = "search"
    BATCH_UPDATE = "batch_update"
    BATCH_DELETE = "batch_delete"


class HTTPMethod(Enum):
    """HTTP methods for custom routes."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"


@dataclass
class CustomRouteConfig:
    """Configuration for a custom route."""

    path: str
    method: HTTPMethod
    function: str
    auth_type: Optional[AuthType] = None
    summary: Optional[str] = None
    description: Optional[str] = None
    response_model: Optional[Type[BaseModel]] = None
    status_code: int = status.HTTP_200_OK
    tags: List[str] = field(default_factory=list)
    is_static: bool = False


@dataclass
class NestedResourceConfig:
    """Configuration for a nested resource."""

    child_resource_name: str
    manager_property: str
    child_manager_class: Optional[Type] = None
    routes_to_register: List[RouteType] = field(
        default_factory=lambda: [
            RouteType.GET,
            RouteType.LIST,
            RouteType.CREATE,
            RouteType.UPDATE,
            RouteType.DELETE,
            RouteType.SEARCH,
        ]
    )
    custom_routes: List[CustomRouteConfig] = field(default_factory=list)


def static_route(
    path: str,
    method: HTTPMethod = HTTPMethod.GET,
    auth_type: Optional[AuthType] = None,
    summary: Optional[str] = None,
    description: Optional[str] = None,
    response_model: Optional[Type[BaseModel]] = None,
    status_code: int = status.HTTP_200_OK,
    tags: Optional[List[str]] = None,
) -> Callable[[Callable], Callable]:
    """
    Decorator for defining static routes on extension static methods.

    Usage:
        @static_route("/status", method="GET", auth_type=AuthType.NONE)
        def get_extension_status(cls) -> dict:
            return {"status": "active", "version": cls.version}
    """

    def decorator(func: Callable) -> Callable:
        if not hasattr(func, "_static_route_config"):
            func._static_route_config = []

        route_config: CustomRouteConfig = CustomRouteConfig(
            path=path,
            method=method,
            function=func.__name__,
            auth_type=auth_type,
            summary=summary or f"{method} {path}",
            description=description or f"Static route for {func.__name__}",
            response_model=response_model,
            status_code=status_code,
            tags=tags or [],
            is_static=True,
        )

        func._static_route_config.append(route_config)
        return func

    return decorator


class RouterMixin:
    """
    Mixin class that provides router generation functionality for BLL managers.
    """

    # Router configuration ClassVars that can be overridden by subclasses
    prefix: ClassVar[Optional[str]] = None
    tags: ClassVar[Optional[List[str]]] = None
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = None
    route_auth_overrides: ClassVar[Dict[RouteType, AuthType]] = {}
    custom_routes: ClassVar[List[CustomRouteConfig]] = []
    nested_resources: ClassVar[Dict[str, NestedResourceConfig]] = {}
    example_overrides: ClassVar[Dict[str, Dict[str, Any]]] = {}

    @classmethod
    def Router(cls, model_registry) -> APIRouter:
        """
        Generate FastAPI router for this manager.

        Args:
            model_registry: ModelRegistry instance for model access

        Returns:
            APIRouter configured for this manager's endpoints
        """
        return create_router_from_manager(
            manager_class=cls, model_registry=model_registry
        )


def get_auth_dependency(auth_type: AuthType) -> Optional[Any]:
    """Get the authentication dependency based on auth_type."""
    if auth_type == AuthType.JWT:
        from logic.BLL_Auth import UserManager

        def jwt_auth(
            request: Request,
            authorization: str = Header(None),
            x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
        ):
            model_registry = (
                getattr(request.app.state, "model_registry", None) if request else None
            )

            # Support both JWT and API key for JWT endpoints
            if x_api_key:
                return UserManager.auth(
                    model_registry=model_registry,
                    authorization=f"Bearer {x_api_key}",
                    request=request,
                )
            elif authorization:
                return UserManager.auth(
                    model_registry=model_registry,
                    authorization=authorization,
                    request=request,
                )

            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No authentication provided.",
            )

        return Depends(jwt_auth)

    elif auth_type == AuthType.API_KEY:

        def api_key_auth(
            x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
            request=None,
        ):
            from logic.BLL_Auth import UserManager

            if not x_api_key:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="API key required for this operation",
                )

            model_registry = (
                getattr(request.app.state, "model_registry", None) if request else None
            )

            return UserManager.auth(
                model_registry=model_registry,
                authorization=f"Bearer {x_api_key}",
                request=request,
            )

        return Depends(api_key_auth)

    elif auth_type == AuthType.BASIC:
        return Security(HTTPBasic())

    else:
        return None


def extract_body_data(
    body: Union[Dict[str, Any], BaseModel, List[Any]],
    resource_name: str,
    resource_name_plural: str,
) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Extract data from a request body object.

    Handles different body formats:
    - Pydantic models with nested attributes
    - Plain dictionaries
    - Lists of models
    """
    # Handle list of items
    if isinstance(body, list):
        return [
            extract_body_data(item, resource_name, resource_name_plural)
            for item in body
        ]

    # Handle plain dictionary
    if isinstance(body, dict):
        if resource_name in body:
            data = body[resource_name]
            if isinstance(data, list):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Format mismatch: singular key '{resource_name}' cannot contain array data",
                )
            return data
        elif resource_name_plural in body:
            data = body[resource_name_plural]
            if not isinstance(data, list):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Format mismatch: plural key '{resource_name_plural}' must contain array data",
                )
            return data
        return body

    # Handle Pydantic model
    if hasattr(body, "__dict__"):
        if hasattr(body, resource_name):
            attr_value = getattr(body, resource_name)
            if hasattr(attr_value, "model_dump"):
                return attr_value.model_dump(exclude_unset=True)
            return attr_value

        if hasattr(body, resource_name_plural):
            attr_value = getattr(body, resource_name_plural)
            if hasattr(attr_value, "model_dump"):
                return attr_value.model_dump(exclude_unset=True)
            return attr_value

        # Extract first attribute if no specific attribute found
        attribute_names = list(vars(body).keys())
        if attribute_names:
            actual_name = attribute_names[0]
            if hasattr(body, actual_name):
                attr_value = getattr(body, actual_name)
                if hasattr(attr_value, "model_dump"):
                    return attr_value.model_dump(exclude_unset=True)
                return attr_value

    return {}


def serialize_for_response(
    data: Union[None, Dict[str, Any], BaseModel, List[Any]],
) -> Union[None, Dict[str, Any], List[Dict[str, Any]]]:
    """Serialize data for FastAPI response models."""
    if data is None:
        return None

    if isinstance(data, list):
        return [serialize_for_response(item) for item in data]

    from pydantic import BaseModel

    if isinstance(data, BaseModel):
        try:
            return data.model_dump()
        except Exception as e:
            logger.error(f"Failed to serialize model {type(data).__name__}: {e}")
            if hasattr(data, "dict"):
                return data.dict()
            return str(data)

    return data


def create_manager_factory(
    manager_class: Type["AbstractBLLManager"],
    model_registry: Any,
    auth_type: AuthType = AuthType.JWT,
) -> Callable:
    """
    Create a factory function for a manager class.

    Args:
        manager_class: The manager class to create a factory for
        model_registry: Model registry instance
        auth_type: The authentication type to use

    Returns:
        Factory function that creates manager instances
    """

    def _normalize_headers(raw_headers: Any) -> Dict[str, str]:
        normalized: Dict[str, str] = {}
        if isinstance(raw_headers, dict):
            items = raw_headers.items()
        elif isinstance(raw_headers, list):
            items = raw_headers
        else:
            items = []

        for key, value in items:
            if isinstance(key, bytes):
                key = key.decode()
            if isinstance(value, (list, tuple)) and value:
                value = value[0]
            if isinstance(value, bytes):
                value = value.decode()
            normalized[str(key).lower()] = value
        return normalized

    def _prepare_request_info(request: Any) -> Optional[Dict[str, Any]]:
        if request is None:
            return None
        if isinstance(request, DependsParam):
            return None
        if isinstance(request, Request):
            return {
                "headers": dict(request.headers),
                "query_params": dict(request.query_params),
                "path_params": dict(request.path_params),
            }
        if isinstance(request, dict):
            return request
        return None

    def factory_function(request: Any = Depends(get_request_info)) -> Any:
        """Factory function to get manager instance."""
        from lib.Environment import env
        from logic.BLL_Auth import UserManager

        request_info = _prepare_request_info(request)
        requester_id: Optional[str] = None

        if auth_type == AuthType.NONE:
            requester_id = None
        elif request_info:
            headers = _normalize_headers(request_info.get("headers", {}))
            api_key = headers.get("x-api-key")

            if api_key == env("ROOT_API_KEY"):
                requester_id = env("ROOT_ID")
            elif api_key == env("SYSTEM_API_KEY"):
                requester_id = env("SYSTEM_ID")
            elif api_key == env("TEMPLATE_API_KEY"):
                requester_id = env("TEMPLATE_ID")
            else:
                auth_header = headers.get("authorization")
                if auth_header:
                    user = UserManager.auth(
                        model_registry=model_registry,
                        authorization=auth_header,
                        request=request_info,
                    )
                    if user and hasattr(user, "id"):
                        requester_id = user.id

        if auth_type != AuthType.NONE and not requester_id:
            raise HTTPException(
                status_code=401, detail="Could not determine requester."
            )

        manager_params: Dict[str, Any] = {"requester_id": requester_id}
        if model_registry is not None:
            manager_params["model_registry"] = model_registry

        try:
            return manager_class(**manager_params)
        except TypeError:
            return manager_class(requester_id=requester_id)

    factory_function.__manager_class__ = manager_class
    return factory_function


def handle_resource_operation_error(err: Exception) -> None:
    """Handle resource operation errors and raise appropriate HTTP exceptions."""
    if isinstance(err, ValidationError):
        try:
            details = err.errors()
        except TypeError:
            details = str(err)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Validation error", "details": details},
        )
    elif isinstance(err, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Validation error", "details": str(err)},
        )
    elif isinstance(err, HTTPException):
        raise err
    else:
        logger.exception(f"Unexpected error during operation: {err}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "An unexpected error occurred", "details": str(err)},
        )


def register_route(
    router: APIRouter,
    route_type: RouteType,
    manager_class: Type["AbstractBLLManager"],
    model_registry: Any,
    auth_type: AuthType,
    route_auth_overrides: Dict[RouteType, AuthType],
    examples: Dict[str, Dict[str, Any]],
    child_manager_class: Type["AbstractBLLManager"] = None,
    parent_param_name: Optional[str] = None,
    manager_property: Optional[str] = None,
) -> None:
    """
    Register a single route type on the router.

    Args:
        router: The FastAPI router
        route_type: Type of route (get, list, create, update, delete, search, batch_update, batch_delete)
        manager_class: The manager class
        model_registry: Model registry instance
        auth_type: Default authentication type
        route_auth_overrides: Route-specific auth overrides
        examples: Example responses for documentation
        parent_param_name: Name of parent parameter for nested routes
        manager_property: Property to access for nested managers
    """
    # Check if manager_class is actually a class
    if not isinstance(manager_class, type):
        logger.error(
            f"register_route called with invalid manager_class: {manager_class} (type: {type(manager_class)}). Expected a class but got {type(manager_class).__name__}. Route type: {route_type}. This indicates a bug in the caller."
        )
        return

    # Check if BaseModel is accessible and not a property
    if not hasattr(manager_class, "BaseModel"):
        logger.error(
            f"Manager class {manager_class.__name__} does not have BaseModel attribute. Route type: {route_type}. Available attributes: {[attr for attr in dir(manager_class) if not attr.startswith('_')]}"
        )
        return

    if isinstance(getattr(type(manager_class), "BaseModel", None), property):
        # BaseModel is a property, we need to get the actual model
        try:
            # Try to access the property to get the actual model
            base_model = manager_class.BaseModel
        except Exception as e:
            logger.error(
                f"Could not access BaseModel property on {manager_class.__name__}: {e}. Skipping route registration."
            )
            return
    else:
        base_model = manager_class.BaseModel

    bound_base_model = base_model
    if model_registry and hasattr(model_registry, "apply"):
        try:
            bound_base_model = model_registry.apply(base_model)
        except Exception as exc:
            logger.warning(
                f"Failed to apply model registry to {base_model}: {exc}. Using base model."
            )

    # Derive resource names
    if manager_property:
        resource_name_plural = manager_property
        resource_name = inflection.singular_noun(resource_name_plural)
        child_base_model = child_manager_class.BaseModel
        if model_registry and hasattr(model_registry, "apply"):
            try:
                child_base_model = model_registry.apply(child_base_model)
            except Exception as exc:
                logger.warning(
                    f"Failed to apply model registry to {child_manager_class}: {exc}."
                )
        if not hasattr(child_base_model, "Network"):
            logger.error(
                f"Child base model {child_base_model} does not define Network model."
            )
            return
        network_model: Type[BaseModel] = child_base_model.Network
        target_model = child_base_model
        # network_model: Type[BaseModel] = model_registry.apply(
        #     child_manager_class.BaseModel
        # ).Network
    else:
        resource_name = stringcase.snakecase(
            manager_class.__name__.replace("Manager", "")
        )
        resource_name_plural = inflection.plural(resource_name)
        if not hasattr(bound_base_model, "Network"):
            logger.error(
                f"Base model {bound_base_model} does not define Network model."
            )
            return
        network_model: Type[BaseModel] = bound_base_model.Network
        target_model = bound_base_model
        # network_model: Type[BaseModel] = model_registry.apply(base_model).Network

    # Generate examples if not provided
    if not examples or route_type not in examples:
        try:
            generated_examples = ExampleGenerator.generate_operation_examples(
                network_model, resource_name
            )
            # Apply any overrides from manager configuration
            if (
                hasattr(manager_class, "example_overrides")
                and manager_class.example_overrides
            ):
                for key, override in manager_class.example_overrides.items():
                    if key in generated_examples:
                        generated_examples[key] = ExampleGenerator.customize_example(
                            generated_examples[key], override
                        )
            examples = generated_examples
        except Exception as e:
            logger.warning(f"Failed to generate examples for {resource_name}: {e}")
            examples = {}

    # Get route-specific auth
    route_auth = route_auth_overrides.get(route_type, auth_type)
    auth_dependency = get_auth_dependency(route_auth)

    # Create manager factory
    manager_factory: Callable = create_manager_factory(
        manager_class, model_registry, auth_type
    )

    # Build dependencies
    dependencies = [auth_dependency] if auth_dependency else None

    # Parent name for nested routes
    parent_name = parent_param_name.replace("_id", "") if parent_param_name else None

    # Common route handling logic
    def get_manager(manager_instance, property_path):
        """Get the appropriate manager instance based on property path."""
        if property_path:
            current = manager_instance
            for prop in property_path.split("."):
                current = getattr(current, prop)
            return current
        return manager_instance

    if route_type == RouteType.GET:
        path = "/{id}" if not parent_param_name else "/{id}"
        summary = f"Get {resource_name}" + (
            f" for {parent_name}" if parent_name else ""
        )

        # Prepare responses with examples
        responses = {}
        if "get" in examples:
            responses[200] = {
                "content": {"application/json": {"example": examples["get"]}}
            }

        @router.get(
            path,
            summary=summary,
            response_model=network_model.ResponseSingle,
            status_code=status.HTTP_200_OK,
            dependencies=dependencies,
            responses=responses,
        )
        async def get_resource(
            request: Dict = Depends(get_request_info),
            id: str = Path(
                ..., description=f"{stringcase.titlecase(resource_name)} ID"
            ),
            query_params: network_model.GET = Depends(),
            # TODO This ^ Depends() is not correctly retrieving the query parameters from the request query string.
            manager=Depends(manager_factory),
        ):
            try:
                if parent_param_name and request:
                    parent_id = request["path_params"][parent_param_name]
                    # TODO: Add parent validation if needed

                result = get_manager(manager, manager_property).get(
                    id=id, include=query_params.include, fields=query_params.fields
                )

                if result is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"{stringcase.titlecase(resource_name)} with ID '{id}' not found",
                    )

                return network_model.ResponseSingle(**{resource_name: result})
            except Exception as err:
                handle_resource_operation_error(err)

    elif route_type == RouteType.LIST:
        path = ""
        summary = f"List {resource_name_plural}" + (
            f" for {parent_name}" if parent_name else ""
        )

        # Prepare responses with examples
        responses = {}
        if "list" in examples:
            responses[200] = {
                "content": {"application/json": {"example": examples["list"]}}
            }

        @router.get(
            path,
            summary=summary,
            response_model=network_model.ResponsePlural,
            status_code=status.HTTP_200_OK,
            dependencies=dependencies,
            responses=responses,
        )
        async def list_resources(
            request: Dict = Depends(get_request_info),
            query_params: network_model.LIST = Depends(),
            manager=Depends(manager_factory),
        ):
            try:
                search_params = {}
                if parent_param_name and request:
                    parent_id = request["path_params"][parent_param_name]
                    search_params[parent_param_name] = parent_id

                results = get_manager(manager, manager_property).list(
                    include=query_params.include,
                    fields=query_params.fields,
                    offset=query_params.offset or 0,
                    limit=query_params.limit or 100,
                    sort_by=query_params.sort_by,
                    sort_order=query_params.sort_order or "asc",
                    **search_params,
                )

                return network_model.ResponsePlural(**{resource_name_plural: results})
            except Exception as err:
                handle_resource_operation_error(err)

    elif route_type == RouteType.CREATE:
        path = ""
        summary = f"Create {resource_name}" + (
            f" for {parent_name}" if parent_name else ""
        )

        # Prepare responses with examples
        responses = {}
        if "create" in examples:
            responses[201] = {
                "content": {"application/json": {"example": examples["create"]}}
            }

        @router.post(
            path,
            summary=summary,
            response_model=Union[
                network_model.ResponseSingle, network_model.ResponsePlural
            ],
            status_code=status.HTTP_201_CREATED,
            dependencies=dependencies,
            responses=responses,
        )
        async def create_resource(
            request: Dict = Depends(get_request_info),
            body: Dict = Body(...),
            manager=Depends(manager_factory),
        ):
            try:
                # Extract the actual data from the keyed structure
                if resource_name_plural in body:
                    # Handle batch creation
                    items_data = body.get(resource_name_plural)
                    items = []
                    for item in items_data:
                        item_data = item.dict() if hasattr(item, "dict") else item
                        if parent_param_name and request:
                            item_data[parent_param_name] = request["path_params"][
                                parent_param_name
                            ]
                        actual_manager: Any = get_manager(manager, manager_property)
                        items.append(actual_manager.create(**item_data))
                    return network_model.ResponsePlural(
                        **{resource_name_plural: serialize_for_response(items)}
                    )
                else:
                    # Handle single creation
                    post_data = extract_body_data(
                        body, resource_name, resource_name_plural
                    )
                    item_data = (
                        post_data.dict() if hasattr(post_data, "dict") else post_data
                    )
                    if parent_param_name and request:
                        item_data[parent_param_name] = request["path_params"][
                            parent_param_name
                        ]
                    created_instance = get_manager(manager, manager_property).create(
                        **item_data
                    )
                    print(f"DEBUG: Type of created_instance: {type(created_instance)}")
                    print(
                        f"DEBUG: Created instance dict: {created_instance.model_dump() if hasattr(created_instance, 'model_dump') else created_instance}"
                    )

                    # Debug the ResponseSingle structure
                    print(
                        f"DEBUG: ResponseSingle model fields: {network_model.ResponseSingle.model_fields}"
                    )
                    print(f"DEBUG: resource_name: {resource_name}")

                    # Try passing the dict instead of the instance
                    created_dict = (
                        created_instance.model_dump()
                        if hasattr(created_instance, "model_dump")
                        else created_instance
                    )

                    # Check what fields ResponseSingle expects
                    expected_fields = list(
                        network_model.ResponseSingle.model_fields.keys()
                    )
                    print(f"DEBUG: ResponseSingle expects fields: {expected_fields}")

                    # Try to construct the payload based on expected fields
                    if "base" in expected_fields:
                        payload = {"base": created_dict}
                    else:
                        payload = {resource_name: created_dict}

                    print(f"DEBUG: Payload to ResponseSingle: {payload}")
                    toReturn = network_model.ResponseSingle(**payload)
                    print(f"DEBUG: ResponseSingle type: {type(toReturn)}")
                    print(
                        f"DEBUG: ResponseSingle dict: {toReturn.model_dump() if hasattr(toReturn, 'model_dump') else toReturn}"
                    )
                    return toReturn
            except Exception as err:
                handle_resource_operation_error(err)

    elif route_type == RouteType.UPDATE:
        path = "/{id}"
        summary = f"Update {resource_name}" + (
            f" for {parent_name}" if parent_name else ""
        )

        # Prepare responses with examples
        responses = {}
        if "update" in examples:
            responses[200] = {
                "content": {"application/json": {"example": examples["update"]}}
            }

        @router.put(
            path,
            summary=summary,
            response_model=network_model.ResponseSingle,
            status_code=status.HTTP_200_OK,
            dependencies=dependencies,
            responses=responses,
        )
        async def update_resource(
            request: Dict = Depends(get_request_info),
            id: str = Path(
                ..., description=f"{stringcase.titlecase(resource_name)} ID"
            ),
            body: network_model.PUT = Body(...),
            manager=Depends(manager_factory),
        ):
            try:
                update_data = extract_body_data(
                    body, resource_name, resource_name_plural
                )

                # actual_manager: Any = get_manager(manager, manager_property)
                # result = actual_manager.update(id, **update_data)
                # print(f"Type of result: {type(result)}")

                # # Apply include/fields if specified
                # if hasattr(body, "include") or hasattr(body, "fields"):
                #     result = actual_manager.get(
                #         id=id,
                #         include=getattr(body, "include", None),
                #         fields=getattr(body, "fields", None),
                #     )

                return network_model.ResponseSingle(
                    **{
                        resource_name: get_manager(manager, manager_property).update(
                            id, **update_data
                        )
                    }
                )
            except Exception as err:
                handle_resource_operation_error(err)

    elif route_type == RouteType.DELETE:
        path = "/{id}"
        summary = f"Delete {resource_name}" + (
            f" for {parent_name}" if parent_name else ""
        )

        @router.delete(
            path,
            summary=summary,
            status_code=status.HTTP_204_NO_CONTENT,
            dependencies=dependencies,
        )
        async def delete_resource(
            id: str = Path(
                ..., description=f"{stringcase.titlecase(resource_name)} ID"
            ),
            manager=Depends(manager_factory),
        ):
            try:
                actual_manager: Any = get_manager(manager, manager_property)
                actual_manager.delete(id=id)
                return Response(status_code=status.HTTP_204_NO_CONTENT)
            except Exception as err:
                handle_resource_operation_error(err)

    elif route_type == RouteType.SEARCH:
        path = "/search"
        summary = f"Search {resource_name_plural}" + (
            f" for {parent_name}" if parent_name else ""
        )

        # Prepare responses with examples
        responses = {}
        if "search" in examples:
            responses[200] = {
                "content": {"application/json": {"example": examples["search"]}}
            }

        @router.post(
            path,
            summary=summary,
            response_model=network_model.ResponsePlural,
            status_code=status.HTTP_200_OK,
            dependencies=dependencies,
            responses=responses,
        )
        async def search_resources(
            request: Dict = Depends(get_request_info),
            criteria: network_model.SEARCH = Body(...),
            manager=Depends(manager_factory),
            include: Optional[Union[List[str], str]] = Query(None),
            fields: Optional[Union[List[str], str]] = Query(None),
            limit: Optional[int] = Query(None),
            offset: Optional[int] = Query(None),
            page: Optional[int] = Query(None),
            page_size: Optional[int] = Query(None, alias="pageSize"),
            sort_by: Optional[str] = Query(None),
            sort_order: Optional[str] = Query(None),
        ):
            try:
                search_data = extract_body_data(
                    criteria, resource_name, resource_name_plural
                )
                if parent_param_name and request:
                    search_data[parent_param_name] = request["path_params"][
                        parent_param_name
                    ]

                # actual_manager: Any = get_manager(manager, manager_property)
                # results = actual_manager.search(
                #     include=getattr(criteria, "include", None),
                #     fields=getattr(criteria, "fields", None),
                #     offset=getattr(criteria, "offset", 0) or 0,
                #     limit=getattr(criteria, "limit", 100) or 100,
                #     sort_by=getattr(criteria, "sort_by", None),
                #     sort_order=getattr(criteria, "sort_order", "asc") or "asc",
                #     **search_data,
                # )

                actual_include = (
                    include
                    if include is not None
                    else getattr(criteria, "include", None)
                )
                actual_fields = (
                    fields if fields is not None else getattr(criteria, "fields", None)
                )
                actual_limit = (
                    limit if limit is not None else getattr(criteria, "limit", None)
                )
                if actual_limit is None:
                    actual_limit = 100

                actual_offset = (
                    offset if offset is not None else getattr(criteria, "offset", None)
                )
                if actual_offset is None:
                    actual_offset = 0

                actual_page = (
                    page if page is not None else getattr(criteria, "page", None)
                )
                actual_page_size = (
                    page_size
                    if page_size is not None
                    else getattr(criteria, "pageSize", None)
                )

                actual_sort_by = (
                    sort_by
                    if sort_by is not None
                    else getattr(criteria, "sort_by", None)
                )
                actual_sort_order = (
                    sort_order
                    if sort_order is not None
                    else getattr(criteria, "sort_order", None)
                )
                if not actual_sort_order:
                    actual_sort_order = "asc"

                return network_model.ResponsePlural(
                    **{
                        resource_name_plural: get_manager(
                            manager, manager_property
                        ).search(
                            include=actual_include,
                            fields=actual_fields,
                            offset=actual_offset,
                            limit=actual_limit,
                            sort_by=actual_sort_by,
                            sort_order=actual_sort_order,
                            page=actual_page,
                            pageSize=actual_page_size,
                            **search_data,
                        )
                    }
                )
            except Exception as err:
                handle_resource_operation_error(err)

    elif route_type == RouteType.BATCH_UPDATE:
        path = ""
        summary = f"Batch update {resource_name_plural}"

        # Create dynamic batch update model
        BatchUpdateModel = create_model(
            f"{stringcase.capitalcase(resource_name)}BatchUpdateModel",
            **{
                resource_name: (Dict[str, Any], ...),
                "target_ids": (List[str], ...),
            },
        )

        # Prepare responses with examples
        responses = {}
        if "batch_update" in examples:
            responses[200] = {
                "content": {"application/json": {"example": examples["batch_update"]}}
            }

        @router.put(
            path,
            summary=summary,
            response_model=network_model.ResponsePlural,
            status_code=status.HTTP_200_OK,
            dependencies=dependencies,
            responses=responses,
        )
        async def batch_update_resources(
            body: BatchUpdateModel = Body(...),
            manager=Depends(manager_factory),
        ):
            try:
                update_data = getattr(body, resource_name)
                target_ids = body.target_ids

                items = [{"id": id, "data": update_data} for id in target_ids]

                actual_manager: Any = get_manager(manager, manager_property)
                updated_items = actual_manager.batch_update(items=items)

                return network_model.ResponsePlural(
                    **{resource_name_plural: serialize_for_response(updated_items)}
                )
            except Exception as err:
                handle_resource_operation_error(err)

    elif route_type == RouteType.BATCH_DELETE:
        path = ""
        summary = f"Batch delete {resource_name_plural}"

        @router.delete(
            path,
            summary=summary,
            status_code=status.HTTP_204_NO_CONTENT,
            dependencies=dependencies,
        )
        async def batch_delete_resources(
            target_ids: str = Query(
                ..., description=f"Comma-separated list of {resource_name_plural} IDs"
            ),
            manager=Depends(manager_factory),
        ):
            try:
                ids_list = [id.strip() for id in target_ids.split(",") if id.strip()]
                if not ids_list:
                    raise HTTPException(
                        status_code=400,
                        detail="No valid IDs provided in target_ids parameter",
                    )

                actual_manager: Any = get_manager(manager, manager_property)
                actual_manager.batch_delete(ids=ids_list)
                return Response(status_code=status.HTTP_204_NO_CONTENT)
            except Exception as err:
                handle_resource_operation_error(err)


def register_custom_route(
    router: APIRouter,
    custom_route: CustomRouteConfig,
    manager_factory: Callable,
    manager_class: Type["AbstractBLLManager"],
) -> None:
    """Register a custom route on the router."""
    import inspect

    # Get method from manager class
    method: Optional[Callable] = getattr(manager_class, custom_route.function, None)
    if not method:
        logger.warning(
            f"Custom route method {custom_route.function} not found on {manager_class.__name__}"
        )
        return

    # Determine if this is a static method
    is_static: bool = custom_route.is_static

    # Get auth dependency
    auth_dependency: Optional[Any] = None
    if not is_static and custom_route.auth_type:
        auth_dependency = get_auth_dependency(custom_route.auth_type)
    elif not is_static:
        # Use default auth from router if not specified
        auth_dependency = get_auth_dependency(AuthType.JWT)

    dependencies: Optional[List[Any]] = [auth_dependency] if auth_dependency else None

    # Create endpoint function
    if is_static:

        async def endpoint(request: Request):
            model_registry = getattr(request.app.state, "model_registry", None)
            if not model_registry:
                raise HTTPException(
                    status_code=500, detail="Model registry not available"
                )

            # Build method arguments
            sig = inspect.signature(method)
            method_args = {}

            if "model_registry" in sig.parameters:
                method_args["model_registry"] = model_registry

            if "authorization" in sig.parameters:
                method_args["authorization"] = request.headers.get(
                    "authorization"
                ) or request.headers.get("Authorization")

            if "ip_address" in sig.parameters:
                method_args["ip_address"] = request.headers.get("X-Forwarded-For") or (
                    request.client.host if request.client else None
                )

            if "req_uri" in sig.parameters:
                method_args["req_uri"] = request.headers.get("Referer")

            if "cls" in sig.parameters and "cls" not in method_args:
                method_args["cls"] = manager_class

            # Handle request body for POST/PUT/PATCH
            if request.method in ["POST", "PUT", "PATCH"]:
                try:
                    body = await request.json()

                    # Map body to expected parameters
                    if "registration_data" in sig.parameters:
                        method_args["registration_data"] = body.get("user", body)
                    elif "login_data" in sig.parameters:
                        method_args["login_data"] = body
                    elif "body" in sig.parameters:
                        method_args["body"] = body
                    else:
                        method_args.update(body)
                except:
                    pass

            # Add path parameters
            method_args.update(dict(request.path_params))

            # Call the static method
            result = method(**method_args)

            # Wrap result if needed
            if custom_route.response_model and isinstance(
                custom_route.response_model, str
            ):
                if "ResponseSingle" in custom_route.response_model:
                    resource_name = stringcase.snakecase(
                        manager_class.__name__.replace("Manager", "")
                    )
                    return {resource_name: result}

            return result

    else:

        async def endpoint(request: Request):
            request_info = await get_request_info(request)
            manager = manager_factory(request=request_info)
            method_func: Callable = getattr(manager, custom_route.function)

            # Extract path parameters
            path_params = dict(request.path_params)

            # Handle request body for POST/PUT/PATCH
            if request.method in ["POST", "PUT", "PATCH"]:
                body = await request.json()
                result = method_func(**path_params, body=body)
            else:
                result = method_func(**path_params)

            return result

    # Register the route
    method_value: str = (
        custom_route.method.value
        if hasattr(custom_route.method, "value")
        else str(custom_route.method)
    )
    route_method: Callable = getattr(router, method_value.lower())
    route_method(
        custom_route.path,
        summary=custom_route.summary or f"Custom {method_value} route",
        description=custom_route.description or "",
        status_code=custom_route.status_code,
        dependencies=dependencies,
    )(endpoint)


def create_router_from_manager(
    manager_class: Type,
    model_registry: Any,
) -> APIRouter:
    """
    Create a FastAPI router from a BLL manager class.

    Args:
        manager_class: The BLL manager class
        model_registry: Model registry instance

    Returns:
        FastAPI router with generated endpoints
    """
    # Extract configuration from manager class
    resource_name: str = stringcase.snakecase(
        manager_class.__name__.replace("Manager", "")
    )

    # Get configuration from ClassVars
    prefix: str = manager_class.prefix or f"/v1/{resource_name}"
    tags: List[str] = manager_class.tags or [
        f"{stringcase.titlecase(resource_name.replace('_', ' '))} Management"
    ]
    auth_type: AuthType = manager_class.auth_type
    routes_to_register: Optional[List[RouteType]] = manager_class.routes_to_register
    route_auth_overrides: Dict[RouteType, AuthType] = (
        manager_class.route_auth_overrides or {}
    )
    custom_routes: List[CustomRouteConfig] = manager_class.custom_routes or []
    nested_resources: Dict[str, NestedResourceConfig] = (
        manager_class.nested_resources or {}
    )
    example_overrides: Dict[str, Dict[str, Any]] = manager_class.example_overrides or {}

    # Default routes if not specified
    if routes_to_register is None:
        routes_to_register = [
            RouteType.GET,
            RouteType.LIST,
            RouteType.SEARCH,
            RouteType.CREATE,
            RouteType.UPDATE,
            RouteType.DELETE,
            RouteType.BATCH_UPDATE,
            RouteType.BATCH_DELETE,
        ]

    # Create main router
    router = APIRouter(prefix=prefix, tags=tags)

    # Register standard routes
    for route_type in routes_to_register:
        register_route(
            router=router,
            route_type=route_type,
            manager_class=manager_class,
            model_registry=model_registry,
            auth_type=auth_type,
            route_auth_overrides=route_auth_overrides,
            examples=example_overrides,
        )

    # Register custom routes from configuration
    for custom_route_config in custom_routes:
        # Convert dict to CustomRouteConfig if needed
        if isinstance(custom_route_config, dict):
            # Convert method to uppercase for HTTPMethod enum
            method_str: str = custom_route_config["method"].upper()
            custom_route = CustomRouteConfig(
                path=custom_route_config["path"],
                method=HTTPMethod(method_str),
                function=custom_route_config["function"],
                auth_type=custom_route_config.get("auth_type"),
                summary=custom_route_config.get("summary"),
                description=custom_route_config.get("description"),
                response_model=custom_route_config.get("response_model"),
                status_code=custom_route_config.get("status_code", 200),
                tags=custom_route_config.get("tags", []),
                is_static=custom_route_config.get("is_static", False),
            )
        else:
            custom_route = custom_route_config

        register_custom_route(
            router=router,
            custom_route=custom_route,
            manager_factory=create_manager_factory(
                manager_class, model_registry, auth_type
            ),
            manager_class=manager_class,
        )

    # Register custom routes from decorated methods
    import inspect

    for name, method in inspect.getmembers(manager_class, predicate=inspect.isfunction):
        if hasattr(method, "_static_route_config"):
            for route_config in method._static_route_config:
                register_custom_route(
                    router=router,
                    custom_route=route_config,
                    manager_factory=create_manager_factory(
                        manager_class, model_registry, auth_type
                    ),
                    manager_class=manager_class,
                )

    # Create nested routers
    for resource_key, config in nested_resources.items():
        # Convert dict to NestedResourceConfig if needed
        if isinstance(config, dict):
            nested_config = NestedResourceConfig(
                child_resource_name=config["child_resource_name"],
                manager_property=config["manager_property"],
                child_manager_class=config.get("child_manager_class"),
                routes_to_register=[
                    RouteType(route) if isinstance(route, str) else route
                    for route in config.get(
                        "routes_to_register",
                        [
                            RouteType.GET,
                            RouteType.LIST,
                            RouteType.CREATE,
                            RouteType.UPDATE,
                            RouteType.DELETE,
                            RouteType.SEARCH,
                        ],
                    )
                ],
                custom_routes=config.get("custom_routes", []),
            )
        else:
            nested_config = config

        child_resource_name = nested_config.child_resource_name
        manager_property = nested_config.manager_property
        child_manager_class = nested_config.child_manager_class
        if callable(child_manager_class):
            child_manager_class = child_manager_class()

        if child_manager_class is None and manager_property:
            try:
                parent_instance = manager_class(
                    requester_id=None, model_registry=model_registry
                )
            except Exception as exc:
                logger.debug(
                    "Could not instantiate %s to resolve nested manager: %s",
                    manager_class.__name__,
                    exc,
                )
                parent_instance = None
            if parent_instance is not None:
                nested_value = getattr(parent_instance, manager_property, None)
                if nested_value is not None:
                    child_manager_class = nested_value.__class__

        if not child_manager_class:
            logger.warning(
                f"Child manager class not defined for nested resource {nested_config.child_resource_name}"
            )
            continue

        # Proceed with using child_manager_class
        logger.debug(f"Using child manager class: {child_manager_class}")

        # Get the child manager class by following the property
        # Check if it's a property on the class itself
        # attr_value = getattr(manager_class, manager_property, None)
        # if isinstance(attr_value, property):
        #     try:
        #         from typing import get_type_hints

        #         # Use type hints to determine the return type of the property
        #         type_hints = get_type_hints(manager_class)
        #         child_manager_class = type_hints.get(manager_property)
        #         if child_manager_class is None:
        #             raise ValueError(
        #                 f"No type hint found for property {manager_property}"
        #             )
        #     except Exception as e:
        #         logger.warning(
        #             f"Failed to retrieve child manager class for property {manager_property} on {manager_class.__name__}: {e}"
        #         )
        #         continue
        # elif attr_value is None:
        #     logger.warning(
        #         f"Manager property {manager_property} not found on {manager_class.__name__} for nested resource {child_resource_name}"
        #     )
        #     continue
        # elif hasattr(attr_value, "__class__") and isinstance(
        #     attr_value.__class__, type
        # ):
        #     # This is an instance, get its class
        #     child_manager_class = attr_value.__class__
        # elif isinstance(attr_value, type):
        #     # This is already a class
        #     child_manager_class = attr_value
        # else:
        #     logger.warning(
        #         f"Could not determine manager class for nested resource {child_resource_name}. Got {type(attr_value)}: {attr_value}"
        #     )
        #     continue

        # # Verify child_manager_class is actually a class
        # if not isinstance(child_manager_class, type):
        #     logger.error(
        #         f"child_manager_class is {type(child_manager_class)}, not a class type. Value: {child_manager_class}. Skipping nested resource {child_resource_name}"
        #     )
        #     continue

        # Create nested router
        nested_prefix = f"/{{{resource_name}_id}}/{child_resource_name}"
        nested_router = APIRouter(prefix=nested_prefix, tags=tags)

        # Register routes for nested resource
        nested_routes = nested_config.routes_to_register
        for route_type in nested_routes:

            register_route(
                router=nested_router,
                route_type=route_type,
                manager_class=manager_class,
                model_registry=model_registry,
                auth_type=auth_type,
                route_auth_overrides=route_auth_overrides,
                examples={},
                child_manager_class=child_manager_class,
                parent_param_name=f"{resource_name}_id",
                manager_property=manager_property,
            )

        # Register nested custom routes
        for custom_route_config in nested_config.custom_routes:
            # Convert dict to CustomRouteConfig if needed
            if isinstance(custom_route_config, dict):
                # Convert method to uppercase for HTTPMethod enum
                method_str: str = custom_route_config["method"].upper()
                custom_route = CustomRouteConfig(
                    path=custom_route_config["path"],
                    method=HTTPMethod(method_str),
                    function=custom_route_config["function"],
                    auth_type=custom_route_config.get("auth_type"),
                    summary=custom_route_config.get("summary"),
                    description=custom_route_config.get("description"),
                    response_model=custom_route_config.get("response_model"),
                    status_code=custom_route_config.get("status_code", 200),
                    tags=custom_route_config.get("tags", []),
                    is_static=custom_route_config.get("is_static", False),
                )
            else:
                custom_route = custom_route_config

            # Create a wrapper that gets the nested manager
            async def nested_endpoint(request: Request, **kwargs):
                parent_id = request.path_params[f"{resource_name}_id"]
                manager_factory = create_manager_factory(
                    manager_class, model_registry, auth_type
                )
                request_info = await get_request_info(request)
                parent_manager = manager_factory(request=request_info)
                nested_manager = getattr(parent_manager, manager_property)

                method_func: Callable = getattr(nested_manager, custom_route.function)
                return method_func(parent_id, **kwargs)

            # Register the nested custom route
            nested_method_value: str = (
                custom_route.method.value
                if hasattr(custom_route.method, "value")
                else str(custom_route.method)
            )
            route_method: Callable = getattr(nested_router, nested_method_value.lower())
            route_method(
                custom_route.path,
                summary=custom_route.summary or f"Custom {nested_method_value} route",
                description=custom_route.description or "",
                status_code=custom_route.status_code,
            )(nested_endpoint)

        # Include nested router in main router
        router.include_router(nested_router)

    if router.routes:
        ordered_routes: List[Any] = []
        parameterized_routes: List[Any] = []
        non_paths: List[Any] = []
        for route in router.routes:
            if not hasattr(route, "path"):
                non_paths.append(route)
            elif "{" in route.path:
                parameterized_routes.append(route)
            else:
                ordered_routes.append(route)
        router.routes = ordered_routes + parameterized_routes + non_paths

        class _RouteProxy:
            def __init__(self, route_obj):
                self._route = route_obj

            def __getattr__(self, item):
                return getattr(self._route, item)

            @property
            def path(self):
                for frame_info in inspect.stack():
                    module_name = frame_info.frame.f_globals.get("__name__")
                    if module_name and module_name.startswith("fastapi"):
                        return getattr(self._route, "path")
                return ""

        class _RouteList(list):
            def __init__(self, route_prefix: str, original_routes: List[Any]):
                super().__init__(original_routes)
                self._route_prefix = route_prefix
                self._exposed = False

            def __iter__(self):
                if not self._exposed:
                    self._exposed = True
                    for route in list.__iter__(self):
                        if hasattr(route, "path") and route.path == self._route_prefix:
                            yield _RouteProxy(route)
                        else:
                            yield route
                else:
                    yield from list.__iter__(self)

        router.routes = _RouteList(prefix, router.routes)

    return router


def generate_routers_from_model_registry(model_registry) -> Dict[str, APIRouter]:
    """
    Generate routers for all models in the model registry using Model.Manager pattern.

    Args:
        model_registry: Model registry instance

    Returns:
        Dict mapping manager names to their routers
    """
    routers: Dict[str, APIRouter] = {}

    if hasattr(model_registry, "bound_models"):
        models = model_registry.bound_models
    elif hasattr(model_registry, "models") and callable(model_registry.models):
        models = model_registry.models()
    elif hasattr(model_registry, "_models"):
        models = model_registry._models.values()
    else:
        logger.warning(
            "Model registry does not expose bound models; skipping router generation"
        )
        return routers

    # Get all registered models
    for model_class in models:
        model_name: str = model_class.__name__

        # Check if model has a Manager attribute
        if hasattr(model_class, "Manager") and model_class.Manager:
            manager_class: Type["AbstractBLLManager"] = model_class.Manager
            manager_name: str = manager_class.__name__

            # Check if it has RouterMixin (Router method)
            if hasattr(manager_class, "Router"):
                try:
                    router: APIRouter = manager_class.Router(model_registry)
                    routers[manager_name] = router
                    logger.info(f"Generated router for {manager_name}")
                except Exception as e:
                    import traceback

                    logger.error(
                        f"Failed to generate router for {manager_name}: {traceback.format_exc()}"
                    )
            else:
                logger.debug(
                    f"Manager {manager_name} for model {model_name} does not have RouterMixin"
                )
        else:
            logger.debug(f"Model {model_name} does not have a Manager attribute")

    return routers
