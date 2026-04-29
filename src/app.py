import json
import os
import sys
from contextlib import contextmanager
from typing import Any, Dict, Optional

# Venv + dependency bootstrap lives in ``bootstrap.py`` so importing
# ``app`` for its ``instance``/``build_app`` factories does not pull in
# subprocess/venv machinery. ``setup_python_path`` is re-exported here
# for backwards compatibility — several other functions in this module
# call it by its short name.
from bootstrap import run_venv_bootstrap as _venv  # noqa: F401
from bootstrap import setup_python_path

from lib.Logging import logger


if __name__ == "__main__":
    _venv()

### -------------------------------------------------------------
### ------- PATH SETUP COMPLETE, IMPORT LOCAL MODULES HERE ------
### -------------------------------------------------------------

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from database.DatabaseManager import DatabaseManager
from lib.Environment import env, inflection
from lib.Logging import logger
from lib.Pydantic import ModelRegistry
from lib.RequestContext import (
    DeadlineExceededError,
    clear_request_context,
    set_request_deadline_ms,
    set_request_user,
)


def setup_extension_dependencies():
    """Install PIP dependencies for all configured extensions using ExtensionRegistry"""
    from extensions.AbstractExtensionProvider import ExtensionRegistry
    from lib.Environment import env
    from lib.Logging import logger

    setup_python_path()

    app_extensions_str = env("APP_EXTENSIONS")
    if not app_extensions_str:
        logger.debug("No extensions configured, skipping extension dependency check")
        return True

    extension_names = [
        name.strip() for name in app_extensions_str.split(",") if name.strip()
    ]

    if not extension_names:
        logger.debug("No valid extension names found")
        return True

    logger.info(f"Installing dependencies for extensions: {extension_names}")

    try:
        registry = ExtensionRegistry(app_extensions_str)
        install_results = registry.install_extension_dependencies(extension_names)

        failed_installs = [
            dep_name
            for dep_name, success in install_results.items()
            if not success and not dep_name.endswith("_error")
        ]

        if failed_installs:
            logger.error(
                f"Failed to install some extension dependencies: {failed_installs}"
            )
            return False
        else:
            logger.info("Extension dependency installation completed")
            return True

    except Exception as e:
        logger.error(f"Error installing extension dependencies: {e}")
        return False


if __name__ == "__main__":
    setup_extension_dependencies()


import json


def install_extension_dependencies_with_restart(extensions_str: str):
    """Install extension dependencies and restart if needed."""
    from extensions.AbstractExtensionProvider import ExtensionRegistry
    from lib.Logging import logger

    if not extensions_str:
        return

    # Check if we're in a restart loop (prevent infinite restarts)
    restart_flag = os.environ.get("_APP_DEPENDENCY_RESTART", "0")
    if restart_flag == "1":
        logger.debug(
            "Skipping dependency installation - already restarted for dependencies"
        )
        return

    extension_names = [
        name.strip() for name in extensions_str.split(",") if name.strip()
    ]
    logger.debug(f"Installing dependencies for extensions: {extension_names}")

    # Create ExtensionRegistry temporarily for dependency installation
    extension_registry = ExtensionRegistry(extensions_str)

    try:
        install_results = extension_registry.install_extension_dependencies(
            extension_names
        )

        failed_installs = [
            dep_name
            for dep_name, success in install_results.items()
            if not success and not dep_name.endswith("_error")
        ]

        if failed_installs:
            logger.error(f"Failed to install extension dependencies: {failed_installs}")
            raise Exception(
                f"Failed to install extension dependencies: {failed_installs}"
            )

        # Check if any dependencies were actually installed (restart needed)
        successful_installs = [
            dep_name
            for dep_name, success in install_results.items()
            if success and not dep_name.endswith("_error")
        ]

        if successful_installs:
            logger.info(
                f"Successfully installed extension dependencies: {successful_installs}"
            )
            logger.info(
                "Restarting application to ensure dependencies are properly loaded..."
            )
            # Set restart flag and restart
            os.environ["_APP_DEPENDENCY_RESTART"] = "1"
            os.execl(sys.executable, sys.executable, *sys.argv)

    except Exception as e:
        logger.error(f"Error installing extension dependencies: {e}")
        raise Exception(f"Failed to setup extension dependencies: {e}")


def create_registry_with_db_manager(
    db_manager, extensions_list: Optional[str] = None
):
    """Create a ModelRegistry with the proper DatabaseManager attached.

    Sentinel: ``extensions_list=None`` means "fall back to APP_EXTENSIONS";
    ``extensions_list=""`` means "no extensions". The env-var lookup
    happens inside the function body so consumers that import the package
    and then set ``os.environ['APP_EXTENSIONS']`` before calling get the
    expected value (Item 65).
    """
    from extensions.AbstractExtensionProvider import ExtensionRegistry
    from lib.Logging import logger

    if extensions_list is None:
        extensions_list = env("APP_EXTENSIONS")

    logger.debug(
        f"create_registry_with_db_manager: extensions_list={extensions_list}, extensions_str={extensions_list}"
    )

    # Create ExtensionRegistry with CSV initialization (replaces deprecated scoped_import)
    extension_registry = ExtensionRegistry(extensions_list or "")

    install_extension_dependencies_with_restart(extensions_list)

    # Discover extension models for backward compatibility
    if extensions_list:
        extension_names = [
            name.strip() for name in extensions_list.split(",") if name.strip()
        ]
        logger.debug(
            f"Calling discover_extension_models with extension_names={extension_names}"
        )

        extension_registry.discover_extension_models(extension_names)
        logger.debug(
            f"After discover_extension_models, extension_models count: {len(extension_registry.extension_models)}"
        )
    else:
        logger.debug(f"No extensions_str, skipping discovery")

    # Create ModelRegistry with ExtensionRegistry and auto-bind models
    registry = ModelRegistry(
        database_manager=db_manager,
        extension_registry=extension_registry,
        auto_bind_models=True,
        extensions_str=extensions_list,
    )

    return registry


@contextmanager
def environment_overrides(overrides: Dict[str, Any]):
    """Context manager for temporarily overriding environment variables."""
    if overrides:
        from unittest.mock import patch

        from lib.Environment import settings

        with patch.dict(os.environ, overrides), patch.multiple(settings, **overrides):
            yield
    else:
        yield


def prepare_overrides(db_prefix: str, extensions: Optional[str]) -> Dict[str, str]:
    """Prepare environment variable overrides based on parameters."""
    overrides = {}

    if db_prefix:
        original_db_name = env("DATABASE_NAME")
        overrides["DATABASE_NAME"] = f"{db_prefix}.{original_db_name}"

    if extensions is not None:
        overrides["APP_EXTENSIONS"] = extensions
        logger.debug(f"Setting APP_EXTENSIONS override to '{extensions}'")

    return overrides


def instance(db_prefix: str = "", extensions: Optional[str] = None):
    """
    Create a FastAPI application instance with database prefix and extension configuration.

    Sentinel (Item 65): ``extensions=None`` means "fall back to
    APP_EXTENSIONS" -- the env lookup happens inside the function body so
    a consumer can ``import serverframework`` then
    ``os.environ['APP_EXTENSIONS'] = 'payment'`` then ``run()`` and have
    the override picked up. ``extensions=""`` means unambiguously "no
    extensions" and bypasses the env fallback.

    Args:
        db_prefix: Prefix to add to the original DATABASE_NAME (e.g., "test" or "test.payment")
        extensions: Extensions to load. ``None`` -> read APP_EXTENSIONS.
            ``""`` -> load none. CSV string -> load those.

    Returns:
        Configured FastAPI application instance with isolated ModelRegistry
    """
    if extensions is None:
        extensions = env("APP_EXTENSIONS")

    logger.debug(
        f"instance() called with db_prefix='{db_prefix}', extensions='{extensions}'"
    )
    logger.info(
        f"Booting {env('APP_NAME')}, please report any issues to {env('APP_REPOSITORY')}"
    )

    with environment_overrides(prepare_overrides(db_prefix, extensions)):
        instance_model_registry = create_registry_with_db_manager(
            DatabaseManager(db_prefix), extensions
        )
        try:
            instance_model_registry.commit()
        except Exception as e:
            logger.error(f"Error booting {env('APP_NAME')} instance: {e}")
            raise Exception(f"Error booting {env('APP_NAME')} instance: {e}") from e
        return build_app(instance_model_registry)


def build_app(model_registry: ModelRegistry):
    """
    FastAPI application factory function with ModelRegistry.
    Returns a configured FastAPI application instance.

    Args:
        model_registry: ModelRegistry instance with bound models
    """
    from lib.Environment import env
    from lib.Logging import logger

    # Item 67 — single source of truth: the installed distribution's
    # metadata. Editable installs (`pip install -e .`) populate this
    # the same way wheel installs do, so in-tree development keeps
    # working. The sibling ``version`` file fallback was removed
    # because it drifted from the actual release version (and was a
    # second source of truth that defeated the purpose of having
    # ``[project.version]`` in pyproject.toml at all). When the package
    # is not installed at all (rare — e.g. running directly from a
    # source checkout without `pip install`), we default to "0.0.0"
    # rather than masking the missing-install with stale file data.
    version: str = "0.0.0"
    try:
        from importlib.metadata import PackageNotFoundError, version as _pkg_version

        for _dist_name in ("serverframework", "server"):
            try:
                version = _pkg_version(_dist_name)
                break
            except PackageNotFoundError:
                continue
    except ImportError:
        pass

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Handles startup and shutdown events for each worker"""
        # Get the database manager from app state (attached during app creation)
        db_mgr = getattr(app.state, "DB", None)
        if db_mgr is None:
            # Fallback: create a default instance if not attached
            # Use test prefix if running in pytest to avoid touching production database
            import os

            db_prefix = (
                "test.lifespan_fallback"
                if os.environ.get("PYTEST_CURRENT_TEST")
                else ""
            )
            db_mgr = DatabaseManager(db_prefix)
            db_mgr.init_engine_config()
        db_mgr.init_worker()
        try:
            yield
        finally:
            await db_mgr.close_worker()

    app = FastAPI(
        title=env("APP_NAME"),
        version=env("APP_VERSION"),
        description=f"{env('APP_NAME')} is {inflection.a(env('APP_DESCRIPTION'))}. Visit the GitHub repo for more information or to report issues. {env('APP_REPOSITORY')}",
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
        openapi_version="3.1.0",
    )
    app.extensions = {}

    # ------------------------------------------------------------------
    # CORS configuration (Item 71a).
    #
    # Production deployments MUST declare allowed origins via the
    # ``APP_CORS_ALLOWED_ORIGINS`` env var (comma-separated). The
    # framework refuses to start if production is configured with a
    # wildcard origin or with ``allow_credentials=True`` + ``*``.
    # Development deployments without an explicit allowlist fall back
    # to ``*`` (without credentials) and emit a startup warning.
    # ------------------------------------------------------------------
    from lib.InboundSecurity import parse_cors_origins, validate_cors_config

    app_env = (env("APP_ENV", default="development") or "development").lower()
    raw_origins = env("APP_CORS_ALLOWED_ORIGINS", default="")
    parsed_origins = parse_cors_origins(raw_origins)

    if not parsed_origins:
        # No explicit allowlist. Production refuses to start; dev warns + uses *.
        if app_env == "production":
            validate_cors_config(
                allow_origins=["*"],
                allow_credentials=True,
                app_env="production",
            )
            # Unreachable — validate_cors_config raises in this branch.
            raise RuntimeError(
                "CORS misconfiguration: APP_ENV=production with no "
                "APP_CORS_ALLOWED_ORIGINS allowlist."
            )
        logger.warning(
            "APP_CORS_ALLOWED_ORIGINS is empty; falling back to '*' without "
            "credentials. Set APP_CORS_ALLOWED_ORIGINS for non-development "
            "deployments."
        )
        cors_origins = ["*"]
        cors_credentials = False
    else:
        # Validate explicit allowlist; production with '*' fails fast.
        cors_origins = parsed_origins
        cors_credentials = "*" not in parsed_origins
        validate_cors_config(
            allow_origins=cors_origins,
            allow_credentials=cors_credentials,
            app_env=app_env,
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=cors_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # JWT extraction middleware
    @app.middleware("http")
    async def extract_jwt_context(request: Request, call_next):
        """Extract JWT token data and set request context"""
        clear_request_context()  # Clear any previous context

        # Get authorization header
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                import jwt

                token = auth_header.replace("Bearer ", "").strip()
                # Decode without verification to get payload (verification happens elsewhere)
                payload = jwt.decode(token, options={"verify_signature": False})

                # Set user context with timezone info
                user_info = {
                    "user_id": payload.get("sub"),
                    "email": payload.get("email"),
                    "timezone": payload.get("timezone", "UTC"),
                }
                set_request_user(user_info)
            except Exception:
                # If JWT decode fails, just continue without setting context
                pass

        # Item 47 — read X-Request-Timeout-Ms (gRPC-style relative ms)
        # and convert to an absolute monotonic deadline at ingress so all
        # subsequent budget checks share one time source.
        timeout_header = request.headers.get("X-Request-Timeout-Ms")
        if timeout_header:
            try:
                set_request_deadline_ms(int(timeout_header))
            except (TypeError, ValueError):
                pass

        try:
            response = await call_next(request)
        except DeadlineExceededError as exc:
            return JSONResponse(
                status_code=504,
                content={
                    "detail": str(exc),
                    "elapsed_ms": exc.elapsed_ms,
                    "layer": exc.layer,
                },
            )

        # NOTE: A previous debugging block here printed POST response bodies
        # to stdout, including JSON payloads. Removed (Item 73) — that path
        # leaked PII and credentials into production logs and offered no
        # observability that structured logging (Item 85) does not.

        clear_request_context()  # Clear context after request
        return response

    # Add middleware to catch JSON parsing errors early
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as StarletteRequest
    from starlette.responses import Response as StarletteResponse

    class JSONParsingMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: StarletteRequest, call_next):
            try:
                # Let the request proceed normally
                response = await call_next(request)
                return response
            except Exception as exc:
                # Check if this is a JSON parsing error
                if "json" in str(exc).lower() and (
                    "decode" in str(exc).lower() or "syntax" in str(exc).lower()
                ):
                    return JSONResponse(
                        status_code=400,
                        content={"detail": "Invalid JSON syntax in request body"},
                    )
                # Re-raise if not a JSON error
                raise

    app.add_middleware(JSONParsingMiddleware)

    # Add exception handler for JSON decode errors (malformed JSON should return 400)
    @app.exception_handler(json.JSONDecodeError)
    async def json_decode_error_handler(request: Request, exc: json.JSONDecodeError):
        return JSONResponse(
            status_code=400, content={"detail": "Invalid JSON syntax in request body"}
        )

    def make_json_serializable(obj):
        """Recursively convert objects to JSON-serializable format."""
        if isinstance(obj, (str, int, float, bool, type(None))):
            return obj
        elif hasattr(obj, "model_dump"):
            # Pydantic model
            try:
                return obj.model_dump(mode="json")
            except Exception as e:
                logger.warning(
                    "make_json_serializable: model_dump failed; "
                    "falling back to str()",
                    exc_info=True,
                )
                return str(obj)
        elif isinstance(obj, dict):
            # Recursively handle dicts
            return {k: make_json_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple, set)):
            # Recursively handle iterables
            return [make_json_serializable(item) for item in obj]
        elif hasattr(obj, "__dict__"):
            # Other objects with __dict__
            try:
                # Try to get a dict representation
                if hasattr(obj, "to_dict"):
                    return obj.to_dict()
                elif hasattr(obj, "dict"):
                    return obj.dict()
                else:
                    # Last resort - convert to string
                    return str(obj)
            except Exception as e:
                logger.warning(
                    "make_json_serializable: dict-style coercion failed; "
                    "falling back to str()",
                    exc_info=True,
                )
                return str(obj)
        else:
            # Fallback to string representation
            return str(obj)

    # Add exception handler for HTTPException to ensure JSON serializable details
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        # Ensure the detail is JSON serializable
        detail = make_json_serializable(exc.detail)

        return JSONResponse(
            status_code=exc.status_code, content={"detail": detail}, headers=exc.headers
        )

    # Add exception handler for request validation errors that might include JSON parsing issues
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ):
        # Get the actual error details from the exception
        if hasattr(exc, "errors"):
            error_list = exc.errors()
        else:
            # For regular validation errors, return 422
            return JSONResponse(status_code=422, content={"detail": "Validation error"})

        # Check if any of the errors are specifically JSON parsing errors
        # Look at the actual error type and location from Pydantic
        for error in error_list:
            error_type = error.get("type", "")
            error_msg = error.get("msg", "")
            error_loc = error.get("loc", [])

            # Check for actual JSON parsing error types from Pydantic
            # These occur when the JSON itself is malformed, not when field validation fails
            json_parsing_error_types = [
                "json_invalid",
                "json_type",
                "json_decode",
                "value_error.jsondecode",
            ]

            # Check for JSON parsing error messages from the JSON parser
            # Be specific to avoid catching Pydantic validation errors like "Input should be..."
            json_parsing_error_messages = [
                "JSON decode error",
                "Invalid JSON",
                "Expecting property name enclosed in double quotes",
                "Expecting value:",  # More specific - actual JSON parser message includes colon
                "Invalid control character",
                "Unterminated string",
                "Extra data:",  # More specific - actual JSON parser message includes colon
                "Expecting ',' delimiter",
                "Expecting ':' delimiter",
                "Invalid \\escape",
                "JSONDecodeError",
            ]

            # Also check if the error location suggests JSON parsing (root level, no field path)
            # JSON parsing errors typically have empty or very short location paths
            # Note: "body" is NOT a JSON parse location - it's a Pydantic validation location
            is_json_parse_location = len(error_loc) == 0 or (
                len(error_loc) == 1 and error_loc[0] == "__root__"
            )

            # If we find a JSON parsing error, return 400
            if error_type in json_parsing_error_types or (
                any(json_msg in error_msg for json_msg in json_parsing_error_messages)
                and is_json_parse_location
            ):
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid JSON syntax in request body"},
                )

        # For regular validation errors (field validation, type validation, etc.), return 422
        # These include errors like: string_type, int_type, missing, etc.
        # Use make_json_serializable to handle any non-serializable objects in error context
        return JSONResponse(
            status_code=422, content={"detail": make_json_serializable(exc.errors())}
        )

    if env("REST").strip().lower() == "true":
        # Build routers using the model registry

        for manager_name, router in model_registry.ep_routers.items():
            try:
                app.include_router(router)
            except Exception as e:
                logger.error(f"CRITICAL: Failed to include router {manager_name}: {e}")
                raise Exception(
                    f"Critical error loading router for {manager_name}: {e}"
                ) from e

        @app.get("/health", tags=["Health"])
        async def health():
            return {"status": "UP"}

        @app.get("/v1", tags=["Authentication"], status_code=204)
        async def verify_jwt(request: Request):
            """Verify JWT token and return 204 if valid, 401 if invalid"""
            from fastapi import Header, HTTPException, Response

            from logic.BLL_Auth import UserManager

            # Get authorization header
            authorization = request.headers.get("authorization")
            if not authorization:
                raise HTTPException(status_code=401, detail="Missing or empty JWT")

            # Check for empty Bearer token (e.g., "Bearer " with no token)
            if authorization.strip() == "Bearer" or authorization.strip() == "Bearer ":
                raise HTTPException(status_code=401, detail="Missing or empty JWT")

            try:
                # Verify the JWT token
                # Pass the model_registry into UserManager.auth (auth expects model_registry as first arg)
                model_registry = getattr(request.app.state, "model_registry", None)
                user = UserManager.auth(
                    model_registry=model_registry,
                    authorization=authorization,
                    request=request,
                )

                if not user:
                    raise HTTPException(status_code=401, detail="Invalid token")

                # Return 204 No Content for successful verification
                return Response(status_code=204)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"JWT verification error: {e}")
                raise HTTPException(status_code=401, detail="Invalid token")

    # Set up GraphQL using the model registry
    if env("GQL").strip().lower() == "true":
        if model_registry.gql:
            from starlette.requests import Request as StarletteRequest
            from strawberry.fastapi import GraphQLRouter

            def get_context(request: StarletteRequest):
                """Context getter for GraphQL that provides authentication context"""
                from lib.Logging import logger

                context = {}

                logger.debug(
                    f"GraphQL context getter called - Headers: {dict(request.headers)}"
                )

                # Extract requester_id from headers for GraphQL authentication
                auth_header = request.headers.get("authorization")
                api_key = request.headers.get("x-api-key")

                logger.debug(
                    f"GraphQL context: auth_header={bool(auth_header)}, api_key={bool(api_key)}"
                )

                # Check for API key first (for system entities)
                if api_key:
                    from lib.Environment import env

                    if api_key == env("ROOT_API_KEY"):
                        context["requester_id"] = env("ROOT_ID")
                        logger.debug(
                            "GraphQL context: Authenticated with ROOT API key, "
                            f"root_id={env('ROOT_ID')}"
                        )
                    elif api_key == env("SYSTEM_API_KEY"):
                        context["requester_id"] = env("SYSTEM_ID")
                        logger.debug(
                            "GraphQL context: Authenticated with SYSTEM API key, "
                            f"system_id={env('SYSTEM_ID')}"
                        )
                    elif api_key == env("TEMPLATE_API_KEY"):
                        context["requester_id"] = env("TEMPLATE_ID")
                        logger.debug(
                            "GraphQL context: Authenticated with TEMPLATE API key, "
                            f"template_id={env('TEMPLATE_ID')}"
                        )

                    if "requester_id" in context:
                        return context

                # Fall back to JWT authentication
                elif auth_header:
                    try:
                        from logic.BLL_Auth import UserManager

                        # Get model_registry from app state and try JWT auth
                        model_registry = getattr(
                            request.app.state, "model_registry", None
                        )
                        logger.debug(
                            f"GraphQL context: model_registry available = {model_registry is not None}"
                        )
                        if model_registry:
                            user = UserManager.auth(
                                model_registry=model_registry,
                                authorization=auth_header,
                                request=request,
                            )
                            if user and hasattr(user, "id"):
                                context["requester_id"] = user.id
                                logger.debug(
                                    f"GraphQL context: Authenticated JWT user with id={user.id}"
                                )
                            else:
                                logger.debug(
                                    f"GraphQL context: JWT authentication failed, user={user}"
                                )
                    except Exception as e:
                        logger.error(
                            f"Failed to authenticate user from GraphQL context: {e}"
                        )

                logger.debug(f"GraphQL context: Final context={context}")
                return context

            graphql_app = GraphQLRouter(
                schema=model_registry.gql, context_getter=get_context
            )
            app.include_router(graphql_app, prefix="/graphql")

    if env("MCP").strip().lower() == "true":
        from fastapi_mcp import FastApiMCP

        mcp = FastApiMCP(app)
        mcp.mount()
    app.state.model_registry = model_registry

    # Test all models for PydanticUndefinedType before OpenAPI generation
    def test_all_models_for_undefined_types():
        from lib.Logging import logger

        logger.debug("Testing all models for PydanticUndefinedType...")

        problematic_models = []

        # Get all registered managers and test their models
        for route in app.routes:
            if hasattr(route, "endpoint"):
                endpoint = route.endpoint

                # Check response models
                if hasattr(endpoint, "response_model") and endpoint.response_model:
                    model_class = endpoint.response_model
                    try:
                        # Try to create a minimal instance and call model_dump
                        if hasattr(model_class, "model_fields"):
                            logger.debug(f"Testing model: {model_class.__name__}")
                            # Create empty instance to test model_dump
                            try:
                                test_instance = model_class()
                                test_instance.model_dump()
                                logger.debug(
                                    f"  ✓ {model_class.__name__} serializes OK"
                                )
                            except Exception as e:
                                if "PydanticUndefinedType" in str(e):
                                    logger.error(
                                        f"  ✗ UNDEFINED TYPE ERROR in {model_class.__name__}: {e}"
                                    )
                                    problematic_models.append((model_class, str(e)))

                                    # Inspect the fields
                                    for (
                                        field_name,
                                        field_info,
                                    ) in model_class.model_fields.items():
                                        annotation = field_info.annotation
                                        if (
                                            "PydanticUndefinedType" in str(annotation)
                                            or str(type(annotation))
                                            == "<class 'pydantic_core._pydantic_core.PydanticUndefinedType'>"
                                        ):
                                            logger.error(
                                                f"    UNDEFINED FIELD: {field_name} -> {annotation}"
                                            )
                                else:
                                    logger.debug(
                                        f"  - {model_class.__name__} has other error: {e}"
                                    )
                    except Exception as e:
                        logger.debug(f"  Error testing {model_class}: {e}")

        if problematic_models:
            logger.error(
                f"Found {len(problematic_models)} models with undefined types:"
            )
            for model_class, error in problematic_models:
                logger.error(
                    f"  - {model_class.__name__} from {getattr(model_class, '__module__', 'unknown')}: {error}"
                )
        else:
            logger.debug("No models with undefined types found")

        return problematic_models

    # Override openapi to catch PydanticUndefinedType errors
    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        try:
            from lib.Logging import logger

            # Test all models first
            problematic_models = test_all_models_for_undefined_types()

            logger.debug("Testing OpenAPI schema generation step by step...")

            from fastapi.openapi.utils import get_openapi

            # Try to isolate which part of OpenAPI generation fails
            try:
                logger.debug("Step 1: Creating base OpenAPI structure...")
                base_schema = {
                    "openapi": "3.1.0",
                    "info": {"title": app.title, "version": app.version},
                    "paths": {},
                }

                logger.debug("Step 2: Generating full schema...")
                openapi_schema = get_openapi(
                    title=app.title,
                    version=app.version,
                    openapi_version=app.openapi_version,
                    description=app.description,
                    routes=app.routes,
                )

                logger.debug("Step 3: Testing schema serialization...")
                # Try to serialize the schema to JSON to trigger the error
                import json

                from fastapi.encoders import jsonable_encoder

                logger.debug("Step 3a: Converting schema to dict...")
                schema_dict = jsonable_encoder(openapi_schema)

                logger.debug("Step 3b: Converting to JSON...")
                json_str = json.dumps(schema_dict)

                logger.debug("✓ OpenAPI schema generated successfully")
                app.openapi_schema = openapi_schema
                return app.openapi_schema

            except Exception as step_error:
                logger.error(f"OpenAPI generation failed at step: {step_error}")
                if "PydanticUndefinedType" in str(step_error):
                    logger.error(
                        "The error is in the OpenAPI schema object itself, not the response models"
                    )

                    # Try to find the problematic part by testing schema components
                    logger.info("Testing schema components individually...")

                    try:
                        from fastapi.openapi.utils import get_openapi_path

                        logger.info("Testing individual routes...")

                        for i, route in enumerate(app.routes):
                            try:
                                if hasattr(route, "path") and hasattr(route, "methods"):
                                    logger.debug(f"Testing route {i}: {route.path}")
                                    # Try to generate OpenAPI for just this route
                                    test_schema = get_openapi(
                                        title="Test",
                                        version="1.0.0",
                                        routes=[route],
                                    )
                                    # Try to serialize it
                                    jsonable_encoder(test_schema)
                            except Exception as route_error:
                                if "PydanticUndefinedType" in str(route_error):
                                    logger.error(
                                        f"PROBLEMATIC ROUTE FOUND: {route.path} - {route.methods}"
                                    )
                                    logger.error(f"Route error: {route_error}")

                                    # Try to inspect the route's details
                                    if hasattr(route, "endpoint"):
                                        endpoint = route.endpoint
                                        logger.error(f"Endpoint: {endpoint}")
                                        if hasattr(endpoint, "response_model"):
                                            logger.error(
                                                f"Response model: {endpoint.response_model}"
                                            )
                                        if hasattr(endpoint, "__annotations__"):
                                            logger.error(
                                                f"Annotations: {endpoint.__annotations__}"
                                            )
                    except Exception as comp_error:
                        logger.error(f"Error testing components: {comp_error}")

                raise step_error

        except Exception as e:
            if "PydanticUndefinedType" in str(e):
                from lib.Logging import logger

                logger.error(
                    f"PydanticUndefinedType error in OpenAPI schema generation: {e}"
                )

                # Return a minimal schema to prevent total failure
                return {
                    "openapi": "3.1.0",
                    "info": {"title": app.title, "version": app.version},
                    "paths": {},
                    "components": {"schemas": {}},
                }
            else:
                raise

    app.openapi = custom_openapi

    return app


if __name__ == "__main__":
    from lib.Environment import env

    env_log_level = env("LOG_LEVEL").lower()
    workers = env("UVICORN_WORKERS")
    if workers.isnumeric():
        workers = int(workers)
    else:
        workers = 1
    host = env("UVICORN_HOST")
    port = env("UVICORN_PORT")
    log_level = env_log_level
    if log_level == "debug":
        log_level = "trace"
    reload = env("UVICORN_RELOAD").lower() == "true"
    logger.debug(f"Booting server...")
    uvicorn.run(
        "app:instance",
        host="0.0.0.0",
        port=1996,
        workers=workers,
        log_level=(
            env_log_level
            if env_log_level in ["info", "debug", "warning", "error", "critical"]
            else "info"
        ),
        proxy_headers=True,
        reload=env_log_level == "debug",
        factory=True,
    )
