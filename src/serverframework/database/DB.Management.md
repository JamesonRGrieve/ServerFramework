# Database Management

## Overview
The database management system provides enterprise-grade database operations through `DatabaseManager.py` - a thread-safe database manager that handles multi-process database operations, automatic transaction management, connection pooling, and comprehensive session handling with support for multiple database backends.

## Core Components

### DatabaseManager (`DatabaseManager.py`)
Thread-safe database manager with configurable database prefixes and consolidated database configuration.

**Key Features:**
- Engine configuration with lazy worker initialization
- Automatic transaction management with commit-on-success/rollback-on-exception
- Both synchronous and asynchronous session support
- Connection pooling with configurable limits
- Thread-local session storage with proper cleanup
- Database-specific declarative base management
- Isolated instance creation for testing with prefixes
- SQLite optimizations with WAL mode and regex support
- WeakSet session tracking for automatic cleanup
- Support for custom database prefixes (e.g., "test", "test.payment")

**Usage:**
```python
# Create instance with optional prefix
db_manager = DatabaseManager(db_prefix="test", test_connection=True)

# Initialize engine configuration
db_manager.init_engine_config(db_prefix="test", test_connection=True)

# Worker initialization (lazy)
db_manager.init_worker()

# Use as FastAPI dependency with context manager
@router.get("/")
def endpoint(db: Session = Depends(db_manager.get_db)):
    # Auto-commit/rollback handling
    pass

# Manual transaction control
with db_manager.get_db(auto_commit=False) as db:
    # Manual transaction management
    db.commit()

# Get raw session (requires manual cleanup)
session = db_manager.get_session()
# ... use session
session.close()

# Access database-specific declarative base
Base = db_manager.Base
```

### Database Configuration (`DatabaseManager.py`)
Consolidated database connectivity and engine setup. SQLite is the production-ready default — the only backend with a passing engine-config test. PostgreSQL/MariaDB/MSSQL/Vector engine-config branches exist and are wired through `init_engine_config`, but the Postgres path is gated by driver pinning (asyncpg/psycopg), CI provisioning of a live Postgres, and the corresponding Big-O / migration tests; the multi-DB claim is honest only after those land. Postgres-specific primitives (Row-Level Security for tenant isolation, `pg_advisory_lock` as the default `AdvisoryLock` backend, `UPDATE ... RETURNING` distributed-counter semantics) are gated on the Postgres path landing first.

**Backend Status:**
- SQLite (production-ready default; regex support, WAL mode optimization, aiosqlite async support)
- PostgreSQL (engine-config branch present; driver pinning + live-CI gating in progress; required for RLS, advisory locks, distributed counters)
- MySQL (with aiomysql async support)
- MariaDB (with aiomysql async support)
- MSSQL (with aioodbc async support)

**Key Functions:**
- `get_database_info()`: Centralized database configuration
- `setup_sqlite_for_regex()`: SQLite regex function registration
- `setup_sqlite_for_concurrency()`: SQLite WAL mode and optimizations
- `db_name_to_path()`: Database name to file path conversion

**Features:**
- Dynamic database configuration from environment variables
- Connection pooling (20 pool size, 30 max overflow for PostgreSQL)
- Automatic SQLite database file creation
- Database-specific declarative base management
- Thread-safe session management with automatic cleanup

### Session Management
**Session Factory Configuration:**
- Autocommit: False
- Autoflush: False
- Expire on commit: False
- Thread-local session storage with cleanup_thread() method
- WeakSet tracking for active sessions
- Thread-safe session management with RLock

**Transaction Patterns:**
- Context managers for automatic cleanup
- Exception-safe rollback handling
- Automatic commit on success, rollback on exception
- Support for manual transaction control
- Thread-local session cleanup via cleanup_thread()

### Isolated Instance Support
**Testing and Multi-Database Support:**
```python
# Create isolated instance for testing
test_db = DatabaseManager(
    db_prefix="test.payment",
    test_connection=False  # Skip connection test during setup
)

# Access database properties
print(test_db.DATABASE_TYPE)    # Database type
print(test_db.DATABASE_NAME)    # Database name with prefix
print(test_db.DATABASE_URI)     # Full connection string
print(test_db.PK_TYPE)          # Primary key type (String/UUID)

# Use isolated instance
with test_db.get_db() as db:
    # Operations on test database
    pass
```

**Benefits:**
- Independent database instances for testing
- Isolated transaction scopes and declarative bases
- Configurable database prefixes with nesting prevention
- Connection testing can be disabled for setup
- Database-specific primary key type handling

## Multi-Database Support

### Configuration
Database type determined by `DATABASE_TYPE` environment variable:
- `sqlite`: Local SQLite database with optimizations
- `postgresql`: PostgreSQL with asyncpg support
- `mysql`: MySQL/MariaDB support
- `mssql`: Microsoft SQL Server support

### Connection Strings
- **SQLite**: `sqlite:///path/to/database.db` (always uses forward slashes, even on Windows)
- **SQLite Async**: `sqlite+aiosqlite:///path/to/database.db`
- **PostgreSQL**: `postgresql://user:pass@host:port/dbname`
- **PostgreSQL Async**: `postgresql+asyncpg://user:pass@host:port/dbname`
- **MySQL**: `mysql://user:pass@host:port/dbname`
- **MySQL Async**: `mysql+aiomysql://user:pass@host:port/dbname`
- **MariaDB**: `mariadb://user:pass@host:port/dbname`
- **MariaDB Async**: `mariadb+aiomysql://user:pass@host:port/dbname`
- **MSSQL**: `mssql://user:pass@host:port/dbname`
- **MSSQL Async**: `mssql+aioodbc://user:pass@host:port/dbname`

### Environment Variables
- `DATABASE_TYPE`: Database type (sqlite/postgresql/mysql/mssql)
- `DATABASE_NAME`: Database name
- `DATABASE_PATH`: SQLite file path (defaults to current directory)
- `DATABASE_USER`: Database username (PostgreSQL/MySQL/MSSQL)
- `DATABASE_PASSWORD`: Database password
- `DATABASE_HOST`: Database host
- `DATABASE_PORT`: Database port
- `DATABASE_SSL`: SSL mode for PostgreSQL

### Database Properties
Each `DatabaseManager` instance provides access to:
- `Base`: Database-specific declarative base
- `DATABASE_TYPE`: Current database type
- `DATABASE_NAME`: Current database name
- `DATABASE_URI`: Full database connection string
- `PK_TYPE`: Primary key type (String for SQLite, UUID for others)

## SQLite Optimizations

### Regex Support
Built-in regex support for SQLite databases:
```sql
SELECT * FROM users WHERE email REGEXP '^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$';
```

### Concurrency Optimizations
- **WAL Mode**: Write-Ahead Logging for better concurrent access
- **Busy Timeout**: 30-second timeout for database locks
- **Synchronous Mode**: NORMAL mode for optimal WAL performance
- **Foreign Keys**: Automatic enforcement of foreign key constraints
- **Cache Size**: 64MB cache for improved performance

### Connection Management
- Thread-safe connection handling
- Automatic pragma configuration on connection
- Proper Windows path handling
- Connection health monitoring

## Testing Support

### Isolated Test Databases
```python
# Automatic test database creation
test_manager = DatabaseManager(
    db_prefix="test.integration",
    test_connection=False  # Skip connection test during setup
)

# Use in tests
def test_database_operations():
    with test_manager.get_db() as db:
        # Test operations
        pass
```

**Features:**
- Automatic test database creation with prefixes
- Isolated test database environments
- Connection pooling optimized for testing
- Thread-safe test execution with proper synchronization
- Thread-safe results collection in multi-threaded tests
- Per-thread session isolation with thread-local storage

### Database Prefixes
Support for database name prefixes to create isolated environments:
- `test`: Creates `test.database_name`
- `test.integration`: Creates `test.integration.database_name`
- `test.migration`: Creates `test.migration.database_name`

**Prefix Nesting Prevention:**
The `get_database_info()` function automatically prevents double-prefixing. If the database name already starts with the requested prefix, it won't be applied again. For example:
- Request prefix `test` on `my_db` → `test.my_db`
- Request prefix `test` on `test.my_db` → `test.my_db` (no double-prefix)

This ensures that nested calls or re-initialization don't create invalid database names like `test.test.database_name`.

## Performance Considerations

### Connection Pooling
- **PostgreSQL**: 20 pool size, 30 max overflow
- **SQLite**: 10 pool size, 20 max overflow (with `check_same_thread=False` and 30s timeout)
- Pre-ping health checks for connection validation
- Pool recycling every 3600 seconds

### Session Management
- Thread-local session storage
- Lazy worker initialization
- Automatic session cleanup
- Context manager support for proper resource management

### Memory Management
- Efficient connection reuse
- Proper session disposal
- Thread-local storage cleanup
- Engine disposal on shutdown

## Error Handling

### Automatic Recovery
- Automatic rollback on exceptions
- Connection health monitoring with pre-ping
- Graceful degradation for connection failures
- Comprehensive logging for debugging

### Transaction Safety
- Context manager exception handling
- Automatic session cleanup on errors
- Thread-safe error propagation
- Proper resource disposal

## Advanced Features

### Async Support
```python
# Async database operations
@router.get("/")
async def async_endpoint(db: AsyncSession = Depends(db_manager.get_async_db)):
    result = await db.execute(select(User))
    return result.scalars().all()
```

### Worker Management
- Parent/worker process separation
- Lazy worker initialization
- Proper cleanup on worker shutdown
- Thread-safe worker management

### Database Metadata
- Database-specific configuration access
- Runtime database type detection
- Connection string management
- Primary key type determination

## Migrations

### Alembic Integration
Schema changes managed via Alembic migrations:
- Core migrations: `src/database/migrations/versions/`
- Extension migrations: `extensions/{name}/migrations/versions/`

### Extension Migrations
Each extension has isolated migrations folder scaffolded by Alembic. Use the `--extension` flag with the Migration.py CLI:
```bash
# Generate extension migration
python -m database.migrations.Migration revision --extension my_extension -m "description"

# Upgrade a specific extension
python -m database.migrations.Migration upgrade --extension my_extension

# Upgrade core and all extensions
python -m database.migrations.Migration upgrade --all
```

**Resolution Order:**
1. All core migrations (dependency order)
2. All extension migrations (extension dependency order)
3. Non-conflicting when resolved sequentially

**Structure:**
```
extensions/my_extension/
├── BLL_*.py              # Extension business logic models
└── migrations/
    └── versions/
        └── xxx_initial.py  # Migration files
```

Note: The `env.py` and `script.py.mako` files are temporarily copied from core migrations during extension migration operations and cleaned up afterward.

**@extension_model Fields:**
- Fields injected via `@extension_model` require manual migration creation
- Use Alembic autogenerate to detect extended fields
- Migrations applied in extension dependency order

This consolidated approach provides a robust, scalable database management system that supports multiple database types while maintaining thread safety, performance, and proper resource management.

## Backup, Restore, Point-in-Time Recovery

Each table declares `backup_class: ClassVar[BackupClass]`: `critical` (data loss unacceptable; nightly snapshots plus continuous WAL archiving), `recoverable` (recoverable from upstream federation; nightly snapshots only), `ephemeral` (cache, session, sticky-routing state; excluded from backups). A scheduled `BackupService` runs nightly and drives the underlying engine's snapshot/dump command (`pg_dump` for Postgres) into a configured `BackupTarget` (the object-storage abstract; S3/GCS/local-filesystem). PITR is supported for engines with WAL streaming via a separate continuous-archive job.

A monthly automated CI job restores the latest backup into a scratch DB, runs a smoke test, and discards. Restore drills run on isolated infrastructure, not against the live DB. RTO/RPO targets are declared per deployment and tracked as `backup_age_seconds` and `last_successful_restore_drill_age_seconds` metrics. A documented runbook describes the manual restore procedure.

Outbox entries past their deadline are marked DLQ on restore rather than re-fired, since the upstream may have already processed them. Quota counters are restored as-of backup time with the documented over-count window — the gap between backup and restore can produce a small over-count that is acknowledged rather than silently masked.

## Zero-Downtime Migrations

Every migration is split into two logical phases. **Expand** adds new structure while leaving the old structure in place so both old and new versions of the application run cleanly against the post-expand DB during a rolling deploy. **Contract** removes the old structure only after the new version has fully rolled out and the old version has been retired.

Invariants the framework enforces at migration generation:

- NOT NULL columns added by `@extension_model` must declare a default, so v1 inserts continue to succeed.
- Column drops are gated by a `removed_in: str` declaration that the migration generator turns into a separate contract migration in a later release.
- FK additions are split into "add column with FK" (expand) and "set NOT NULL on the FK" (contract).

A startup check rejects a migration that violates these invariants. The expand/contract split mirrors the standard pattern from Liquibase, gh-ost, and Postgres operational guides; the framework extends Alembic templates to emit a stub for the contract migration in the next release.