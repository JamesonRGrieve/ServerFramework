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
Consolidated database connectivity and engine setup with multi-database support.

**Supported Databases:**
- SQLite (with regex support and WAL mode optimization)
- PostgreSQL (with asyncpg support)
- MariaDB
- MSSQL

**Key Functions:**
- `get_database_info()`: Centralized database configuration
- `setup_sqlite_for_regex()`: SQLite regex function registration
- `setup_sqlite_for_concurrency()`: SQLite WAL mode and optimizations
- `db_name_to_path()`: Database name to file path conversion

**Features:**
- Dynamic database configuration from environment variables
- Connection pooling (20 pool size, 10 max overflow for PostgreSQL)
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
- **PostgreSQL**: `postgresql://user:pass@host:port/dbname`
- **Async PostgreSQL**: `postgresql+asyncpg://user:pass@host:port/dbname`
- **SQLite Async**: `sqlite+aiosqlite:///path/to/database.db`

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

## Performance Considerations

### Connection Pooling
- **PostgreSQL**: 20 pool size, 10 max overflow
- **SQLite**: No pooling (single connection per thread)
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
Each extension has isolated migrations folder scaffolded by Alembic:
```bash
# Generate extension migration
ALEMBIC_EXTENSION=my_extension alembic revision --autogenerate -m "description"
```

**Resolution Order:**
1. All core migrations (dependency order)
2. All extension migrations (extension dependency order)
3. Non-conflicting when resolved sequentially

**Structure:**
```
extensions/my_extension/
└── migrations/
    ├── env.py              # Extension-specific Alembic env
    └── versions/
        └── xxx_initial.py  # Migration files
```

**@extension_model Fields:**
- Fields injected via `@extension_model` require manual migration creation
- Use Alembic autogenerate to detect extended fields
- Migrations applied in extension dependency order

This consolidated approach provides a robust, scalable database management system that supports multiple database types while maintaining thread safety, performance, and proper resource management.