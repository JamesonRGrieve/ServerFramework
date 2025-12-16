# Framework Testing Architecture

The framework provides a revolutionary testing approach that eliminates mocking entirely, instead using real implementations with proper isolation to ensure tests validate actual behavior rather than assumptions. This comprehensive testing framework uses abstract base classes to provide standardized patterns across all architectural layers.

## Testing Philosophy

### Revolutionary No-Mock Approach
Unlike traditional testing frameworks that rely heavily on mocks and stubs, this framework takes a fundamentally different approach:

- **Real Functionality**: Every test uses actual implementations, ensuring tests catch real issues
- **Database Isolation**: Each extension permutation receives a completely isolated database instance with automatic cleanup
- **End-to-End Validation**: Complete request-response cycles are tested as they would occur in production
- **Parallel Execution**: Advanced isolation allows tests to run concurrently without interference
- **Deterministic Results**: Real implementations with controlled environments ensure consistent results

### Architectural Test Patterns
The testing framework mirrors the main architecture with specialized abstract base classes:

- **Abstract Base Classes**: Inheritance-based patterns ensure consistent testing across all components
- **Automatic Discovery**: Standard `*_test.py` naming enables automatic test collection
- **Layered Testing**: Each architectural layer has dedicated test abstractions matching its patterns
- **Hook System Testing**: Comprehensive validation of the framework's powerful hook system
- **Extension Testing**: Isolated testing environments for each extension's functionality

## Database Layer Testing

### AbstractDBTest
- **CRUD Operations**: Standardized Create, Read, Update, Delete testing patterns
- **Permission Validation**: Role-based access control testing with SQL-level filtering
- **Entity Relationships**: Foreign key and relationship constraint testing
- **Migration Testing**: Database schema evolution and rollback validation
- **Seeding Validation**: Seed data integrity and dependency resolution testing

### Database Isolation
- **Extension Permutation Databases**: Each extension permutation gets its own isolated database instance
- **Automatic Cleanup**: Database teardown after extension test suite completion
- **Transaction Rollback**: Transaction isolation within test suites where applicable
- **Schema Consistency**: Validation of schema generation from Pydantic models

## Business Logic Layer Testing

### AbstractBLLTest
- **Manager Testing**: Comprehensive BLL manager functionality validation
- **CRUD Operations**: Business logic validation for all entity operations
- **Batch Operations**: Multi-entity operation testing with transaction consistency
- **Hook System Testing**: Before/after hook execution and priority ordering
- **Validation Testing**: Pydantic model validation and error handling
- **Search Functionality**: Search transformer and filtering pattern testing

### AbstractSVCTest
- **Service Lifecycle**: Background service startup, operation, and shutdown testing
- **Error Handling**: Service-level error recovery and retry logic validation
- **Configuration Testing**: Environment-based service configuration validation
- **Database Integration**: Service-level database access pattern testing

### Authentication Testing
- **User Management**: Complete user lifecycle testing (creation, authentication, deletion)
- **Team/Role Management**: Role assignment and permission inheritance testing
- **JWT Validation**: Token generation, validation, and expiration testing
- **Invitation System**: Team invitation workflow and role assignment testing

## Endpoint Layer Testing

### AbstractEPTest
- **REST API Testing**: Complete HTTP method testing (GET, POST, PUT, DELETE)
- **Authentication Flows**: JWT and API key authentication testing
- **Request/Response Validation**: Schema validation for all endpoint interactions
- **Error Handling**: HTTP status code and error message validation
- **Field Projection Testing**: Field selection coverage automatically skips relationship navigation fields to keep assertions focused on scalar payloads
- **Nested Resource Testing**: Hierarchical resource relationship testing
- **Pagination Testing**: Large dataset pagination and filtering validation

### GraphQL Testing
- **Schema Generation**: Dynamic GraphQL schema creation from Pydantic models
- **Query Execution**: Complex GraphQL query testing with nested relationships
- **Mutation Testing**: GraphQL mutation operations with validation
- **Subscription Testing**: Real-time subscription functionality validation
- **Type Safety**: GraphQL type system consistency with Pydantic models

## Extension System Testing

### AbstractEXTTest
Configuration-driven extension testing with isolated test server instances.

**Extension Test Isolation:**
- Each extension gets a dedicated test server: `test.{extension_name}.database.db`
- Server runs with **only that extension** (and its dependencies) loaded via `APP_EXTENSIONS`
- Complete database and environment isolation from other extensions
- Automatic cleanup after extension test suite completion

**Test Server Creation:**
```python
class TestMyExtension(AbstractEXTTest):
    extension_class = EXT_MyExtension
    test_config = AbstractEXTTest.full_config(
        expected_abilities={"my_ability"}
    )

    # Fixtures provided by ExtensionServerMixin:
    # - server: Isolated TestClient for extension
    # - model_registry: Extension's isolated ModelRegistry
    # - extension_db: Database session for extension
    # - admin_a, team_a: Test user/team fixtures
    # - admin_b, team_b, user_b, mod_b: Additional test fixtures
```

**Configuration-Driven Tests:**
- Test types control which tests run: STRUCTURE, METADATA, DEPENDENCIES, ABILITIES, ENVIRONMENT, ROTATION, PERFORMANCE, CONCURRENCY, MODEL_REGISTRY, DATABASE_ISOLATION
- Parameterized tests execute based on configuration
- Performance thresholds customizable per extension
- Skip flags for optional test categories

**Key Test Areas:**
- **Structure Validation**: Required attributes, properties, and methods
- **Metadata Validation**: Name, version, description format
- **Dependencies**: Sys, pip, and extension dependency resolution
- **Abilities**: Ability discovery and registration
- **Model Registry**: Isolated registry per extension
- **Database Isolation**: Unique database prefix per extension
- **Rotation System**: Root rotation manager functionality
- **Performance Metrics**: Caching effectiveness, concurrent access
- **Hook System**: Ability and hook decorator validation

**Shared Fixtures from ExtensionServerMixin (src/extensions/AbstractEXTTest.py:61-168):**
```python
@pytest.fixture(scope="module")
def server(self):
    """Create isolated test server for the extension."""
    extension_name = self.extension_class.name.lower()
    test_db_prefix = f"test.{extension_name}"
    extension_list = extension_name  # Only this extension loaded

    app = instance(db_prefix=test_db_prefix, extensions=extension_list)
    client = TestClient(app)
    yield client
```

**Test Configuration Patterns:**
```python
# Basic test configuration (minimal)
test_config = AbstractEXTTest.basic_config()

# Full test configuration (comprehensive)
test_config = AbstractEXTTest.full_config(
    expected_abilities={"ability1", "ability2"}
)

# Performance-focused configuration
test_config = AbstractEXTTest.performance_config()

# Custom configuration
test_config = AbstractEXTTest.create_config(
    test_types={ExtensionTestType.STRUCTURE, ExtensionTestType.ABILITIES},
    expected_abilities={"custom_ability"},
    skip_rotation=True,
    skip_performance=True
)
```

### AbstractPRVTest
Configuration-driven provider testing with extension-inherited test isolation.

**Provider Test Inheritance:**
- Providers inherit test environment from parent extension
- Same database prefix: `test.{parent_extension}.database.db`
- Same extension loading: `APP_EXTENSIONS={parent_extension}`
- Providers tested within parent extension's isolated environment
- Tests defined in AbstractPRVTest (src/extensions/AbstractPRVTest.py:341-793)

**Test Server Fixtures:**
```python
class TestMyProvider(AbstractPRVTest):
    provider_class = PRV_MyProvider_MyExtension
    test_config = AbstractPRVTest.full_config(
        expected_abilities={"provider_ability"},
        expected_services={"service_name"}
    )

    # Fixtures available:
    # - extension_server: Parent extension's test server
    # - extension_db: Parent extension's database
```

**Key Test Areas:**
- **Structure Validation**: Provider attributes, methods, and properties
- **Metadata Validation**: Name and description
- **Dependencies**: Provider-specific dependency validation
- **Abilities**: Ability declaration and discovery
- **Services**: Service listing and availability
- **Environment Variables**: Configuration validation
- **Rotation Integration**: Integration with parent extension's rotation manager
- **Performance**: Caching and concurrent access patterns
- **Error Handling**: Provider failure scenarios
- **Instance Bonding**: `bond_instance()` implementation validation

**Test Configuration Patterns:**
```python
# Basic provider test configuration
test_config = AbstractPRVTest.basic_config()

# Full provider test configuration
test_config = AbstractPRVTest.full_config(
    expected_abilities={"api_call", "webhook"},
    expected_services={"payment_processing"}
)

# Performance-focused configuration
test_config = AbstractPRVTest.performance_config()

# GraphQL testing for external models
graphql_config = AbstractPRVTest.create_graphql_config(
    entity_name="customer",
    model_class=CustomerModel,
    test_types={GraphQLTestType.QUERY_SINGLE, GraphQLTestType.MUTATION_CREATE}
)
```

**Extension vs Provider Test Hierarchy:**
```
Extension Test (AbstractEXTTest):
┌────────────────────────────────────────┐
│ Database: test.my_extension.database.db │
│ Server: APP_EXTENSIONS=my_extension    │
│ Tests: Extension structure, abilities   │
└────────────────────────────────────────┘
                ↓ (inherited by)
Provider Test (AbstractPRVTest):
┌────────────────────────────────────────┐
│ Database: Same as parent extension     │
│ Server: Same as parent extension       │
│ Tests: Provider-specific functionality │
└────────────────────────────────────────┘
```

### Core Extension Testing
- **MFA Testing**: Multi-factor authentication flow validation (TOTP, email, SMS)
- **Email Testing**: SendGrid integration and template rendering testing
- **Payment Testing**: Stripe payment processing and subscription management testing
- **Database Testing**: Multi-database support and natural language query testing

## Test Execution Patterns

### Pytest Integration
- **Parallel Execution**: pytest-xdist for concurrent test execution
- **Test Markers**: Category-based test organization (`-m db`, `-m bll`, `-m ep`, `-m auth`)
- **Fixture Management**: Database setup and teardown fixtures
- **Test Discovery**: Automatic test file and method discovery

### Test Data Management
- **Seed Data Testing**: Validation of seed data integrity and relationships
- **Factory Patterns**: Test data generation with realistic examples
- **Cleanup Strategies**: Automatic test data cleanup after execution
- **Isolation Guarantees**: No test data contamination between tests

### Performance Testing
- **Load Testing**: API endpoint performance under load
- **Database Performance**: Query optimization and index validation
- **Memory Testing**: Memory usage patterns and leak detection
- **Concurrency Testing**: Multi-user scenario validation

## Testing Commands

### Basic Test Execution
```bash
# Run all tests
pytest

# Run specific test markers
pytest -m db        # Database tests
pytest -m bll       # Business logic tests
pytest -m ep        # Endpoint tests
pytest -m auth      # Authentication tests

# Run single test file
pytest path/to/test_file.py

# Run specific test method
pytest path/to/test_file.py::test_method_name
```

### Advanced Testing
```bash
# Parallel test execution
pytest -n auto

# Verbose output
pytest -v

# Coverage reporting
pytest --cov=src

# Test with specific database
pytest --db=postgresql
```

## Quality Assurance

### Test Coverage Requirements
- **Minimum Coverage**: 90% code coverage across all layers
- **Critical Path Coverage**: 100% coverage for authentication and permission systems
- **Edge Case Testing**: Comprehensive error condition and boundary testing
- **Integration Testing**: End-to-end workflow validation

### Continuous Integration
- **Pre-commit Hooks**: Automatic test execution before commits
- **Branch Protection**: Tests must pass before merging
- **Performance Benchmarks**: Performance regression testing
- **Security Testing**: Automated security vulnerability scanning

## Library Foundation Testing

### Configuration Testing
- **Environment Validation**: Type-safe configuration testing with invalid value handling
- **Runtime Registration**: Dynamic configuration variable registration validation
- **Domain Parsing**: Comprehensive URI/email parsing test cases
- **Settings Inheritance**: Configuration cascade and override testing

### Dependency Testing
- **Multi-Platform Validation**: Cross-platform package manager testing
- **Version Constraints**: Semantic version compatibility testing
- **Conflict Resolution**: Dependency conflict detection and resolution
- **Installation Simulation**: Mock-free installation testing with rollback

### Model Utility Testing
- **Introspection Validation**: Model field and relationship discovery testing
- **Reference Resolution**: Forward reference and circular dependency testing
- **Schema Generation**: Complex schema creation with edge case handling
- **Registry Isolation**: Multiple registry instance testing

### Integration Testing
- **Component Integration**: Cross-layer functionality validation
- **Extension Integration**: Plugin system integration testing
- **Performance Testing**: Load testing and resource usage validation
- **Security Testing**: Authentication, authorization, and input validation

## Testing Benefits

### Confidence in Production
- **Real Behavior Validation**: Tests verify actual implementation behavior
- **Production Parity**: Test environment closely mirrors production
- **Comprehensive Coverage**: All code paths tested with real scenarios
- **Early Bug Detection**: Issues caught before deployment

### Developer Experience
- **Clear Test Patterns**: Abstract base classes provide consistent structure
- **Fast Feedback**: Parallel execution reduces test runtime
- **Debugging Support**: Real implementations make debugging straightforward
- **Test as Documentation**: Tests demonstrate proper usage patterns

### Maintenance Advantages
- **No Mock Maintenance**: Eliminates brittle mock updates
- **Refactoring Safety**: Tests catch breaking changes immediately
- **Regression Prevention**: Comprehensive test suite prevents regressions
- **Living Documentation**: Tests always reflect current behavior

This testing approach represents a fundamental shift from traditional mock-heavy testing to a more reliable, maintainable system that validates real behavior, providing unprecedented confidence in code quality and production readiness.
