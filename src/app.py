### -------------------------------------------------------------
### -- INITIAL VENV SETUP - DO NOT IMPORT ANY LOCAL FILES HERE --
### -------------------------------------------------------------
import json
import os
import shutil
import subprocess
import sys
import venv
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from lib.Logging import logger


def setup_python_path():
    """Ensure the Python path is set up correctly"""
    current_file_path = Path(__file__).resolve()
    src_dir = current_file_path.parent
    root_dir = src_dir.parent
    if str(root_dir) not in sys.path:
        sys.path.insert(0, str(root_dir))
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def _venv():
    """Create a virtual environment if it doesn't exist and install requirements"""
    from lib.Logging import logger

    current_file_path = Path(__file__).resolve()
    src_dir = current_file_path.parent
    root_dir = src_dir.parent
    venv_dir = root_dir / ".venv"
    requirements_file = root_dir / "requirements.txt"

    # Check if uv is available
    use_uv = shutil.which("uv") is not None

    # If we're not in a virtual environment, create one and restart
    if sys.prefix == sys.base_prefix:
        if not venv_dir.exists():
            logger.debug(f"Creating virtual environment at {venv_dir}")
            try:
                if use_uv:
                    logger.debug("Using uv for faster virtual environment creation...")
                    subprocess.run(["uv", "venv", str(venv_dir)], check=True)
                else:
                    venv.create(venv_dir, with_pip=True)
            except Exception as e:
                logger.debug(f"Failed to create virtual environment: {e}")
                return False

        python_path = venv_dir / ("Scripts" if os.name == "nt" else "bin") / "python"
        logger.debug(f"Restarting with virtual environment at {python_path}...")
        try:
            os.execl(str(python_path), str(python_path), *sys.argv)
        except Exception as e:
            logger.debug(f"Failed to restart with virtual environment: {e}")
            return False

    # We're now in the virtual environment, install requirements
    logger.debug("Running in virtual environment, checking requirements...")

    if requirements_file.exists():
        logger.debug("Requirements file found, installing...")
        try:
            if use_uv:
                logger.debug("Using uv for faster package installation...")
                result = subprocess.run(
                    ["uv", "pip", "install", "-r", str(requirements_file)],
                    capture_output=True,
                    text=True,
                    check=True,
                )
            else:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "-r",
                        str(requirements_file),
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )

            from lib.Logging import logger

            logger.info("Requirements.txt installed successfully!")
        except subprocess.CalledProcessError as e:
            logger.debug(f"Failed to install requirements.txt: {e.stderr}")
            return False
        except Exception as e:
            logger.debug(f"Error installing requirements.txt: {e}")
            return False
    else:
        logger.debug(f"Requirements file not found at {requirements_file}")

    logger.debug("Requirements complete, configuring PATH...")
    setup_python_path()
    from lib.Logging import logger

    logger.info("PATH configuration complete, local modules loadable.")
    return True


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
from lib.RequestContext import clear_request_context, set_request_user


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
    db_manager, extensions_list: str = env("APP_EXTENSIONS")
):
    """Create a ModelRegistry with the proper DatabaseManager attached."""
    from extensions.AbstractExtensionProvider import ExtensionRegistry
    from lib.Logging import logger

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


def instance(db_prefix: str = "", extensions: str = env("APP_EXTENSIONS")):
    """
    Create a FastAPI application instance with database prefix and extension configuration.

    Args:
        db_prefix: Prefix to add to the original DATABASE_NAME (e.g., "test" or "test.payment")
        extensions: Extensions to load (e.g., "" for core, "payment" for payment extension)

    Returns:
        Configured FastAPI application instance with isolated ModelRegistry
    """
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

    this_directory = os.path.abspath(os.path.dirname(__file__))
    with open(os.path.join(this_directory, "version"), encoding="utf-8") as f:
        version = f.read().strip()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Handles startup and shutdown events for each worker"""
        # Get the database manager from app state (attached during app creation)
        db_mgr = getattr(app.state, "DB", None)
        if db_mgr is None:
            # Fallback: create a default instance if not attached
            db_mgr = DatabaseManager()
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

    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
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

        response = await call_next(request)

        # Debug: Log response body for POST requests
        if request.method == "POST":
            try:
                # For StreamingResponse, we need to consume the body
                if hasattr(response, "body_iterator"):
                    body_parts = []
                    async for chunk in response.body_iterator:
                        body_parts.append(chunk)
                    body_bytes = b"".join(body_parts)
                    body_str = body_bytes.decode("utf-8")
                    print(f"DEBUG POST RESPONSE BODY: {body_str}")

                    # Try to parse as JSON
                    try:
                        import json

                        body_json = json.loads(body_str)
                        print(
                            f"DEBUG POST RESPONSE JSON: {json.dumps(body_json, indent=2)}"
                        )
                    except:
                        pass

                    # Recreate the response with the consumed body
                    from starlette.responses import Response

                    response = Response(
                        content=body_bytes,
                        status_code=response.status_code,
                        headers=response.headers,
                        media_type=response.media_type,
                    )
                else:
                    print(f"DEBUG: Response type {type(response)} - no body_iterator")
            except Exception as e:
                print(f"DEBUG: Could not read response: {e}")

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

    # Add exception handler for HTTPException to ensure JSON serializable details
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        # Ensure the detail is JSON serializable
        detail = exc.detail

        def make_json_serializable(obj):
            """Recursively convert objects to JSON-serializable format."""
            if isinstance(obj, (str, int, float, bool, type(None))):
                return obj
            elif hasattr(obj, "model_dump"):
                # Pydantic model
                try:
                    return obj.model_dump(mode="json")
                except:
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
                except:
                    return str(obj)
            else:
                # Fallback to string representation
                return str(obj)

        # Convert detail to JSON-serializable format
        detail = make_json_serializable(detail)

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
            json_parsing_error_messages = [
                "JSON decode error",
                "Invalid JSON",
                "Expecting property name enclosed in double quotes",
                "Expecting value",
                "Invalid control character",
                "Unterminated string",
                "Extra data",
                "Expecting ',' delimiter",
                "Expecting ':' delimiter",
                "Invalid \\escape",
                "Expecting",  # General JSON parsing errors from json.loads
                "JSONDecodeError",
            ]

            # Also check if the error location suggests JSON parsing (root level, no field path)
            # JSON parsing errors typically have empty or very short location paths
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
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

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
