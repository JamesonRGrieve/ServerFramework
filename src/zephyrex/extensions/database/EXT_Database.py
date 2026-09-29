# SPDX-License-Identifier: AGPL-3.0-or-later
from abc import abstractmethod
from typing import Any, ClassVar, Dict, List, Optional, Sequence, Set, Type

from fastapi import HTTPException

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel
from zephyrex.pydantic2.registry import classproperty

SQL_CHAT_GUIDANCE = "Convert your request to SQL and use execute_sql to run it."


class DatabaseConnection(AbstractProviderInstance):
    """A bonded database provider instance: the rotated instance and the
    connection settings resolved for it."""

    def __init__(self, instance: ProviderInstanceModel, config: Dict[str, Any]) -> None:
        super().__init__(instance)
        self.config = config


class AbstractDatabaseExtensionProvider(AbstractStaticProvider):
    """Abstract base class for database service providers.

    Providers are static; the connection settings belong to the rotated
    ``ProviderInstanceModel`` each ability receives. Failures raise the typed
    external errors so the rotation can tell a provider outage
    (``TransientExternalError``: fail over) from a bad statement
    (``InvalidInputExternalError``: surface to the caller).
    """

    extension: ClassVar[Optional[Type[AbstractStaticExtension]]] = None

    # Provider metadata
    name: ClassVar[str] = ""  # Must be overridden by subclasses
    friendly_name: ClassVar[str] = ""  # Must be overridden by subclasses
    description: ClassVar[str] = ""  # Must be overridden by subclasses

    # Database type for this provider
    db_type: ClassVar[str] = ""  # Must be overridden by subclasses

    # Abilities provided by all database providers
    _abilities: ClassVar[Set[str]] = {
        "database",
        "sql",
        "data_storage",
    }

    # Environment variables this provider needs
    _env: ClassVar[Dict[str, Any]] = {}  # Override in subclasses

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> DatabaseConnection:
        """Bond ``instance`` with the connection settings resolved for it."""
        return DatabaseConnection(instance, cls.connection_config(instance))

    @classmethod
    @abstractmethod
    def connection_config(
        cls, instance: Optional[ProviderInstanceModel]
    ) -> Dict[str, Any]:
        """The connection settings for ``instance``: its own fields and
        settings first, the environment as fallback. ``None`` resolves from
        the environment alone (configuration checks have no instance)."""

    @classmethod
    @abstractmethod
    async def execute_sql(
        cls, instance: ProviderInstanceModel, query: str, **kwargs: Any
    ) -> str:
        """Execute a custom SQL query in the database."""

    @classmethod
    @abstractmethod
    async def get_schema(cls, instance: ProviderInstanceModel, **kwargs: Any) -> str:
        """Get the schema of the database."""

    @classmethod
    @abstractmethod
    async def chat_with_db(
        cls, instance: ProviderInstanceModel, request: str, **kwargs: Any
    ) -> str:
        """Chat with the database using natural language query."""

    @classmethod
    @abstractmethod
    async def execute_query(
        cls, instance: ProviderInstanceModel, query: str, **kwargs: Any
    ) -> str:
        """Execute a database-specific query (SQL, InfluxQL/Flux, MongoDB, GraphQL)."""

    @classmethod
    @abstractmethod
    async def write_data(
        cls, instance: ProviderInstanceModel, data: str, **kwargs: Any
    ) -> str:
        """Write data to the database."""

    # -- Connection-setting resolution ---------------------------------------

    @classmethod
    def resolve_setting(
        cls,
        instance: Optional[ProviderInstanceModel],
        key: str,
        env_var: Optional[str] = None,
        *,
        field: Optional[str] = None,
        default: Optional[str] = None,
    ) -> Optional[str]:
        """The first non-empty of: the instance's ``field`` column, the
        instance's ``key`` setting, the ``env_var`` environment value, and
        ``default``."""
        if instance is not None:
            if field is not None:
                value = getattr(instance, field)
                if value:
                    return str(value)
            setting = instance.get_setting(key)
            if setting:
                return setting
        if env_var is not None:
            env_value = cls.get_env_value(env_var)
            if env_value:
                return str(env_value)
        return default

    @classmethod
    def resolve_port(
        cls,
        instance: Optional[ProviderInstanceModel],
        env_var: str,
        default: int,
    ) -> int:
        """The ``database_port`` setting (see :meth:`resolve_setting`) as an
        integer."""
        raw = cls.resolve_setting(instance, "database_port", env_var)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError as exc:
            raise TransientExternalError(
                f"{cls.friendly_name} port is not a number",
                provider=cls.name,
                cause=exc,
            ) from exc

    @classmethod
    def server_config(
        cls, instance: Optional[ProviderInstanceModel], default_port: int
    ) -> Dict[str, Any]:
        """Settings of a networked database server: the password is the
        instance's ``api_key``; host, port, database name and username are
        instance settings; each falls back to ``DATABASE_*``. ``model_name``
        is an AI model name and never names a database."""
        return {
            "database_host": cls.resolve_setting(
                instance, "database_host", "DATABASE_HOST"
            ),
            "database_port": cls.resolve_port(instance, "DATABASE_PORT", default_port),
            "database_name": cls.resolve_setting(
                instance, "database_name", "DATABASE_NAME"
            ),
            "database_username": cls.resolve_setting(
                instance, "database_username", "DATABASE_USERNAME"
            ),
            "database_password": cls.resolve_setting(
                instance, "database_password", "DATABASE_PASSWORD", field="api_key"
            ),
        }

    @classmethod
    def server_config_issues(cls, driver_available: bool, package: str) -> List[str]:
        """What is missing to reach the environment-configured server."""
        issues: List[str] = []
        if not driver_available:
            issues.append(f"{package} driver not installed")
        config = cls.connection_config(None)
        if not config["database_host"]:
            issues.append(f"{cls.name} host not configured")
        if not config["database_name"]:
            issues.append(f"{cls.name} database name not configured")
        return issues

    # -- Typed failures -------------------------------------------------------

    @classmethod
    def require_driver(cls, available: bool, package: str) -> None:
        """Fail over when the provider's driver package is not installed."""
        if not available:
            raise TransientExternalError(
                f"{package} package not installed", provider=cls.name
            )

    @classmethod
    def require_config(cls, config: Dict[str, Any], *keys: str) -> None:
        """Fail over when any of ``keys`` is not configured."""
        missing = [key for key in keys if not config.get(key)]
        if missing:
            raise TransientExternalError(
                f"{cls.friendly_name} is not configured: missing {', '.join(missing)}",
                provider=cls.name,
            )

    @classmethod
    def connection_failed(cls, exc: Exception) -> TransientExternalError:
        """The error for a failed connection attempt. Only the exception type
        is logged: driver messages can echo connection strings."""
        logger.warning("%s connection failed: %s", cls.name, type(exc).__name__)
        return TransientExternalError(
            f"Error connecting to {cls.friendly_name}", provider=cls.name, cause=exc
        )

    @classmethod
    def query_failed(
        cls, exc: Exception, operation: str = "SQL query"
    ) -> InvalidInputExternalError:
        """The error for a statement the database rejected."""
        logger.warning("%s rejected a %s: %s", cls.name, operation, exc)
        return InvalidInputExternalError(
            f"Error executing {operation}: {exc}", provider=cls.name, cause=exc
        )

    # -- DB-API statement execution ------------------------------------------

    @staticmethod
    def clean_sql(query: str) -> str:
        """Strip a Markdown ```sql fence and fold the statement onto one line."""
        if "```sql" in query:
            query = query.split("```sql")[1].split("```")[0]
        return query.replace("```", "").replace("\n", " ").strip()

    @staticmethod
    def format_rows(column_names: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
        """A result set as text: a lone value bare, otherwise quoted CSV."""
        if not rows:
            return "Query executed successfully. No rows returned."
        if len(rows) == 1 and len(rows[0]) == 1:
            return str(rows[0][0])
        parts = [",".join(f'"{column}"' for column in column_names)]
        parts.extend(",".join(f'"{value}"' for value in row) for row in rows)
        return "\n".join(parts) + "\n"

    @classmethod
    def run_sql(cls, connection: Any, query: str, **cursor_kwargs: Any) -> str:
        """Run ``query`` on an open DB-API ``connection`` and close it.

        A statement that returns no result set is committed and reported by
        its row count. A statement the database rejects raises
        ``InvalidInputExternalError``.
        """
        query = cls.clean_sql(query)
        logger.debug("Executing %s query: %s", cls.name, query)
        try:
            cursor = connection.cursor(**cursor_kwargs)
            try:
                cursor.execute(query)
                if cursor.description is None:
                    connection.commit()
                    return (
                        f"Query executed successfully. {cursor.rowcount} rows affected."
                    )
                rows = cursor.fetchall()
                column_names = [desc[0] for desc in cursor.description]
            finally:
                cursor.close()
        except Exception as exc:
            raise cls.query_failed(exc) from exc
        finally:
            connection.close()
        return cls.format_rows(column_names, rows)

    @classmethod
    async def insert_data(
        cls, instance: ProviderInstanceModel, data: str, **kwargs: Any
    ) -> str:
        """``write_data`` for SQL providers: ``data`` must be an INSERT."""
        if not data.strip().upper().startswith("INSERT"):
            raise InvalidInputExternalError(
                f"Data writing for {cls.name} requires INSERT SQL statements",
                provider=cls.name,
            )
        return await cls.execute_sql(instance, data, **kwargs)

    @classmethod
    async def schema_guidance(
        cls,
        instance: ProviderInstanceModel,
        request: str,
        guidance: str,
        schema_label: str = "Database schema",
        **kwargs: Any,
    ) -> str:
        """``chat_with_db`` without a bundled NL model: the schema plus how to
        run a query."""
        schema = await cls.get_schema(instance, **kwargs)
        return (
            f'Natural language query: "{request}"\n\n'
            f"{schema_label}:\n{schema}\n\n"
            f"{guidance}"
        )

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
        # Executes on the first healthy provider instance of the root
        # rotation; each provider implements it taking the rotated instance:
        #     async def execute_sql(cls, instance, query, **kwargs) -> str
        result = await EXT_Database.execute_sql("SELECT * FROM users")
    """

    # Extension metadata (class attributes)
    name: ClassVar[str] = "database"
    friendly_name: ClassVar[str] = "Database Connectivity"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Database extension providing comprehensive database connectivity via Provider Rotation System"
    )

    # Environment variables exposed by the extension
    _env: ClassVar[Dict[str, Any]] = {
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
    _abilities: ClassVar[Set[str]] = {
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
        for provider_class in cls.providers:
            if hasattr(provider_class, "_abilities"):
                abilities.update(provider_class._abilities)

        return abilities

    @classmethod
    async def _rotate_provider(cls, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """Run ``method_name`` on the provider serving each rotated instance,
        with failover. The provider method receives the rotated
        ``ProviderInstanceModel`` first and connects with its settings; a
        ``TransientExternalError`` fails over to the next instance."""
        root = cls.root
        if root is None:
            raise HTTPException(
                status_code=503, detail="No database provider is configured"
            )
        return await root.arotate(cls.provider_call(method_name), *args, **kwargs)

    @classmethod
    @ability("execute_sql")
    async def execute_sql(cls, query: str, **kwargs) -> str:
        """Execute a custom SQL query in the database, with provider failover."""
        return str(await cls._rotate_provider("execute_sql", query, **kwargs))

    @classmethod
    @ability("get_schema")
    async def get_schema(cls, **kwargs) -> str:
        """Get the schema of the database, with provider failover."""
        return str(await cls._rotate_provider("get_schema", **kwargs))

    @classmethod
    @ability("chat_with_db")
    async def chat_with_db(cls, request: str, **kwargs) -> str:
        """Chat with the database in natural language, with provider failover."""
        return str(await cls._rotate_provider("chat_with_db", request, **kwargs))

    @classmethod
    @ability("execute_query")
    async def execute_query(cls, query: str, **kwargs) -> str:
        """Execute a database-specific query (e.g. InfluxQL, Flux, MongoDB),
        with provider failover."""
        return str(await cls._rotate_provider("execute_query", query, **kwargs))

    @classmethod
    @ability("write_data")
    async def write_data(cls, data: str, **kwargs) -> str:
        """Write data (e.g. to a time-series database), with provider failover."""
        return str(await cls._rotate_provider("write_data", data, **kwargs))

    @classmethod
    def get_database_classifications(
        cls, db_type: str | None = None
    ) -> Dict[str, List[str]]:
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
        for provider_class in cls.providers:
            if hasattr(provider_class, "name"):
                provider_names.add(provider_class.name)
        return provider_names

    @classmethod
    def on_startup(cls):
        """
        Called during application startup.
        """
        logger.debug("Database extension startup hook called")

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
        if not cls.providers:
            issues.append("No database providers available")

        # Validate provider-specific configurations
        for provider_class in cls.providers:
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
            for provider_class in cls.providers:
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
