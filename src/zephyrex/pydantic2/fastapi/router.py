from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Type,
)

import stringcase
from fastapi import APIRouter, Request

from zephyrex.lib.Logging import logger

from .types import (
    AuthType,
    CustomRouteConfig,
    HTTPMethod,
    NestedResourceConfig,
    RouteType,
)
from .query import get_request_info
from .resource import create_manager_factory
from .routes import register_custom_route, register_route

if TYPE_CHECKING:
    from zephyrex.pydantic2.manager_contract import ManagerContract as ManagerContract


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

    # Get configuration from ClassVars. Item 39: when the manager declares
    # a non-default ``version`` and no explicit ``prefix``, derive the
    # prefix from the version token rather than the hard-coded "v1".
    version_token = getattr(manager_class, "version", None) or "v1"
    prefix: str = (
        getattr(manager_class, "prefix", None) or f"/{version_token}/{resource_name}"
    )
    tags: List[str] = getattr(manager_class, "tags", None) or [
        f"{stringcase.titlecase(resource_name.replace('_', ' '))} Management"
    ]
    auth_type: AuthType = getattr(manager_class, "auth_type", AuthType.JWT)
    routes_to_register: Optional[List[RouteType]] = getattr(
        manager_class, "routes_to_register", None
    )
    route_auth_overrides: Dict[RouteType, AuthType] = (
        getattr(manager_class, "route_auth_overrides", None) or {}
    )

    # System entity auto-configuration: if BaseModel.is_system_entity=True,
    # automatically require API key authentication for write operations
    base_model = getattr(manager_class, "BaseModel", None) or getattr(
        manager_class, "_model", None
    )
    is_system_entity = (
        getattr(base_model, "is_system_entity", False) if base_model else False
    )
    if is_system_entity:
        write_routes = [
            RouteType.CREATE,
            RouteType.UPDATE,
            RouteType.DELETE,
            RouteType.BATCH_UPDATE,
            RouteType.BATCH_DELETE,
        ]
        # Only override if not already explicitly set
        for write_route in write_routes:
            if write_route not in route_auth_overrides:
                route_auth_overrides[write_route] = AuthType.API_KEY

    custom_routes: List[CustomRouteConfig] = (
        getattr(manager_class, "custom_routes", None) or []
    )
    nested_resources: Dict[str, NestedResourceConfig] = (
        getattr(manager_class, "nested_resources", None) or {}
    )
    example_overrides: Dict[str, Dict[str, Any]] = (
        getattr(manager_class, "example_overrides", None) or {}
    )

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
    # If a manager explicitly set an empty list but explicitly *overrides*
    # the inherited ``update`` method (common for user/current-user
    # managers), register at least the UPDATE route so
    # PUT /v1/<resource>/{id} exists and doesn't 404. Inheriting
    # ``update`` from ``AbstractBLLManager`` is no longer sufficient —
    # otherwise managers that legitimately want zero CRUD routes (e.g.
    # action-only managers behind ``@custom_route``) would silently get
    # a write surface they never asked for and never gated.
    elif isinstance(routes_to_register, list) and len(routes_to_register) == 0:
        if "update" in getattr(manager_class, "__dict__", {}):
            routes_to_register = [RouteType.UPDATE]

    # Create main router
    router = APIRouter(prefix=prefix, tags=tags)  # type: ignore[arg-type]

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

    # Item 40 hook — register typed @custom_route-decorated methods.
    try:
        from zephyrex.lib.CustomRoute import (
            register_custom_routes as _register_typed_custom_routes,
        )

        _register_typed_custom_routes(
            router,
            manager_class,
            manager_factory=create_manager_factory(
                manager_class, model_registry, auth_type
            ),
        )
    except Exception as _exc:  # pragma: no cover - defensive: never break CRUD
        logger.debug("Item 40 custom-route registration skipped: %s", _exc)

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
        if callable(child_manager_class) and not isinstance(child_manager_class, type):
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
        nested_router = APIRouter(prefix=nested_prefix, tags=tags)  # type: ignore[arg-type]

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
                method_str: str = custom_route_config["method"].upper()  # type: ignore[no-redef]
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

            # Create a factory function to properly capture variables in closure
            def create_nested_endpoint(
                route_function: str,
                res_name: str,
                mgr_class: Type,
            ):
                async def nested_endpoint(request: Request):
                    parent_id = request.path_params[f"{res_name}_id"]
                    factory = create_manager_factory(
                        mgr_class, model_registry, auth_type
                    )
                    request_info = await get_request_info(request)
                    parent_manager = factory(request=request_info)

                    # Call method on parent manager (where nested custom routes are defined)
                    method_func: Callable = getattr(parent_manager, route_function)
                    return method_func(parent_id)

                return nested_endpoint

            # Create endpoint with captured values
            nested_endpoint = create_nested_endpoint(
                custom_route.function,
                resource_name,
                manager_class,
            )

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

    # Item 39 — tag every endpoint registered on this router with the
    # owning manager class so the inbound middleware in app.py can read
    # ``deprecated_in`` / ``sunset_in`` per response and inject the
    # ``Deprecation`` / ``Sunset`` HTTP headers without a separate
    # prefix-to-manager registry.
    #
    # Walk the underlying list directly (``list.__iter__``) rather than
    # ``router.routes`` — the latter is a custom ``_RouteList`` whose
    # first iteration is one-shot and yields a proxy that the openapi
    # generator depends on; consuming it here would leak that proxy.
    for route in list.__iter__(router.routes):
        endpoint = getattr(route, "endpoint", None)
        if endpoint is not None and not hasattr(endpoint, "_router_mixin_manager"):
            try:
                endpoint._router_mixin_manager = manager_class
            except (AttributeError, TypeError):
                pass

    return router


def generate_routers_from_model_registry(model_registry) -> Dict[str, APIRouter]:
    """
    Generate routers for all models in the model registry using Model.Manager pattern.

    Routers are sorted by prefix length (longest first) to ensure more specific routes
    are registered before less specific ones. This prevents route conflicts like
    /v1/provider/instance/{id} matching /v1/provider/instance/setting.

    Args:
        model_registry: Model registry instance

    Returns:
        Dict mapping manager names to their routers, ordered by prefix length (longest first)
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
            manager_class: Type["ManagerContract"] = model_class.Manager
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
                try:
                    router = create_router_from_manager(manager_class, model_registry)
                    if router and router.routes:
                        routers[manager_name] = router
                        logger.info(f"Generated router for {manager_name}")
                except Exception as e:
                    logger.debug(
                        f"Could not auto-generate router for {manager_name}: {e}"
                    )
        else:
            logger.debug(f"Model {model_name} does not have a Manager attribute")

    # Sort routers by prefix length (longest first) to ensure more specific routes
    # are registered before less specific ones in FastAPI.
    # This prevents route conflicts like /v1/provider/instance/{id} matching
    # /v1/provider/instance/setting (where "setting" would be interpreted as {id}).
    def get_router_prefix_length(item):
        manager_name, router = item
        # Get the prefix from the router, default to empty string if not found
        prefix = getattr(router, "prefix", "") or ""
        return len(prefix)

    sorted_routers = dict(
        sorted(routers.items(), key=get_router_prefix_length, reverse=True)
    )

    return sorted_routers
