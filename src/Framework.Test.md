# Framework Testing Architecture

The framework provides a revolutionary testing approach that eliminates mocking entirely, instead using real implementations with proper isolation to ensure tests validate actual behavior rather than assumptions. This comprehensive testing framework uses abstract base classes to provide standardized patterns across all architectural layers.

## Testing Philosophy

### Revolutionary No-Mock Approach
Unlike traditional testing frameworks that rely heavily on mocks and stubs, this framework takes a fundamentally different approach:

- **Real Functionality**: Every test uses actual implementations, ensuring tests catch real issues
- **Database Isolation**: Each extension permutation receives a completely isolated database instance with automatic cleanup
- **End-to-End Validation**: Complete request-response cycles are tested as they would occur in production
- **Parallel Execution**: Advanced isolation allows tests to run concurrently using pytest async/threading
- **Deterministic Results**: Real implementations with controlled environments ensure consistent results

**Philosophy Clarification**: The "no mocking" philosophy applies to:
- BLL manager tests (use real database)
- Endpoint tests (use real server + database)
- Extension tests (use isolated real environments)

Unit testing of pure utility functions (SQL filter generation, permission calculations) may use mocks for isolation, as these test pure logic without side effects.

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
Configuration-driven extension testing with isolated test servers.

**Isolation:**
- Each extension: dedicated database `test.{extension_name}.database.db`
- Server loads only that extension + dependencies via `APP_EXTENSIONS`
- Automatic cleanup after suite

**Usage:**
```python
class TestMyExtension(AbstractEXTTest):
    extension_class = EXT_MyExtension
    test_config = AbstractEXTTest.full_config(expected_abilities={"my_ability"})

    # Fixtures: server, model_registry, extension_db, admin_a, team_a, etc.
```

**Test Types:**
STRUCTURE, METADATA, DEPENDENCIES, ABILITIES, ENVIRONMENT, ROTATION, PERFORMANCE, CONCURRENCY, MODEL_REGISTRY, DATABASE_ISOLATION

**Config Patterns:**
```python
test_config = AbstractEXTTest.basic_config()  # Minimal
test_config = AbstractEXTTest.full_config(expected_abilities={...})  # Comprehensive
test_config = AbstractEXTTest.performance_config()  # Performance focus
test_config = AbstractEXTTest.create_config(test_types={...}, skip_rotation=True)  # Custom
```

### AbstractPRVTest
Provider testing inherits parent extension's test environment (same DB, same server).

**Usage:**
```python
class TestMyProvider(AbstractPRVTest):
    provider_class = PRV_MyProvider_MyExtension
    test_config = AbstractPRVTest.full_config(
        expected_abilities={"provider_ability"},
        expected_services={"service_name"}
    )
    # Fixtures: extension_server, extension_db
```

**Test Areas:**
Structure, metadata, dependencies, abilities, services, environment, rotation integration, performance, error handling, instance bonding

**Config Patterns:**
```python
test_config = AbstractPRVTest.basic_config()
test_config = AbstractPRVTest.full_config(expected_abilities={...}, expected_services={...})
test_config = AbstractPRVTest.performance_config()
graphql_config = AbstractPRVTest.create_graphql_config(entity_name="...", model_class=...)
```

**Hierarchy:**
```
Extension Test → Provider Test (inherits DB + server)
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

## Testing Tools and Framework

### Core Testing Stack
All layers use the following tools:
- **pytest**: Primary testing framework with automatic test discovery
- **pytest-cov**: Code coverage reporting
- **pytest-xdist**: Parallel test execution
- **pytest-dependency**: Test dependency management

### Testing Commands

#### Basic Test Execution
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

# Run specific test class
pytest path/to/test_file.py::TestClassName
```

#### Advanced Testing
```bash
# Parallel test execution
pytest -n auto

# Verbose output
pytest -v

# Very verbose output with all details
pytest -vv

# Coverage reporting
pytest --cov=src

# Coverage with HTML report
pytest --cov=src --cov-report=html

# Test with specific database
pytest --db=postgresql

# Stop on first failure
pytest -x

# Show local variables in tracebacks
pytest -l
```

### Common Test Fixtures

Standard fixtures available across all test layers:
- **server**: Test server instance with isolated environment
- **db**: Database session for test operations
- **model_registry**: Pydantic model registry instance
- **admin_a**, **admin_b**: Admin user instances for testing
- **user_a**, **user_b**: Regular user instances for testing
- **team_a**, **team_b**: Team instances for testing

### Best Practices

#### Test Design
- Extend appropriate AbstractTest class for your layer
- Provide required overrides (class_under_test, create_fields, update_fields)
- Use descriptive test method names indicating behavior being tested
- Keep tests focused on single behavior or scenario
- Use `pytest.mark.dependency` *sparingly* — only for short vertical chains where one test's failure makes the rest meaningless (e.g., `install_pip → bond → send` in provider tests). Avoid cross-file or cross-module dependency markers; they couple unrelated test files and slow failure discovery instead of expediting it.
- Prefer parametrization over per-case test methods. Abstract security matrices (e.g., `EMAIL_SECURITY_DENY_MATRIX` in `extensions/AbstractPRVTest.py`) let new providers inherit dozens of denial tests without per-class duplication.

#### Data Management
- Use provided fixtures for consistent test data
- Clean up test data after execution
- Avoid hardcoded values - use parametrization
- Test with realistic data scenarios

#### Error Testing
- Test both success and failure paths
- Verify error messages and status codes
- Test edge cases and boundary conditions
- Validate error handling behavior

#### Execution
- Ensure tests can run independently
- Avoid cross-test dependencies unless explicitly marked
- Use markers to categorize tests by layer or feature
- Keep test execution time reasonable

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
