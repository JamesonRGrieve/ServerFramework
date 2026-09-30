# SPDX-License-Identifier: AGPL-3.0-or-later
"""MySQL database provider (Provider Rotation System, static).

Ported from the pre-zephyrex AGInfrastructure MySQL provider into the current
static ``AbstractDatabaseExtensionProvider`` format. The rotated instance
carries the password in ``api_key``; host, port, database name and username
come from its settings, each falling back to the ``DATABASE_*`` environment.
"""

from typing import Any, ClassVar, Dict, List, Optional

from zephyrex.extensions.database.EXT_Database import (
    SQL_CHAT_GUIDANCE,
    AbstractDatabaseExtensionProvider as AbstractDatabaseProvider,
)
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

try:  # optional driver — guarded so discovery never fails on a missing package
    import mysql.connector as _mysql_connector

    _mysql_available = True
except ImportError:  # pragma: no cover - optional driver
    _mysql_connector = None
    _mysql_available = False

MYSQL_DEFAULT_PORT = 3306


class PRV_MySQL(AbstractDatabaseProvider):
    """MySQL database provider (static, rotation-compatible)."""

    name: ClassVar[str] = "MySQL"
    friendly_name: ClassVar[str] = "MySQL Database"
    description: ClassVar[str] = "MySQL relational database provider"
    db_type: ClassVar[str] = "mysql"

    _driver_available: ClassVar[bool] = _mysql_available

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="mysql-connector-python",
                friendly_name="MySQL Connector/Python",
                semver=">=9.0.0",
                reason="MySQL database provider driver",
            )
        ]
    )

    _env: ClassVar[Dict[str, Any]] = {
        "DATABASE_HOST": "",
        "DATABASE_PORT": "3306",
        "DATABASE_NAME": "",
        "DATABASE_USERNAME": "",
        "DATABASE_PASSWORD": "",
    }

    _abilities = {"database", "sql", "data_storage", "relational_db"}

    @classmethod
    def connection_config(
        cls, instance: Optional[ProviderInstanceModel]
    ) -> Dict[str, Any]:
        return cls.server_config(instance, MYSQL_DEFAULT_PORT)

    @classmethod
    def _get_connection(cls, config: Dict[str, Any]) -> Any:
        """Open a mysql.connector connection with the resolved configuration."""
        cls.require_driver(cls._driver_available, "mysql-connector-python")
        cls.require_config(config, "database_host", "database_name")
        try:
            return _mysql_connector.connect(
                host=config["database_host"],
                port=config["database_port"],
                database=config["database_name"],
                user=config["database_username"],
                password=config["database_password"],
            )
        except Exception as exc:
            raise cls.connection_failed(exc) from exc

    @classmethod
    async def execute_sql(
        cls, instance: ProviderInstanceModel, query: str, **kwargs: Any
    ) -> str:
        """Execute a SQL query and return the result as a string / CSV."""
        connection = cls._get_connection(cls.bond_instance(instance).config)
        return cls.run_sql(connection, query)

    @classmethod
    async def get_schema(cls, instance: ProviderInstanceModel, **kwargs: Any) -> str:
        """Introspect table definitions from information_schema."""
        config = cls.bond_instance(instance).config
        connection = cls._get_connection(config)
        table_columns: Dict[str, List[str]] = {}
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "SELECT table_name, column_name, data_type, is_nullable, "
                    "column_default FROM information_schema.columns "
                    "WHERE table_schema = %s ORDER BY table_name, ordinal_position;",
                    (config["database_name"],),
                )
                for table_name, col, dtype, nullable, default in cursor.fetchall():
                    piece = f"{col} {dtype}"
                    if default is not None:
                        piece += f" DEFAULT {default}"
                    if nullable == "NO":
                        piece += " NOT NULL"
                    table_columns.setdefault(table_name, []).append(piece)
            finally:
                cursor.close()
        except Exception as exc:
            raise cls.query_failed(exc, "schema query") from exc
        finally:
            connection.close()
        result = "\n\n".join(
            f"CREATE TABLE `{table_name}` (" + ", ".join(cols) + ");"
            for table_name, cols in table_columns.items()
        )
        return result if result.strip() else "No schema information available"

    @classmethod
    async def chat_with_db(
        cls, instance: ProviderInstanceModel, request: str, **kwargs: Any
    ) -> str:
        """Return the schema plus guidance (no bundled NL-to-SQL model here)."""
        return await cls.schema_guidance(instance, request, SQL_CHAT_GUIDANCE, **kwargs)

    @classmethod
    def validate_config(cls) -> List[str]:
        """Configuration problems of the environment-configured database."""
        return cls.server_config_issues(cls._driver_available, "mysql-connector-python")

    @classmethod
    async def execute_query(
        cls, instance: ProviderInstanceModel, query: str, **kwargs: Any
    ) -> str:
        """Provider-specific query alias (identical to execute_sql here)."""
        return await cls.execute_sql(instance, query, **kwargs)

    @classmethod
    async def write_data(
        cls, instance: ProviderInstanceModel, data: str, **kwargs: Any
    ) -> str:
        """Write data via an INSERT statement."""
        return await cls.insert_data(instance, data, **kwargs)
