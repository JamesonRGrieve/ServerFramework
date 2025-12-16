# Database Extension

This document describes the Database extension implementation.

> **Extension Architecture**: For general extension patterns, architecture, and concepts, see [EXT.Patterns.md](../EXT.Patterns.md).

The Database extension provides comprehensive database connectivity for AGInfrastructure, supporting multiple database types through the Provider Rotation System for failover and load balancing.

## Overview

The `database` extension enables interaction with various database systems including relational (PostgreSQL, MySQL, SQLite), document (MongoDB), time-series (InfluxDB), and graph (GraphQL) databases. It provides a unified interface for database operations while supporting database-specific features.

## Architecture

### Extension Class Structure
```python
class EXT_Database(AbstractStaticExtension):
    name: str = "database"
    version: str = "1.0.0"
    description: str = "Database extension providing comprehensive database connectivity via Provider Rotation System"
    
    # Static abilities
    _abilities: Set[str] = {
        "database_query",
        "database_schema", 
        "database_chat",
        "sql_execution",
        "data_storage",
        "relational_db",
        "nosql_db",
        "time_series_db",
    }
```

### Database Type Classifications

The extension categorizes databases into the following types:

| Classification | Database Types |
|----------------|---------------|
| Relational | PostgreSQL, MySQL, MariaDB, Microsoft SQL Server, SQLite |
| Document | MongoDB |
| Time-Series | InfluxDB |
| Graph | GraphQL |
| Vector | PostgreSQL (with pgvector extension) |

## Provider System

The Database extension uses the Provider Rotation System to manage multiple database connections:

### Abstract Provider
```python
class AbstractProvider_Database(AbstractStaticProvider):
    """Abstract provider for database operations."""
    extension_type: ClassVar[str] = "database"
    
    @abstractmethod
    @ability("execute_sql")
    async def execute_sql(self, query: str, **kwargs) -> str:
        """Execute SQL query - must be implemented by concrete providers."""
        pass
        
    @abstractmethod  
    @ability("get_schema")
    async def get_schema(self, **kwargs) -> str:
        """Get database schema - must be implemented by concrete providers."""
        pass
```

### Concrete Providers

The extension includes providers for:
- **SQLite** (`PRV_SQLite`) - Serverless file-based database
- **InfluxDB** (`PRV_InfluxDB`) - Time-series database
- **PostgreSQL** - Relational database with vector support
- **MySQL** - Popular relational database
- **MongoDB** - Document database
- **GraphQL** - Graph query interface

## Dependencies

### PIP Dependencies
- `psycopg2-binary>=2.9.0` - PostgreSQL support
- `pymongo>=4.0.0` - MongoDB support
- `influxdb>=5.3.0` - InfluxDB 1.x support
- `influxdb-client>=1.36.0` - InfluxDB 2.x support
- `gql>=3.4.0` - GraphQL support
- `requests>=2.28.0` - HTTP-based connections

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_TYPE` | `"sqlite"` | Type of database to use |
| `DATABASE_HOST` | `""` | Database host address |
| `DATABASE_PORT` | `""` | Database port (defaults vary by type) |
| `DATABASE_NAME` | `""` | Database name |
| `DATABASE_USERNAME` | `""` | Database username |
| `DATABASE_PASSWORD` | `""` | Database password |
| `DATABASE_FILE` | `""` | SQLite database file path |
| `INFLUXDB_VERSION` | `"2"` | InfluxDB version (1 or 2) |
| `INFLUXDB_ORG` | `""` | InfluxDB organization |
| `INFLUXDB_TOKEN` | `""` | InfluxDB authentication token |
| `INFLUXDB_BUCKET` | `""` | InfluxDB bucket name |

## Abilities

The Database extension provides the following abilities through the Provider Rotation System:

### Core Abilities
- **execute_sql** - Execute SQL queries in relational databases
- **get_schema** - Retrieve database schema information
- **chat_with_db** - Natural language database queries
- **execute_query** - Execute database-specific queries (InfluxQL, MongoDB queries, etc.)
- **write_data** - Write data to time-series databases

### Usage Examples

```python
# Execute SQL using rotation system
result = await EXT_Database.root.rotate(
    EXT_Database.execute_sql,
    query="SELECT * FROM users WHERE active = true"
)

# Get database schema
schema = await EXT_Database.root.rotate(
    EXT_Database.get_schema
)

# Natural language query
response = await EXT_Database.root.rotate(
    EXT_Database.chat_with_db,
    request="Show me all users who signed up last month"
)
```

## Provider-Specific Features

### PostgreSQL with pgvector
When pgvector extension is enabled, PostgreSQL supports vector similarity searches:

```python
# Vector search capability
result = await postgres_provider.vector_search(
    table="embeddings",
    column="embedding", 
    vector=[0.1, 0.2, 0.3, ...],
    metric="cosine",
    limit=10
)
```

### InfluxDB
Time-series data operations:

```python
# Write time-series data
await influxdb_provider.write_data(
    measurement="temperature",
    tags={"sensor": "A1", "location": "room1"},
    fields={"value": 23.5},
    timestamp="2024-01-01T00:00:00Z"
)
```

### GraphQL
GraphQL query execution:

```python
# Execute GraphQL query
result = await graphql_provider.execute_graphql("""
    query {
        users {
            id
            name
            posts {
                title
            }
        }
    }
""")
```

## Default Ports

The extension provides default ports for each database type:
- PostgreSQL: 5432
- MySQL/MariaDB: 3306  
- Microsoft SQL Server: 1433
- MongoDB: 27017
- InfluxDB: 8086
- GraphQL: 4000
- SQLite: N/A (file-based)

## Health Monitoring

The extension provides health checking capabilities:

```python
health_status = EXT_Database.check_health()
# Returns:
# {
#     "overall_healthy": true,
#     "providers": {
#         "sqlite": {"healthy": true, "issues": []},
#         "influxdb": {"healthy": false, "issues": ["No token configured"]}
#     },
#     "timestamp": "2024-01-01T00:00:00"
# }
```

## Seed Data

The extension automatically registers database providers based on environment configuration:
- SQLite provider when `DATABASE_TYPE=sqlite` and `DATABASE_FILE` is set
- InfluxDB provider when InfluxDB environment variables are configured
- PostgreSQL provider when `DATABASE_TYPE=postgresql` and connection details are provided
- MySQL provider when `DATABASE_TYPE=mysql` and connection details are provided

## Security Features

### Required Permissions
- `database:query` - Execute queries
- `database:schema` - View schema
- `database:write` - Write data
- `database:admin` - Administrative operations

### Connection Security
- Encrypted connections supported for all network databases
- Credential management through environment variables
- Provider instance bonding for secure API access

## Testing

The extension includes comprehensive test coverage for:
- Provider discovery and registration
- Database operations through rotation system
- Health monitoring
- Configuration validation

### Running Tests
```bash
# Run all database extension tests
pytest src/extensions/database/ -v

# Run specific provider tests
pytest src/extensions/database/PRV_SQLite_test.py -v
```

## Best Practices

### For Developers
1. Use the Provider Rotation System for all database operations
2. Implement proper error handling for database failures
3. Use parameterized queries to prevent SQL injection
4. Test with multiple database providers

### For Operations
1. Configure appropriate connection pools
2. Set up health monitoring for critical databases
3. Use read replicas for query load distribution
4. Implement proper backup strategies

### Security Considerations
1. Use encrypted connections for network databases
2. Implement least-privilege database users
3. Rotate database credentials regularly
4. Monitor for suspicious query patterns