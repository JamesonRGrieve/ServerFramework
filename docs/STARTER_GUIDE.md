# Quick Start && Requirements

### Clone the repo:

`git clone https://git.zephyrex.dev/ZephyrexTechnologies/ServerFramework.git`

### Basic Operations
- **Start the application**: `python -m zephyrex run` (after `pip install -e ".[dev]"`; serves on port 1996)
- **Run tests**: `pytest` should be configured to discover tests through the VS Code test explorer
- **Format code**: `black src/` (configured with 88-character line length)
- **Type checking**: `mypy src/` (if available in dev dependencies)

### Advanced Documentation 

There is this obsidian vault with an extension that hides empty folders pre-installed, so the layer specific docs are easy to find and are formatted nicely.

# Backend Architecture and Idioms

This is a FastAPI, SQLAlchemy and Strawberry based server framework with a layered architecture designed for scalability and modularity. The codebase follows strict separation of concerns with an extension-based plugin system.

The goal of the project is to have common sense defaults for each of these 3 core package implementations, with the option to override any of them through ClassVar in `logic`, developers building implementations should **only have to create one or more extensions** (folders in the `extensions` folder) and should not have to touch any other files in order to achieve any reasonable custom functionality. 

## Key Architectural Patterns

- **RORO Pattern**: All methods follow "Receive Object, Return Object" pattern
- **Hook System**: Comprehensive before/after hook support for all BLL operations
- **Model Registry**: Centralized Pydantic model management with automatic SQLAlchemy binding
- **Mixin System**: Common behaviors like `DatabaseMixin`, `RouterMixin`, `RotationMixin`, and `ApplicationModel` are used to extend logic, add CRUD operations, and connect models to the core runtime without inheritance bloat.
- **Registry Pattern**: Static registries (e.g. `ExtensionRegistry`, `ModelRegistry`) are used to auto-discover and coordinate classes, avoiding manual wiring and enabling loose coupling with high discoverability.

## Files Naming Convention

| Prefix   | Meaning                 |
| -------- | ----------------------- |
| `DB_`    | Database                |
| `BLL_`   | Business Logic          |
| `EP_`    | API Endpoints           |
| `EXT_`   | Extensions              |
| `PRV_`   | Providers               |
| `SVC_`   | Services                |
| `*_test` | Tests                   |

# Endpoints

- **REST Docs**: http://localhost:1996/docs 
- **GQL Docs**: http://localhost:1996/graphql  


