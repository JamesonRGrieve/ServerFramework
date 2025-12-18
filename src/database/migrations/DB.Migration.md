# Extensible Migration System

This document describes the extensible database migration system implemented in `Migration.py`. The system provides unified management of both core database migrations and extension-specific migrations using Alembic with sophisticated environment configuration and automated file management.

## Overview

The migration system is built on Alembic and provides the following features:

1. **Unified MigrationManager**: Instance-based architecture for core and extension migrations
2. **Dynamic File Management**: Automatic creation and cleanup of temporary configuration files
3. **Extension Isolation**: Independent migration histories using separate version tables
4. **Smart Table Detection**: Automatic detection of table ownership for proper migration separation
5. **Test Mode Support**: Isolated test environments with dedicated version directories
6. **Dependency Ordering**: Automatic dependency resolution for migration execution
7. **Model Integration**: Seamless integration with ModelRegistry and Pydantic models
8. **Advanced Configuration**: Dynamic alembic.ini generation with extension-specific settings
9. **Comprehensive Cleanup**: Automatic cleanup of temporary files and test environments

## Configuration

Extension configuration is managed exclusively through the `APP_EXTENSIONS` environment variable. This variable should contain a comma-separated list of the extension names that should be included in migration operations. This should be configured in an `.env` file, and the defaults are set in `lib/Environment.py`.

Example:
```bash
APP_EXTENSIONS="my_extension,my_other_extension,another_extension"
```

- If `APP_EXTENSIONS` is not set or is empty, no extension migrations will be processed.
- All extensions listed in `APP_EXTENSIONS` will be available for migration operations.
- Even if files for other extensions are present, they will not be targettable unless present in the environment variable.

## Extension Table Detection

Extension table ownership is determined solely by the location of the `BLL_*.py` file in which they reside. Extension model files must be in `src/extensions/<extension_name>/` in a `BLL_*.py` file to be considered owned by <extension_name>. Tables that extend core tables using `@extension_model` decorator are detected via the model extension registry. 

## Usage

The migration system is controlled through the unified `Migration.py` script:

### Running Migrations

**Upgrade the core database:**
```bash
python src/database/migrations/Migration.py upgrade
```

**Upgrade a specific extension:**
```bash
python src/database/migrations/Migration.py upgrade --extension extension_name
```

**Upgrade all (core + all extensions):**
```bash
python src/database/migrations/Migration.py upgrade --all
```

**Specify a target revision:**
```bash
python src/database/migrations/Migration.py upgrade --target revision_id
```

### Creating Migrations

**Create a new migration for core:**
```bash
python src/database/migrations/Migration.py revision -m "description"
```
*Note: `--auto` (autogenerate) is now the default. Use `--no-autogenerate` to create an empty migration file.*

**Create a new migration for an extension:**
```bash
python src/database/migrations/Migration.py revision --extension extension_name -m "description"
```
*Note: Use `--no-autogenerate` to create an empty migration file.*

**Regenerate migrations (delete all and start fresh):**
```bash
# Uses default message "initial schema"
python src/database/migrations/Migration.py revision --regenerate
# Or provide a custom message
python src/database/migrations/Migration.py revision --regenerate -m "Custom initial message"
```

**Regenerate all migrations (core + all extensions):**
```bash
# Uses default message "initial schema" for all regenerated migrations
python src/database/migrations/Migration.py revision --regenerate --all
# Or provide a custom message
python src/database/migrations/Migration.py revision --regenerate --all -m "Custom initial message"
```

*Autogeneration is used by default for regeneration. Empty migrations resulting from autogeneration (no changes detected) will be automatically deleted.*

### Checking Status

**Show migration history:**
```bash
python src/database/migrations/Migration.py history
python src/database/migrations/Migration.py history --extension extension_name
```

**Show current version:**
```bash
python src/database/migrations/Migration.py current
python src/database/migrations/Migration.py current --extension extension_name
```

### Downgrading

**Downgrade core:**
```bash
python src/database/migrations/Migration.py downgrade --target target_revision
```

**Downgrade extension:**
```bash
python src/database/migrations/Migration.py downgrade --extension extension_name --target target_revision
```

**Downgrade all:**
```bash
python src/database/migrations/Migration.py downgrade --all --target target_revision
```

### Debugging

**Show detailed environment and configuration information:**
```bash
python src/database/migrations/Migration.py debug
```

This will show:
- Environment variables related to the database (including `APP_EXTENSIONS`)
- Database configuration
- Paths being used
- Alembic configuration
- Extension configuration and discovered extensions

## How It Works

### MigrationManager Architecture

The migration system is built around an instance-based `MigrationManager` class that handles both core and extension migrations:

```python
# Create a MigrationManager instance
manager = MigrationManager(
    test_mode=False, 
    custom_db_info=None,
    extensions_dir="extensions",  # Configurable
    database_dir="database"       # Configurable
)

# Run operations
manager.run_alembic_command("upgrade", "head")
manager.create_extension_migration("my_extension", "Add new tables", auto=True)
manager.run_all_migrations("upgrade", "head")

# Advanced operations
manager.regenerate_migrations(all_extensions=True)
manager.debug_environment()
```

**Key Features:**
- **Instance-based architecture**: Each MigrationManager instance maintains its own configuration and state
- **Test mode support**: Pass `test_mode=True` to use `test_versions` directories and isolated test databases
- **Custom database configuration**: Override default database settings with `custom_db_info` parameter
- **Automatic cleanup**: Temporary files (alembic.ini, env.py, script.py.mako) are automatically created and cleaned up
- **Extension isolation**: Each extension uses separate version tables (`alembic_version_{extension_name}`)

### Database Model Structure

The system isolates tables based on their source files:

1. Core tables: Defined in `src/logic/BLL_*.py` files
2. Extension tables: Defined in `src/extensions/<extension_name>/BLL_*.py` files

When generating migrations, the system identifies which tables belong to which module, ensuring proper separation:

- Core migrations only include changes to core tables
- Extension migrations only include changes to tables owned by that extension
- Core tables extended by extensions (via `@extension_model`) are included in the extending extension's migrations

### Migration History

Each extension maintains its own independent migration history through:

1. A unique branch label (`ext_<extension_name>`) - only used for the first migration
2. A dedicated version table (`alembic_version_ext_<extension_name>`)

This ensures that:
- Extension migrations can be applied or removed without affecting core migrations
- Core table migrations are never dependent on extension migrations
- Extensions can evolve independently
- Subsequent extension migrations don't re-create branch labels

### Migration File Generation

The system dynamically generates necessary configuration and template files for each migration operation:

**Temporary File Management:**
- `alembic.ini`: Generated dynamically with appropriate configuration for core or extension migrations
- `script.py.mako`: Created temporarily in the migrations directory when needed for revision generation
- `env.py`: Copied to extension directories when needed, then cleaned up automatically

**Configuration Generation:**
- Core migrations use `get_default_alembic_ini_dict()` with appropriate database URL and version directory
- Extension migrations use `get_extension_alembic_ini_dict()` with extension-specific settings:
  - Separate version table: `alembic_version_{extension_name}`
  - Extension-specific script location
  - Proper branch labeling for the first migration only

**Automatic Cleanup:**
All temporary files are automatically removed after each operation completes, ensuring clean directory structure while preserving migration history and model files.

### Import Statements in Environment Files

Both the core and extension environment files (env.py) import from the MigrationManager class using the following pattern:

```python
# Import MigrationManager class from Migration.py
from database.migrations.Migration import MigrationManager

# Setup paths before importing anything else
paths = MigrationManager.env_setup_python_path(Path(__file__).resolve())
```

Then throughout the env.py file, the static methods are called with the class prefix:

```python
# Examples of using static methods
# Import database configuration from Base.py
from database.DatabaseManager import get_database_info
db_info = get_database_info()
db_type, db_name, db_url = db_info["type"], db_info["name"], db_info["url"]

module = MigrationManager.env_import_module_safely("module.path")
tables_tagged = MigrationManager.env_tag_tables_with_extension(module, extension_name)
```

This approach ensures all environment files use the same implementation of these functions while maintaining proper encapsulation within the Base module.

### Database Model Registration

To ensure proper isolation and table ownership:

1. Each BLL model file should follow the naming convention: `BLL_*.py`
2. Models inherit from `ApplicationModel` and `DatabaseMixin` (Pydantic2SQLAlchemy system)
3. Core models should be in `src/logic/BLL_*.py`
4. Extension models should be in `src/extensions/<extension_name>/BLL_*.py`
5. To extend core models, use the `@extension_model(CoreModel)` decorator pattern
6. SQLAlchemy tables are auto-generated from Pydantic models via the `.DB` property

## Shared Functions Architecture

The migration system uses a hybrid architecture combining instance-based management with shared static methods:

### MigrationManager Class Structure

1. **Instance Methods**: Core migration operations that maintain state and configuration
   - `run_alembic_command()`: Execute alembic commands with proper environment setup
   - `create_extension_migration()`: Create migrations for specific extensions
   - `run_all_migrations()`: Run migrations for core and all extensions
   - `cleanup_extension_files()`: Clean up temporary files after operations
   - `debug_environment()`: Show detailed configuration information

2. **Static Methods with `env_` Prefix**: Shared utilities for environment file operations
   - `MigrationManager.env_setup_python_path()`: Sets up the Python path for imports
   - `MigrationManager.env_import_module_safely()`: Handles module imports with multiple strategies
   - `MigrationManager.env_is_table_owned_by_extension()`: Determines table ownership by extension
   - `MigrationManager.env_include_object()`: Filters objects for inclusion in migrations
   - `MigrationManager.env_setup_alembic_config()`: Configures Alembic settings consistently

### Environment File Integration

The `env.py` files (both core and extension-generated copies) import and use the static methods:

```python
from database.migrations.Migration import MigrationManager

# Setup paths before importing anything else
paths = MigrationManager.env_setup_python_path(Path(__file__).resolve())

# Use shared filtering logic
def include_object(object, name, type_, reflected, compare_to):
    return MigrationManager.env_include_object(
        object, name, type_, reflected, compare_to, Base
    )
```

This architecture provides:
- **Consistency**: Both core and extension migrations use identical logic
- **Maintainability**: Common functionality is centralized in static methods
- **Flexibility**: Instance methods handle configuration and state management
- **Code Reuse**: Static methods are shared across all environment contexts

## Implementation Details

The migration system has been optimized for maintainability and robustness through several architectural improvements:

### Enhanced Logging System

A log file is generated/appended to in `src/database/migrations` whenever a migration command is executed.
- **Consistent Log Context**: Improved contextual information in log messages
- **Exception Tracebacks**: Full tracebacks for better debugging
- **Log Levels**: Proper use of debug, info, warning, and error levels


### Improved Error Handling
- **Exception Handling**: All operations now have proper try/except blocks
- **Fallback Mechanisms**: Graceful recovery from failures with fallback strategies
- **Transaction Safety**: Better handling of database transaction failures
- **Cleanup Guarantee**: Resource cleanup now occurs even when operations fail

### Thread-Safe Migration Execution
- **Atomic File Locking**: The migration system uses atomic file creation (`O_CREAT | O_EXCL`) to prevent race conditions when multiple processes/threads attempt to run migrations simultaneously
- **Database-Specific Locks**: Lock files are named `.migration.{db_name}.lock` to allow parallel migrations for different databases while preventing concurrent migrations on the same database
- **Timeout Handling**: If the lock cannot be acquired within 60 seconds, the system attempts to force-remove stuck locks and retry
- **Automatic Lock Release**: The lock is always released in a `finally` block, ensuring cleanup even on exceptions

### Centralized Configuration
- **Centralized State**: All configuration is managed by the MigrationManager
- **Environment Variable Handling**: More robust parsing of environment variables
- **Validation**: Better validation of configuration parameters
- **Detailed Logging**: Configuration details are logged for easier troubleshooting

### Utility Functions
- **File Operations**: Enhanced file management with better error handling
- **Path Normalization**: More reliable path handling
- **Subprocess Execution**: Improved command execution with detailed logging
- **Database Connection**: More reliable database connection handling

### Migration Operations
- **Command Retry Logic**: Automatic retry for failed commands in specific scenarios
- **Resource Management**: Better management of temporary files
- **Progress Reporting**: More detailed progress information
- **Error Recovery**: Improved handling of common migration failure scenarios

These architectural improvements maintain all functionality while making the codebase more maintainable, robust, and easier to troubleshoot.

## Common Issues

1. **Empty migrations for extensions:**
   - Ensure extension name is correctly set in `APP_EXTENSIONS` environment variable
   - Check that the extension BLL models are properly defined with `DatabaseMixin`
   - Verify models inherit from `ApplicationModel` and have `.DB` property accessible
   - Use `python src/database/migrations/Migration.py debug` to see configured extensions

2. **Migration not detecting table changes:**
   - Verify BLL model files follow `BLL_*.py` naming convention
   - Ensure models are in correct location (`src/logic/` for core, `src/extensions/<name>/` for extensions)
   - Check that models properly inherit from `ApplicationModel` and `DatabaseMixin`
   - Confirm SQLAlchemy models are generated (access `.DB` property doesn't raise errors)

3. **Import errors during migration:**
   - Ensure all required dependencies are installed (especially `stringcase`)
   - Run `pip install -r requirements.txt --use-pep517` to fix build issues with legacy packages
   - Check that all BLL model imports resolve correctly

## Programmatic Access to Migrations

When you need to run migrations programmatically from your application code, use the `MigrationManager` class directly. Here are examples:

### In app.py

When migrations are invoked from app.py, they will use `src/database/database.db` as the database.

```python
from database.migrations.Migration import MigrationManager

def setup_database():
    # Create MigrationManager instance and run migrations
    manager = MigrationManager()
    if not manager.run_all_migrations("upgrade", "head"):
        logger.error("Failed to apply migrations.")
        raise Exception("Failed to apply migrations.")
```

### In conftest.py

When migrations are invoked from test code, use test mode with isolated database configuration.

```python
from database.migrations.Migration import MigrationManager

def setup_test_database():
    # Create MigrationManager instance in test mode with custom database
    custom_db_info = {
        "type": "sqlite",
        "name": "database.test.migration",
        "url": "sqlite:///database.test.migration.db",
        "file_path": "database.test.migration.db"
    }
    
    manager = MigrationManager(test_mode=True, custom_db_info=custom_db_info)
    migration_result = manager.run_all_migrations("upgrade", "head")
    if not migration_result:
        logger.warning("Some migrations failed to apply, but continuing with tests")
```

## Cleanup

The migration system automatically cleans up temporary files after each command execution through several cleanup methods:

**Automatic Cleanup:**
- `cleanup_temporary_files()`: Removes temporary alembic.ini, env.py, and script.py.mako files
- `cleanup_extension_files()`: Cleans up temporary files from extension directories
- `cleanup_test_environment()`: Removes test_versions directories and test database files when in test mode

**Files Automatically Cleaned:**
- `alembic.ini` - Temporary configuration file
- `env.py` - Temporary environment script (in extensions)
- `script.py.mako` - Temporary migration template

**Preservation Policy:**
1. Preserves all files in the `versions/` and `test_versions/` directories
2. Preserves all migration files (`*.py`)
3. Preserves all model files (`BLL_*.py`)
4. Only removes the specific temporary files listed above
5. Cleanup happens automatically after every command, even on error

## Best Practices

1. **Follow naming conventions:** Use `BLL_*.py` for business logic/model files (core: `src/logic/`, extensions: `src/extensions/<name>/`)
2. **Use Pydantic2SQLAlchemy patterns:** Models should inherit from `ApplicationModel` and `DatabaseMixin`
3. **Extension independence:** Minimize cross-extension dependencies
4. **Model extensions:** Use `@extension_model(CoreModel)` decorator to extend core tables from extensions
5. **Version control migrations:** Never modify migrations applied to production
6. **Descriptive messages:** Clearly describe what each migration does
7. **Environment configuration:** Ensure `APP_EXTENSIONS` lists all extensions before running migrations
8. **Test migrations:** Always test on non-production data first
9. **Run all migrations on deploy:** Execute both core and extension migrations during deployment
10. **Instance-based architecture:** Create MigrationManager instances with appropriate `test_mode` and `custom_db_info` parameters