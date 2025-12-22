# Migration Testing Framework

> **Common testing resources:** [Framework.Test.md](../../Framework.Test.md) | **Database testing:** [DB.Test.md](../DB.Test.md)

## Overview

The migration testing framework provides comprehensive testing for Alembic database migrations. Tests are organized into three categories based on test scope and complexity.

## Test Organization

```
src/database/migrations/
├── Migration_meta_test.py   # Lightweight infrastructure tests
├── Migration_mock_test.py   # Mock-based migration flow tests
├── Migration_real_test.py   # Full integration migration tests
└── Migration.Test.md        # This documentation
```

## Test Categories

### Meta Tests (`Migration_meta_test.py`)

Lightweight tests that validate migration infrastructure without requiring full database setup:

- **File cleanup functionality** - Tests temporary file management
- **Extension configuration parsing** - Tests CSV extension list parsing
- **Command validation** - Tests migration command argument validation
- **Environment setup** - Tests Python path and module import
- **Alembic configuration** - Tests configuration structure and templates

```python
class TestMigrationMeta:
    """Lightweight meta tests that test migration infrastructure without full setup"""

    def test_cleanup_files(self):
        """Test basic file cleanup functionality."""

    def test_command_validation_no_message(self):
        """Test command validation when no message is provided."""

    def test_env_setup_alembic_config(self):
        """Test Alembic configuration setup."""
```

### Mock Tests (`Migration_mock_test.py`)

Tests migration workflows using mock databases and configurations:

- **Migration generation** - Tests revision creation
- **Upgrade/downgrade flows** - Tests migration application
- **Extension migrations** - Tests extension-specific migrations
- **Error handling** - Tests failure scenarios

### Real Tests (`Migration_real_test.py`)

Full integration tests against actual databases:

- **End-to-end migration** - Complete upgrade/downgrade cycles
- **Data integrity** - Validates data preservation
- **Multi-database support** - Tests SQLite and PostgreSQL

## Test Fixtures

### Meta Test Setup

```python
def setup_method(self, method):
    """Lightweight setup for meta tests"""
    self.temp_dir = Path(tempfile.mkdtemp())
    self.created_files = []
    self.created_dirs = []

def teardown_method(self, method):
    """Clean up temporary test files"""
    if self.temp_dir.exists():
        shutil.rmtree(self.temp_dir)
```

## Key Test Methods

### Command Validation

```python
def test_command_validation_no_message(self):
    """Test that revision commands require a message."""
    def validate_revision_command(command_args):
        if "revision" in command_args and "-m" not in command_args:
            return False, "Error: --message is required"
        return True, ""
```

### Configuration Testing

```python
def test_abstract_configuration(self):
    """Test that the abstract class configuration works correctly."""
    config = {
        "test_type": "meta",
        "create_mock_extensions": False,
        "matrix_targets": ["core"],
        "test_variations": {...}
    }
```

### Alembic Configuration

```python
def test_env_setup_alembic_config(self):
    """Test Alembic configuration setup."""
    config_dict = {
        "script_location": "migrations",
        "sqlalchemy.url": "sqlite:///test.db",
        "file_template": "%%(year)d_%%(month).2d_%%(day).2d_...",
    }
```

### Template Validation

```python
def test_get_script_py_mako_template(self):
    """Test script.py.mako template content."""
    template_content = '''"""${message}"""
revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}

def upgrade():
    ${upgrades if upgrades else "pass"}

def downgrade():
    ${downgrades if downgrades else "pass"}
'''
```

## Extension Migrations

### Extension Configuration

```python
def test_get_extension_alembic_ini_dict(self):
    """Test extension-specific Alembic INI configuration."""
    ext_name = "test_extension"
    ext_config = {
        "alembic": {
            "script_location": f"extensions/{ext_name}/migrations",
        }
    }
```

### CSV Extension Parsing

```python
def test_get_configured_extensions(self):
    """Test extension configuration parsing."""
    test_cases = [
        ("", []),
        ("ext1", ["ext1"]),
        ("ext1,ext2", ["ext1", "ext2"]),
        ("ext1, ext2, ext3", ["ext1", "ext2", "ext3"]),
    ]
```

## Error Handling Tests

```python
def test_error_handling_missing_message(self):
    """Test error handling for missing message parameter."""

def test_error_handling_invalid_command(self):
    """Test error handling for invalid commands."""
    valid_commands = ["revision", "upgrade", "downgrade", "current", "history"]
```

## Running Migration Tests

```bash
# Run all migration tests
pytest src/database/migrations/ -v

# Run only meta tests (fast)
pytest src/database/migrations/Migration_meta_test.py -v

# Run mock tests
pytest src/database/migrations/Migration_mock_test.py -v

# Run real integration tests
pytest src/database/migrations/Migration_real_test.py -v

# Run specific test
pytest src/database/migrations/Migration_meta_test.py::TestMigrationMeta::test_cleanup_files -v
```

## Best Practices

### Test Isolation

- Meta tests use temporary directories for file operations
- Mock tests use in-memory databases where possible
- Real tests use transaction rollback for cleanup

### File Management

```python
# Track files for cleanup
self.created_files.append(temp_file)
self.created_dirs.append(test_subdir)

# Cleanup in teardown
for file in self.created_files:
    if file.exists():
        file.unlink()
```

### Environment Variables

```python
def test_env_parse_csv_env_var(self):
    """Test parsing CSV environment variables."""
    result = [item.strip() for item in csv_string.split(",") if item.strip()]
```

## Configuration Reference

### Alembic INI Structure

```python
default_config = {
    "alembic": {
        "script_location": "migrations",
        "file_template": "%%(year)d_%%(month).2d_%%(day).2d_%%(hour).2d%%(minute).2d-%%(rev)s_%%(slug)s",
    },
    "loggers": {
        "keys": "root,sqlalchemy,alembic",
    },
}
```

### Context Configuration

```python
context_config = {
    "target_metadata": None,
    "literal_binds": True,
    "dialect_opts": {"paramstyle": "named"},
}
```

## Test Matrix

| Test Category | Database | Speed | Isolation |
|--------------|----------|-------|-----------|
| Meta | None | Fast | Full |
| Mock | In-memory | Medium | Full |
| Real | SQLite/PostgreSQL | Slow | Transaction |

## Integration with CI

Migration tests are typically run in CI with the following strategy:

1. **Meta tests** - Run on every commit (fast feedback)
2. **Mock tests** - Run on PR creation
3. **Real tests** - Run on merge to main branch
