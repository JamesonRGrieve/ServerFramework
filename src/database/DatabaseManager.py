"""
Database manager with parent/worker process separation and thread-safe session handling.
Provides automatic transaction management with commit-on-success and rollback-on-exception.
Consolidated database configuration and declarative base management.
"""

import multiprocessing
import os
import threading
from contextlib import asynccontextmanager, contextmanager
from enum import Enum
from os import makedirs, path
from threading import local
from typing import AsyncGenerator, Generator, Optional
from weakref import WeakSet

from sqlalchemy import UUID, String, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from lib.Environment import env
from lib.Logging import logger

Operation = Enum("Operation", ["CREATE", "READ", "UPDATE", "DELETE"])


def setup_sqlite_for_regex(engine):
    """
    Register the REGEXP function with SQLite.
    This should be called after creating the SQLite engine.
    """
    import re
    import sqlite3

    def regexp(expr, item):
        if item is None:
            return False
        try:
            reg = re.compile(expr)
            return reg.search(item) is not None
        except Exception:
            return False

    # Register the function will be done on individual connections

    # For SQLAlchemy, we need to register it with the engine's connect event
    @event.listens_for(engine, "connect")
    def do_connect(dbapi_connection, connection_record):
        dbapi_connection.create_function("REGEXP", 2, regexp)


def setup_sqlite_for_concurrency(engine):
    """
    Configure SQLite for better concurrent access.
    Sets up WAL mode, busy timeout, and other optimizations.
    """

    @event.listens_for(engine, "connect")
    def do_connect(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            # Enable WAL mode for better concurrent access
            cursor.execute("PRAGMA journal_mode=WAL")
            # Set busy timeout to 30 seconds (30000 ms)
            cursor.execute("PRAGMA busy_timeout=30000")
            # Optimize synchronous mode for WAL
            cursor.execute("PRAGMA synchronous=NORMAL")
            # Enable foreign key constraints
            cursor.execute("PRAGMA foreign_keys=ON")
            # Optimize cache size (negative value = KB)
            cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
        except Exception as e:
            logger.warning(f"Failed to set SQLite pragmas: {e}")
        finally:
            cursor.close()


def get_database_info(db_prefix: str = ""):
    """Get database configuration information.

    Args:
        db_prefix: Prefix to add to the original DATABASE_NAME (e.g., "test" or "test.payment")

    Returns:
        dict: A dictionary containing database configuration with keys:
            - type: Database type (sqlite/postgresql)
            - name: Database name
            - url: Full database URL
            - file_path: Full path to database file (for SQLite only)
    """
    # Read directly from os.environ to support test environment patching
    db_type = os.getenv("DATABASE_TYPE") or env("DATABASE_TYPE")
    original_db_name = os.getenv("DATABASE_NAME") or env("DATABASE_NAME")

    # Apply prefix if provided, but prevent nesting
    if db_prefix:
        # Prevent prefix nesting by checking if the prefix is already applied
        if not original_db_name.startswith(f"{db_prefix}."):
            db_name = f"{db_prefix}.{original_db_name}"
        else:
            db_name = original_db_name
    else:
        db_name = original_db_name

    if db_type != "sqlite":
        # PostgreSQL connection setup
        db_user = os.getenv("DATABASE_USER") or env("DATABASE_USER")
        db_pass = os.getenv("DATABASE_PASSWORD") or env("DATABASE_PASSWORD")
        db_host = os.getenv("DATABASE_HOST") or env("DATABASE_HOST")
        db_port = os.getenv("DATABASE_PORT") or env("DATABASE_PORT")
        db_ssl = os.getenv("DATABASE_SSL") or env("DATABASE_SSL")

        if db_ssl == "disable":
            login_uri = f"{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
        else:
            login_uri = (
                f"{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}?sslmode={db_ssl}"
            )

        db_url = f"postgresql://{login_uri}"
        return {"type": db_type, "name": db_name, "url": db_url, "file_path": None}
    else:
        # SQLite connection setup
        db_path = (
            os.getenv("DATABASE_PATH") or env("DATABASE_PATH")
            if os.getenv("DATABASE_PATH") or env("DATABASE_PATH")
            else os.path.dirname(os.path.abspath(__file__))
        )

        # Normalize the database path
        db_path = os.path.abspath(db_path)

        # Create database filename with .db extension
        db_filename = f"{db_name}.db"
        db_file = os.path.join(db_path, db_filename)

        # Ensure path is absolute
        if not os.path.isabs(db_file):
            db_file = os.path.abspath(db_file)

        # SQLite URIs must always use forward slashes, even on Windows
        # Convert any backslashes to forward slashes for the URI
        db_file_uri = db_file.replace("\\", "/")

        # Create absolute URI for SQLite
        db_url = f"sqlite:///{db_file_uri}"

        # Ensure the parent directory exists
        db_dir = path.dirname(path.abspath(db_file))
        try:
            if not path.exists(db_dir):
                makedirs(db_dir)
                logger.info(f"Created directory path: {db_dir}")
        except Exception as e:
            logger.error(f"Error creating directory path: {e}")
            raise

        # Check if the database file exists
        if not path.exists(db_file):
            try:
                # Create an empty file
                open(db_file, "a").close()
                logger.info(f"Created new SQLite database file: {db_file}")
            except Exception as e:
                logger.error(f"Error creating SQLite database file: {e}")
                raise

        return {"type": db_type, "name": db_name, "url": db_url, "file_path": db_file}


class DatabaseManager:
    """
    Thread-safe database manager with parent/worker process separation.
    Engine configuration happens in parent process, sessions in workers.
    Provides automatic transaction management and isolated declarative bases.
    """

    _instance = None
    _lock = threading.Lock()

    def __init__(self, db_prefix: str = "", test_connection: bool = False):
        # Engine configurations (set in parent process)
        self.engine_config: Optional[dict] = None
        self.async_engine_config: Optional[dict] = None
        self._setup_engine: Optional[Engine] = None
        self.db_prefix: str = ""

        # Worker-specific attributes (initialized per worker)
        self.engine: Optional[Engine] = None
        self.async_engine: Optional[AsyncEngine] = None
        self._session_factory: Optional[sessionmaker] = None
        self._async_session_factory: Optional[async_sessionmaker] = None
        self._worker_initialized = False

        # Database-specific declarative base and metadata
        self._base = None
        self._database_type = None
        self._database_name = None
        self._database_uri = None
        self._pk_type = None

        # Thread-local storage for session management
        self._thread_local = local()

        # Track active sessions for cleanup (thread-safe)
        self._active_sessions = WeakSet()
        self._sessions_lock = threading.RLock()
        if db_prefix:
            self.init_engine_config(db_prefix, test_connection)
        else:
            self.init_engine_config()

    @classmethod
    def get_instance(cls) -> "DatabaseManager":
        """Get or create the singleton instance with thread-safe double-checked locking."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def init_engine_config(
        self, db_prefix: str = "", test_connection: bool = True
    ) -> None:
        """Initialize engine configuration in parent process.

        Args:
            db_prefix: Prefix to add to the original DATABASE_NAME (e.g., "test" or "test.payment")
            test_connection: Whether to test the database connection during initialization
        """
        logger.info("Initializing database engine configuration in parent process")

        self.db_prefix = db_prefix

        # Get database info with optional prefix
        db_info = get_database_info(db_prefix)
        database_uri = db_info["url"]
        database_type = db_info["type"]

        # Store database configuration
        self._database_type = database_type
        self._database_name = db_info["name"]
        self._database_uri = database_uri
        self._database_file_path = db_info.get("file_path")  # Store file path for migrations
        self._pk_type = String if database_type == "sqlite" else UUID

        if database_type == "sqlite":
            self.engine_config = {
                "url": database_uri,
                "connect_args": {
                    "check_same_thread": False,
                    "timeout": 30,  # 30 second timeout for database locks
                },
                "pool_pre_ping": True,
                "pool_recycle": 3600,
                # Increase pool size for tests
                "pool_size": 10,
                "max_overflow": 20,
            }
            async_url = database_uri.replace("sqlite://", "sqlite+aiosqlite://")
            self.async_engine_config = {
                "url": async_url,
                "connect_args": {
                    "check_same_thread": False,
                    "timeout": 30,  # 30 second timeout for database locks
                },
                "pool_pre_ping": True,
                "pool_recycle": 3600,
                "pool_size": 10,
                "max_overflow": 20,
            }
        else:
            self.engine_config = {
                "url": database_uri,
                "pool_size": 20,
                "max_overflow": 30,  # Increased for tests
                "pool_pre_ping": True,
                "pool_recycle": 3600,
            }
            async_url = database_uri.replace("postgresql://", "postgresql+asyncpg://")
            self.async_engine_config = {
                "url": async_url,
                "pool_size": 20,
                "max_overflow": 30,  # Increased for tests
                "pool_pre_ping": True,
                "pool_recycle": 3600,
            }

        # Validate database type
        if database_type not in ["sqlite", "postgresql", "mysql", "mariadb", "mssql"]:
            raise ValueError(f"Unsupported database type: {database_type}")

        # Create setup engine for parent process initialization
        self._setup_engine = create_engine(**self.engine_config)

        # Set up SQLite REGEXP function and concurrency optimizations if needed
        if database_type == "sqlite":
            setup_sqlite_for_regex(self._setup_engine)
            setup_sqlite_for_concurrency(self._setup_engine)

        # Test the connection if requested
        if test_connection:
            try:
                connection = self._setup_engine.connect()
                connection.close()
                logger.info(f"Successfully connected to database: {database_uri}")
            except Exception as e:
                logger.error(f"Error connecting to database: {e}")
                raise e

    @property
    def Base(self):
        """Get the declarative base for this database instance."""
        if self._base is None:
            self._base = declarative_base()
        return self._base

    @property
    def DATABASE_TYPE(self):
        """Get the database type for this instance."""
        return self._database_type

    @property
    def DATABASE_NAME(self):
        """Get the database name for this instance."""
        return self._database_name

    @property
    def DATABASE_URI(self):
        """Get the database URI for this instance."""
        return self._database_uri

    @property
    def PK_TYPE(self):
        """Get the primary key type for this instance."""
        return self._pk_type

    def get_setup_engine(self) -> Engine:
        """Get the setup engine used for parent process initialization."""
        if not self._setup_engine:
            raise RuntimeError("Setup engine not initialized")
        return self._setup_engine

    def init_worker(self) -> None:
        """Initialize database connections for this worker."""
        if self._worker_initialized:
            return

        if not self.engine_config or not self.async_engine_config:
            raise RuntimeError("Engine configuration not initialized in parent process")

        logger.info("Initializing database connections for worker")

        # Create engines using pre-configured settings
        self.engine = create_engine(**self.engine_config)
        self.async_engine = create_async_engine(**self.async_engine_config)

        # Set up SQLite REGEXP function and concurrency optimizations if needed
        if self._database_type == "sqlite":
            setup_sqlite_for_regex(self.engine)
            setup_sqlite_for_concurrency(self.engine)

        # Create session factories
        self._session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine,
            expire_on_commit=False,
        )

        self._async_session_factory = async_sessionmaker(
            self.async_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

        self._worker_initialized = True

    async def close_worker(self) -> None:
        """Clean up database connections for this worker."""
        if not self._worker_initialized:
            return

        logger.info("Closing database connections for worker")

        # Close all active sessions
        self._close_all_sessions()

        # Close any thread-local sessions
        if hasattr(self._thread_local, "session"):
            try:
                self._thread_local.session.close()
            except Exception:
                pass

        if hasattr(self._thread_local, "async_session"):
            try:
                await self._thread_local.async_session.close()
            except Exception:
                pass

        # Dispose engines
        if self.engine:
            try:
                self.engine.dispose()
            except Exception:
                pass

        if self.async_engine:
            try:
                await self.async_engine.dispose()
            except Exception:
                pass

        # Dispose setup engine if it exists
        if self._setup_engine:
            try:
                self._setup_engine.dispose()
            except Exception:
                pass

        self._worker_initialized = False

    def _close_all_sessions(self) -> None:
        """Close all tracked active sessions."""
        with self._sessions_lock:
            # Create a copy to avoid modification during iteration
            sessions_to_close = list(self._active_sessions)
            for session in sessions_to_close:
                try:
                    if hasattr(session, "close"):
                        session.close()
                except Exception as e:
                    logger.warning(f"Error closing session: {e}")
            self._active_sessions.clear()

    def get_session(self) -> Session:
        """Get a database session for this database instance.

        WARNING: This method returns a raw session that MUST be manually closed!
        Consider using get_db() context manager instead for automatic cleanup.

        Returns:
            SQLAlchemy Session connected to this database instance
        """
        if not self._worker_initialized:
            self.init_worker()

        session = self._session_factory()
        # Attach the db_manager instance to the session
        setattr(session, "_db_manager", self)

        # Track the session for cleanup
        with self._sessions_lock:
            self._active_sessions.add(session)

        return session

    @contextmanager
    def _get_db_session(
        self, *, auto_commit: bool = True
    ) -> Generator[Session, None, None]:
        """
        Internal method for getting a database session.
        Args:
            auto_commit: If True, automatically commits if no exceptions occur
        """
        if not self._worker_initialized:
            self.init_worker()

        session = self._session_factory()

        # Track the session
        with self._sessions_lock:
            self._active_sessions.add(session)

        # Store in thread-local for cleanup testing
        self._thread_local.session = session

        try:
            yield session
            if auto_commit:
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            try:
                session.close()
            except Exception as e:
                logger.warning(f"Error closing session: {e}")
            finally:
                # Remove from tracking
                with self._sessions_lock:
                    self._active_sessions.discard(session)

    @asynccontextmanager
    async def _get_async_db_session(
        self, *, auto_commit: bool = True
    ) -> AsyncGenerator[AsyncSession, None]:
        """
        Internal method for getting an async database session.
        Args:
            auto_commit: If True, automatically commits if no exceptions occur
        """
        if not self._worker_initialized:
            self.init_worker()

        async with self._async_session_factory() as session:
            try:
                yield session
                if auto_commit:
                    await session.commit()
            except Exception:
                await session.rollback()
                raise

    def get_db(self, auto_commit: bool = True) -> Generator[Session, None, None]:
        """
        FastAPI dependency for getting a database session.
        Args:
            auto_commit: If True, automatically commits if no exceptions occur.
                       Set to False when you need to control transaction boundaries manually.

        Usage:
            # Auto-commit mode (default)
            @router.get("/")
            def endpoint(db: Session = Depends(db_manager.get_db)):
                user = db.query(User).first()
                # Transaction automatically committed if no exceptions

            # Manual commit mode
            @router.get("/")
            def endpoint(db: Session = Depends(Depends(lambda: db_manager.get_db(auto_commit=False)))):
                user = db.query(User).first()
                db.commit()  # Manual commit required
        """
        with self._get_db_session(auto_commit=auto_commit) as session:
            yield session

    async def get_async_db(
        self, auto_commit: bool = True
    ) -> AsyncGenerator[AsyncSession, None]:
        """
        FastAPI dependency for getting an async database session.
        Args:
            auto_commit: If True, automatically commits if no exceptions occur.
                       Set to False when you need to control transaction boundaries manually.

        Usage:
            # Auto-commit mode (default)
            @router.get("/")
            async def endpoint(db: AsyncSession = Depends(db_manager.get_async_db)):
                result = await db.execute(select(User))
                # Transaction automatically committed if no exceptions

            # Manual commit mode
            @router.get("/")
            async def endpoint(
                db: AsyncSession = Depends(lambda: db_manager.get_async_db(auto_commit=False))
            ):
                result = await db.execute(select(User))
                await db.commit()  # Manual commit required
        """
        async with self._get_async_db_session(auto_commit=auto_commit) as session:
            yield session

    def cleanup_thread(self) -> None:
        """Clean up thread-local resources."""
        if hasattr(self._thread_local, "session"):
            try:
                self._thread_local.session.close()
            except Exception:
                pass  # Session might already be closed
            delattr(self._thread_local, "session")

    def dispose_all(self) -> None:
        """Dispose all engines and clean up resources."""
        # Close all active sessions first
        self._close_all_sessions()

        # Dispose setup engine
        if self._setup_engine:
            try:
                self._setup_engine.dispose()
            except Exception:
                pass

        # Dispose worker engines
        if self.engine:
            try:
                self.engine.dispose()
            except Exception:
                pass

        if self.async_engine:
            try:
                # Note: async_engine.dispose() is synchronous
                self.async_engine.sync_dispose()
            except Exception:
                pass

    def get_active_session_count(self) -> int:
        """Get the number of currently active sessions (for debugging)."""
        with self._sessions_lock:
            return len(self._active_sessions)


def db_name_to_path(
    db_name: str, base_dir: Optional[str] = None, full_url: bool = False
):
    """Convert database name to file path or URL.

    Args:
        db_name: Database name (e.g., "test.migration.meta")
        base_dir: Base directory for the database file. If None, uses DATABASE_PATH or current file directory
        full_url: If True, returns full SQLite URL; if False, returns file path

    Returns:
        str: Database file path or SQLite URL depending on full_url parameter
    """
    # Determine base directory
    if base_dir is None:
        base_dir = (
            os.getenv("DATABASE_PATH") or env("DATABASE_PATH")
            if os.getenv("DATABASE_PATH") or env("DATABASE_PATH")
            else os.path.dirname(os.path.abspath(__file__))
        )

    # Normalize the database path
    base_dir = os.path.abspath(base_dir)

    # Create database filename with .db extension
    db_filename = f"{db_name}.db"
    db_file = os.path.join(base_dir, db_filename)

    # Ensure path is absolute
    if not os.path.isabs(db_file):
        db_file = os.path.abspath(db_file)

    # Ensure proper Windows path format with backslashes for SQLite
    if os.name == "nt":  # Windows system
        db_file = db_file.replace("/", "\\")

    if full_url:
        return f"sqlite:///{db_file}"
    else:
        return db_file
