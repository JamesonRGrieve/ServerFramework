from dataclasses import dataclass, field
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ClassVar,
    Dict,
    List,
    Optional,
    Protocol,
    Type,
    TypeVar,
)

from fastapi import APIRouter, status
from pydantic import BaseModel

if TYPE_CHECKING:

    class NetworkModelProtocol(Protocol):
        """Structural shape of a model's dynamically-generated ``Network``
        class (built at runtime by
        ``PydanticUtility._generate_network_class`` via ``type(...)`` and
        attached as ``model.Network``). Declared here only for static
        analysis of this file's ``network_model`` usages -- the real class
        can't be expressed concretely since it's generated per-model.
        """

        GET: Type[BaseModel]
        LIST: Type[BaseModel]
        POST: Type[BaseModel]
        PUT: Type[BaseModel]
        SEARCH: Type[BaseModel]
        ResponseSingle: Type[BaseModel]
        ResponsePlural: Type[BaseModel]


# Type variable for network models
T = TypeVar("T", bound=BaseModel)


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
            func._static_route_config = []  # type: ignore[attr-defined]

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

        func._static_route_config.append(route_config)  # type: ignore[attr-defined]
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

    # Item 39 — per-resource endpoint versioning surface.
    #
    # ``version`` is the URL-path version segment (default ``"v1"`` to
    # match the framework's existing ``/v1/<resource>`` shape). When
    # ``prefix`` is None, the route prefix is derived as ``f"/{version}/
    # {resource_name}"``. Multiple managers may register the same
    # resource at different versions — declare e.g.
    # ``UserManagerV2(AbstractBLLManager, RouterMixin): version = "v2"``
    # alongside the existing ``UserManager`` and both versions route
    # concurrently, both appear in OpenAPI, and the SDK generator emits
    # version-suffixed methods. See IMPROVEMENTS_ORDERED.md Item 39 for
    # the full deprecation contract.
    version: ClassVar[str] = "v1"

    # ``deprecated_in`` / ``sunset_in`` carry the deprecation contract.
    # When set, the framework adds the ``Deprecation`` and ``Sunset``
    # HTTP headers to every response on this version's routes and emits
    # a logged warning per call after the deprecation date. Values are
    # ISO-8601 datetimes (UTC) or version tokens — the framework does
    # not enforce a specific format here, only that the values are
    # opaque strings forwarded to clients verbatim.
    deprecated_in: ClassVar[Optional[str]] = None
    sunset_in: ClassVar[Optional[str]] = None

    @classmethod
    def Router(cls, model_registry) -> APIRouter:
        """
        Generate FastAPI router for this manager.

        Args:
            model_registry: ModelRegistry instance for model access

        Returns:
            APIRouter configured for this manager's endpoints
        """
        # Imported lazily inside the method: ``router`` sits at the top of the
        # load-time DAG (it imports ``routes`` -> ``resource``/``query`` ->
        # ``types``), so a module-level import here would form a cycle. This
        # call only runs at router-generation time, by which point the
        # package is fully initialised.
        from .router import create_router_from_manager

        return create_router_from_manager(
            manager_class=cls, model_registry=model_registry
        )
