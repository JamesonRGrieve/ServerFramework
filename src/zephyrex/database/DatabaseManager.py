"""
Database manager with parent/worker process separation and thread-safe session handling.
Provides automatic transaction management with commit-on-success and rollback-on-exception.
Consolidated database configuration and declarative base management.
"""

import multiprocessing
import os
import tempfile
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

from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger

Operation = Enum("Operation", ["CREATE", "READ", "UPDATE", "DELETE"])


def _redact_db_uri(uri: Optional[str]) -> str:
    """Strip credentials from a SQLAlchemy URI before it reaches a log line.

    Postgres/MySQL/MariaDB/MSSQL URIs embed ``user:password@host``. Logging
    the URI as-is leaks the password to stdout/aggregators. SQLite paths and
    in-memory URIs are returned verbatim because they carry no secret.
    """
    if not uri:
        return ""
    try:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(uri)
        if parsed.password is None and parsed.username is None:
            return uri
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        userinfo = ""
        if parsed.username:
            userinfo = f"{parsed.username}:***@"
        netloc = f"{userinfo}{host}"
        return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        # Defensive: any parser error must not leak the original URI either.
        return "<redacted>"


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
            else tempfile.gettempdir()
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


def _sync_to_async_url(sync_url: str) -> str:
    """Convert a synchronous SQLAlchemy database URL to its async equivalent.

    Handles all supported database backends:
      - sqlite     -> sqlite+aiosqlite
      - postgresql -> postgresql+asyncpg
      - mysql      -> mysql+aiomysql
      - mariadb    -> mariadb+aiomysql
      - mssql      -> mssql+aioodbc

    If the URL does not match a known sync scheme, it is returned unchanged.
    """
    _SYNC_TO_ASYNC_SCHEMES = (
        ("sqlite://", "sqlite+aiosqlite://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("mysql://", "mysql+aiomysql://"),
        ("mariadb://", "mariadb+aiomysql://"),
        ("mssql://", "mssql+aioodbc://"),
    )
    for sync_scheme, async_scheme in _SYNC_TO_ASYNC_SCHEMES:
        if sync_url.startswith(sync_scheme):
            return sync_url.replace(sync_scheme, async_scheme, 1)
    return sync_url


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

        # Item 54 — read-replica routing state.
        # Replica engines and session factories are keyed by URL. The
        # `_replica_pool` (from `database/ReadReplica.py`) drives selection
        # via round-robin with health gating. `replica_urls` is parsed at
        # engine-config time from `DB_REPLICA_URLS`; an empty list disables
        # replica routing entirely (every read still binds primary).
        self.replica_urls: list = []
        self._replica_pool = None  # ReplicaPool, populated in init_worker
        self._replica_engines: dict = {}
        self._replica_session_factories: dict = {}
        self._replica_async_engines: dict = {}
        self._replica_async_session_factories: dict = {}

        # Database-specific declarative base and metadata
        self._base = None
        self._database_type = None
        self._database_name = None
        self._database_uri = None
        self._pk_type = None

        # Thread-local storage for session management
        self._thread_local = local()

        # Track active sessions for cleanup (thread-safe)
        self._active_sessions = WeakSet()  # type: ignore[var-annotated]
        self._sessions_lock = threading.RLock()
        if db_prefix:
            self.init_engine_config(db_prefix, test_connection)
        else:
            self.init_engine_config()

    @classmethod
    def get_instance(cls, db_prefix: str = "") -> "DatabaseManager":
        """Get or create the singleton instance with thread-safe double-checked locking.

        Args:
            db_prefix: Optional database prefix. If not provided and running in pytest,
                      uses 'test.singleton' to avoid touching production database.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    # Use test prefix if running in pytest and no prefix provided
                    if not db_prefix and os.environ.get("PYTEST_CURRENT_TEST"):
                        db_prefix = "test.singleton"
                    cls._instance = cls(db_prefix)
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
        self._database_file_path = db_info.get(
            "file_path"
        )  # Store file path for migrations
        self._pk_type = String if database_type == "sqlite" else UUID  # type: ignore[assignment]

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
            self.async_engine_config = {
                "url": _sync_to_async_url(database_uri),
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
            self.async_engine_config = {
                "url": _sync_to_async_url(database_uri),
                "pool_size": 20,
                "max_overflow": 30,  # Increased for tests
                "pool_pre_ping": True,
                "pool_recycle": 3600,
            }

        # Validate database type
        if database_type not in ["sqlite", "postgresql", "mysql", "mariadb", "mssql"]:
            raise ValueError(f"Unsupported database type: {database_type}")

        # Item 54 — parse DB_REPLICA_URLS into the per-instance replica list.
        # Empty (default) means no replicas; reads always bind primary.
        # Each URL is a complete connection string of the same shape as
        # DATABASE_URI; per-replica engine/session-factory pairs are built
        # in init_worker().
        replica_env = os.getenv("DB_REPLICA_URLS") or env("DB_REPLICA_URLS") or ""
        self.replica_urls = [
            u.strip() for u in replica_env.split(",") if u.strip()
        ]

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
                logger.info(
                    f"Successfully connected to database: {_redact_db_uri(database_uri)}"
                )
            except Exception as e:
                logger.error(
                    f"Error connecting to database "
                    f"({_redact_db_uri(database_uri)}): {e}"
                )
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

        # Item 54 — build per-replica engines + session factories and the
        # `ReplicaPool` that drives round-robin selection. Each replica URL
        # uses the same engine kwargs as primary (pool sizing, pre-ping)
        # but no setup-engine equivalent — replicas are always read-only
        # in the deployment topology, so they never run setup operations.
        self._replica_engines.clear()
        self._replica_session_factories.clear()
        self._replica_async_engines.clear()
        self._replica_async_session_factories.clear()

        for url in self.replica_urls:
            try:
                cfg = dict(self.engine_config)
                cfg["url"] = url
                engine = create_engine(**cfg)
                if self._database_type == "sqlite":
                    setup_sqlite_for_regex(engine)
                    setup_sqlite_for_concurrency(engine)
                self._replica_engines[url] = engine
                self._replica_session_factories[url] = sessionmaker(
                    autocommit=False,
                    autoflush=False,
                    bind=engine,
                    expire_on_commit=False,
                )
                # Async replica — use the shared sync-to-async mapping.
                async_cfg = dict(self.async_engine_config)
                async_cfg["url"] = _sync_to_async_url(url)
                async_engine = create_async_engine(**async_cfg)
                self._replica_async_engines[url] = async_engine
                self._replica_async_session_factories[url] = async_sessionmaker(
                    async_engine,
                    class_=AsyncSession,
                    expire_on_commit=False,
                    autoflush=False,
                )
            except Exception as exc:
                logger.warning(
                    f"Item 54 replica engine init failed for {url}: {exc}"
                )

        # Build the replica pool now that we know which URLs were created.
        from zephyrex.database.ReadReplica import ReplicaPool

        self._replica_pool = ReplicaPool(list(self._replica_engines.keys()))

        self._worker_initialized = True

    # ------------------------------------------------------------------
    # Item 54 — replica session selection
    # ------------------------------------------------------------------

    def _select_session_factory(self) -> "sessionmaker":
        """Pick a session factory: replica when @read_only fires AND a
        replica is configured AND no primary write has occurred in this
        request; primary otherwise.

        Read-after-write consistency: once `mark_primary_write_seen()` has
        been called in the current request context, this method binds
        primary regardless of `@read_only`. The contextvar is set by the
        before-flush event listener registered on every primary session.
        """
        from zephyrex.database.ReadReplica import should_route_to_replica

        if (
            should_route_to_replica()
            and self._replica_pool is not None
            and self._replica_session_factories
        ):
            url = self._replica_pool.next_url()
            if url is not None and url in self._replica_session_factories:
                return self._replica_session_factories[url]
        return self._session_factory  # type: ignore[return-value]

    def _select_async_session_factory(self) -> "async_sessionmaker":
        """Async counterpart of `_select_session_factory`."""
        from zephyrex.database.ReadReplica import should_route_to_replica

        if (
            should_route_to_replica()
            and self._replica_pool is not None
            and self._replica_async_session_factories
        ):
            url = self._replica_pool.next_url()
            if url is not None and url in self._replica_async_session_factories:
                return self._replica_async_session_factories[url]
        return self._async_session_factory  # type: ignore[return-value]

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

        # Item 54 — dispose replica engines
        for engine in self._replica_engines.values():
            try:
                engine.dispose()
            except Exception:
                pass
        for async_engine in self._replica_async_engines.values():
            try:
                await async_engine.dispose()
            except Exception:
                pass
        self._replica_engines.clear()
        self._replica_session_factories.clear()
        self._replica_async_engines.clear()
        self._replica_async_session_factories.clear()
        self._replica_pool = None

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

        Item 54 — when `@read_only` is active and a replica is configured,
        the returned session is bound to a replica engine. Otherwise it's
        bound to primary. Read-after-write consistency is preserved by the
        before-flush listener attached below: the first flush against a
        primary session marks the request `primary_write_seen` so subsequent
        reads in the same request bind primary regardless of `@read_only`.

        Returns:
            SQLAlchemy Session connected to this database instance.
        """
        if not self._worker_initialized:
            self.init_worker()

        factory = self._select_session_factory()
        session = factory()
        # Attach the db_manager instance to the session
        setattr(session, "_db_manager", self)

        # Item 54 — register the primary-write watcher only on primary
        # sessions; replica sessions cannot write so the listener would be
        # a misleading no-op there.
        if factory is self._session_factory:
            self._attach_primary_write_listener(session)

        # Item 55 — emit `SET LOCAL app.current_<key>` GUCs at every
        # transaction begin. No-op on non-Postgres dialects so SQLite
        # tests stay portable.
        from zephyrex.database.TenantScoped import bind_session_tenant_gucs

        bind_session_tenant_gucs(session)

        # Track the session for cleanup
        with self._sessions_lock:
            self._active_sessions.add(session)

        return session  # type: ignore[no-any-return]

    @staticmethod
    def _attach_primary_write_listener(session: Session) -> None:
        """Register a one-shot before-flush hook that calls
        `mark_primary_write_seen()` on the first non-empty flush.

        Item 54 read-after-write contract: once the request has flushed
        any pending changes against primary, every subsequent read in the
        same request binds primary regardless of `@read_only`. The flush
        is the canonical "we wrote" signal because SA bundles all dirty/
        new/deleted instances into a flush call.
        """
        from sqlalchemy import event

        from zephyrex.database.ReadReplica import mark_primary_write_seen

        def _before_flush(_session, flush_context, instances):
            if _session.new or _session.dirty or _session.deleted:
                mark_primary_write_seen()

        event.listen(session, "before_flush", _before_flush)

    @contextmanager
    def _get_db_session(
        self, *, auto_commit: bool = True
    ) -> Generator[Session, None, None]:
        """
        Internal method for getting a database session.

        Item 54 — when `@read_only` is active and a replica is configured,
        the session is bound to a replica engine; otherwise primary. The
        before-flush listener on primary sessions trips
        `mark_primary_write_seen()` so subsequent reads in the same logical
        request bind primary regardless of `@read_only`.

        Args:
            auto_commit: If True, automatically commits if no exceptions occur
        """
        if not self._worker_initialized:
            self.init_worker()

        factory = self._select_session_factory()
        session = factory()
        if factory is self._session_factory:
            self._attach_primary_write_listener(session)

        # Item 55 — RLS GUC binder (no-op on SQLite).
        from zephyrex.database.TenantScoped import bind_session_tenant_gucs

        bind_session_tenant_gucs(session)

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

        Item 54 — replica routing applies symmetrically to async sessions:
        `@read_only` + replica configured → bind a replica session; else
        bind primary. The async before-flush listener trips
        `mark_primary_write_seen()` on primary sessions only.

        Args:
            auto_commit: If True, automatically commits if no exceptions occur
        """
        if not self._worker_initialized:
            self.init_worker()

        factory = self._select_async_session_factory()
        async with factory() as session:
            if factory is self._async_session_factory:
                self._attach_primary_write_listener_async(session)
            # Item 55 — async RLS GUC binder. SA's async session exposes
            # `sync_session` as a proxy for SA event hooks; the binder
            # attaches the same `after_begin` listener there.
            from zephyrex.database.TenantScoped import (
                bind_session_tenant_gucs,
            )

            sync_proxy = getattr(session, "sync_session", None)
            if sync_proxy is not None:
                bind_session_tenant_gucs(sync_proxy)
            try:
                yield session
                if auto_commit:
                    await session.commit()
            except Exception:
                await session.rollback()
                raise

    @staticmethod
    def _attach_primary_write_listener_async(session: AsyncSession) -> None:
        """Async counterpart of `_attach_primary_write_listener`.

        SA's async session exposes a `sync_session` proxy whose `before_flush`
        event is the right hook for the same write-detection contract.
        """
        from sqlalchemy import event

        from zephyrex.database.ReadReplica import mark_primary_write_seen

        sync_proxy = getattr(session, "sync_session", None)
        if sync_proxy is None:
            return

        def _before_flush(_session, flush_context, instances):
            if _session.new or _session.dirty or _session.deleted:
                mark_primary_write_seen()

        event.listen(sync_proxy, "before_flush", _before_flush)

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
                self.async_engine.sync_dispose()  # type: ignore[attr-defined]
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
            else os.getcwd()
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
