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
from zephyrex.bootstrap import run_venv_bootstrap as _venv  # noqa: F401
from zephyrex.bootstrap import setup_python_path

from zephyrex.lib.Logging import logger

if __name__ == "__main__":
    _venv()

### -------------------------------------------------------------
### ------- PATH SETUP COMPLETE, IMPORT LOCAL MODULES HERE ------
### -------------------------------------------------------------

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from zephyrex.database.DatabaseManager import DatabaseManager
from zephyrex.lib.Environment import env, inflection
from zephyrex.lib.Logging import logger
from zephyrex.lib.Pydantic import ModelRegistry
from zephyrex.lib.RequestContext import (
    DeadlineExceededError,
    clear_request_context,
    mint_correlation_id,
    set_correlation_id,
    set_request_deadline_ms,
    set_request_user,
)


def parse_extension_csv(csv: str) -> list[str]:
    """Split a comma-separated extensions string into a trimmed, non-empty list.

    Extension names must be valid Python identifiers (alphanumeric + underscore).
    Names containing path separators, shell metacharacters, or dots are rejected.
    """
    import re

    names = [name.strip() for name in csv.split(",") if name.strip()]
    validated = []
    for name in names:
        if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
            validated.append(name)
        else:
            logger.warning("Rejected invalid extension name: %s", name)
    return validated


def _install_extension_deps(
    extensions_str: str,
) -> tuple[list[str], list[str]]:
    """Install extension dependencies and return ``(failed, successful)`` lists."""
    from zephyrex.extensions.AbstractExtensionProvider import ExtensionRegistry

    extension_names = parse_extension_csv(extensions_str)
    if not extension_names:
        return [], []

    registry = ExtensionRegistry(extensions_str)
    install_results = registry.install_extension_dependencies(extension_names)

    failed = [
        dep_name
        for dep_name, success in install_results.items()
        if not success and not dep_name.endswith("_error")
    ]
    successful = [
        dep_name
        for dep_name, success in install_results.items()
        if success and not dep_name.endswith("_error")
    ]
    return failed, successful


def setup_extension_dependencies():
    """Install PIP dependencies for all configured extensions using ExtensionRegistry"""
    from zephyrex.lib.Environment import env
    from zephyrex.lib.Logging import logger

    setup_python_path()

    app_extensions_str = env("APP_EXTENSIONS")
    if not app_extensions_str:
        logger.debug("No extensions configured, skipping extension dependency check")
        return True

    logger.info(f"Installing dependencies for extensions: {app_extensions_str}")

    try:
        failed, _successful = _install_extension_deps(app_extensions_str)
        if failed:
            logger.error(f"Failed to install some extension dependencies: {failed}")
            return False
        logger.info("Extension dependency installation completed")
        return True

    except Exception as e:
        logger.error(f"Error installing extension dependencies: {e}")
        return False


if __name__ == "__main__":
    setup_extension_dependencies()


import json
import re as _re_module

# Strict W3C trace-id: exactly 32 lowercase hex chars (the all-zero value is
# the W3C "invalid" sentinel and is rejected). The ingest path lowercases
# before matching so case-insensitive clients are still accepted (M-9).
_STRICT_TRACE_ID_RE = _re_module.compile(r"^[0-9a-f]{32}$")


def install_extension_dependencies_with_restart(extensions_str: str):
    """Install extension dependencies and restart if needed.

    Opt-in via INSTALL_EXTENSION_DEPS=true. Production deployments
    should install deps at image build time instead.
    """
    from zephyrex.lib.Logging import logger

    if not extensions_str:
        return

    if os.environ.get("INSTALL_EXTENSION_DEPS", "false").lower() not in ("true", "1", "yes"):
        return

    restart_flag = os.environ.get("_APP_DEPENDENCY_RESTART", "0")
    if restart_flag == "1":
        logger.debug(
            "Skipping dependency installation - already restarted for dependencies"
        )
        return

    logger.debug(f"Installing dependencies for extensions: {extensions_str}")

    try:
        failed, successful = _install_extension_deps(extensions_str)

        if failed:
            logger.error(f"Failed to install extension dependencies: {failed}")
            from zephyrex import ExtensionLoadError
            raise ExtensionLoadError(f"Failed to install extension dependencies: {failed}")

        if successful:
            logger.info(f"Successfully installed extension dependencies: {successful}")
            logger.info(
                "Restarting application to ensure dependencies are properly loaded..."
            )
            os.environ["_APP_DEPENDENCY_RESTART"] = "1"
            os.execl(sys.executable, sys.executable, *sys.argv)

    except Exception as e:
        logger.error(f"Error installing extension dependencies: {e}")
        from zephyrex import ExtensionLoadError
        raise ExtensionLoadError(f"Failed to setup extension dependencies: {e}")


def create_registry_with_db_manager(db_manager, extensions_list: Optional[str] = None):
    """Create a ModelRegistry with the proper DatabaseManager attached.

    Sentinel: ``extensions_list=None`` means "fall back to APP_EXTENSIONS";
    ``extensions_list=""`` means "no extensions". The env-var lookup
    happens inside the function body so consumers that import the package
    and then set ``os.environ['APP_EXTENSIONS']`` before calling get the
    expected value (Item 65).
    """
    from zephyrex.extensions.AbstractExtensionProvider import ExtensionRegistry
    from zephyrex.lib.Logging import logger

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
        extension_names = parse_extension_csv(extensions_list)
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

        from zephyrex.lib.Environment import settings

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


def instance(
    db_prefix: str = "",
    extensions: Optional[str] = None,
    extensions_path: Optional[str] = None,
):
    """
    Create a FastAPI application instance with database prefix and extension configuration.

    Sentinel (Item 65): ``extensions=None`` means "fall back to
    APP_EXTENSIONS" -- the env lookup happens inside the function body so
    a consumer can ``import zephyrex`` then
    ``os.environ['APP_EXTENSIONS'] = 'payment'`` then ``run()`` and have
    the override picked up. ``extensions=""`` means unambiguously "no
    extensions" and bypasses the env fallback.

    Args:
        db_prefix: Prefix to add to the original DATABASE_NAME (e.g., "test" or "test.payment")
        extensions: Extensions to load. ``None`` -> read APP_EXTENSIONS.
            ``""`` -> load none. CSV string -> load those.
        extensions_path: Filesystem path to discover extensions from.
            Consumer extensions live here; bundled extensions are still found
            automatically. ``None`` -> read EXTENSIONS_PATH env var or use bundled.

    Returns:
        Configured FastAPI application instance with isolated ModelRegistry
    """
    if extensions_path is not None:
        from zephyrex.lib.Paths import set_extensions_root

        set_extensions_root(extensions_path)
    elif os.environ.get("EXTENSIONS_PATH"):
        from zephyrex.lib.Paths import set_extensions_root

        set_extensions_root(os.environ["EXTENSIONS_PATH"])

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
            from zephyrex import StartupError
            raise StartupError(f"Error booting {env('APP_NAME')} instance: {e}") from e
        return build_app(instance_model_registry)


# Sentinel exit code used by the SIGHUP handler so the supervisor can tell
# "operator asked us to restart" apart from "we crashed". 75 == EX_TEMPFAIL.
SIGHUP_RESTART_EXIT_CODE = 75


def install_sighup_handler(app: FastAPI) -> None:
    """Register a SIGHUP handler that performs a clean graceful restart.

    Item 20 refinement: in-process rebind of FastAPI routers, hooks, and
    background services is intentionally out of scope -- it requires
    rewriting Pydantic/SQLAlchemy/Strawberry registration paths to
    survive class-identity changes. Instead we perform the diff
    computation in-process (so the operator's logs document what
    changed) and then exit with ``SIGHUP_RESTART_EXIT_CODE`` so the
    supervisor (systemd ``Restart=on-failure``, k8s ``restartPolicy:
    Always``, ``docker --restart=always``) respawns us with the new
    extension state.

    This handler is a no-op on platforms that do not have ``SIGHUP``
    (e.g. Windows) and inside pytest runs (the env-var check below).
    """
    import signal

    if not hasattr(signal, "SIGHUP"):
        # Windows etc. -- no SIGHUP. Operators on those platforms run a
        # blue-green deployment instead.
        return

    def _handle_sighup(signum, frame):  # pragma: no cover -- signal path
        from zephyrex.extensions.HotReload import rebuild_registry
        from zephyrex.lib.Logging import logger

        logger.info("SIGHUP received -- draining, then computing extension registry diff")
        app.state.draining = True
        try:
            registry = getattr(app.state, "model_registry", None)
            ext_registry = (
                getattr(registry, "extension_registry", None) if registry else None
            )
            if ext_registry is None:
                logger.warning(
                    "SIGHUP: no extension registry on app.state; exiting anyway"
                )
            else:
                _, diff = rebuild_registry(ext_registry, run_migrations=True)
                logger.info(f"SIGHUP: {diff.summary()}")
        except Exception as e:
            logger.error(f"SIGHUP diff/migration failed: {e}", exc_info=True)

        # Suppress the actual exit when running under pytest so test
        # assertions can verify handler installation without aborting
        # the test runner.
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return

        logger.info(
            f"SIGHUP graceful restart: exiting with code {SIGHUP_RESTART_EXIT_CODE} "
            f"so the supervisor respawns with new extension state"
        )
        # Use os._exit so we drop in-flight requests rather than risking a
        # deadlock during shutdown. The supervisor restarts us immediately
        # so dropped requests cost a single retry, not a multi-second
        # graceful shutdown. Operators that need bounded request drain run
        # blue-green at the LB level instead.
        os._exit(SIGHUP_RESTART_EXIT_CODE)

    def _handle_sigterm(signum, frame):  # pragma: no cover -- signal path
        from zephyrex.lib.Logging import logger

        logger.info("SIGTERM received -- draining")
        app.state.draining = True

    try:
        signal.signal(signal.SIGHUP, _handle_sighup)
        signal.signal(signal.SIGTERM, _handle_sigterm)
    except (ValueError, OSError) as e:
        # ``signal.signal`` only works on the main thread. Workers in some
        # uvicorn configurations call ``build_app`` off-main; in that
        # case we silently skip -- the master process owns the signal.
        logger.debug(f"Could not install SIGHUP handler: {e}")


def build_app(model_registry: ModelRegistry):
    """
    FastAPI application factory function with ModelRegistry.
    Returns a configured FastAPI application instance.

    Args:
        model_registry: ModelRegistry instance with bound models
    """
    from zephyrex.lib.Environment import env
    from zephyrex.lib.Logging import logger

    from zephyrex import get_framework_version

    version = get_framework_version()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Handles startup and shutdown events for each worker"""
        db_mgr = getattr(app.state, "DB", None)
        if db_mgr is None:
            import os

            db_prefix = (
                "test.lifespan_fallback"
                if os.environ.get("PYTEST_CURRENT_TEST")
                else ""
            )
            db_mgr = DatabaseManager(db_prefix)
            db_mgr.init_engine_config()
        db_mgr.init_worker()

        valkey_client = None
        valkey_uri = getattr(app.state, "_valkey_uri", None)
        if valkey_uri:
            try:
                from zephyrex.extensions.database_memory.PRV_Valkey import PRV_Valkey

                _stub = type("_FrameworkInstance", (), {"api_key": valkey_uri})()
                valkey_client = PRV_Valkey.connect(_stub)
                await valkey_client.ping()
                app.state.valkey = valkey_client
                _redacted = (
                    valkey_uri.split("@")[-1] if "@" in valkey_uri else valkey_uri
                )
                logger.info("Valkey connected: %s", _redacted)

                from zephyrex.extensions.database_memory.EXT_DatabaseMemory import (
                    EXT_DatabaseMemory,
                )

                EXT_DatabaseMemory.wire_framework_backends(valkey_client)
            except ImportError:
                logger.warning(
                    "VALKEY_URI is set but 'redis' package is not installed. "
                    "Install with: pip install zephyrex[cache]. "
                    "Falling back to in-memory backends."
                )
                valkey_client = None
            except Exception as exc:
                logger.warning(
                    "Valkey connection failed (%s); falling back to in-memory backends",
                    exc,
                )
                valkey_client = None

        if valkey_client is None:
            workers = int(os.environ.get("UVICORN_WORKERS", "1"))
            if workers > 1:
                logger.warning(
                    "UVICORN_WORKERS=%d but VALKEY_URI is not set — "
                    "rate limiter, event bus, advisory locks, and replay cache "
                    "are per-process in-memory singletons. Set VALKEY_URI to "
                    "enable distributed backends.",
                    workers,
                )

        try:
            yield
        finally:
            if valkey_client is not None:
                try:
                    from zephyrex.extensions.database_memory.PRV_Valkey import (
                        PRV_Valkey,
                    )

                    _stub = type("_FrameworkInstance", (), {"api_key": valkey_uri})()
                    await PRV_Valkey.close(_stub)
                except Exception as exc:
                    logger.debug("Valkey close error: %s", exc)
            await db_mgr.close_worker()

    # H-4 — gate the OpenAPI surface in production. Unauthenticated docs
    # let an attacker enumerate every route, parameter, and admin endpoint
    # without any rate-limited probe. Set EXPOSE_DOCS=true if a deployment
    # genuinely wants public docs.
    from zephyrex.lib.Environment import is_production

    expose_docs = (env("EXPOSE_DOCS") or "").strip().lower() == "true"
    docs_enabled = expose_docs or not is_production()

    app = FastAPI(
        title=env("APP_NAME"),
        version=env("APP_VERSION"),
        description=f"{env('APP_NAME')} is {inflection.a(env('APP_DESCRIPTION'))}. Visit the GitHub repo for more information or to report issues. {env('APP_REPOSITORY')}",
        openapi_url="/openapi.json" if docs_enabled else None,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        lifespan=lifespan,
        openapi_version="3.1.0",
    )
    app.extensions = {}  # type: ignore[attr-defined]

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
    from zephyrex.lib.Environment import is_production
    from zephyrex.lib.InboundSecurity import parse_cors_origins, validate_cors_config

    app_env = "production" if is_production() else "development"
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

    # Item 71b — inbound rate-limit enforcement.
    #
    # The middleware is mounted unconditionally; it is a no-op for routes
    # that have not been stamped with @rate_limit. Route discovery runs
    # below after every router has been mounted onto the app.
    from zephyrex.lib.InboundSecurity import (
        BodySizeLimitMiddleware,
        ETagMiddleware,
        JSONDepthMiddleware,
        PathSanitizationMiddleware,
        ProxyHeaderSanitizationMiddleware,
        RateLimitMiddleware,
        RequestSmugglingMiddleware,
        SecurityHeadersMiddleware,
    )

    # H-4 — strict response-header defaults (CSP, nosniff, frame-deny,
    # referrer-policy, permissions-policy, HSTS in production). M-1 —
    # request-body size cap so multipart / oversized JSON cannot OOM
    # the worker. Mount before any router so a 413 short-circuits before
    # the body is buffered downstream.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestSmugglingMiddleware)
    app.add_middleware(ProxyHeaderSanitizationMiddleware)
    app.add_middleware(PathSanitizationMiddleware)
    app.add_middleware(JSONDepthMiddleware)
    app.add_middleware(ETagMiddleware)

    class DrainingMiddleware:
        """Returns 503 when the app is draining (SIGHUP graceful shutdown)."""

        def __init__(self, asgi_app):
            self.app = asgi_app

        async def __call__(self, scope, receive, send):
            if scope.get("type") == "http" and getattr(app.state, "draining", False):
                body = json.dumps({"detail": "Service unavailable — shutting down"}).encode()
                await send({
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"retry-after", b"5"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                })
                await send({"type": "http.response.body", "body": body})
                return
            await self.app(scope, receive, send)

    app.add_middleware(DrainingMiddleware)

    # Multi-format content negotiation (JSON, TOON, YAML, TOML, XML).
    # Transcodes non-JSON request bodies to JSON for Pydantic/FastAPI and
    # re-serializes JSON responses to the format requested via Accept header
    # or URL suffix. Mounted after security middleware so format transcoding
    # only runs on requests that passed rate-limit and body-size checks.
    from zephyrex.lib.ContentNegotiation import (  # noqa: E402
        ContentNegotiationMiddleware,
    )

    app.add_middleware(ContentNegotiationMiddleware)

    # JWT extraction middleware. RequestContext.user is the canonical actor
    # identity for rate limiting (InboundSecurity), tenancy filters, and
    # downstream BLL authorization. It MUST only be populated from a
    # signature-verified token, otherwise this is a global auth bypass.
    @app.middleware("http")
    async def extract_jwt_context(request: Request, call_next):
        """Extract JWT token data and set request context"""
        clear_request_context()  # Clear any previous context

        # Get authorization header
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                import jwt

                from zephyrex.lib.Environment import env

                token = auth_header.replace("Bearer ", "").strip()
                jwt_secret = env("JWT_SECRET")
                if not jwt_secret:
                    raise jwt.InvalidTokenError("JWT_SECRET unset")
                # Match ``UserManager.verify_token`` exactly: require the
                # full claim set (``exp``, ``nbf``, ``iat``, ``jti``,
                # ``aud``, ``iss``) and validate aud/iss values. Anything
                # weaker here lets a forged or cross-tenant token populate
                # ``RequestContext.user`` for routes that consume it but
                # do not also call ``verify_token`` themselves.
                from zephyrex.logic.BLL_Auth import UserManager

                payload = UserManager._decode_jwt(token)

                model_registry = getattr(request.app.state, "model_registry", None)
                if model_registry is not None:

                    UserManager._enforce_session_not_revoked(payload, model_registry)

                user_info = {
                    "user_id": payload.get("sub"),
                    "email": payload.get("email"),
                    "timezone": payload.get("timezone", "UTC"),
                }
                set_request_user(user_info)
            except Exception:
                # Verification failed — leave context empty; downstream auth
                # dependencies will produce the 401.
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

        # Item 34 — propagate the W3C ``traceparent`` from the inbound
        # request into the contextvar that ``ProviderHTTPClient`` reads.
        # The value is forwarded verbatim on every outbound provider call
        # so traces correlate across the API → BLL → rotation → outbound
        # boundary in OTel-compatible backends. ``baggage`` is treated
        # the same way (forwarded but not parsed). When the header is
        # absent, no trace context is set — this preserves the no-op
        # path for environments that have not configured tracing.
        traceparent_header = request.headers.get("traceparent")
        traceparent_token = None
        if traceparent_header:
            try:
                from zephyrex.lib.ProviderHTTPClient import set_traceparent

                traceparent_token = set_traceparent(traceparent_header)
            except ImportError:
                # ProviderHTTPClient not on path (test isolation, etc.) — no-op.
                traceparent_token = None

        # Item 85 — derive the per-request correlation id. When a
        # ``traceparent`` header is present, parse out the 32-hex
        # trace-id segment so log lines correlate with distributed-
        # tracing backends (W3C format:
        # ``<version>-<trace-id>-<parent-id>-<flags>``). Otherwise mint
        # a fresh ``uuid4().hex`` so every request still has an id.
        # M-9 — accept only strict-W3C lowercase hex; uppercase / mixed
        # case is normalized to lower so downstream parsers stay happy
        # and the value is still safe to interpolate into log lines.
        correlation_id_value: Optional[str] = None
        if traceparent_header:
            parts = traceparent_header.split("-")
            if len(parts) >= 2:
                trace_id = parts[1].lower()
                if _STRICT_TRACE_ID_RE.match(trace_id) and trace_id != "0" * 32:
                    correlation_id_value = trace_id
        if correlation_id_value is None:
            correlation_id_value = mint_correlation_id()
        set_correlation_id(correlation_id_value)

        try:
            response = await call_next(request)
        except DeadlineExceededError as exc:
            logger.debug("Request deadline exceeded: %s (layer=%s)", exc, exc.layer)
            return JSONResponse(
                status_code=504,
                content={"detail": "Request deadline exceeded"},
            )
        finally:
            # Item 34 — restore the prior traceparent contextvar (or
            # clear it) so a request with a header does not leak into
            # the next request's outbound calls.
            if traceparent_token is not None:
                try:
                    from zephyrex.lib.ProviderHTTPClient import _traceparent

                    _traceparent.reset(traceparent_token)
                except (ImportError, ValueError):
                    pass

        # NOTE: A previous debugging block here printed POST response bodies
        # to stdout, including JSON payloads. Removed (Item 73) — that path
        # leaked PII and credentials into production logs and offered no
        # observability that structured logging (Item 85) does not.

        # Item 39 — inject ``Deprecation`` / ``Sunset`` HTTP headers when
        # the matched route's manager declares ``deprecated_in`` /
        # ``sunset_in``. The values are opaque strings forwarded
        # verbatim so clients can surface the contract without the
        # framework imposing a date format.
        try:
            route = request.scope.get("route")
            endpoint = getattr(route, "endpoint", None) if route else None
            manager_class = getattr(endpoint, "_router_mixin_manager", None)
            if manager_class is not None:
                deprecated_in = getattr(manager_class, "deprecated_in", None)
                sunset_in = getattr(manager_class, "sunset_in", None)
                if deprecated_in:
                    response.headers["Deprecation"] = str(deprecated_in)
                if sunset_in:
                    response.headers["Sunset"] = str(sunset_in)
        except Exception:
            # Header injection is best-effort; never break the response.
            pass

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

    from zephyrex.lib.Pydantic2FastAPI import _error_envelope

    @app.exception_handler(json.JSONDecodeError)
    async def json_decode_error_handler(request: Request, exc: json.JSONDecodeError):
        return JSONResponse(
            status_code=400,
            content={"detail": _error_envelope("Invalid JSON syntax in request body", code="json_parse_error")},
        )

    @app.exception_handler(TypeError)
    async def type_error_handler(request: Request, exc: TypeError):
        logger.debug(f"TypeError in request handler: {exc}")
        return JSONResponse(
            status_code=422,
            content={"detail": _error_envelope("Invalid request body format", code="invalid_body")},
        )

    @app.exception_handler(AttributeError)
    async def attribute_error_handler(request: Request, exc: AttributeError):
        logger.debug(f"AttributeError in request handler: {exc}")
        return JSONResponse(
            status_code=422,
            content={"detail": _error_envelope("Invalid request body format", code="invalid_body")},
        )

    from zephyrex.lib.AdvisoryLock import LockTimeoutError

    @app.exception_handler(LockTimeoutError)
    async def lock_timeout_handler(request: Request, exc: LockTimeoutError):
        return JSONResponse(
            status_code=423,
            content={"detail": _error_envelope("Resource is locked", code="locked")},
            headers={"Retry-After": "5"},
        )

    try:
        from zephyrex.extensions.quota.BLL_Quota import QuotaExhaustedError

        @app.exception_handler(QuotaExhaustedError)
        async def quota_exhausted_handler(request: Request, exc: QuotaExhaustedError):
            return JSONResponse(
                status_code=507,
                content={"detail": _error_envelope(
                    "Quota exceeded", code="quota_exceeded",
                    scope=exc.scope, ability=exc.ability, period=exc.period,
                )},
            )
    except ImportError:
        pass

    def make_json_serializable(obj):
        """Recursively convert objects to JSON-serializable format.

        M-6 — secret types (Pydantic ``SecretStr``, framework ``SecretValue``,
        ``CredentialRef``) are explicitly redacted to ``"***"`` BEFORE the
        ``model_dump`` / ``str()`` fallback. Without this, a credential
        carried in an exception detail (or a model returned by a route that
        forgot to scrub it) reaches the client in cleartext.
        """
        # Hard-redact known secret carriers first.
        try:
            from pydantic import SecretBytes, SecretStr

            if isinstance(obj, (SecretStr, SecretBytes)):
                return "***"
        except ImportError:
            pass
        try:
            from zephyrex.lib.Credentials import (
                CredentialRef,
                SecretValue,
            )

            if isinstance(obj, (SecretValue, CredentialRef)):
                return "***"
        except ImportError:
            pass

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

    # Item 85 — generic uncaught-exception handler. Routes the exception
    # to the configured ``ErrorReporter`` (Sentry, Rollbar, or no-op)
    # after logging, so production deployments get a single integration
    # point for error aggregation. The handler always returns a generic
    # 500 — handlers above (HTTPException, RequestValidationError,
    # JSONDecodeError) own the structured-error responses for the
    # framework's typed failure modes.
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        from zephyrex.lib.Logging import get_error_reporter

        logger.error(
            f"Unhandled exception in {request.method} {request.url.path}: {exc}",
            exc_info=True,
        )
        try:
            get_error_reporter().report(
                exc,
                {"path": request.url.path, "method": request.method},
            )
        except Exception as reporter_error:
            logger.warning(
                "ErrorReporter.report raised; swallowing to avoid masking "
                f"the original exception. ({reporter_error})"
            )
        return JSONResponse(
            status_code=500,
            content={"detail": _error_envelope("Internal Server Error", code="internal_error")},
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
            return JSONResponse(status_code=422, content={"detail": _error_envelope("Validation error", code="validation_error")})

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
                from zephyrex import RouterRegistrationError

                raise RouterRegistrationError(
                    f"Critical error loading router for {manager_name}: {e}"
                ) from e

        @app.get("/health", tags=["Health"])
        async def health():
            return {"status": "UP"}

        from zephyrex.endpoints.Operations import create_operations_router

        def _db_health_check() -> bool:
            try:
                from sqlalchemy import text

                with model_registry.DB.manager._get_db_session() as session:
                    session.execute(text("SELECT 1"))
                return True
            except Exception:
                return False

        ops_router = create_operations_router(db_check=_db_health_check)
        app.include_router(ops_router)

        @app.get("/v1", tags=["Authentication"], status_code=204)
        async def verify_jwt(request: Request):
            """Verify JWT token and return 204 if valid, 401 if invalid"""
            from fastapi import Header, HTTPException, Response

            from zephyrex.logic.BLL_Auth import UserManager

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
                from zephyrex.lib.Logging import logger

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

                # Check for API key first (for system entities). Constant-
                # time comparison via the canonical resolver (H-3, H-6) so
                # REST / GraphQL / factory agree on the principal.
                if api_key:
                    from zephyrex.lib.InboundSecurity import (
                        resolve_principal_from_api_key,
                    )

                    principal = resolve_principal_from_api_key(api_key)
                    if principal:
                        context["requester_id"] = principal
                        logger.debug(
                            f"GraphQL context: Authenticated via API key, principal={principal}"
                        )
                        return context

                # Fall back to JWT authentication
                elif auth_header:
                    try:
                        from zephyrex.logic.BLL_Auth import UserManager

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

                # Item 16 — bind a per-request federation response cache and
                # batched-field resolver. The federation extension is
                # optional; degrade silently when it isn't loaded.
                try:
                    from zephyrex.extensions.federation.BLL_Federation_GQL import (
                        BatchedFieldResolver,
                        ResponseCache,
                        bind_batched_resolver,
                        bind_response_cache,
                    )

                    bind_response_cache(ResponseCache())
                    bind_batched_resolver(BatchedFieldResolver())
                except ImportError:
                    pass

                # The requester's credentials hash anchors the cache so two
                # distinct requesters never share cached upstream results.
                cred_hash = api_key or auth_header or ""
                context["federation_credentials_hash"] = cred_hash

                logger.debug(f"GraphQL context: Final context={context}")
                return context

            graphql_app = GraphQLRouter(
                schema=model_registry.gql, context_getter=get_context
            )
            app.include_router(graphql_app, prefix="/graphql")

    # Item 16 — mount the GQL→REST projection routers from federation.
    # Each router exposes one REST route per upstream root field so REST
    # clients can hit a GraphQL upstream without learning GraphQL.
    federation_report = getattr(model_registry, "_federation_report", None)
    if federation_report is not None and federation_report.rest_routers:
        for router in federation_report.rest_routers:
            try:
                app.include_router(router)
            except Exception as exc:
                logger.warning("Failed to mount federation REST router: %s", exc)

    if env("MCP").strip().lower() == "true":
        from zephyrex.lib.MCPBridge import mount_mcp

        mcp_mount = env("MCP_MOUNT_PATH") or "/mcp"
        app.state.mcp = mount_mcp(app, name=env("APP_NAME"), mount_path=mcp_mount)
    valkey_uri = env("VALKEY_URI").strip()
    if valkey_uri:
        _valkey_importable = True
        try:
            import redis.asyncio as _redis_check  # noqa: F401
        except ImportError:
            logger.warning(
                "VALKEY_URI is set but 'redis' package is not installed. "
                "Install with: pip install zephyrex[cache]. "
                "Falling back to in-memory backends."
            )
            _valkey_importable = False

        if _valkey_importable:
            app.state._valkey_uri = valkey_uri

            @app.get("/health/valkey", tags=["Health"])
            async def valkey_health():
                client = getattr(app.state, "valkey", None)
                if client is None:
                    return {"status": "DISABLED"}
                try:
                    info = await client.info("server")
                    await client.ping()
                    return {
                        "status": "UP",
                        "version": info.get("redis_version", "unknown"),
                    }
                except Exception as exc:
                    return JSONResponse(
                        status_code=503,
                        content={"status": "DOWN", "error": str(exc)},
                    )

            @app.middleware("http")
            async def invalidate_response_cache_on_mutation(
                request: Request, call_next
            ):
                response = await call_next(request)
                if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                    try:
                        from zephyrex.lib.ResponseCache import get_response_cache

                        rcache = get_response_cache()
                        if rcache is not None and response.status_code < 400:
                            path_prefix = "/".join(request.url.path.split("/")[:4])
                            await rcache.invalidate_prefix(path_prefix)
                    except Exception:
                        pass
                return response

    app.state.model_registry = model_registry
    app.state.draining = False
    app.state.federation_report = federation_report

    # ------------------------------------------------------------------
    # SIGHUP-driven graceful restart (Item 20).
    #
    # On SIGHUP we compute a registry diff against on-disk state and then
    # exit cleanly with code 75 (EX_TEMPFAIL). The supervisor (systemd /
    # k8s / docker --restart=always) interprets that as restart-me and
    # respawns us with the new code on disk. This is the "graceful exit
    # and respawn" fallback explicitly permitted by the Item 20
    # refinement -- true in-process rebind of routers/hooks/services
    # without a process restart is out of scope, because Pydantic
    # validators, SQLAlchemy mappers, and Strawberry schemas all cache
    # class identity at registration time and cannot be re-bound without
    # a full registry rewrite.
    #
    # Tests skip the actual exit (PYTEST_CURRENT_TEST) and rely on the
    # in-process ``rebuild_registry`` helper directly.
    # ------------------------------------------------------------------
    install_sighup_handler(app)

    # ------------------------------------------------------------------
    # 418 I'm a Teapot — honeypot trap for scanner probes.
    #
    # Routes that no legitimate client would hit but automated scanners
    # always try. Returns 418 instead of 404 so: (a) the scanner knows
    # it's been fingerprinted, (b) real 404s stay meaningful, (c) the
    # audit log can alert on 418s as a scanner-presence signal.
    # ------------------------------------------------------------------
    _HONEYPOT_PATHS = [
        "/wp-admin", "/wp-login.php", "/wp-content",
        "/administrator", "/admin.php", "/phpmyadmin",
        "/.env", "/.git/config", "/.git/HEAD",
        "/config.php", "/info.php", "/phpinfo.php",
        "/server-status", "/server-info",
        "/actuator", "/actuator/health", "/actuator/env",
        "/console", "/debug/vars", "/debug/pprof",
        "/elmah.axd", "/trace.axd",
        "/api/v1/pods", "/api/v1/nodes",
        "/_all_dbs", "/_config",
        "/solr/admin", "/manager/html",
    ]

    async def _honeypot_response(request: Request):
        logger.warning(
            "Honeypot probe: %s %s from %s",
            request.method,
            request.url.path,
            request.client.host if request.client else "unknown",
        )
        return JSONResponse(status_code=418, content={
            "detail": "I'm a teapot",
            "brew": "short and stout",
            "tip_me_over": "pour me out",
            "documentation": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        })

    for _hp_path in _HONEYPOT_PATHS:
        app.add_api_route(
            _hp_path,
            _honeypot_response,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
            include_in_schema=False,
        )

    # Test all models for PydanticUndefinedType before OpenAPI generation
    def test_all_models_for_undefined_types():
        from zephyrex.lib.Logging import logger

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
            from zephyrex.lib.Logging import logger

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
                from zephyrex.lib.Logging import logger

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

    app.openapi = custom_openapi  # type: ignore[method-assign]

    # Item 71b — populate the rate-limit registry from every mounted route
    # whose endpoint carries `@rate_limit(...)` metadata. This runs once at
    # build_app time; subsequent route additions (hot reload, install) must
    # call discover_rate_limited_routes themselves.
    try:
        from zephyrex.lib.InboundSecurity import discover_rate_limited_routes

        registered = discover_rate_limited_routes(app)
        if registered:
            logger.info(f"Item 71 rate-limit: registered {registered} route policies")
    except Exception as exc:
        logger.warning(f"Item 71 rate-limit registry population failed: {exc}")

    return app
