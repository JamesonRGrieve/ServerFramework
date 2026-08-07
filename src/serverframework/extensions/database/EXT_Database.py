from abc import abstractmethod
from typing import Any, ClassVar, Dict, List, Optional, Set, Type

from serverframework.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from serverframework.lib.Dependencies import Dependencies, PIP_Dependency
from serverframework.lib.Logging import logger
from serverframework.lib.Pydantic import classproperty


class AbstractDatabaseExtensionProvider(AbstractStaticProvider):
    """Abstract base class for database service providers."""

    extension: ClassVar[Optional[Type[AbstractStaticExtension]]] = None

    # Provider metadata
    name: str = ""  # Must be overridden by subclasses
    friendly_name: str = ""  # Must be overridden by subclasses
    description: str = ""  # Must be overridden by subclasses

    # Database type for this provider
    db_type: str = ""  # Must be overridden by subclasses

    # Abilities provided by all database providers
    _abilities: Set[str] = {
        "database",
        "sql",
        "data_storage",
    }

    # Environment variables this provider needs
    _env: Dict[str, Any] = {}  # Override in subclasses

    @classmethod
    @abstractmethod
    def bond_instance(cls, config: Dict[str, Any]) -> None:
        """Configure the provider with the given configuration."""

    @classmethod
    @abstractmethod
    async def execute_sql(cls, query: str, **kwargs) -> str:
        """Execute a custom SQL query in the database."""

    @classmethod
    @abstractmethod
    async def get_schema(cls, **kwargs) -> str:
        """Get the schema of the database."""

    @classmethod
    @abstractmethod
    async def chat_with_db(cls, request: str, **kwargs) -> str:
        """Chat with the database using natural language query."""

    @classmethod
    def get_db_type(cls) -> str:
        """Get the type of database this provider interacts with."""

        return cls.db_type

    @classmethod
    def get_db_classifications(cls) -> Set[str]:
        """Get the classifications for this database type."""

        if cls.extension and hasattr(cls.extension, "get_database_classifications"):
            try:
                db_type = cls.db_type.lower().split()[0]
                classifications = cls.extension.get_database_classifications(
                    db_type=db_type
                )
                return set(classifications.keys())
            except Exception as exc:
                logger.debug(
                    f"Error getting database classifications from extension for {cls.db_type}: {exc}"
                )

        db_type = cls.db_type.lower()
        if any(
            t in db_type
            for t in [
                "postgresql",
                "postgres",
                "mysql",
                "mariadb",
                "sqlite",
                "sql server",
                "mssql",
            ]
        ):
            return {"relational"}
        if "mongodb" in db_type:
            return {"document"}
        if "influxdb" in db_type:
            return {"time_series"}
        if "graphql" in db_type:
            return {"graph"}
        return set()

    @classmethod
    def get_abilities(cls) -> Set[str]:
        """Return the abilities this provider offers."""

        abilities = cls._abilities.copy()
        classifications = cls.get_db_classifications()
        if "relational" in classifications:
            abilities.add("relational_db")
        if "document" in classifications:
            abilities.add("nosql_db")
        if "time_series" in classifications:
            abilities.add("time_series_db")
        if "graph" in classifications:
            abilities.add("graph_db")
        return abilities

    @classmethod
    def validate_config(cls) -> List[str]:
        """Validate the provider configuration."""

        issues = []
        for key, default in cls._env.items():
            if not cls.get_env_value(key) and default == "":
                issues.append(f"{key} not configured")
        return issues

    @classmethod
    def get_provider_info(cls) -> Dict[str, Any]:
        """Get information about this provider."""

        return {
            "name": cls.name,
            "friendly_name": cls.friendly_name,
            "description": cls.description,
            "type": cls.db_type,
            "classification": list(cls.get_db_classifications()),
            "abilities": list(cls.get_abilities()),
        }


class EXT_Database(AbstractStaticExtension):
    """
    Database extension for AGInfrastructure.

    Provides database connectivity and functionality for various database systems including
    PostgreSQL, MySQL, SQLite, InfluxDB, MongoDB, and GraphQL. This extension uses the
    Provider Rotation System for failover and load balancing across multiple database
    providers.

    The extension focuses on:
    - Multi-database support (relational, document, time-series, graph) through provider rotation
    - SQL execution and schema management
    - Database chat and natural language query abilities
    - Database classification and type management
    - Integration with multiple database providers via rotation system

    Usage:
        # Execute SQL using rotation system
        result = await EXT_Database.root.rotate(
            EXT_Database.execute_sql,
            query="SELECT * FROM users"
        )
    """

    # Extension metadata (class attributes)
    name: str = "database"
    friendly_name: str = "Database Connectivity"
    version: str = "1.0.0"
    description: str = (
        "Database extension providing comprehensive database connectivity via Provider Rotation System"
    )

    # Provider discovery - cache for providers
    _providers: List[Type] = []

    # Environment variables exposed by the extension
    _env: Dict[str, Any] = {
        "DATABASE_TYPE": "sqlite",
        "DATABASE_HOST": "",
        "DATABASE_PORT": "",
        "DATABASE_NAME": "",
        "DATABASE_USERNAME": "",
        "DATABASE_PASSWORD": "",
        "DATABASE_FILE": "",
        "INFLUXDB_VERSION": "2",
        "INFLUXDB_ORG": "",
        "INFLUXDB_TOKEN": "",
        "INFLUXDB_BUCKET": "",
    }

    # Unified dependencies using the Dependencies class
    dependencies = Dependencies(
        [
            PIP_Dependency(
                name="psycopg2-binary",
                friendly_name="PostgreSQL Python Library (Binary)",
                optional=False,
                semver=">=2.9.0",
                reason="PostgreSQL database provider support (pre-compiled)",
            ),
            PIP_Dependency(
                name="pymongo",
                friendly_name="MongoDB Python Library",
                optional=False,
                semver=">=4.0.0",
                reason="MongoDB database provider support",
            ),
            PIP_Dependency(
                name="influxdb",
                friendly_name="InfluxDB Python Library",
                optional=False,
                semver=">=5.3.0",
                reason="InfluxDB 1.x database provider support",
            ),
            PIP_Dependency(
                name="influxdb-client",
                friendly_name="InfluxDB 2.x Python Library",
                optional=False,
                semver=">=1.36.0",
                reason="InfluxDB 2.x database provider support",
            ),
            PIP_Dependency(
                name="gql",
                friendly_name="GraphQL Core Library",
                optional=False,
                semver=">=3.4.0",
                reason="GraphQL database provider support",
            ),
            PIP_Dependency(
                name="requests",
                friendly_name="HTTP Requests Library",
                optional=False,
                semver=">=2.28.0",
                reason="HTTP-based database connections",
            ),
        ]
    )

    # Static abilities provided by the extension
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

    # Database type classifications
    DATABASE_TYPES = {
        "relational": [
            "postgres",
            "postgresql",
            "mysql",
            "mariadb",
            "mssql",
            "sqlserver",
            "sqlite",
        ],
        "document": ["mongodb"],
        "time_series": ["influxdb"],
        "graph": ["graphql"],
        "vector": ["postgres", "postgresql"],  # Postgres with pgvector extension
    }

    @classproperty
    def providers(cls) -> List[Type]:
        """
        Auto-discover all database providers in this extension's folder.
        Cached after first access.
        """
        if not cls._providers:
            # Use parent class's provider discovery implementation
            # This replaces the deprecated scoped_import usage
            cls._providers = super().providers
        return cls._providers

    @classmethod
    def get_default_port(cls, database_type: str) -> int:
        """
        Get default port for a database type.
        """
        database_type = database_type.lower()
        port_map = {
            "postgres": 5432,
            "postgresql": 5432,
            "mysql": 3306,
            "mariadb": 3306,
            "mssql": 1433,
            "sqlserver": 1433,
            "mongodb": 27017,
            "influxdb": 8086,
            "graphql": 4000,
            "sqlite": 0,  # Not used for SQLite
        }
        return port_map.get(database_type, 0)

    @classmethod
    def get_abilities(cls) -> Set[str]:
        """Return the abilities this extension provides."""
        abilities = cls._abilities.copy()

        # Add provider-specific abilities
        for provider_class in cls.providers():
            if hasattr(provider_class, "_abilities"):
                abilities.update(provider_class._abilities)

        return abilities

    @classmethod
    def has_ability(cls, ability: str) -> bool:
        """Check if this extension has a specific ability."""
        return ability in cls.get_abilities()

    @classmethod
    @ability("execute_sql")
    async def execute_sql(cls, query: str, **kwargs) -> str:
        """
        Execute a custom SQL query in the database.
        Uses provider rotation for failover.
        """
        return await cls.root.rotate(
            lambda provider: provider.execute_sql(query, **kwargs)
        )

    @classmethod
    @ability("get_schema")
    async def get_schema(cls, **kwargs) -> str:
        """
        Get the schema of the database.
        Uses provider rotation for failover.
        """
        return await cls.root.rotate(lambda provider: provider.get_schema(**kwargs))

    @classmethod
    @ability("chat_with_db")
    async def chat_with_db(cls, request: str, **kwargs) -> str:
        """
        Chat with the database using natural language query.
        Uses provider rotation for failover.
        """
        return await cls.root.rotate(
            lambda provider: provider.chat_with_db(request, **kwargs)
        )

    @classmethod
    @ability("execute_query")
    async def execute_query(cls, query: str, **kwargs) -> str:
        """
        Execute a database-specific query (e.g., InfluxQL, Flux, MongoDB query).
        Uses provider rotation for failover.
        """
        return await cls.root.rotate(
            lambda provider: provider.execute_query(query, **kwargs)
        )

    @classmethod
    @ability("write_data")
    async def write_data(cls, data: str, **kwargs) -> str:
        """
        Write data to the database (for time-series databases like InfluxDB).
        Uses provider rotation for failover.
        """
        return await cls.root.rotate(
            lambda provider: provider.write_data(data, **kwargs)
        )

    @classmethod
    def get_database_classifications(cls, db_type: str | None = None) -> Dict[str, List[str]]:
        """
        Get database type classifications. If db_type is provided, returns only the
        classifications for that specific database type.
        """
        if db_type:
            db_type = db_type.lower()
            result = {}
            for classification, types in cls.DATABASE_TYPES.items():
                if db_type in types:
                    result[classification] = [db_type]
            return result
        return cls.DATABASE_TYPES

    @classmethod
    def get_provider_names(cls) -> Set[str]:
        """Return available database provider names."""
        provider_names = set()
        for provider_class in cls.providers():
            if hasattr(provider_class, "name"):
                provider_names.add(provider_class.name)
        return provider_names

    @classmethod
    def on_startup(cls):
        """
        Called during application startup.
        """
        logger.debug("Database extension startup hook called")

        # Import the BLL hooks module to register them
        try:
            from serverframework.extensions.database import BLL_Database

            logger.debug("Database extension seed injection hooks registered")
        except ImportError:
            logger.debug("No BLL_Database hooks module found")

    @classmethod
    def on_shutdown(cls):
        """
        Called during application shutdown.
        """
        logger.debug("Database extension shutdown hook called")

    @classmethod
    def validate_config(cls) -> List[str]:
        """
        Validate the extension configuration.
        """
        issues = []

        # Check if any provider is available
        if not cls.providers():
            issues.append("No database providers available")

        # Validate provider-specific configurations
        for provider_class in cls.providers():
            if hasattr(provider_class, "validate_config"):
                provider_issues = provider_class.validate_config()
                if provider_issues:
                    issues.extend(
                        [f"{provider_class.name}: {issue}" for issue in provider_issues]
                    )

        return issues

    @classmethod
    def get_required_permissions(cls) -> List[str]:
        """
        Return the list of permissions required by this extension.
        """
        return [
            "database:query",
            "database:schema",
            "database:write",
            "database:admin",
        ]

    @classproperty
    def env(cls) -> Dict[str, Any]:
        """Get environment variables for this extension."""
        return cls._env

    @classproperty
    def pip_dependencies(cls):
        """Get PIP dependencies for backward compatibility."""
        return cls.dependencies.pip

    @classproperty
    def ext_dependencies(cls):
        """Get extension dependencies for backward compatibility."""
        return cls.dependencies.ext

    @classproperty
    def sys_dependencies(cls):
        """Get system dependencies for backward compatibility."""
        return cls.dependencies.sys

    @classmethod
    def get_seed_data(cls) -> List[Dict[str, Any]]:
        """
        Return seed data for database providers and instances.
        """
        from serverframework.lib.Environment import env

        providers_data = []
        instances_data = []

        # SQLite Provider
        if env("DATABASE_TYPE") == "sqlite" and env("DATABASE_FILE"):
            providers_data.append(
                {
                    "name": "SQLite",
                    "friendly_name": "SQLite Database",
                    "system": True,
                }
            )
            instances_data.append(
                {
                    "name": "Root_SQLite",
                    "_provider_name": "SQLite",
                    "api_key": env("DATABASE_FILE"),  # Store file path in api_key
                    "enabled": True,
                }
            )
            logger.debug("Registering SQLite provider via database extension")

        # InfluxDB Provider
        if all(
            [
                env("INFLUXDB_URL"),
                env("INFLUXDB_TOKEN"),
                env("INFLUXDB_ORG"),
                env("INFLUXDB_BUCKET"),
            ]
        ):
            providers_data.append(
                {
                    "name": "InfluxDB",
                    "friendly_name": "InfluxDB Time Series Database",
                    "system": True,
                }
            )
            instances_data.append(
                {
                    "name": "Root_InfluxDB",
                    "_provider_name": "InfluxDB",
                    "api_key": env("INFLUXDB_TOKEN"),
                    "model_name": env("INFLUXDB_BUCKET"),
                    "enabled": True,
                }
            )
            logger.debug("Registering InfluxDB provider via database extension")

        # PostgreSQL Provider
        if all(
            [
                env("DATABASE_TYPE") == "postgresql",
                env("DATABASE_HOST"),
                env("DATABASE_NAME"),
                env("DATABASE_USERNAME"),
                env("DATABASE_PASSWORD"),
            ]
        ):
            providers_data.append(
                {
                    "name": "PostgreSQL",
                    "friendly_name": "PostgreSQL Database",
                    "system": True,
                }
            )
            instances_data.append(
                {
                    "name": "Root_PostgreSQL",
                    "_provider_name": "PostgreSQL",
                    "api_key": env("DATABASE_PASSWORD"),
                    "model_name": env("DATABASE_NAME"),
                    "enabled": True,
                }
            )
            logger.debug("Registering PostgreSQL provider via database extension")

        # MySQL Provider
        if all(
            [
                env("DATABASE_TYPE") == "mysql",
                env("DATABASE_HOST"),
                env("DATABASE_NAME"),
                env("DATABASE_USERNAME"),
                env("DATABASE_PASSWORD"),
            ]
        ):
            providers_data.append(
                {
                    "name": "MySQL",
                    "friendly_name": "MySQL Database",
                    "system": True,
                }
            )
            instances_data.append(
                {
                    "name": "Root_MySQL",
                    "_provider_name": "MySQL",
                    "api_key": env("DATABASE_PASSWORD"),
                    "model_name": env("DATABASE_NAME"),
                    "enabled": True,
                }
            )
            logger.debug("Registering MySQL provider via database extension")

        return providers_data + instances_data

    @classmethod
    def check_health(cls) -> Dict[str, Any]:
        """
        Check health of all configured database providers.
        """
        health_status = {
            "overall_healthy": True,
            "providers": {},
            "timestamp": None,
        }

        try:
            # Check each configured provider
            for provider_class in cls.providers():
                provider_name = getattr(provider_class, "name", "Unknown")

                try:
                    # Use provider's validation method if available
                    if hasattr(provider_class, "validate_config"):
                        issues = provider_class.validate_config()
                        healthy = len(issues) == 0

                        health_status["providers"][provider_name] = {  # type: ignore[index]
                            "healthy": healthy,
                            "issues": issues,
                        }

                        if not healthy:
                            health_status["overall_healthy"] = False
                    else:
                        health_status["providers"][provider_name] = {  # type: ignore[index]
                            "healthy": True,
                            "issues": [],
                        }

                except Exception as e:
                    health_status["providers"][provider_name] = {  # type: ignore[index]
                        "healthy": False,
                        "issues": [f"Health check error: {str(e)}"],
                    }
                    health_status["overall_healthy"] = False

            from datetime import datetime, timezone

            health_status["timestamp"] = datetime.now(timezone.utc).isoformat()

        except Exception as e:
            logger.error(f"Error checking database provider health: {e}")
            health_status["overall_healthy"] = False
            health_status["error"] = str(e)

        return health_status


AbstractDatabaseExtensionProvider.extension = EXT_Database
