import base64
import os
import sys
import time
import uuid
from pathlib import Path
from types import NoneType
from typing import Annotated, Any, Dict, List, Tuple, get_args, get_origin, ForwardRef

import pytest
from faker import Faker
from fastapi.testclient import TestClient
from filelock import FileLock, Timeout
from pydantic import BaseModel as PydanticBaseModel

# IMPORTANT: Set APP_EXTENSIONS BEFORE any application imports
# Don't set globally - let fixtures handle environment isolation
# This allows both core tests (APP_EXTENSIONS="") and extension tests (APP_EXTENSIONS="ext_name") to work independently

# Setup paths correctly - follow Server.py pattern
src_path = Path(__file__).resolve().parent
project_root = src_path.parent


# =============================================================================
# PYTEST-XDIST WORKER COORDINATION
# =============================================================================
# When running with pytest-xdist (-n N), each worker runs in a separate process.
# This can cause race conditions when multiple workers try to:
# 1. Initialize/migrate the database simultaneously
# 2. Create session-scoped fixtures that depend on shared database state
#
# The following hooks ensure proper coordination between workers.
# =============================================================================


# =============================================================================
# EXTERNAL API TEST MARKERS (Item 15)
# =============================================================================
# Two markers govern external-API tests:
#   @pytest.mark.external_api(provider="stripe")
#       Marks a test that requires real (sandbox) credentials. If the
#       credentials are absent, the test is automatically xfailed with a
#       clear reason naming the missing env vars; if they are present, the
#       test runs end-to-end against the sandbox.
#   @pytest.mark.external_smoke
#       Marks a test that deliberately runs without credentials and asserts
#       the framework's no-credentials path surfaces correctly.
#
# See src/extensions/EXT.Test.External.md for the operator-facing contract.
# =============================================================================

# Mapping from provider name to the env vars that, if present, would unblock
# its sandbox tests. Extensions add their providers via
# ``register_external_api_provider`` at import time (typically from the
# extension's own conftest.py). The defaults shipped here cover the
# providers the framework ships with itself.
EXTERNAL_API_PROVIDER_ENV_VARS: Dict[str, List[str]] = {
    # Stripe sandbox — secret key and a webhook signing secret for inbound
    # tests (Item 5). Both must be set for a sandbox test to run.
    "stripe": ["STRIPE_API_KEY", "STRIPE_WEBHOOK_SECRET"],
    # SendGrid sandbox.
    "sendgrid": ["SENDGRID_API_KEY"],
    # Twilio sandbox.
    "twilio": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"],
    # GitHub — for federated tests against a sandbox account.
    "github": ["GITHUB_TOKEN"],
}


def register_external_api_provider(name: str, env_vars: List[str]) -> None:
    """Register a provider's sandbox env-var requirements.

    Extensions that ship their own external-API tests call this from their
    conftest.py (or from a top-level extension fixture file) to extend the
    default mapping above. Idempotent: re-registering a provider replaces
    its env-var list.
    """
    EXTERNAL_API_PROVIDER_ENV_VARS[name] = list(env_vars)


def requires_sandbox_credentials(provider: str) -> Tuple[bool, str]:
    """Return ``(has_creds, reason)`` for a provider's sandbox credentials.

    ``has_creds`` is True iff every env var in the provider's mapping is
    set to a non-empty value. ``reason`` is the xfail/skip message to
    display when ``has_creds`` is False — it names the missing env vars
    so the operator knows what to set.
    """
    env_vars = EXTERNAL_API_PROVIDER_ENV_VARS.get(provider)
    if env_vars is None:
        return (
            False,
            (
                f"unknown external-api provider {provider!r}; register it "
                f"via conftest.register_external_api_provider(name, env_vars)"
            ),
        )
    missing = [v for v in env_vars if not os.environ.get(v)]
    if missing:
        return (
            False,
            (
                f"missing sandbox credentials for {provider!r}: set "
                f"{', '.join(missing)} to enable this test"
            ),
        )
    return True, ""


def pytest_collection_modifyitems(config, items):
    """Auto-xfail external_api tests when sandbox credentials are absent.

    Tests marked ``@pytest.mark.external_api(provider="stripe")`` get an
    automatic ``xfail(strict=False)`` when the provider's env vars are
    missing — they still run but a missing-credential failure becomes an
    xfail-pass rather than a hard fail.

    Tests marked ``@pytest.mark.external_smoke`` are unconditional — they
    deliberately exercise the no-credentials path.
    """
    import pytest as _pytest

    for item in items:
        marker = item.get_closest_marker("external_api")
        if marker is None:
            continue
        provider = (
            marker.kwargs.get("provider")
            or (marker.args[0] if marker.args else None)
        )
        if not provider:
            continue
        has_creds, reason = requires_sandbox_credentials(provider)
        if not has_creds:
            item.add_marker(_pytest.mark.xfail(reason=reason, strict=False))


def pytest_configure(config):
    """
    Called after command line options have been parsed and all plugins are loaded.

    For xdist workers, we use file locking to ensure only one worker initializes
    the database at a time. Other workers wait until initialization is complete.
    """
    # Register the Item 15 external-API markers if the loaded ini hasn't
    # already declared them. Idempotent — pytest tolerates duplicate
    # addinivalue_line calls.
    config.addinivalue_line(
        "markers",
        (
            "external_api(provider): test requires real sandbox credentials "
            "for the named provider; auto-xfailed when credentials are absent"
        ),
    )
    config.addinivalue_line(
        "markers",
        (
            "external_smoke: smoke test that runs without credentials and "
            "asserts the no-credentials configuration-failure path surfaces"
        ),
    )

    # Check if we're running with xdist
    worker_id = os.environ.get("PYTEST_XDIST_WORKER")

    if worker_id is not None:
        # We're running as an xdist worker
        # Use a file lock to coordinate database initialization
        lock_file = src_path / ".pytest_db_init.lock"
        ready_file = src_path / ".pytest_db_ready"

        lock = FileLock(lock_file, timeout=180)  # 3 minute timeout

        try:
            # Try to acquire the lock (non-blocking first check)
            with lock:
                if not ready_file.exists():
                    # We're the first worker - mark that we'll do initialization
                    # The actual initialization happens in fixtures, but we create
                    # the ready file after acquiring the lock to signal other workers
                    pass
                # Even if ready file exists, we continue - initialization happens in fixtures
        except Timeout:
            # Another worker is holding the lock for too long
            # This shouldn't happen normally, but we continue anyway
            pass
    else:
        # We're the main process (controller) or running without xdist
        # Clean up any stale lock/ready files from previous runs
        lock_file = src_path / ".pytest_db_init.lock"
        ready_file = src_path / ".pytest_db_ready"

        for f in [lock_file, ready_file]:
            if f.exists():
                try:
                    f.unlink()
                except OSError:
                    pass


def pytest_sessionfinish(session, exitstatus):
    """
    Called after whole test run finished.
    Clean up coordination files.
    """
    worker_id = os.environ.get("PYTEST_XDIST_WORKER")

    # Only the controller or single-process run should clean up
    if worker_id is None:
        cleanup_files = [
            src_path / ".pytest_db_init.lock",
            src_path / ".pytest_db_ready",
            src_path / ".pytest_mock_db_init.lock",
            src_path / ".pytest_mock_db_ready",
            src_path / ".pytest_server_db_init.lock",
            src_path / ".pytest_server_db_ready",
        ]

        for f in cleanup_files:
            if f.exists():
                try:
                    f.unlink()
                except OSError:
                    pass


# Add project root and src directories to path
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from lib.Environment import env
from lib.Logging import logger
from lib.Pydantic import BaseModel as FrameworkBaseModel

# Clear all registry caches to prevent conflicts during test setup
from lib.Pydantic2SQLAlchemy import clear_registry_cache

clear_registry_cache()
logger.debug("Cleared all registry caches before test setup")
# Import all required functions from Server directly
from app import setup_python_path

# Don't import global session directly - use self.db_manager.get_session() pattern
from logic.BLL_Auth import (
    RoleModel,
    TeamModel,
    UserCredentialManager,
    UserModel,
    UserTeamModel,
)

# No mocker fixture needed - we use real implementations instead of mocking


logger.debug(f"Added to sys.path: {src_path}, {project_root}")

MODEL_BASE_CLASSES: Tuple[type, ...] = tuple(
    {base for base in (FrameworkBaseModel, PydanticBaseModel) if base is not None}
)


def _annotation_includes_model(annotation: Any) -> bool:
    """Return True when the provided annotation references a model type."""
    if annotation is None:
        return False

    if isinstance(annotation, ForwardRef):
        return True

    origin = get_origin(annotation)
    if origin is Annotated:
        annotated_args = get_args(annotation)
        return bool(annotated_args) and _annotation_includes_model(annotated_args[0])

    if origin is None:
        return isinstance(annotation, type) and any(
            issubclass(annotation, model_base) for model_base in MODEL_BASE_CLASSES
        )

    return any(
        _annotation_includes_model(arg)
        for arg in get_args(annotation)
        if arg is not NoneType
    )


def is_relationship_field(model_field: Any) -> bool:
    """Determine whether a Pydantic model field represents a relationship."""
    annotation = getattr(model_field, "annotation", None)
    return _annotation_includes_model(annotation)


def get_field_test_candidates(model_class: Any) -> List[str]:
    """Return scalar field names that qualify for field selection tests."""
    if not hasattr(model_class, "model_fields"):
        return []

    candidates: List[str] = []
    for field_name, model_field in model_class.model_fields.items():
        if field_name.startswith("_"):
            continue
        if is_relationship_field(model_field):
            continue
        candidates.append(field_name)
    return candidates


def pytest_generate_tests(metafunc):
    """Generate test parameters for parameterized tests."""

    # Handle field parameterization for GET field tests
    if "field_name" in metafunc.fixturenames:
        test_class = metafunc.cls

        # Check if it's an EP test with field testing
        if test_class and (
            "test_GET_200_fields" in metafunc.function.__name__
            or "test_GET_200_list_fields" in metafunc.function.__name__
            or "test_POST_201_fields" in metafunc.function.__name__
            or "test_PUT_200_fields" in metafunc.function.__name__
            or "test_GET_200_search_fields" in metafunc.function.__name__
        ):
            # Get the model class to extract fields from
            model_class = None

            # Try to get model class from class_under_test
            if hasattr(test_class, "class_under_test") and test_class.class_under_test:
                model_class = test_class.class_under_test

            # Try to infer from entity_name
            elif hasattr(test_class, "entity_name") and test_class.entity_name:
                try:
                    # Convert entity_name to model class name
                    import stringcase

                    model_name = f"{stringcase.pascalcase(test_class.entity_name)}Model"

                    # Try to import from common BLL modules
                    for module_name in [
                        "logic.BLL_Auth",
                        f"logic.BLL_{stringcase.pascalcase(test_class.entity_name)}",
                    ]:
                        try:
                            module = __import__(module_name, fromlist=[model_name])
                            model_class = getattr(module, model_name)
                            break
                        except (ImportError, AttributeError):
                            continue
                except Exception:
                    pass

            if model_class and hasattr(model_class, "model_fields"):
                # Generate parameter for each field in the model
                field_names = get_field_test_candidates(model_class)

                if field_names:
                    metafunc.parametrize("field_name", field_names)
                else:
                    import warnings

                    warnings.warn(
                        f"{test_class.__name__}: No valid fields found for field testing",
                        UserWarning,
                    )
            else:
                import warnings

                warnings.warn(
                    f"{test_class.__name__}: Model class not found for field testing - set class_under_test or entity_name",
                    UserWarning,
                )

    # Handle search parameterization for both BLL and EP tests
    if (
        "search_field" in metafunc.fixturenames
        and "search_operator" in metafunc.fixturenames
    ):
        # Get the test class
        test_class = metafunc.cls

        # Check if it's a BLL test with generate_search_test_parameters
        if test_class and hasattr(test_class, "generate_search_test_parameters"):
            # BLL tests should have class_under_test
            if (
                not hasattr(test_class, "class_under_test")
                or not test_class.class_under_test
            ):
                import warnings

                warnings.warn(
                    f"{test_class.__name__}: class_under_test not defined - search tests will not be parameterized",
                    UserWarning,
                )
                return

            params = test_class.generate_search_test_parameters()
            if params:
                metafunc.parametrize("search_field,search_operator", params)

        # Check if it's an EP test that needs search parameterization
        elif test_class and "test_POST_200_search" in metafunc.function.__name__:
            # Import here to avoid circular imports
            from logic.AbstractBLLTest import AbstractBLLTest

            # EP tests should have class_under_test set to the model
            if (
                not hasattr(test_class, "class_under_test")
                or not test_class.class_under_test
            ):
                import warnings

                warnings.warn(
                    f"{test_class.__name__}: class_under_test not defined - search tests will not be parameterized",
                    UserWarning,
                )
                return

            # Create a temporary class to use BLL's parameter generation
            class TempBLL(AbstractBLLTest):
                @classmethod
                def get_model_class(cls, entity_name=None):
                    return test_class.class_under_test

            params = TempBLL.generate_search_test_parameters()
            if params:
                metafunc.parametrize("search_field,search_operator", params)

    # Handle includes test parameterization for EP tests
    if (
        "test_GET_200_includes" in metafunc.function.__name__
        or "test_GET_200_list_includes" in metafunc.function.__name__
        or "test_POST_200_search_includes" in metafunc.function.__name__
    ):
        test_class = metafunc.cls
        if test_class and hasattr(test_class, "_get_navigation_properties"):
            # Get navigation properties for the test class
            try:
                # Create a temporary instance to get navigation properties
                temp_instance = test_class()
                navigation_properties = temp_instance._get_navigation_properties()

                if navigation_properties:
                    test_params = navigation_properties
                    param_ids = []
                    for param in test_params:
                        if hasattr(param, "query"):
                            suffix = (
                                "-with-fields"
                                if getattr(param, "combine_with_fields", False)
                                else ""
                            )
                            param_ids.append(f"{param.query}{suffix}")
                        else:
                            param_ids.append(str(param))
                    metafunc.parametrize(
                        "navigation_property", test_params, ids=param_ids
                    )
                else:
                    # Parametrize with None so the test method can skip itself
                    metafunc.parametrize("navigation_property", [None])
            except Exception as e:
                logger.warning(
                    f"Error generating navigation property tests for {test_class.__name__}: {e}"
                )
                # Parametrize with None so the test method can skip itself
                metafunc.parametrize("navigation_property", [None])


# IMPORTANT: Configure test environment BEFORE any imports from the application
# Database setup is now handled automatically by the app instance


@pytest.fixture(scope="session")
def mock_server():
    """
    Get a server for testing.
    This fixture handles database setup through the normal app initialization.
    Each xdist worker gets its own database to avoid conflicts.

    Note: This fixture is for core system tests. Extension tests should use
    AbstractEXTTest.extension_server for proper isolation.
    """
    # Each xdist worker gets its own database to avoid migration conflicts
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
    db_prefix = f"mock.{worker_id}"

    logger.debug(
        f"Worker {worker_id}: Setting up mock server with db_prefix={db_prefix}"
    )

    # Clear all registry caches to prevent conflicts during test setup
    from lib.Pydantic2SQLAlchemy import clear_registry_cache

    clear_registry_cache()
    logger.debug("Cleared all registry caches before server setup")

    # Follow the same initialization process as app.py
    logger.debug("Setting up Python path...")
    setup_python_path()

    # Use the new instance function with worker-specific prefix and no extensions
    from app import instance

    app = instance(db_prefix=db_prefix, extensions="")

    logger.debug(f"Worker {worker_id}: Mock server initialization complete")

    yield TestClient(app)


@pytest.fixture(scope="session")
def server():
    """
    Get a server for testing.
    This fixture handles database setup through the normal app initialization.
    Each xdist worker gets its own database to avoid conflicts.

    Note: This fixture is for core system tests. Extension tests should use
    AbstractEXTTest.extension_server for proper isolation.
    """
    # Each xdist worker gets its own database to avoid migration conflicts
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
    db_prefix = f"test.{worker_id}"

    logger.debug(f"Worker {worker_id}: Setting up server with db_prefix={db_prefix}")

    # Clear all registry caches to prevent conflicts during test setup
    from lib.Pydantic2SQLAlchemy import clear_registry_cache

    clear_registry_cache()
    logger.debug("Cleared all registry caches before server setup")

    # Follow the same initialization process as app.py
    logger.debug("Setting up Python path...")
    setup_python_path()

    # Use the new instance function with worker-specific prefix and no extensions
    from app import instance

    app = instance(db_prefix=db_prefix, extensions="")
    test_client = TestClient(app)

    logger.debug(f"Worker {worker_id}: Server initialization complete")

    yield test_client

    # Cleanup after all tests are done
    try:
        if hasattr(app.state, "DB"):
            db_manager = app.state.model_registry.database_manager
            if hasattr(db_manager, "cleanup_thread"):
                db_manager.cleanup_thread()
            if hasattr(db_manager, "dispose_all"):
                db_manager.dispose_all()
    except Exception as e:
        logger.debug(f"Error cleaning up server database manager: {e}")


@pytest.fixture(scope="session")
def model_registry(server):
    """Get the isolated model registry from the server for testing."""
    if not hasattr(server.app.state, "model_registry"):
        raise RuntimeError(
            "No isolated model registry found on server.app.state. Tests must use the server fixture."
        )
    return server.app.state.model_registry


@pytest.fixture(scope="function")
def db(model_registry):
    """Get a database session for testing with automatic cleanup"""
    # Use context manager for automatic session cleanup
    with model_registry.database_manager._get_db_session() as session:
        yield session
    # Context manager handles cleanup automatically


@pytest.fixture(scope="function")
def isolated_server():
    """
    Create an isolated server for individual test functions.
    Each test gets a completely fresh environment with its own database and model registry.
    Use this for tests that need complete isolation from other tests.

    Note: Uses test.isolated.{worker_id}.database.db to avoid database conflicts across xdist workers.
    """
    # Clear all registry caches to prevent conflicts
    from lib.Pydantic2SQLAlchemy import clear_registry_cache

    clear_registry_cache()
    logger.debug("Cleared registry caches for isolated server")

    from app import instance

    # Use worker-specific prefix for isolated tests to avoid conflicts across xdist workers
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
    db_prefix = f"test.isolated.{worker_id}"

    app = instance(db_prefix=db_prefix, extensions="")
    test_client = TestClient(app)

    yield test_client

    # Cleanup after test is done
    try:
        if hasattr(app.state, "DB"):
            db_manager = app.state.model_registry.database_manager
            if hasattr(db_manager, "cleanup_thread"):
                db_manager.cleanup_thread()
            if hasattr(db_manager, "dispose_all"):
                db_manager.dispose_all()
    except Exception as e:
        logger.debug(f"Error cleaning up isolated server database manager: {e}")


@pytest.fixture(scope="function")
def isolated_extension_server():
    """
    Create an isolated server for extension testing.
    This fixture allows tests to specify which extensions to load.

    Usage:
        def test_with_payment_extension(isolated_extension_server):
            server = isolated_extension_server("payment")
            # Test with only payment extension loaded

    Note: Uses test.{extension_name}.{worker_id}.database.db naming convention
    to avoid conflicts across xdist workers.
    """
    # Get worker ID for database isolation
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")

    def _create_server(extensions: str = ""):
        # Clear all registry caches to prevent conflicts
        from lib.Pydantic2SQLAlchemy import clear_registry_cache

        clear_registry_cache()
        logger.debug(
            f"Cleared registry caches for extension server with extensions: {extensions}"
        )

        from app import instance

        # Use extension name and worker ID for database prefix
        # This creates test.{extension_name}.{worker_id}.database.db instead of random files
        first_extension = extensions.split(",")[0].strip() if extensions else "isolated"
        db_prefix = f"test.{first_extension}.{worker_id}"

        return TestClient(instance(db_prefix=db_prefix, extensions=extensions))

    return _create_server


@pytest.fixture(scope="session")
def sandbox_credentials_for():
    """Return a callable ``(provider) -> Dict[str, str]`` mapping env vars to values.

    Item 15: tests that exercise real upstream calls run against sandbox or
    test-mode credentials. This fixture returns the env-var dict for the
    requested provider — empty when the credentials are absent, populated
    when they are set in the environment. Pair with the ``external_api``
    marker (which auto-xfails when creds are missing) so the test body can
    use the values directly without a presence check.

    Usage::

        @pytest.mark.external_api(provider="stripe")
        def test_real_stripe_call(sandbox_credentials_for):
            creds = sandbox_credentials_for("stripe")
            # creds is {"STRIPE_API_KEY": "sk_test_...", ...}
    """

    def _get(provider: str) -> Dict[str, str]:
        env_vars = EXTERNAL_API_PROVIDER_ENV_VARS.get(provider, [])
        return {v: os.environ.get(v, "") for v in env_vars}

    return _get


def generate_test_email(prefix="test"):
    """Generate a unique test email using Faker"""
    return f"{prefix}_{uuid.uuid4().hex[:8]}@example.com"


class UserWithJWT(UserModel):
    jwt: str


def create_user(
    server,
    email=None,
    password="testpassword",
    first_name="Test",
    last_name="User",
):
    """Helper function to create a test user and their credentials"""
    if email is None:
        email = generate_test_email()

    # Get the model registry from the server app state
    model_registry = getattr(server.app.state, "model_registry", None)
    if model_registry is None:
        raise RuntimeError("model_registry not found in server app state")

    # Get the SQLAlchemy model using the new .DB(declarative_base) method
    User = UserModel.DB(model_registry.DB.manager.Base)
    existing_users = User.list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        filters=[User.email == email],
        return_type="dto",
        override_dto=UserModel,
    )

    if existing_users:
        user = existing_users[0]
        if not isinstance(user, UserModel):
            user = UserModel(**user)
    else:
        user = User.create(
            requester_id=env("SYSTEM_ID"),
            model_registry=model_registry,
            return_type="dto",
            override_dto=UserModel,
            email=email,
            username=email.split("@")[0],
            first_name=first_name,
            last_name=last_name,
            display_name=f"{first_name} {last_name} Display",
        )
    if password:
        with UserCredentialManager(
            requester_id=user.id,
            model_registry=model_registry,
        ) as credential_manager:
            credential_manager.create(user_id=user.id, password=password)

    if hasattr(user, "model_dump"):
        user_dict = user.model_dump()
    elif isinstance(user, dict):
        user_dict = user
    else:
        user_dict = {
            field: getattr(user, field, None) for field in UserModel.model_fields.keys()
        }

    return UserWithJWT(**user_dict, jwt=authorize_user(server, user.email))


def authorize_user(server, email: str, password="testpassword"):
    credentials = f"{email}:{password}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    response = server.post(
        "/v1/user/authorize", headers={"Authorization": f"Basic {encoded_credentials}"}
    )
    assert "token" in response.json(), "JWT token missing from authorization response."
    return response.json()["token"]


def create_team(server, user_id, name="Test Team", parent_id=None):
    """Helper function to create a test team"""
    faker = Faker()

    # Get the model registry from the server app state
    model_registry = getattr(server.app.state, "model_registry", None)
    if model_registry is None:
        raise RuntimeError("model_registry not found in server app state")

    # Get the SQLAlchemy model using the new .DB(declarative_base) method
    Team = TeamModel.DB(model_registry.DB.manager.Base)
    team = Team.create(
        requester_id=user_id,
        model_registry=model_registry,
        return_type="dto",
        override_dto=TeamModel,
        name=name,
        description=faker.catch_phrase(),
        encryption_key=faker.uuid4(),
        created_by_user_id=user_id,
        parent_id=parent_id,
    )
    add_user_to_team(
        server,
        user_id,
        team.id,
        env("ADMIN_ROLE_ID"),
    )
    return team


def create_role(
    server,
    user_id,
    team_id,
    name="mod",
    friendly_name="Moderator",
    parent_id=env("USER_ROLE_ID"),
):
    """Helper function to create a custom role"""
    # Get the model registry from the server app state
    model_registry = getattr(server.app.state, "model_registry", None)
    if model_registry is None:
        raise RuntimeError("model_registry not found in server app state")

    # Get the SQLAlchemy model using the new .DB(declarative_base) method
    Role = RoleModel.DB(model_registry.DB.manager.Base)
    return Role.create(
        requester_id=user_id,
        model_registry=model_registry,
        return_type="dto",
        override_dto=RoleModel,
        name=name,
        friendly_name=friendly_name,
        parent_id=parent_id,
        team_id=team_id,
    )


def add_user_to_team(server, user_id, team_id, role_id, requester_id=env("SYSTEM_ID")):
    """Add a user to a team with the specified role"""
    # Get the model registry from the server app state
    model_registry = getattr(server.app.state, "model_registry", None)
    if model_registry is None:
        raise RuntimeError("model_registry not found in server app state")

    # Get the SQLAlchemy model using the new .DB(declarative_base) method
    UserTeam = UserTeamModel.DB(model_registry.DB.manager.Base)
    # Check if user is already a member of this team
    existing_membership = UserTeam.list(
        requester_id=requester_id,
        model_registry=model_registry,
        user_id=user_id,
        team_id=team_id,
    )

    if existing_membership:
        # Update existing membership instead of creating a duplicate
        from logic.BLL_Auth import UserTeamManager

        user_team_manager = UserTeamManager(
            requester_id=requester_id,
            model_registry=model_registry,
        )
        return user_team_manager.update(
            id=existing_membership[0].id,
            role_id=role_id,
            enabled=True,
        )
    else:
        # Create new team membership
        return UserTeam.create(
            requester_id=user_id,
            model_registry=model_registry,
            return_type="dto",
            override_dto=UserTeamModel,
            user_id=user_id,
            team_id=team_id,
            role_id=role_id,
        )


# Test utility functions for ModelRegistry testing
def bind_test_models(registry, *models):
    """Helper function to bind test models to a registry."""
    for model in models:
        registry.bind(model)


def create_test_extension_server(extension_names):
    """Helper function to create a test server with specific extensions.

    Note: Uses test.{extension_name}.database.db naming convention.
    """
    from app import instance

    extensions = (
        ",".join(extension_names)
        if isinstance(extension_names, list)
        else extension_names
    )

    # Use first extension name for database prefix
    # This creates test.{extension_name}.database.db instead of random files
    first_extension = (
        extension_names[0]
        if isinstance(extension_names, list)
        else extension_names.split(",")[0]
    ).strip()
    db_prefix = f"test.{first_extension}"

    return TestClient(instance(db_prefix=db_prefix, extensions=extensions))


@pytest.fixture(scope="session")
def admin_a(server):
    """Admin user for team_a"""
    return create_user(
        server,
        email=generate_test_email("admin_a"),
        last_name="AdminA",
    )


@pytest.fixture(scope="session")
def team_a(server, admin_a):
    """Create team_a for testing"""
    return create_team(server, admin_a.id, name="Team A")


@pytest.fixture(scope="session")
def admin_b(server):
    """Admin user for team_b"""
    return create_user(
        server,
        email=generate_test_email("admin_b"),
        last_name="AdminB",
    )


@pytest.fixture(scope="session")
def team_b(server, admin_b):
    """Create team_b for testing"""
    return create_team(server, admin_b.id, name="Team B")


@pytest.fixture(scope="session")
def user_b(server, team_b):
    """Regular user for team_b"""
    user = create_user(server, email=generate_test_email("user_b"), last_name="UserB")
    add_user_to_team(server, user.id, team_b.id, env("USER_ROLE_ID"))
    return user


@pytest.fixture(scope="session")
def mod_b_role(server, admin_a, team_b):
    """Moderator role for team_b"""
    return create_role(
        server,
        admin_a.id,
        team_b.id,
        name="mod_b",
        friendly_name="Moderator B",
        parent_id=env("USER_ROLE_ID"),
    )


@pytest.fixture(scope="session")
def mod_b(server, admin_b, team_b, mod_b_role):
    """Moderator user for team_b"""
    user = create_user(server, email=generate_test_email("mod_b"), last_name="ModB")
    add_user_to_team(server, user.id, team_b.id, mod_b_role.id, requester_id=admin_b.id)
    return user


@pytest.fixture(scope="session")
def team_p(server):
    """Create parent team_p for testing"""
    return create_team(server, env("SYSTEM_ID"), name="Team Parent")


@pytest.fixture(scope="session")
def admin_p(server, team_p):
    """Admin user for parent team_p"""
    user = create_user(
        server,
        email=generate_test_email("admin_p"),
        first_name="Admin",
        last_name="P",
    )
    add_user_to_team(server, user.id, team_p.id, env("ADMIN_ROLE_ID"))
    return user


@pytest.fixture(scope="session")
def mod_p_role(server, admin_p, team_p):
    """Create team-scoped moderator role for team_p"""
    return create_role(
        server,
        admin_p.id,
        team_p.id,
        name="moderator_p",
        friendly_name="Parent Team Moderator",
        parent_id=env("USER_ROLE_ID"),
    )


@pytest.fixture(scope="session")
def mod_p(server, admin_p, team_p, mod_p_role):
    """Moderator user for parent team_p"""
    user = create_user(
        server,
        email=generate_test_email("mod_p"),
        first_name="Mod",
        last_name="P",
    )
    add_user_to_team(server, user.id, team_p.id, mod_p_role.id, requester_id=admin_p.id)
    return user


@pytest.fixture(scope="session")
def user_p(server, team_p, admin_p):
    """Regular user for parent team_p"""
    user = create_user(
        server,
        email=generate_test_email("user_p"),
        first_name="User",
        last_name="P",
    )
    add_user_to_team(
        server, user.id, team_p.id, env("USER_ROLE_ID"), requester_id=admin_p.id
    )
    return user


@pytest.fixture(scope="session")
def team_c(server, team_p):
    """Create child team_c that belongs to parent team_p"""
    return create_team(server, env("SYSTEM_ID"), name="Team Child", parent_id=team_p.id)


@pytest.fixture(scope="session")
def admin_c(server, team_c):
    """Admin user for child team_c"""
    user = create_user(
        server,
        email=generate_test_email("admin_c"),
        first_name="Admin",
        last_name="C",
    )
    add_user_to_team(server, user.id, team_c.id, env("ADMIN_ROLE_ID"))
    return user


@pytest.fixture(scope="session")
def mod_c_role(server, admin_p, team_c):
    """Create team-scoped moderator role for team_c"""
    return create_role(
        server,
        admin_p.id,
        team_c.id,
        name="moderator_c",
        friendly_name="Child Team Moderator",
        parent_id=env("USER_ROLE_ID"),
    )


@pytest.fixture(scope="session")
def mod_c(server, admin_p, team_c, mod_c_role):
    """Moderator user for child team_c"""
    user = create_user(
        server,
        email=generate_test_email("mod_c"),
        first_name="Mod",
        last_name="C",
    )
    add_user_to_team(server, user.id, team_c.id, mod_c_role.id, requester_id=admin_p.id)
    return user


@pytest.fixture(scope="session")
def user_c(server, team_c, admin_p):
    """Regular user for child team_c"""
    user = create_user(
        server,
        email=generate_test_email("user_c"),
        first_name="User",
        last_name="C",
    )
    add_user_to_team(
        server, user.id, team_c.id, env("USER_ROLE_ID"), requester_id=admin_p.id
    )
    return user
