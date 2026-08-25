# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared pytest fixtures for zephyrex and its consumer projects.

Register from a conftest with::

    pytest_plugins = ["zephyrex.testing.fixtures"]

so the framework and every downstream app boot their test server, database
session, and admin/team fixtures from one place (DRY). The framework-only
external-API sandbox fixture and the pytest hooks stay in the framework's
own conftest.
"""

import os
import uuid

import pytest
from faker import Faker
from fastapi.testclient import TestClient

from zephyrex.bootstrap import setup_python_path
from zephyrex.lib.Environment import refresh_settings
from zephyrex.lib.Logging import logger
from zephyrex.pydantic2.sqlalchemy import prepare_test_registry
from zephyrex.testing.factories import (
    add_user_to_team,
    authorize_user,
    create_role,
    create_team,
    create_user,
    make_admin_a,
    make_admin_b,
    make_mod_b,
    make_mod_b_role,
    make_team_a,
    make_team_b,
    make_user_b,
)

CORE_COMPANION_EXTENSIONS = (
    "metadata",
    "auth_lockout",
    "auth_recovery_questions",
    "auth_invitations",
    "auth_session",
    "acl_rbac",
)

_CORE_TEST_EXTENSIONS = ",".join(CORE_COMPANION_EXTENSIONS)


# =============================================================================
# CENTRALIZED TEST DATA GENERATORS (from PR #104)
# =============================================================================

faker = Faker()


class FieldGenerators:
    """Reusable field generators for test data — eliminates per-test Faker boilerplate.

    Usage::

        class TestMyEntity(AbstractBLLTest):
            create_fields = {
                "email": FieldGenerators.email(),
                "name": FieldGenerators.unique_name("Entity"),
            }
    """

    @staticmethod
    def email(prefix: str = "test"):
        return lambda: f"{prefix}_{faker.word()}_{faker.random_int()}@example.com"

    @staticmethod
    def unique_name(entity_type: str = "Test"):
        return lambda: f"{entity_type} {faker.word()} {faker.random_int()}"

    @staticmethod
    def username(prefix: str = "user"):
        return lambda: f"{prefix}_{faker.word()}_{faker.random_int()}"

    @staticmethod
    def uuid_string():
        return lambda: str(uuid.uuid4())

    @staticmethod
    def sentence():
        return lambda: faker.sentence()

    @staticmethod
    def word():
        return lambda: faker.word()

    @staticmethod
    def company():
        return lambda: faker.company()

    @staticmethod
    def first_name():
        return lambda: faker.first_name()

    @staticmethod
    def last_name():
        return lambda: faker.last_name()


# Registry for JWT helpers that mint sessions outside the login flow.
# Populated by the `server` / `mock_server` fixtures below. M-1 made
# `jti` mandatory on every JWT and gated on a live SessionModel row, so
# test helpers that emit ad-hoc tokens need a registry to mint the row.
_TEST_MODEL_REGISTRY = None


def _set_test_model_registry(registry) -> None:
    global _TEST_MODEL_REGISTRY
    _TEST_MODEL_REGISTRY = registry


def get_test_model_registry():
    return _TEST_MODEL_REGISTRY


@pytest.fixture(autouse=True)
def _restore_settings_after_test():
    """Restore critical env vars and the settings singleton after each test.

    Tests that use ``patch.dict(os.environ, ..., clear=True)`` wipe all env
    vars for the duration of the ``with`` block.  If any code path within
    that block materialises a new ``AppSettings`` (directly or via
    ``refresh_settings``), the singleton picks up an auto-generated
    JWT_SECRET that persists after the env is restored — breaking JWT
    verification for later tests on the same xdist worker.

    Force-reset the env vars before refreshing settings to ensure the
    values are correct regardless of monkeypatch/patch.dict restoration
    order.
    """
    yield
    os.environ["JWT_SECRET"] = "test-jwt-secret-32-bytes-or-more-aaaaaa"
    os.environ["JWT_AUDIENCE"] = "test-aud"
    os.environ["JWT_ISSUER"] = "test-iss"
    from zephyrex.lib.Environment import refresh_settings

    refresh_settings()


def _build_test_server(db_prefix: str, extensions: str = _CORE_TEST_EXTENSIONS):
    """Build a FastAPI test app with the given database prefix and extensions.

    Shared bootstrap sequence used by both ``server`` and ``mock_server``
    fixtures so the initialization logic lives in one place.
    """
    prepare_test_registry()
    from zephyrex.lib.Environment import refresh_settings

    refresh_settings()

    logger.debug("Setting up Python path...")
    setup_python_path()

    from zephyrex.app import instance

    return instance(db_prefix=db_prefix, extensions=extensions)


@pytest.fixture(scope="session")
def mock_server():
    """
    Get a server for testing.
    This fixture handles database setup through the normal app initialization.
    Each xdist worker gets its own database to avoid conflicts.

    Note: This fixture is for core system tests. Extension tests should use
    AbstractEXTTest.extension_server for proper isolation.
    """
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
    db_prefix = f"mock.{worker_id}"

    logger.debug(
        f"Worker {worker_id}: Setting up mock server with db_prefix={db_prefix}"
    )

    app = _build_test_server(db_prefix)

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
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
    db_prefix = f"test.{worker_id}"

    logger.debug(f"Worker {worker_id}: Setting up server with db_prefix={db_prefix}")

    app = _build_test_server(db_prefix)
    test_client = TestClient(app)
    _set_test_model_registry(app.state.model_registry)

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
    prepare_test_registry()

    from zephyrex.app import instance

    # Use worker-specific prefix for isolated tests to avoid conflicts across xdist workers
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
    db_prefix = f"test.isolated.{worker_id}"

    app = instance(db_prefix=db_prefix, extensions=_CORE_TEST_EXTENSIONS)
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
        prepare_test_registry()

        from zephyrex.app import instance

        # Use extension name and worker ID for database prefix
        # This creates test.{extension_name}.{worker_id}.database.db instead of random files
        first_extension = extensions.split(",")[0].strip() if extensions else "isolated"
        db_prefix = f"test.{first_extension}.{worker_id}"

        return TestClient(instance(db_prefix=db_prefix, extensions=extensions))

    return _create_server


@pytest.fixture(scope="session")
def admin_a(server):
    """Admin user for team_a"""
    return make_admin_a(server)


@pytest.fixture(scope="session")
def team_a(server, admin_a):
    """Create team_a for testing"""
    return make_team_a(server, admin_a)


@pytest.fixture(scope="session")
def admin_b(server):
    """Admin user for team_b"""
    return make_admin_b(server)


@pytest.fixture(scope="session")
def team_b(server, admin_b):
    """Create team_b for testing"""
    return make_team_b(server, admin_b)


@pytest.fixture(scope="session")
def user_b(server, team_b):
    """Regular user for team_b"""
    return make_user_b(server, team_b)


@pytest.fixture(scope="session")
def mod_b_role(server, admin_a, team_b):
    """Moderator role for team_b"""
    return make_mod_b_role(server, admin_a, team_b)


@pytest.fixture(scope="session")
def mod_b(server, admin_b, team_b, mod_b_role):
    """Moderator user for team_b"""
    return make_mod_b(server, admin_b, team_b, mod_b_role)


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
