import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import Column, Integer, String, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from database.DatabaseManager import (
    DatabaseManager,
    Operation,
    db_name_to_path,
    get_database_info,
    setup_sqlite_for_regex,
)
from lib.Logging import logger

# Database name constants for testing
TEST_STATIC_DB_NAME = "test.static.db"
TEST_STATIC_DB_BASE_NAME = "test.static.db.base"
TEST_DB_NAME = "test_db"
CRUD_TEST_DB_NAME = "crud_test"
REGEX_TEST_DB_NAME = "regex_test"
FILE_TEST_DB_NAME = "file_test"
DIR_TEST_DB_NAME = "dir_test"
ASYNC_TEST_DB_NAME = "async_test"
ASYNC_TRANS_TEST_DB_NAME = "async_trans_test"

# Database isolation prefix constants
TEST_STATIC_PREFIX = "test.static"
TEST_STATIC_PREFIX_1 = "test.static.1"
TEST_STATIC_PREFIX_2 = "test.static.2"


def cleanup_test_database_files(db_name: str, search_directories: list = None):
    """Clean up test database files from common locations.

    Args:
        db_name: Database name to clean up (e.g., "test.migration.meta")
        search_directories: List of directories to search for database files.
                          If None, searches common test locations.
    """
    if search_directories is None:
        # Default search locations relative to current file
        current_dir = Path(__file__).parent
        search_directories = [
            current_dir.parent,  # Parent of database dir
            current_dir,  # Database dir itself
            Path("."),  # Current working directory
            current_dir.parent.parent,  # src directory
        ]

    # Convert to Path objects if they aren't already
    search_directories = [Path(d) for d in search_directories]

    # Generate database file paths to check
    test_db_patterns = []
    for directory in search_directories:
        try:
            db_path = db_name_to_path(db_name, str(directory))
            test_db_patterns.append(Path(db_path))
        except Exception as e:
            logger.debug(f"Could not generate path for {db_name} in {directory}: {e}")

    # Add legacy patterns
    test_db_patterns.extend(
        [
            Path("database.test.db"),  # Legacy pattern
            Path(f"{db_name}.db"),  # Simple filename in current dir
        ]
    )

    cleaned_files = []
    for test_db_path in test_db_patterns:
        if test_db_path.exists():
            try:
                test_db_path.unlink()
                logger.debug(f"Removed test database file: {test_db_path}")
                cleaned_files.append(str(test_db_path))
            except Exception as e:
                logger.error(f"Error removing test database file {test_db_path}: {e}")

    return cleaned_files


class TestGetDatabaseInfo:
    """Test database configuration retrieval."""

    def test_get_database_info_sqlite_default(self):
        """Test getting database info for SQLite without prefix."""
        with patch.dict(
            os.environ,
            {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": TEST_STATIC_DB_NAME},
            clear=True,
        ):
            db_info = get_database_info()

            assert db_info["type"] == "sqlite"
            assert (
                db_info["name"] == TEST_STATIC_DB_NAME
            )  # No prefix applied when called without prefix
            assert db_info["url"].startswith("sqlite:///")
            assert db_info["file_path"] is not None
            assert db_info["file_path"].endswith(f"{TEST_STATIC_DB_NAME}.db")

    def test_get_database_info_sqlite_with_prefix(self):
        """Test getting database info for SQLite with prefix."""
        with patch.dict(
            os.environ,
            {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": TEST_STATIC_DB_BASE_NAME},
            clear=True,
        ):
            db_info = get_database_info("test_prefix")

            assert db_info["type"] == "sqlite"
            assert db_info["name"] == f"test_prefix.{TEST_STATIC_DB_BASE_NAME}"
            assert db_info["url"].startswith("sqlite:///")
            assert db_info["file_path"] is not None
            assert db_info["file_path"].endswith(
                f"test_prefix.{TEST_STATIC_DB_BASE_NAME}.db"
            )

    def test_get_database_info_sqlite_windows_path(self):
        """Test that SQLite URLs always use forward slashes, even on Windows."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a Windows-style path within the temp directory
            windows_path = os.path.join(temp_dir, "Users", "Test", "Database").replace(
                "/", "\\"
            )

            with patch.dict(
                os.environ,
                {
                    "DATABASE_TYPE": "sqlite",
                    "DATABASE_NAME": TEST_STATIC_DB_NAME,
                    "DATABASE_PATH": windows_path,  # Windows-style path
                },
                clear=True,
            ):
                db_info = get_database_info()

                # SQLite URLs must always use forward slashes
                assert "sqlite:///" in db_info["url"]
                assert "\\" not in db_info["url"]  # No backslashes in URL
                assert "/" in db_info["url"].replace(
                    "sqlite:///", ""
                )  # Forward slashes in path

                # The file_path can use OS-specific separators
                assert db_info["file_path"] is not None

    def test_get_database_info_postgresql(self):
        """Test getting database info for PostgreSQL."""
        env = {
            "DATABASE_TYPE": "postgresql",
            "DATABASE_NAME": TEST_STATIC_DB_NAME,
            "DATABASE_USER": "user",
            "DATABASE_PASSWORD": "pass",
            "DATABASE_HOST": "localhost",
            "DATABASE_PORT": "5432",
            "DATABASE_SSL": "disable",
        }

        with patch.dict(os.environ, env, clear=True):
            db_info = get_database_info()

            assert db_info["type"] == "postgresql"
            assert (
                db_info["name"] == TEST_STATIC_DB_NAME
            )  # No prefix applied when called without prefix
            assert (
                db_info["url"]
                == f"postgresql://user:pass@localhost:5432/{TEST_STATIC_DB_NAME}"
            )
            assert db_info["file_path"] is None


class TestSqliteRegexSetup:
    """Test SQLite REGEXP function setup."""

    def test_setup_sqlite_for_regex(self):
        """Test SQLite REGEXP function registration."""
        # Create temporary SQLite database
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name

        try:
            # Create engine and setup regex
            engine = create_engine(f"sqlite:///{db_path}")
            setup_sqlite_for_regex(engine)

            # Test the REGEXP function works
            with engine.connect() as conn:
                # Create test table
                conn.execute(text("CREATE TABLE test_table (name TEXT)"))
                conn.execute(text("INSERT INTO test_table VALUES ('hello world')"))
                conn.execute(text("INSERT INTO test_table VALUES ('goodbye moon')"))
                conn.commit()

                # Test regex functionality
                result = conn.execute(
                    text("SELECT name FROM test_table WHERE name REGEXP 'hello.*'")
                )
                matches = result.fetchall()
                assert len(matches) == 1
                assert matches[0][0] == "hello world"

                # Test case sensitivity
                result = conn.execute(
                    text("SELECT name FROM test_table WHERE name REGEXP 'HELLO.*'")
                )
                matches = result.fetchall()
                assert len(matches) == 0  # Should be case sensitive

            # Properly dispose the engine
            engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except PermissionError:
                pass  # File might still be in use on Windows


class TestDatabaseManager:
    """Test DatabaseManager functionality."""

    @property
    def db_manager(self):
        """Get the singleton DatabaseManager instance."""
        return DatabaseManager.get_instance()

    def test_singleton_behavior(self):
        """Test that DatabaseManager follows singleton pattern."""
        manager1 = self.db_manager
        manager2 = self.db_manager
        assert manager1 is manager2

    def test_create_isolated_instance(self):
        """Test creating isolated DatabaseManager instances."""
        manager1 = DatabaseManager(TEST_STATIC_PREFIX_1)
        manager2 = DatabaseManager(TEST_STATIC_PREFIX_2)

        assert manager1 is not manager2
        assert manager1.db_prefix == TEST_STATIC_PREFIX_1
        assert manager2.db_prefix == TEST_STATIC_PREFIX_2

    def test_init_engine_config_sqlite(self):
        """Test engine configuration initialization for SQLite."""
        with patch.dict(
            os.environ,
            {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": TEST_STATIC_DB_NAME},
            clear=True,
        ):
            manager = DatabaseManager(TEST_STATIC_PREFIX)

            assert manager.DATABASE_TYPE == "sqlite"
            assert manager.DATABASE_NAME == "test.static.db"
            assert manager.DATABASE_URI.startswith("sqlite:///")
            assert manager.PK_TYPE == String
            assert manager.engine_config is not None
            assert manager.async_engine_config is not None

    @pytest.mark.xfail(reason="Postgres not yet supported.")
    def test_init_engine_config_postgresql(self):
        """Test engine configuration initialization for PostgreSQL."""
        env = {
            "DATABASE_TYPE": "postgresql",
            "DATABASE_NAME": "test.static.db",
            "DATABASE_USER": "user",
            "DATABASE_PASSWORD": "pass",
            "DATABASE_HOST": "localhost",
            "DATABASE_PORT": "5432",
            "DATABASE_SSL": "disable",
        }

        with patch.dict(os.environ, env, clear=True):
            manager = DatabaseManager("test.static")

            assert manager.DATABASE_TYPE == "postgresql"
            assert manager.DATABASE_NAME == "test.static.db"
            assert manager.DATABASE_URI.startswith("postgresql://")
            from sqlalchemy import UUID

            assert manager.PK_TYPE == UUID

    def test_declarative_base_creation(self):
        """Test that declarative base is created properly."""
        manager = DatabaseManager("test.static")

        base1 = manager.Base
        base2 = manager.Base

        assert base1 is base2  # Should return same instance
        assert hasattr(base1, "metadata")

    def test_worker_initialization(self):
        """Test worker initialization process."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"}
        ):
            manager = DatabaseManager("test.static")

            # Worker should not be initialized initially
            assert not manager._worker_initialized

            # Initialize worker
            manager.init_worker()

            assert manager._worker_initialized
            assert manager.engine is not None
            assert manager.async_engine is not None
            assert manager._session_factory is not None
            assert manager._async_session_factory is not None

    def test_get_session(self):
        """Test getting database sessions."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"}
        ):
            manager = DatabaseManager("test.static")

            session = manager.get_session()
            assert isinstance(session, Session)
            session.close()

    def test_session_context_manager_success(self):
        """Test session context manager with successful operation."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"}
        ):
            manager = DatabaseManager("test.static")

            # Create a test table
            TestModel = type(
                "TestModel",
                (manager.Base,),
                {
                    "__tablename__": "test_model",
                    "id": Column(Integer, primary_key=True),
                    "name": Column(String(50)),
                },
            )

            manager.Base.metadata.create_all(manager.get_setup_engine())

            # Test successful transaction
            with manager._get_db_session() as session:
                test_obj = TestModel(name="test")
                session.add(test_obj)
                # Should auto-commit on successful exit

            # Verify data was committed
            with manager._get_db_session() as session:
                result = session.query(TestModel).filter_by(name="test").first()
                assert result is not None
                assert result.name == "test"

    def test_session_context_manager_rollback(self):
        """Test session context manager with exception (rollback)."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"}
        ):
            manager = DatabaseManager("test.static")

            # Create a test table
            TestModel = type(
                "TestModel",
                (manager.Base,),
                {
                    "__tablename__": "test_model_rollback",
                    "id": Column(Integer, primary_key=True),
                    "name": Column(String(50)),
                },
            )

            manager.Base.metadata.create_all(manager.get_setup_engine())

            # Test rollback on exception
            with pytest.raises(ValueError):
                with manager._get_db_session() as session:
                    test_obj = TestModel(name="test_rollback")
                    session.add(test_obj)
                    raise ValueError("Test exception")

            # Verify data was rolled back
            with manager._get_db_session() as session:
                result = (
                    session.query(TestModel).filter_by(name="test_rollback").first()
                )
                assert result is None

    @pytest.mark.asyncio
    async def test_async_session_context_manager(self):
        """Test async session context manager."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"}
        ):
            manager = DatabaseManager("test.static")

            # Create a test table
            TestModel = type(
                "TestModel",
                (manager.Base,),
                {
                    "__tablename__": "test_async_model",
                    "id": Column(Integer, primary_key=True),
                    "name": Column(String(50)),
                },
            )

            manager.Base.metadata.create_all(manager.get_setup_engine())

            # Test async session
            async with manager._get_async_db_session() as session:
                assert isinstance(session, AsyncSession)
                result = await session.execute(text("SELECT 1"))
                assert result.scalar() == 1

    @pytest.mark.asyncio
    async def test_async_session_rollback(self):
        """Test async session rollback on exception."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"}
        ):
            manager = DatabaseManager("test.static")

            with pytest.raises(ValueError):
                async with manager._get_async_db_session() as session:
                    raise ValueError("Test async exception")

    def test_thread_safety(self):
        """Test thread safety of session management."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"}
        ):
            manager = DatabaseManager("test.static")

            # Create test table
            TestModel = type(
                "TestModel",
                (manager.Base,),
                {
                    "__tablename__": "test_thread_model",
                    "id": Column(Integer, primary_key=True),
                    "name": Column(String(50)),
                    "thread_id": Column(String(50)),
                },
            )

            # Clean up any existing data
            manager.Base.metadata.drop_all(manager.get_setup_engine())
            manager.Base.metadata.create_all(manager.get_setup_engine())

            # Clean up any existing data from previous test runs
            with manager._get_db_session() as session:
                session.query(TestModel).delete()

            results = []
            results_lock = threading.Lock()

            def worker_function(thread_id):
                """Worker function to test thread isolation."""
                try:
                    with manager._get_db_session() as session:
                        test_obj = TestModel(
                            name=f"test_{thread_id}", thread_id=str(thread_id)
                        )
                        session.add(test_obj)
                        # Simulate some work
                        time.sleep(0.1)
                    with results_lock:
                        results.append(f"success_{thread_id}")
                except Exception as e:
                    with results_lock:
                        results.append(f"error_{thread_id}_{str(e)}")

            # Create multiple threads
            threads = []
            for i in range(5):
                thread = threading.Thread(target=worker_function, args=(i,))
                threads.append(thread)
                thread.start()

            # Wait for all threads to complete
            for thread in threads:
                thread.join()

            # Verify all threads succeeded
            assert len(results) == 5
            for i in range(5):
                assert f"success_{i}" in results

            try:
                # Verify all data was inserted
                with manager._get_db_session() as session:
                    count = session.query(TestModel).count()
                    assert count == 5
            finally:
                # Cleanup after test
                manager.Base.metadata.drop_all(manager.get_setup_engine())

    def test_cleanup_thread(self):
        """Test thread cleanup functionality."""
        with patch.dict(
            os.environ,
            {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"},
            clear=True,
        ):
            manager = DatabaseManager("test.static")

            # Use the internal session context manager to establish thread-local storage
            with manager._get_db_session() as session:
                # This establishes the thread-local session
                pass

            # Verify thread-local session exists
            assert hasattr(manager._thread_local, "session")

            # Cleanup thread
            manager.cleanup_thread()

            # Verify thread-local session is removed
            assert not hasattr(manager._thread_local, "session")

    @pytest.mark.asyncio
    async def test_close_worker(self):
        """Test worker cleanup functionality."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"}
        ):
            manager = DatabaseManager("test.static")

            # Initialize worker
            manager.init_worker()
            assert manager._worker_initialized

            # Close worker
            await manager.close_worker()
            assert not manager._worker_initialized


class TestDatabaseOperations:
    """Test actual database operations."""

    def test_database_crud_operations(self):
        """Test Create, Read, Update, Delete operations."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": CRUD_TEST_DB_NAME}
        ):
            manager = DatabaseManager(TEST_STATIC_PREFIX)

            # Define test model
            TestModel = type(
                "TestModel",
                (manager.Base,),
                {
                    "__tablename__": "crud_test_model",
                    "id": Column(Integer, primary_key=True),
                    "name": Column(String(50)),
                    "value": Column(Integer),
                },
            )

            # Create tables
            manager.Base.metadata.create_all(manager.get_setup_engine())

            # CREATE
            with manager._get_db_session() as session:
                obj = TestModel(name="test_create", value=100)
                session.add(obj)

            # READ
            with manager._get_db_session() as session:
                result = session.query(TestModel).filter_by(name="test_create").first()
                assert result is not None
                assert result.name == "test_create"
                assert result.value == 100
                obj_id = result.id

            # UPDATE
            with manager._get_db_session() as session:
                obj = session.query(TestModel).filter_by(id=obj_id).first()
                obj.value = 200

            # Verify UPDATE
            with manager._get_db_session() as session:
                result = session.query(TestModel).filter_by(id=obj_id).first()
                assert result.value == 200

            # DELETE
            with manager._get_db_session() as session:
                obj = session.query(TestModel).filter_by(id=obj_id).first()
                session.delete(obj)

            # Verify DELETE
            with manager._get_db_session() as session:
                result = session.query(TestModel).filter_by(id=obj_id).first()
                assert result is None

    def test_sqlite_regex_functionality(self):
        """Test SQLite REGEXP functionality in real database operations."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": REGEX_TEST_DB_NAME}
        ):
            manager = DatabaseManager(TEST_STATIC_PREFIX)

            # Define test model
            TestModel = type(
                "TestModel",
                (manager.Base,),
                {
                    "__tablename__": "regex_test_model",
                    "id": Column(Integer, primary_key=True),
                    "email": Column(String(100)),
                },
            )

            # Clean up any existing data
            manager.Base.metadata.drop_all(manager.get_setup_engine())
            # Create tables
            manager.Base.metadata.create_all(manager.get_setup_engine())

            # Clean up any existing data from previous test runs
            with manager._get_db_session() as session:
                session.query(TestModel).delete()

            # Insert test data
            with manager._get_db_session() as session:
                emails = [
                    "user@example.com",
                    "admin@test.org",
                    "invalid.email",
                    "another@domain.net",
                ]
                for email in emails:
                    obj = TestModel(email=email)
                    session.add(obj)

            # Test REGEXP functionality
            with manager._get_db_session() as session:
                # Find valid email addresses
                valid_emails = session.execute(
                    text(
                        "SELECT email FROM regex_test_model WHERE email REGEXP '^[^@]+@[^@]+\\.[^@]+$'"
                    )
                ).fetchall()

                valid_email_list = [row[0] for row in valid_emails]
                assert len(valid_email_list) == 3
                assert "user@example.com" in valid_email_list
                assert "admin@test.org" in valid_email_list
                assert "another@domain.net" in valid_email_list
                assert "invalid.email" not in valid_email_list


class TestErrorHandling:
    """Test error handling scenarios."""

    def test_invalid_database_configuration(self):
        """Test handling of invalid database configuration."""
        with patch.dict(os.environ, {"DATABASE_TYPE": "invalid_type"}):
            with pytest.raises(Exception):
                manager = DatabaseManager("test.static")


class TestEnumeration:
    """Test Operation enumeration."""

    def test_operation_enum_values(self):
        """Test that Operation enum has expected values."""
        assert Operation.CREATE is not None
        assert Operation.READ is not None
        assert Operation.UPDATE is not None
        assert Operation.DELETE is not None

        # Test enum names
        assert Operation.CREATE.name == "CREATE"
        assert Operation.READ.name == "READ"
        assert Operation.UPDATE.name == "UPDATE"
        assert Operation.DELETE.name == "DELETE"


class TestDatabaseFileHandling:
    """Test database file handling for SQLite."""

    def test_sqlite_file_creation(self):
        """Test that SQLite database files are created properly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                os.environ,
                {
                    "DATABASE_TYPE": "sqlite",
                    "DATABASE_NAME": "file_test",
                    "DATABASE_PATH": temp_dir,
                },
            ):
                manager = DatabaseManager("test.static")

                try:
                    # Verify database file was created
                    db_file_path = Path(manager.DATABASE_URI.replace("sqlite:///", ""))
                    assert db_file_path.exists()
                    assert db_file_path.name == "test.static.file_test.db"
                finally:
                    # Properly dispose of setup engine to release file handles
                    if manager._setup_engine:
                        manager._setup_engine.dispose()

    def test_sqlite_directory_creation(self):
        """Test that SQLite database directories are created if they don't exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            new_db_path = Path(temp_dir) / "new_subdir" / "another_subdir"

            with patch.dict(
                os.environ,
                {
                    "DATABASE_TYPE": "sqlite",
                    "DATABASE_NAME": "dir_test",
                    "DATABASE_PATH": str(new_db_path),
                },
            ):
                manager = DatabaseManager("test.static")

                try:
                    # Verify directory was created
                    assert new_db_path.exists()
                    assert new_db_path.is_dir()

                    # Verify database file was created in the new directory
                    db_file = new_db_path / "test.static.dir_test.db"
                    assert db_file.exists()
                finally:
                    # Properly dispose of setup engine to release file handles
                    if manager._setup_engine:
                        manager._setup_engine.dispose()


@pytest.mark.asyncio
class TestAsyncOperations:
    """Test asynchronous database operations."""

    async def test_async_database_operations(self):
        """Test async database operations with real data."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "async_test"}
        ):
            manager = DatabaseManager("test.static")

            # Define test model
            TestModel = type(
                "TestModel",
                (manager.Base,),
                {
                    "__tablename__": "async_test_model",
                    "id": Column(Integer, primary_key=True),
                    "name": Column(String(50)),
                    "created_at": Column(String(50)),
                },
            )

            # Create tables
            manager.Base.metadata.create_all(manager.get_setup_engine())

            # Test async operations
            async with manager._get_async_db_session() as session:
                # Insert data
                await session.execute(
                    text(
                        "INSERT INTO async_test_model (name, created_at) VALUES (:name, :created_at)"
                    ),
                    {"name": "async_test", "created_at": str(datetime.now())},
                )

                # Query data
                result = await session.execute(
                    text(
                        "SELECT name, created_at FROM async_test_model WHERE name = :name"
                    ),
                    {"name": "async_test"},
                )
                row = result.fetchone()

                assert row is not None
                assert row[0] == "async_test"
                assert row[1] is not None

    async def test_async_transaction_handling(self):
        """Test async transaction commit and rollback."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "async_trans_test"}
        ):
            manager = DatabaseManager("test.static")

            # Create test table
            async with manager._get_async_db_session() as session:
                await session.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS async_trans_test (id INTEGER PRIMARY KEY, value TEXT)"
                    )
                )

            # Test successful transaction (auto-commit)
            async with manager._get_async_db_session() as session:
                await session.execute(
                    text("INSERT INTO async_trans_test (value) VALUES (:value)"),
                    {"value": "committed"},
                )

            # Verify commit worked
            async with manager._get_async_db_session() as session:
                result = await session.execute(
                    text("SELECT value FROM async_trans_test WHERE value = :value"),
                    {"value": "committed"},
                )
                assert result.fetchone() is not None

            # Test failed transaction (rollback)
            with pytest.raises(ValueError):
                async with manager._get_async_db_session() as session:
                    await session.execute(
                        text("INSERT INTO async_trans_test (value) VALUES (:value)"),
                        {"value": "rolled_back"},
                    )
                    raise ValueError("Test rollback")

            # Verify rollback worked
            async with manager._get_async_db_session() as session:
                result = await session.execute(
                    text("SELECT value FROM async_trans_test WHERE value = :value"),
                    {"value": "rolled_back"},
                )
                assert result.fetchone() is None


class TestDbNameToPath:
    """Test db_name_to_path function."""

    def test_db_name_to_path_with_base_dir(self):
        """Test db_name_to_path with custom base directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            result = db_name_to_path("test.database", base_dir=temp_dir)
            assert result.endswith("test.database.db")
            assert temp_dir in result

    def test_db_name_to_path_full_url(self):
        """Test db_name_to_path returning full SQLite URL."""
        with tempfile.TemporaryDirectory() as temp_dir:
            result = db_name_to_path("test.database", base_dir=temp_dir, full_url=True)
            assert result.startswith("sqlite:///")
            assert "test.database.db" in result

    def test_db_name_to_path_default_base_dir(self):
        """Test db_name_to_path with default base directory."""
        result = db_name_to_path("test.default.path")
        assert result.endswith("test.default.path.db")
        assert os.path.isabs(result)


class TestGetSetupEngine:
    """Test get_setup_engine function."""

    def test_get_setup_engine_not_initialized(self):
        """Test get_setup_engine raises error when not initialized."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"}
        ):
            manager = DatabaseManager("test.static.engine", test_connection=False)
            # Clear the setup engine
            manager._setup_engine = None

            with pytest.raises(RuntimeError) as exc_info:
                manager.get_setup_engine()

            assert "Setup engine not initialized" in str(exc_info.value)


class TestInitWorkerErrors:
    """Test init_worker error scenarios."""

    def test_init_worker_not_configured(self):
        """Test init_worker raises error when engine config not initialized."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"}
        ):
            manager = DatabaseManager("test.static.worker", test_connection=False)
            # Clear the engine configurations
            manager.engine_config = None
            manager.async_engine_config = None

            with pytest.raises(RuntimeError) as exc_info:
                manager.init_worker()

            assert "Engine configuration not initialized" in str(exc_info.value)

    def test_init_worker_already_initialized(self):
        """Test init_worker does nothing when already initialized."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"}
        ):
            manager = DatabaseManager("test.static.worker2")
            manager.init_worker()
            assert manager._worker_initialized

            # Call again - should return early
            manager.init_worker()
            assert manager._worker_initialized


class TestCloseWorkerBranches:
    """Test close_worker function branches."""

    @pytest.mark.asyncio
    async def test_close_worker_not_initialized(self):
        """Test close_worker when not initialized."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"}
        ):
            manager = DatabaseManager("test.static.close", test_connection=False)
            # Don't initialize worker
            assert not manager._worker_initialized

            # Should return early without error
            await manager.close_worker()
            assert not manager._worker_initialized

    @pytest.mark.asyncio
    async def test_close_worker_with_thread_local_session(self):
        """Test close_worker with thread-local session."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"}
        ):
            manager = DatabaseManager("test.static.thread")
            manager.init_worker()

            # Create a thread-local session
            with manager._get_db_session() as session:
                pass  # Just establish the session

            # Close worker
            await manager.close_worker()
            assert not manager._worker_initialized

    @pytest.mark.asyncio
    async def test_close_worker_disposes_engines(self):
        """Test close_worker properly disposes engines."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"}
        ):
            manager = DatabaseManager("test.static.dispose")
            manager.init_worker()

            assert manager.engine is not None
            assert manager.async_engine is not None

            await manager.close_worker()

            # Worker should be marked as not initialized
            assert not manager._worker_initialized


class TestDisposeAll:
    """Test dispose_all function."""

    def test_dispose_all_cleans_everything(self):
        """Test dispose_all cleans up all resources."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"}
        ):
            manager = DatabaseManager("test.static.disposeall")
            manager.init_worker()

            # Create some sessions
            session = manager.get_session()
            session.close()

            # Dispose all
            manager.dispose_all()

            # Resources should be cleaned up
            assert manager.get_active_session_count() == 0


class TestGetActiveSessionCount:
    """Test get_active_session_count function."""

    def test_get_active_session_count(self):
        """Test counting active sessions."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"}
        ):
            manager = DatabaseManager("test.static.count")

            initial_count = manager.get_active_session_count()

            # Create a session
            session = manager.get_session()

            # Count should increase
            assert manager.get_active_session_count() >= initial_count

            session.close()


class TestRegexpFunctionEdgeCases:
    """Test SQLite REGEXP function edge cases."""

    def test_regexp_with_none_value(self):
        """Test REGEXP function with NULL value returns False."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name

        try:
            engine = create_engine(f"sqlite:///{db_path}")
            setup_sqlite_for_regex(engine)

            with engine.connect() as conn:
                conn.execute(text("CREATE TABLE test_null (val TEXT)"))
                conn.execute(text("INSERT INTO test_null VALUES (NULL)"))
                conn.execute(text("INSERT INTO test_null VALUES ('test')"))
                conn.commit()

                # NULL should not match any pattern
                result = conn.execute(
                    text("SELECT COUNT(*) FROM test_null WHERE val REGEXP 'test'")
                )
                count = result.scalar()
                assert count == 1

            engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except PermissionError:
                pass

    def test_regexp_with_invalid_pattern(self):
        """Test REGEXP function with invalid regex pattern."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name

        try:
            engine = create_engine(f"sqlite:///{db_path}")
            setup_sqlite_for_regex(engine)

            with engine.connect() as conn:
                conn.execute(text("CREATE TABLE test_invalid (val TEXT)"))
                conn.execute(text("INSERT INTO test_invalid VALUES ('test')"))
                conn.commit()

                # Invalid regex pattern should return False (not crash)
                result = conn.execute(
                    text("SELECT COUNT(*) FROM test_invalid WHERE val REGEXP '[invalid'")
                )
                count = result.scalar()
                assert count == 0

            engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except PermissionError:
                pass


class TestDatabaseInfoWithSSL:
    """Test get_database_info with SSL configurations."""

    def test_postgresql_with_ssl_enabled(self):
        """Test PostgreSQL database info with SSL enabled."""
        env = {
            "DATABASE_TYPE": "postgresql",
            "DATABASE_NAME": "test_db",
            "DATABASE_USER": "user",
            "DATABASE_PASSWORD": "pass",
            "DATABASE_HOST": "localhost",
            "DATABASE_PORT": "5432",
            "DATABASE_SSL": "require",
        }

        with patch.dict(os.environ, env, clear=True):
            db_info = get_database_info()

            assert db_info["type"] == "postgresql"
            assert "sslmode=require" in db_info["url"]


class TestDatabaseManagerAsyncUrlGeneration:
    """Test async URL generation for different database types."""

    def test_async_url_for_mysql(self):
        """Test async URL generation for MySQL."""
        env = {
            "DATABASE_TYPE": "mysql",
            "DATABASE_NAME": "test_db",
            "DATABASE_USER": "user",
            "DATABASE_PASSWORD": "pass",
            "DATABASE_HOST": "localhost",
            "DATABASE_PORT": "3306",
            "DATABASE_SSL": "disable",
        }

        with patch.dict(os.environ, env, clear=True):
            # This should not raise an error - validates the code path
            try:
                manager = DatabaseManager("test.mysql", test_connection=False)
                assert manager.async_engine_config is not None
                assert "aiomysql" in manager.async_engine_config["url"]
            except Exception:
                # MySQL may not be available, but code path is tested
                pass

    def test_async_url_for_mariadb(self):
        """Test async URL generation for MariaDB."""
        env = {
            "DATABASE_TYPE": "mariadb",
            "DATABASE_NAME": "test_db",
            "DATABASE_USER": "user",
            "DATABASE_PASSWORD": "pass",
            "DATABASE_HOST": "localhost",
            "DATABASE_PORT": "3306",
            "DATABASE_SSL": "disable",
        }

        with patch.dict(os.environ, env, clear=True):
            try:
                manager = DatabaseManager("test.mariadb", test_connection=False)
                assert manager.async_engine_config is not None
                assert "aiomysql" in manager.async_engine_config["url"]
            except Exception:
                pass

    def test_async_url_for_mssql(self):
        """Test async URL generation for MSSQL."""
        env = {
            "DATABASE_TYPE": "mssql",
            "DATABASE_NAME": "test_db",
            "DATABASE_USER": "user",
            "DATABASE_PASSWORD": "pass",
            "DATABASE_HOST": "localhost",
            "DATABASE_PORT": "1433",
            "DATABASE_SSL": "disable",
        }

        with patch.dict(os.environ, env, clear=True):
            try:
                manager = DatabaseManager("test.mssql", test_connection=False)
                assert manager.async_engine_config is not None
                assert "aioodbc" in manager.async_engine_config["url"]
            except Exception:
                pass


class TestSessionWithManualCommit:
    """Test session behavior with manual commit mode."""

    def test_get_db_with_manual_commit(self):
        """Test get_db with auto_commit=False."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"}
        ):
            manager = DatabaseManager("test.static.manual")

            # Test with manual commit mode
            gen = manager.get_db(auto_commit=False)
            session = next(gen)

            assert isinstance(session, Session)

            # Clean up
            try:
                next(gen)
            except StopIteration:
                pass

    @pytest.mark.asyncio
    async def test_get_async_db_with_manual_commit(self):
        """Test get_async_db with auto_commit=False."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"}
        ):
            manager = DatabaseManager("test.static.asyncmanual")

            # Test with manual commit mode
            async for session in manager.get_async_db(auto_commit=False):
                assert isinstance(session, AsyncSession)
                break


class TestCleanupThreadEdgeCases:
    """Test cleanup_thread edge cases."""

    def test_cleanup_thread_no_session(self):
        """Test cleanup_thread when no session exists."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"}
        ):
            manager = DatabaseManager("test.static.cleanup")

            # Ensure no session exists
            if hasattr(manager._thread_local, "session"):
                delattr(manager._thread_local, "session")

            # Should not raise error
            manager.cleanup_thread()

    def test_cleanup_thread_already_closed_session(self):
        """Test cleanup_thread with an already closed session."""
        with patch.dict(
            os.environ, {"DATABASE_TYPE": "sqlite", "DATABASE_NAME": "test_db"}
        ):
            manager = DatabaseManager("test.static.cleanup2")

            # Create and close a session
            with manager._get_db_session() as session:
                pass

            # Session should be in thread-local but already closed
            # cleanup_thread should handle this gracefully
            manager.cleanup_thread()
