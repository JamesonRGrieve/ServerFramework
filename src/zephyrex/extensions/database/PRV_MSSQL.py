# SPDX-License-Identifier: AGPL-3.0-or-later
"""Microsoft SQL Server database provider (Provider Rotation System, static).

Ported from the pre-zephyrex AGInfrastructure MSSQL provider into the current
static ``AbstractDatabaseExtensionProvider`` format.
"""

from typing import Any, Dict, List

from zephyrex.extensions.database.EXT_Database import (
    AbstractDatabaseExtensionProvider as AbstractDatabaseProvider,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger

try:  # optional driver — guarded so discovery never fails on a missing package
    import pyodbc as _pyodbc

    _pyodbc_available = True
except ImportError:  # pragma: no cover - optional driver
    _pyodbc = None  # type: ignore[assignment]
    _pyodbc_available = False


class PRV_MSSQL(AbstractDatabaseProvider):
    """Microsoft SQL Server database provider (static, rotation-compatible)."""

    name: str = "MSSQL"
    friendly_name: str = "Microsoft SQL Server"
    description: str = "Microsoft SQL Server relational database provider"
    db_type: str = "mssql"

    _env: Dict[str, Any] = {
        "DATABASE_HOST": "",
        "DATABASE_PORT": "1433",
        "DATABASE_NAME": "",
        "DATABASE_USERNAME": "",
        "DATABASE_PASSWORD": "",
        "MSSQL_ODBC_DRIVER": "ODBC Driver 18 for SQL Server",
    }

    _abilities = {"database", "sql", "data_storage", "relational_db"}

    _connection_config: Dict[str, Any] = {}

    @classmethod
    def bond_instance(cls, config: Dict[str, Any]) -> None:
        """Configure the provider from a config dict (falls back to env)."""
        try:
            port_raw = config.get("database_port") or env("DATABASE_PORT") or 1433
            cls._connection_config = {
                **config,
                "database_host": config.get("database_host") or env("DATABASE_HOST"),
                "database_port": int(port_raw),
                "database_name": config.get("database_name") or env("DATABASE_NAME"),
                "database_username": (
                    config.get("database_username") or env("DATABASE_USERNAME")
                ),
                "database_password": (
                    config.get("database_password") or env("DATABASE_PASSWORD")
                ),
                "odbc_driver": (
                    config.get("odbc_driver")
                    or env("MSSQL_ODBC_DRIVER")
                    or "ODBC Driver 18 for SQL Server"
                ),
            }
            logger.debug(
                "MSSQL provider bonded with host: %s",
                cls._connection_config.get("database_host"),
            )
        except Exception as e:
            logger.error(f"Failed to configure MSSQL provider: {e}")
            raise

    @classmethod
    def _get_connection(cls):
        """Open a pyodbc connection using the bonded configuration."""
        if not _pyodbc_available:
            logger.error("pyodbc package not available")
            return None
        cfg = cls._connection_config
        try:
            connection_string = (
                f"DRIVER={{{cfg.get('odbc_driver', 'ODBC Driver 18 for SQL Server')}}};"
                f"SERVER={cfg.get('database_host')},{cfg.get('database_port', 1433)};"
                f"DATABASE={cfg.get('database_name')};"
                f"UID={cfg.get('database_username')};"
                f"PWD={cfg.get('database_password')};"
                "TrustServerCertificate=yes"
            )
            return _pyodbc.connect(connection_string)
        except Exception as e:
            logger.error(f"Error connecting to MSSQL Database: {e}")
            return None

    @classmethod
    async def execute_sql(cls, query: str, **kwargs) -> str:
        """Execute a SQL query and return the result as a string / CSV."""
        try:
            if "```sql" in query:
                query = query.split("```sql")[1].split("```")[0]
            query = query.replace("```", "").replace("\n", " ").strip()
            logger.debug(f"Executing MSSQL query: {query}")

            connection = cls._get_connection()
            if not connection:
                return "Error connecting to MSSQL Database"

            cursor = connection.cursor()
            try:
                cursor.execute(query)
                if cursor.description is None:
                    connection.commit()
                    return (
                        "Query executed successfully. "
                        f"{cursor.rowcount} rows affected."
                    )
                rows = cursor.fetchall()
                if not rows:
                    return "Query executed successfully. No rows returned."
                if len(rows) == 1 and len(rows[0]) == 1:
                    return str(rows[0][0])
                column_names = [desc[0] for desc in cursor.description]
                out = ",".join(f'"{c}"' for c in column_names) + "\n"
                for row in rows:
                    out += ",".join(f'"{v}"' for v in row) + "\n"
                return out
            finally:
                cursor.close()
                connection.close()
        except Exception as e:
            logger.error(f"Error executing MSSQL query: {e}")
            return f"Error executing SQL query: {str(e)}"

    @classmethod
    async def get_schema(cls, **kwargs) -> str:
        """Introspect table definitions from information_schema."""
        try:
            connection = cls._get_connection()
            if not connection:
                return "Error connecting to MSSQL Database"
            cursor = connection.cursor()
            sql_export: List[str] = []
            try:
                cursor.execute(
                    "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, "
                    "COLUMN_DEFAULT FROM INFORMATION_SCHEMA.COLUMNS "
                    "ORDER BY TABLE_NAME, ORDINAL_POSITION;"
                )
                table_columns: Dict[str, List[str]] = {}
                for table_name, col, dtype, nullable, default in cursor.fetchall():
                    piece = f"{col} {dtype}"
                    if default is not None:
                        piece += f" DEFAULT {default}"
                    if nullable == "NO":
                        piece += " NOT NULL"
                    table_columns.setdefault(table_name, []).append(piece)
                for table_name, cols in table_columns.items():
                    sql_export.append(
                        f"CREATE TABLE [{table_name}] (" + ", ".join(cols) + ");"
                    )
                result = "\n\n".join(sql_export)
                return result if result.strip() else "No schema information available"
            finally:
                cursor.close()
                connection.close()
        except Exception as e:
            logger.error(f"Error getting MSSQL schema: {e}")
            return f"Error getting database schema: {str(e)}"

    @classmethod
    async def chat_with_db(cls, request: str, **kwargs) -> str:
        """Return the schema plus guidance (no bundled NL-to-SQL model here)."""
        schema = await cls.get_schema(**kwargs)
        return (
            f'Natural language query: "{request}"\n\n'
            f"Database schema:\n{schema}\n\n"
            "Convert your request to SQL and use execute_sql to run it."
        )

    @classmethod
    def validate_config(cls) -> List[str]:
        """Return a list of configuration problems (empty when healthy)."""
        issues: List[str] = []
        if not _pyodbc_available:
            issues.append("pyodbc driver not installed")
        if not (cls._connection_config.get("database_host") or env("DATABASE_HOST")):
            issues.append("MSSQL host not configured")
        if not (cls._connection_config.get("database_name") or env("DATABASE_NAME")):
            issues.append("MSSQL database name not configured")
        return issues

    @classmethod
    async def execute_query(cls, query: str, **kwargs) -> str:
        """Provider-specific query alias (identical to execute_sql here)."""
        return await cls.execute_sql(query, **kwargs)

    @classmethod
    async def write_data(cls, data: str, **kwargs) -> str:
        """Write data via an INSERT statement."""
        if data.strip().upper().startswith("INSERT"):
            return await cls.execute_sql(data, **kwargs)
        return "Data writing for MSSQL requires INSERT SQL statements"
