# Framework Architecture

This FastAPI-based server framework provides a comprehensive, production-ready foundation for building scalable API applications with a unique Pydantic-first approach that eliminates dual schema maintenance while providing enterprise-grade features.

## Core Architecture

### Layered Design
The framework follows a strict layered architecture with clear separation of concerns:

- **Library Layer (`lib/`)**: Foundation utilities for configuration, dependencies, model management, and logging
- **Database Layer (`DB_*.py`)**: SQLAlchemy models automatically generated from Pydantic schemas with declarative base isolation
- **Business Logic Layer (`BLL_*.py`)**: Pydantic-first schema design with comprehensive CRUD operations and hook support
- **Endpoint Layer (`EP_*.py`)**: Automatic FastAPI router generation from BLL managers with authentication and documentation
- **Extension System (`EXT_*.py`)**: Modular plugin architecture with auto-discovery and isolated migrations
- **Provider System (`PRV_*.py`)**: External API integration with failover support and rotation capabilities

### Revolutionary Patterns
- **Pydantic-First Schema Design**: Business models defined in Pydantic automatically generate SQLAlchemy database models, eliminating dual schema maintenance and ensuring single source of truth
- **RORO Pattern**: All methods follow "Receive Object, Return Object" for consistency and type safety
- **Comprehensive Hook System**: Type-safe before/after method hooks with priority ordering enable cross-cutting concerns
- **Model Registry Pattern**: Isolated model management allowing multiple applications with different model sets
- **Automatic Router Generation**: BLL managers with RouterMixin eliminate manual endpoint creation
- **Extension Isolation**: Each extension maintains independent migrations and configuration

## Database Layer

### Management
- **DatabaseManager**: Thread-safe connection pooling with multi-database support (PostgreSQL, SQLite, MariaDB, MSSQL, Vector)
- **Declarative Base Isolation**: Each DatabaseManager instance maintains separate declarative base
- **Migration System**: Alembic-based with core and extension-specific migrations, automatic dependency resolution

### Permissions
- **Role-Based Access Control**: Team-scoped role hierarchies with granular resource permissions
- **SQL-Level Filtering**: Permission enforcement at database query level for security
- **Dynamic Authorization**: Context-aware permission validation with inheritance patterns

### Patterns
- **Entity Mixins**: Reusable model components for common patterns (timestamps, soft delete, etc.)
- **Seeding System**: Intelligent seed data with automatic dependency resolution and dynamic discovery
- **Search Architecture**: Flexible search transformation patterns with custom filtering

## Business Logic Layer

### Core Abstractions
- **AbstractBLLManager**: Base class providing standardized CRUD operations with hook support
- **AbstractService**: Background service lifecycle management with database access
- **Model Mixins**: Reusable Pydantic model components for common entity patterns

### Schema Design
- **Pydantic-First**: Business models defined in Pydantic automatically generate SQLAlchemy database models
- **Three-Model Pattern**: Entity (core), Reference (relationships), Network (API schemas) models per entity
- **Automatic Binding**: ModelRegistry system handles Pydantic-SQLAlchemy integration

### Authentication
- **JWT-Based**: Token authentication with root API key for system entities
- **User Management**: Complete user lifecycle with team/role management
- **Invitation System**: Team invitation workflow with role assignment

### Service Layer
- **Background Services**: Long-running services with lifecycle management and error handling
- **Configuration**: Environment-based service configuration with validation
- **Database Integration**: Service-level database access patterns with transaction management

## Endpoint Layer

### REST Patterns
- **AbstractEPRouter**: Automatic CRUD endpoint generation from BLL managers
- **Authentication Types**: Flexible authentication strategies (JWT, API key, optional)
- **Nested Resources**: Support for hierarchical resource relationships
- **Example Generation**: Automatic API documentation examples using field pattern recognition

### GraphQL Integration
- **Automatic Mapping**: Pydantic models automatically converted to GraphQL schemas
- **Dynamic Schema**: Runtime schema generation with type safety
- **Real-Time Subscriptions**: WebSocket-based real-time data updates
- **Unified Queries**: Single endpoint for complex data retrieval patterns

## Extension System

### Architecture
- **Static Classes**: Extensions implemented as static/abstract classes without instantiation
- **Auto-Discovery**: Filesystem-based discovery of extensions and providers
- **Modular Installation**: Extensions can be enabled/disabled via environment variables
- **Independent Migrations**: Each extension maintains its own migration path

### Provider Rotation
- **External API Management**: Unified interface for external service integration
- **Failover Support**: Automatic provider rotation on failure
- **AbstractExternalModel**: Standardized external API integration patterns
- **Configuration Management**: Provider-specific configuration with validation

### Core Extensions
- **auth_mfa**: Multi-factor authentication (TOTP, email, SMS)
- **email**: SendGrid integration with template support
- **payment**: Stripe payment processing with subscription management
- **database**: Multi-database support with natural language querying

## Framework Design Philosophy

### Extension-Only Implementation Pattern
A core principle of this framework is that **any implementation should be achieved by ONLY creating one or more new folders in the `extensions/` directory**. This ensures that updates to the core framework can be merged without conflicts.

**Key Benefits:**
- **Zero-Conflict Updates**: Core framework updates never conflict with custom implementations
- **Clean Separation**: Custom code completely separated from framework code
- **Version Control Safety**: Core framework and custom extensions maintain independent histories
- **Modular Architecture**: All domain logic packaged as isolated, reusable extensions
- **Easy Maintenance**: Extensions can be added, removed, or updated independently

**Implementation Guidelines:**
1. **Never modify core framework files** - All customization goes in `extensions/`
2. **Create new extension folders** - Each feature/domain gets its own extension directory
3. **Use extension patterns** - Leverage EXT_*, BLL_*, DB_*, EP_*, PRV_* patterns
4. **Maintain extension independence** - Extensions should be self-contained modules
5. **Follow extension structure** - Use standard extension file organization

**Extension Structure Example:**
```
extensions/
└── my_feature/                # Your custom implementation
    ├── EXT_MyFeature.py       # Extension definition
    ├── BLL_MyModel.py         # Business logic and Pydantic models
    ├── EP_MyEndpoints.py      # API endpoints (or use RouterMixin)
    ├── PRV_MyProvider.py      # External service provider (if needed)
    └── versions/              # Extension-specific migrations
        └── 001_initial.py
```

**What This Means:**
- ✅ Add new extensions in `extensions/` directory
- ✅ Extend existing models using `@extension_model` decorator
- ✅ Register hooks in extensions for cross-cutting concerns
- ✅ Create extension-specific migrations in extension `versions/` folder
- ❌ Avoid modifying files in `src/lib/`, `src/database/`, `src/logic/`, `src/endpoints/`
- ❌ Avoid direct modifications to core migrations

**Update Strategy:**
- Fork/branch the core framework for your implementation (expected pattern)
- Keep all custom code in `extensions/` directory only
- When framework updates are released, merge them into your fork
- **Zero merge conflicts** if you only modified/added files in `extensions/`
- **Merge conflicts only occur** if you modified core framework files

This design ensures that your custom implementations remain isolated from framework updates, enabling seamless upgrades while preserving all custom functionality. By keeping customizations exclusively in extensions, framework updates can be merged without conflicts.

## Development Principles

### Code Organization
- **Extension-Only Development**: All implementations in `extensions/` directory for conflict-free updates
- **UUID Primary Keys**: Consistent UUID usage across all entities
- **Relative Imports**: All imports relative to `src/` directory
- **Early Error Handling**: Fail fast with FastAPI HTTPExceptions at database layer
- **No Mocking**: Real functionality testing without mocks

### Performance
- **Connection Pooling**: Database connection management for scalability
- **Parallel Testing**: Concurrent test execution with isolation
- **Lazy Loading**: On-demand component loading and initialization
- **Caching Strategies**: Built-in caching for frequently accessed data

### Security
- **Permission Enforcement**: SQL-level permission filtering
- **JWT Security**: Secure token-based authentication
- **Input Validation**: Comprehensive Pydantic validation
- **SQL Injection Prevention**: SQLAlchemy ORM protection

## Library Foundation

### Configuration Management
- **Type-Safe Settings**: Pydantic-based AppSettings with environment variable validation
- **Runtime Registration**: Extensions can register configuration variables dynamically
- **Domain Extraction**: Robust URI/email parsing for multi-format domain handling
- **Inflection Engine**: Consistent naming transformations across the framework

### Dependency System
- **Multi-Platform Support**: System package management across APT, Homebrew, WinGet, Chocolatey, and Snap
- **Python Package Management**: PIP dependency handling with version constraint validation
- **Extension Dependencies**: Automatic loading order resolution with circular dependency detection
- **Requirements.txt Integration**: Validation against existing requirements with conflict detection

### Model Utilities
- **Introspection System**: Comprehensive Pydantic model analysis with relationship discovery
- **Forward Reference Resolution**: String type annotation resolution across modules
- **Schema Generation**: Recursive schema creation with circular reference handling
- **NetworkMixin**: Dynamic REST API model generation from Pydantic models

### Logging Infrastructure
- **Custom Log Levels**: Extended hierarchy including VERBOSE and SQL levels
- **Environment Configuration**: LOG_LEVEL driven output control
- **Structured Output**: Consistent formatting across all framework components
- **Performance Optimization**: Minimal overhead with level-based filtering

## Framework Benefits

### Developer Productivity
- **Minimal Boilerplate**: Automatic generation of database models, API endpoints, and documentation
- **Type Safety**: End-to-end type checking from API to database through Pydantic
- **Declarative Configuration**: Define behavior through class attributes and decorators
- **Comprehensive Testing**: Abstract test classes provide complete coverage patterns

### Scalability & Performance
- **Connection Pooling**: Efficient database connection management
- **Lazy Loading**: On-demand component initialization
- **Parallel Testing**: Concurrent test execution with isolation
- **Caching Strategies**: Built-in caching at multiple levels

### Maintainability
- **Single Source of Truth**: Pydantic models drive entire stack
- **Clear Separation**: Layered architecture with defined responsibilities
- **Extension Isolation**: Plugins can be added/removed without core changes
- **Comprehensive Documentation**: Integrated with code generation

This framework represents a paradigm shift in API development, eliminating repetitive tasks while maintaining flexibility for complex requirements through its innovative Pydantic-first approach and comprehensive automation capabilities.