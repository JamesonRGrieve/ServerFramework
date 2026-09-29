# SPDX-License-Identifier: AGPL-3.0-or-later
"""Microsoft SQL Server database provider (Provider Rotation System, static).

Ported from the pre-zephyrex AGInfrastructure MSSQL provider into the current
static ``AbstractDatabaseExtensionProvider`` format. The rotated instance
carries the password in ``api_key``; host, port, database name, username and
ODBC driver come from its settings, each falling back to the environment.
"""

from typing import Any, ClassVar, Dict, List, Optional

from zephyrex.extensions.database.EXT_Database import (
    SQL_CHAT_GUIDANCE,
    AbstractDatabaseExtensionProvider as AbstractDatabaseProvider,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

try:  # optional driver — guarded so discovery never fails on a missing package
    import pyodbc as _pyodbc

    _pyodbc_available = True
except ImportError:  # pragma: no cover - optional driver
    _pyodbc = None  # type: ignore[assignment]
    _pyodbc_available = False

MSSQL_DEFAULT_PORT = 1433
MSSQL_DEFAULT_ODBC_DRIVER = "ODBC Driver 18 for SQL Server"


class PRV_MSSQL(AbstractDatabaseProvider):
    """Microsoft SQL Server database provider (static, rotation-compatible)."""

    name: ClassVar[str] = "MSSQL"
    friendly_name: ClassVar[str] = "Microsoft SQL Server"
    description: ClassVar[str] = "Microsoft SQL Server relational database provider"
    db_type: ClassVar[str] = "mssql"

    _env: ClassVar[Dict[str, Any]] = {
        "DATABASE_HOST": "",
        "DATABASE_PORT": "1433",
        "DATABASE_NAME": "",
        "DATABASE_USERNAME": "",
        "DATABASE_PASSWORD": "",
        "MSSQL_ODBC_DRIVER": MSSQL_DEFAULT_ODBC_DRIVER,
    }

    _abilities = {"database", "sql", "data_storage", "relational_db"}

    @classmethod
    def connection_config(
        cls, instance: Optional[ProviderInstanceModel]
    ) -> Dict[str, Any]:
        return {
            **cls.server_config(instance, MSSQL_DEFAULT_PORT),
            "odbc_driver": cls.resolve_setting(
                instance,
                "odbc_driver",
                "MSSQL_ODBC_DRIVER",
                default=MSSQL_DEFAULT_ODBC_DRIVER,
            ),
        }

    @classmethod
    def _get_connection(cls, config: Dict[str, Any]) -> Any:
        """Open a pyodbc connection with the resolved configuration."""
        cls.require_driver(_pyodbc_available, "pyodbc")
        cls.require_config(config, "database_host", "database_name")
        connection_string = (
            f"DRIVER={{{config['odbc_driver']}}};"
            f"SERVER={config['database_host']},{config['database_port']};"
            f"DATABASE={config['database_name']};"
            f"UID={config['database_username']};"
            f"PWD={config['database_password']};"
            "TrustServerCertificate=yes"
        )
        try:
            return _pyodbc.connect(connection_string)
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
        connection = cls._get_connection(cls.bond_instance(instance).config)
        table_columns: Dict[str, List[str]] = {}
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, "
                    "COLUMN_DEFAULT FROM INFORMATION_SCHEMA.COLUMNS "
                    "ORDER BY TABLE_NAME, ORDINAL_POSITION;"
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
            f"CREATE TABLE [{table_name}] (" + ", ".join(cols) + ");"
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
        return cls.server_config_issues(_pyodbc_available, "pyodbc")

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
