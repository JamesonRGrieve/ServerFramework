# SPDX-License-Identifier: AGPL-3.0-or-later
"""MySQL database provider (Provider Rotation System, static).

Ported from the pre-zephyrex AGInfrastructure MySQL provider into the current
static ``AbstractDatabaseExtensionProvider`` format.
"""

from typing import Any, Dict, List

from zephyrex.extensions.database.EXT_Database import (
    AbstractDatabaseExtensionProvider as AbstractDatabaseProvider,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger

try:  # optional driver — guarded so discovery never fails on a missing package
    import mysql.connector as _mysql_connector

    _mysql_available = True
except ImportError:  # pragma: no cover - optional driver
    _mysql_connector = None  # type: ignore[assignment]
    _mysql_available = False


class PRV_MySQL(AbstractDatabaseProvider):
    """MySQL database provider (static, rotation-compatible)."""

    name: str = "MySQL"
    friendly_name: str = "MySQL Database"
    description: str = "MySQL relational database provider"
    db_type: str = "mysql"

    _driver_available: bool = _mysql_available

    _env: Dict[str, Any] = {
        "DATABASE_HOST": "",
        "DATABASE_PORT": "3306",
        "DATABASE_NAME": "",
        "DATABASE_USERNAME": "",
        "DATABASE_PASSWORD": "",
    }

    _abilities = {"database", "sql", "data_storage", "relational_db"}

    _connection_config: Dict[str, Any] = {}

    @classmethod
    def bond_instance(cls, config: Dict[str, Any]) -> None:
        """Configure the provider from a config dict (falls back to env)."""
        try:
            port_raw = config.get("database_port") or env("DATABASE_PORT") or 3306
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
            }
            logger.debug(
                "%s provider bonded with host: %s",
                cls.name,
                cls._connection_config.get("database_host"),
            )
        except Exception as e:
            logger.error(f"Failed to configure {cls.name} provider: {e}")
            raise

    @classmethod
    def _get_connection(cls):
        """Open a mysql.connector connection using the bonded configuration."""
        if not cls._driver_available:
            logger.error("mysql-connector-python package not available")
            return None
        cfg = cls._connection_config
        try:
            return _mysql_connector.connect(
                host=cfg.get("database_host"),
                port=cfg.get("database_port", 3306),
                database=cfg.get("database_name"),
                user=cfg.get("database_username"),
                password=cfg.get("database_password"),
            )
        except Exception as e:
            logger.error(f"Error connecting to {cls.name} Database: {e}")
            return None

    @classmethod
    async def execute_sql(cls, query: str, **kwargs) -> str:
        """Execute a SQL query and return the result as a string / CSV."""
        try:
            if "```sql" in query:
                query = query.split("```sql")[1].split("```")[0]
            query = query.replace("```", "").replace("\n", " ").strip()
            logger.debug(f"Executing {cls.name} query: {query}")

            connection = cls._get_connection()
            if not connection:
                return f"Error connecting to {cls.name} Database"

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
            logger.error(f"Error executing {cls.name} query: {e}")
            return f"Error executing SQL query: {str(e)}"

    @classmethod
    async def get_schema(cls, **kwargs) -> str:
        """Introspect table definitions from information_schema."""
        try:
            connection = cls._get_connection()
            if not connection:
                return f"Error connecting to {cls.name} Database"
            db_name = cls._connection_config.get("database_name") or env(
                "DATABASE_NAME"
            )
            cursor = connection.cursor()
            sql_export: List[str] = []
            try:
                cursor.execute(
                    "SELECT table_name, column_name, data_type, is_nullable, "
                    "column_default FROM information_schema.columns "
                    "WHERE table_schema = %s ORDER BY table_name, ordinal_position;",
                    (db_name,),
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
                        f"CREATE TABLE `{table_name}` (" + ", ".join(cols) + ");"
                    )
                result = "\n\n".join(sql_export)
                return result if result.strip() else "No schema information available"
            finally:
                cursor.close()
                connection.close()
        except Exception as e:
            logger.error(f"Error getting {cls.name} schema: {e}")
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
        if not cls._driver_available:
            issues.append("mysql-connector-python driver not installed")
        if not (cls._connection_config.get("database_host") or env("DATABASE_HOST")):
            issues.append(f"{cls.name} host not configured")
        if not (cls._connection_config.get("database_name") or env("DATABASE_NAME")):
            issues.append(f"{cls.name} database name not configured")
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
        return f"Data writing for {cls.name} requires INSERT SQL statements"
