# SPDX-License-Identifier: AGPL-3.0-or-later
"""PostgreSQL database provider (Provider Rotation System, static).

Ported from the pre-zephyrex AGInfrastructure PostgreSQL provider into the
current static ``AbstractDatabaseExtensionProvider`` format. Supports standard
relational access plus optional pgvector similarity search.
"""

from typing import Any, Dict, List, Optional

from zephyrex.extensions.database.EXT_Database import (
    AbstractDatabaseExtensionProvider as AbstractDatabaseProvider,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger

try:  # optional driver — guarded so discovery never fails on a missing package
    import psycopg2
    import psycopg2.extras

    _psycopg2_available = True
except ImportError:  # pragma: no cover - optional driver
    psycopg2 = None  # type: ignore[assignment]
    _psycopg2_available = False


class PRV_Postgres(AbstractDatabaseProvider):
    """PostgreSQL database provider (static, rotation-compatible)."""

    name: str = "PostgreSQL"
    friendly_name: str = "PostgreSQL Database"
    description: str = (
        "PostgreSQL relational database provider with optional pgvector support"
    )
    db_type: str = "postgresql"

    _env: Dict[str, Any] = {
        "DATABASE_HOST": "",
        "DATABASE_PORT": "5432",
        "DATABASE_NAME": "",
        "DATABASE_USERNAME": "",
        "DATABASE_PASSWORD": "",
    }

    _abilities = {
        "database",
        "sql",
        "data_storage",
        "relational_db",
        "vector_db",
    }

    _connection_config: Dict[str, Any] = {}

    @classmethod
    def bond_instance(cls, config: Dict[str, Any]) -> None:
        """Configure the provider from a config dict (falls back to env)."""
        try:
            port_raw = config.get("database_port") or env("DATABASE_PORT") or 5432
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
                "has_vector_extension": bool(config.get("has_vector_extension", False)),
            }
            logger.debug(
                "PostgreSQL provider bonded with host: %s",
                cls._connection_config.get("database_host"),
            )
        except Exception as e:
            logger.error(f"Failed to configure PostgreSQL provider: {e}")
            raise

    @classmethod
    def _get_connection(cls):
        """Open a psycopg2 connection using the bonded configuration."""
        if not _psycopg2_available:
            logger.error("psycopg2 package not available")
            return None
        cfg = cls._connection_config
        try:
            return psycopg2.connect(
                host=cfg.get("database_host"),
                dbname=cfg.get("database_name"),
                port=cfg.get("database_port", 5432),
                user=cfg.get("database_username"),
                password=cfg.get("database_password"),
            )
        except Exception as e:
            logger.error(f"Error connecting to PostgreSQL Database: {e}")
            return None

    @classmethod
    async def execute_sql(cls, query: str, **kwargs) -> str:
        """Execute a SQL query and return the result as a string / CSV."""
        try:
            if "```sql" in query:
                query = query.split("```sql")[1].split("```")[0]
            query = query.replace("```", "").replace("\n", " ").strip()
            logger.debug(f"Executing PostgreSQL query: {query}")

            connection = cls._get_connection()
            if not connection:
                return "Error connecting to PostgreSQL Database"

            cursor = connection.cursor(cursor_factory=psycopg2.extras.DictCursor)
            try:
                cursor.execute(query)
                if cursor.description is None:
                    # Non-SELECT statement
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
            logger.error(f"Error executing PostgreSQL query: {e}")
            return f"Error executing SQL query: {str(e)}"

    @classmethod
    async def get_schema(cls, **kwargs) -> str:
        """Introspect table definitions and foreign-key relations."""
        try:
            connection = cls._get_connection()
            if not connection:
                return "Error connecting to PostgreSQL Database"

            cursor = connection.cursor(cursor_factory=psycopg2.extras.DictCursor)
            sql_export: List[str] = []
            key_relations: List[str] = []
            try:
                cursor.execute(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name NOT IN ('pg_catalog', 'information_schema');"
                )
                schemas = [r["schema_name"] for r in cursor.fetchall()]

                for schema_name in schemas:
                    cursor.execute(
                        """
                        SELECT tc.table_name AS foreign_table,
                               kcu.column_name AS foreign_column,
                               ccu.table_name AS primary_table,
                               ccu.column_name AS primary_column
                        FROM information_schema.table_constraints AS tc
                        JOIN information_schema.key_column_usage AS kcu
                          ON tc.constraint_name = kcu.constraint_name
                          AND tc.table_schema = kcu.table_schema
                        JOIN information_schema.constraint_column_usage AS ccu
                          ON ccu.constraint_name = tc.constraint_name
                          AND ccu.table_schema = tc.table_schema
                        WHERE tc.constraint_type = 'FOREIGN KEY'
                          AND tc.table_schema = %s;
                        """,
                        (schema_name,),
                    )
                    for rel in cursor.fetchall():
                        key_relations.append(
                            f"-- {rel['foreign_table']}.{rel['foreign_column']} "
                            f"can be joined with {rel['primary_table']}."
                            f"{rel['primary_column']}"
                        )

                    cursor.execute(
                        """
                        SELECT table_name, column_name, data_type,
                               column_default, is_nullable
                        FROM information_schema.columns
                        WHERE table_schema = %s
                        ORDER BY table_name, ordinal_position;
                        """,
                        (schema_name,),
                    )
                    table_columns: Dict[str, List[Dict[str, Any]]] = {}
                    for row in cursor.fetchall():
                        table_columns.setdefault(row["table_name"], []).append(
                            {
                                "column_name": row["column_name"],
                                "data_type": row["data_type"],
                                "column_default": row["column_default"],
                                "is_nullable": row["is_nullable"],
                            }
                        )
                    for table_name, columns in table_columns.items():
                        parts = []
                        for col in columns:
                            piece = f"{col['column_name']} {col['data_type']}"
                            if col["column_default"]:
                                piece += f" DEFAULT {col['column_default']}"
                            if col["is_nullable"] == "NO":
                                piece += " NOT NULL"
                            parts.append(piece)
                        sql_export.append(
                            f'CREATE TABLE "{schema_name}"."{table_name}" ('
                            + ", ".join(parts)
                            + ");"
                        )
                result = "\n\n".join(sql_export + key_relations)
                return result if result.strip() else "No schema information available"
            finally:
                cursor.close()
                connection.close()
        except Exception as e:
            logger.error(f"Error getting PostgreSQL schema: {e}")
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
        if not _psycopg2_available:
            issues.append("psycopg2 driver not installed")
        host = cls._connection_config.get("database_host") or env("DATABASE_HOST")
        if not host:
            issues.append("PostgreSQL host not configured")
        name = cls._connection_config.get("database_name") or env("DATABASE_NAME")
        if not name:
            issues.append("PostgreSQL database name not configured")
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
        return "Data writing for PostgreSQL requires INSERT SQL statements"
