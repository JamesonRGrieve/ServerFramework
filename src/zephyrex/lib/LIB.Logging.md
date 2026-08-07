# Logging System

## Overview
Centralized logging system built on Loguru with custom log levels, environment-based configuration, timezone formatting, and structured output for comprehensive application monitoring and debugging.

## Core Components

### Logger Configuration (`Logging.py`)
Loguru-based logging system with custom levels and environment-driven configuration.

**Key Features:**
- Custom log level definitions beyond standard levels
- Environment variable configuration (`LOG_LEVEL` and `TZ`)
- Structured output to stdout with timezone formatting
- Color-coded log levels for readability
- Global logger instance for framework-wide use

### Custom Log Levels

#### Extended Level Hierarchy
Additional log levels for granular logging control.

**Level Mapping:**
- **CRITICAL**: 50 - System failure conditions
- **ERROR**: 40 - Error conditions requiring attention
- **WARNING**: 30 - Warning conditions
- **INFO**: 20 - Informational messages
- **DEBUG**: 10 - Debug information
- **VERBOSE**: 5 - Detailed debug information (blue)
- **SQL**: 3 - SQL query logging (magenta)
- **NOTSET**: 0 - All messages

#### Level Configuration
Dynamic level configuration with color assignment for enhanced readability.

**Features:**
- Color-coded output for visual distinction
- Numeric level assignment for filtering
- Framework-specific levels for specialized logging
- Consistent level hierarchy across modules

### Environment Integration

#### LOG_LEVEL Configuration
Environment variable-driven log level selection with intelligent defaults.

**Configuration:**
- Environment variable: `LOG_LEVEL`
- Default fallback handling
- Dynamic level adjustment
- Runtime level modification support

#### Timezone Formatting
Automatic timezone conversion for log timestamps based on server configuration.

**Features:**
- Server timezone configuration via `TZ` environment variable
- UTC to local timezone conversion for display
- ZoneInfo integration for accurate timezone handling
- Preserves UTC internally while displaying local time

### Framework Integration

#### Global Logger Access
Centralized logger instance accessible throughout the framework.

**Usage Pattern:**
```python
from zephyrex.lib.Logging import logger
logger.info("Application started")
logger.debug("Processing request")
logger.sql("SELECT * FROM users")
```

#### Module-Level Logging
Consistent logging patterns across all framework modules.

**Features:**
- Standardized import pattern
- Consistent message formatting
- Contextual logging levels
- Error tracking integration

### Specialized Logging

#### SQL Query Logging
Dedicated logging level for database query monitoring.

**Features:**
- Magenta color coding for visibility
- SQL-specific log level (3)
- Query performance tracking
- Database debugging support

#### Verbose Debug Logging
Enhanced debug logging for detailed troubleshooting.

**Features:**
- Blue color coding for distinction
- Verbose level (5) below standard debug
- Detailed execution flow tracking
- Development environment optimization

### Performance Considerations

#### Efficient Logging
Optimized logging configuration for minimal performance impact.

**Optimizations:**
- Lazy evaluation of log messages
- Level-based filtering
- Minimal overhead for disabled levels
- Efficient output handling

#### Production Configuration
Production-optimized logging configuration with appropriate filtering.

**Features:**
- Higher minimum log levels in production
- Structured output for log aggregation
- Error-focused logging in production
- Performance monitoring integration

## Integration Patterns

### Application Startup
Logger configuration during application initialization with environment-based setup.

### Error Handling
Consistent error logging across all framework components with proper context and stack traces.

### Database Operations
SQL query logging for performance monitoring and debugging with query-specific formatting.

### Extension System
Extension-specific logging with proper categorization and filtering capabilities.

## Usage Guidelines

### Log Level Selection
**CRITICAL**: System failures, security incidents
**ERROR**: Application errors, exceptions
**WARNING**: Potential issues, deprecated usage
**INFO**: General application flow, important events
**DEBUG**: Development debugging, detailed flow
**VERBOSE**: Extremely detailed debugging
**SQL**: Database queries, performance data

### Message Formatting
Consistent message formatting across the framework with structured information and contextual data.

### Context Information
Include relevant context in log messages such as user IDs, request IDs, operation types, and error details.

### Performance Impact
Monitor logging performance impact in production environments with appropriate level filtering and efficient message construction.

## Best Practices

1. **Level Appropriateness**: Use appropriate log levels for different types of information
2. **Message Clarity**: Write clear, actionable log messages with sufficient context
3. **Performance Awareness**: Avoid expensive operations in log message construction
4. **Structured Data**: Include structured information for log analysis tools
5. **Error Context**: Provide full context for errors including stack traces when appropriate
6. **Production Filtering**: Use appropriate log levels in production to avoid noise
7. **Monitoring Integration**: Design log messages for effective monitoring and alerting

## Structured Application Logging Contract

Every log line carries a `correlation_id` populated from `RequestContext.correlation_id` — set by inbound middleware, derived from the `traceparent` header when present, otherwise minted. `correlation_id` propagates across `asyncio.to_thread` and `asyncio.create_task` via a context-var copying helper the framework supplies (`contextvars.copy_context().run(fn)` for `to_thread`, a wrapper around `create_task` that snapshots context).

### Log Level Taxonomy

- `DEBUG` — developer-only, off in production.
- `INFO` — operational milestones (startup, shutdown, config-load, retention pass complete).
- `WARNING` — degraded behavior; dashboard signal (rotation skip, optional dependency missing, replica unhealthy).
- `ERROR` — operator should be alerted; the request failed but the process continues.
- `CRITICAL` — process-level failure; reserved for the framework's own startup-validation failures.

Application-log retention defaults to 30 days, configurable per deployment. This is independent of audit-log retention which lives on `meta_logging` events.

### `ErrorReporter`

A pluggable `ErrorReporter` ABC accepts `report(exception, context)` calls. Framework-shipped adapters cover Sentry, Rollbar, and a no-op default. Every uncaught exception in a request handler, every `blocking=True` hook failure, every `failed`-state service crash routes through this surface in addition to being logged. The reporter receives the exception, the originating `RequestContext` snapshot, and the current `correlation_id`.

### No `print()` in non-test source

`print()` calls in production source bypass the logging layer, miss `correlation_id` correlation, and (for response-body prints in particular) leak PII into logs. There are no `print()` calls in non-test source; the `logger.debug(...)` / `logger.info(...)` / etc. surface is the only valid emission path.