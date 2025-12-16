## Key Directives / Rules
### DO, ALWAYS:
- If functionality won't work without a parameter, it should be a required positional one without a default, not an optional one with an inline check.
- Any time you modify functionality in a file, ensure the accompanying `_test.py` file contains comprehensive tests for the modification WITHOUT MOCKS, as well as ensuring you update any relevant `.md` documentation in the same directory that references the code you changed.
- Write concise code (avoid obvious comments and use one-liners where possible).
- When requested to perform an implementation or refactor, critically analyze the requirements and ask any and all necessary clarifying questions to ensure your complete understanding of the goal.

### DO NOT, EVER, UNDER ANY CIRCUMSTANCE:
- Make assumptions, respond with "is likely", "probably" or "might be".
- Use frame-local or thread-local variables instead of passing data via parameters.
- Skip a failing test instead of fixing the root issue.
- Fix broken functionality and keep the broken functionality as a fallback instead of just implementing proper functionality.
- Re-implement existing functionality in a second location to bypass it instead of fixing the original implementation.
- Use bandaid fixes instead of fixing the core functionality.
- Mock functionality for testing instead of testing real functionality. Mocking functionality for testing is a cancer that needs to be excised - it defeats the entire point of tests.

### Python Syntax Guidelines:
- Always import the children of `datetime` I.E. `from datetime import date` - NEVER `import datetime` and `datetime.date`.
- All imports should be relative to ./src - this means NEVER `from src.x import y` - ALWAYS `from x import y`
- 
### Documentation Guidelines:
- Markdown documentation should be concise and written in a manner in which you could reconstruct the described code therefrom with 95% accuracy but with minimal snippets. It should be a clear architectural summary, not usage examples (that's what Swagger and Strawberry are for).

## Running Tests
When presented with a "_test" file, run the file using the command:

`source ./.venv.linux/bin/activate && python -m pytest (your test path) -v --lf` 

and repair any deficiencies starting with the easiest, most common or "lowest hanging fruit".

## Development Commands

### Basic Operations
- **Start the application**: `python src/app.py` (handles virtual environment setup automatically)
- **Run tests**: `pytest` (use specific test markers for targeted testing)
- **Format code**: `black src/` (configured with 88-character line length)
- **Type checking**: `mypy src/` (if available in dev dependencies)

### Testing
- **Test isolation**: Each test uses isolated database instances with proper cleanup

### Database Operations
- **Apply migrations**: Handled automatically on application startup
- **Generate migrations**: Use the Migration framework in `src/database/migrations/`
- **Database setup**: Automated through `DatabaseManager` with support for SQLite/PostgreSQL

## Architecture Overview

This is a **FastAPI-based server framework** with a layered architecture designed for scalability and modularity. The codebase follows strict separation of concerns with an extension-based plugin system.

### Core Architecture Layers

1. **Database Layer (DB_*.py)**: SQLAlchemy ORM models with declarative base
2. **Business Logic Layer (BLL_*.py)**: Core business logic managers with CRUD operations
3. **Endpoint Layer (EP_*.py)**: FastAPI router implementations with automatic CRUD endpoint generation
4. **Extensions (EXT_*.py)**: Plugin system for adding modular functionality
5. **Providers (PRV_*.py)**: Standardized interfaces to external services

### Key Architectural Patterns

- **RORO Pattern**: All methods follow "Receive Object, Return Object" pattern
- **Hook System**: Comprehensive before/after hook support for all BLL operations
- **Model Registry**: Centralized Pydantic model management with automatic SQLAlchemy binding
- **Declarative Base Isolation**: Each DatabaseManager instance has its own declarative base
- **Multi-Database Support**: PostgreSQL, SQLite, MariaDB, MSSQL, and Vector databases

### File Naming Conventions

- `DB_*.py`: Database models (SQLAlchemy)
- `BLL_*.py`: Business logic managers 
- `EP_*.py`: API endpoint routers
- `EXT_*.py`: Extension modules
- `PRV_*.py`: Provider interfaces
- `*_test.py`: Test files (use abstract test base classes)

### Model Architecture

Each entity follows a three-model pattern:
- **Entity Model**: Core attributes and validation
- **Reference Model**: Relationships to other entities  
- **Network Model**: API schemas (POST, PUT, SEARCH, Response classes)

### Extension System

Extensions are modular plugins that can be enabled/disabled via `APP_EXTENSIONS` environment variable. Each extension:
- Provides its own migrations in `extensions/{name}/migrations/`
- Can define PIP dependencies for automatic installation
- Follows the same layered architecture (DB/BLL/EP/EXT files)
- Inherits from `AbstractExtension` base class

### Testing Framework

- **Abstract Test Classes**: Use provided abstract test base classes instead of creating new ones
- **Database Isolation**: Each test gets its own database instance with automatic cleanup
- **Hook Testing**: Comprehensive hook system testing with `@hook_bll` decorator
- **Parallel Execution**: Tests run in parallel by default using pytest-xdist
- **Never skip failing tests** - fix the root cause instead

### Authentication & Authorization

- **JWT-based authentication** with root API key for mutation of system entities
- **Role-based permissions** with team-scoped role heirarchies
- **System entities** require root API key authentication for write operations
- **User context** automatically injected into all BLL operations

### Key Development Principles

- Use UUID primary keys throughout (String type for SQLite, UUID for PostgreSQL)
- Handle errors at the beginning of functions with early raises, throwing FastAPI HTTPExceptions, as close to the database as possible
- All BLL managers support hook registration for extensibility
- Follow the existing search transformer pattern for custom search functionality
- Use the ModelRegistry system for proper Pydantic/SQLAlchemy integration
- Database migrations are automatically applied on startup
