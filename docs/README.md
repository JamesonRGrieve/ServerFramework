# JamesonRGrieve's Server Framework

![Generation Paths](./Generation.png)

## Documentation Viewing

### Recommended: Obsidian

This documentation is best viewed using [Obsidian](https://obsidian.md/) with the custom plugin included in this repository. The plugin automatically hides folders without documentation, providing a clean, focused view of all available documentation.

**Setup:**
1. Install [Obsidian](https://obsidian.md/)
2. Open this repository as an Obsidian vault
3. The custom plugin (`hide-folders-without-md`) will automatically activate
4. Navigate through the documentation using Obsidian's graph view and linked references

### Alternative: Traditional Navigation

Documentation can also be viewed directly in your text editor or GitHub, though you won't benefit from the cross-referencing and visualization features that Obsidian provides.

## Documentation Directory

### Framework Overview
- **[src/Framework.md](../src/Framework.md)** - Comprehensive framework architecture overview
- **[src/Framework.Test.md](../src/Framework.Test.md)** - Testing philosophy and patterns

### Core Library Components
- **[src/lib/LIB.Overview.md](../src/lib/LIB.Overview.md)** - Library components overview and integration
- **[src/lib/LIB.Environment.md](../src/lib/LIB.Environment.md)** - Configuration and environment management
- **[src/lib/LIB.Dependencies.md](../src/lib/LIB.Dependencies.md)** - System, Python, and extension dependency management
- **[src/lib/LIB.Pydantic.md](../src/lib/LIB.Pydantic.md)** - Model utilities and registry management
- **[src/lib/LIB.Pydantic2FastAPI.md](../src/lib/LIB.Pydantic2FastAPI.md)** - Automatic FastAPI router generation
- **[src/lib/LIB.Logging.md](../src/lib/LIB.Logging.md)** - Centralized logging system

### Database Layer
- **[src/database/DB.Management.md](../src/database/DB.Management.md)** - Database management and configuration
- **[src/database/DB.Patterns.md](../src/database/DB.Patterns.md)** - Database design patterns and mixins
- **[src/database/DB.Permissions.md](../src/database/DB.Permissions.md)** - Permission system architecture
- **[src/database/DB.Seeding.md](../src/database/DB.Seeding.md)** - Data seeding and initialization
- **[src/database/DB.Test.md](../src/database/DB.Test.md)** - Database testing patterns

### Business Logic Layer
- **[src/logic/BLL.Patterns.md](../src/logic/BLL.Patterns.md)** - Business logic patterns and best practices
- **[src/logic/BLL.Abstraction.md](../src/logic/BLL.Abstraction.md)** - Abstract BLL manager functionality
- **[src/logic/BLL.Authentication.md](../src/logic/BLL.Authentication.md)** - Authentication system implementation
- **[src/logic/BLL.Hooks.md](../src/logic/BLL.Hooks.md)** - Hook system architecture and usage
- **[src/logic/BLL.Schema.md](../src/logic/BLL.Schema.md)** - Pydantic schema patterns
- **[src/logic/BLL.Test.md](../src/logic/BLL.Test.md)** - Business logic testing patterns
- **[src/logic/SVC.Patterns.md](../src/logic/SVC.Patterns.md)** - Background service patterns
- **[src/logic/SVC.Test.md](../src/logic/SVC.Test.md)** - Service testing patterns

### Endpoint Layer
- **[src/endpoints/EP.Patterns.md](../src/endpoints/EP.Patterns.md)** - API endpoint patterns and usage
- **[src/endpoints/EP.Abstraction.md](../src/endpoints/EP.Abstraction.md)** - Abstract endpoint router
- **[src/endpoints/EP.ExampleFactory.md](../src/endpoints/EP.ExampleFactory.md)** - Automatic example generation
- **[src/endpoints/EP.GQL.md](../src/endpoints/EP.GQL.md)** - GraphQL integration
- **[src/endpoints/EP.Schema.md](../src/endpoints/EP.Schema.md)** - API schema patterns
- **[src/endpoints/EP.Test.md](../src/endpoints/EP.Test.md)** - Endpoint testing patterns

### Extension System
- **[src/extensions/EXT.md](../src/extensions/EXT.md)** - Extension system architecture
- **[src/extensions/PRV.md](../src/extensions/PRV.md)** - Provider rotation system

### Migration System
- **[src/database/migrations/DB.Migrations.md](../src/database/migrations/DB.Migrations.md)** - Database migration patterns

## Quick Start

### Installation
```sh
git clone git@github.com:JamesonRGrieve/ServerFramework.git
cd ServerFramework
```

### Requirements
- Python 3.10+
```sh
pip install -r requirements.txt
python3 src/app.py
```

### Basic Configuration
```
APP_NAME=MyApp
SERVER_URI=http://localhost:1996
APP_EXTENSIONS=email,auth_mfa,database,payment
```

## Documentation Philosophy

1. **Architectural Focus**: Documentation describes the "why" and "how" of components, not just the "what"
2. **Minimal Code Snippets**: Code examples are minimal; the documentation focuses on patterns and concepts
3. **Cross-Referenced**: Heavy use of links between related documentation
4. **Layer Separation**: Documentation organized by architectural layer
5. **Pattern-Based**: Emphasis on reusable patterns over specific implementations

## Contributing to Documentation

When adding new documentation:

1. Follow the existing naming convention: `LAYER.Component.md`
2. Focus on architectural decisions and patterns
3. Link to related documentation using relative paths
4. Keep code snippets minimal and focused
5. Include "Best Practices" sections where appropriate

## Framework Benefits

This framework provides:

- **Pydantic-First Design**: Single source of truth for all schemas
- **Zero Boilerplate**: Automatic generation of database models, endpoints, and documentation
- **True Testing**: No mocks, real implementations with proper isolation
- **Extension Architecture**: Modular plugin system with isolated migrations
- **Type Safety**: End-to-end type checking from API to database

For a comprehensive overview, start with [Framework.md](../src/Framework.md)