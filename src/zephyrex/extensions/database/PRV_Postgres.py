# SPDX-License-Identifier: AGPL-3.0-or-later
"""PostgreSQL database provider (Provider Rotation System, static).

Ported from the pre-zephyrex AGInfrastructure PostgreSQL provider into the
current static ``AbstractDatabaseExtensionProvider`` format. The rotated
instance carries the password in ``api_key``; host, port, database name and
username come from its settings, each falling back to the ``DATABASE_*``
environment.
"""

from typing import Any, ClassVar, Dict, List, Optional

from zephyrex.extensions.database.EXT_Database import (
    SQL_CHAT_GUIDANCE,
    AbstractDatabaseExtensionProvider as AbstractDatabaseProvider,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

try:  # optional driver — guarded so discovery never fails on a missing package
    import psycopg2
    import psycopg2.extras

    _psycopg2_available = True
except ImportError:  # pragma: no cover - optional driver
    psycopg2 = None
    _psycopg2_available = False

POSTGRES_DEFAULT_PORT = 5432


class PRV_Postgres(AbstractDatabaseProvider):
    """PostgreSQL database provider (static, rotation-compatible)."""

    name: ClassVar[str] = "PostgreSQL"
    friendly_name: ClassVar[str] = "PostgreSQL Database"
    description: ClassVar[str] = (
        "PostgreSQL relational database provider with optional pgvector support"
    )
    db_type: ClassVar[str] = "postgresql"

    _env: ClassVar[Dict[str, Any]] = {
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

    @classmethod
    def connection_config(
        cls, instance: Optional[ProviderInstanceModel]
    ) -> Dict[str, Any]:
        return cls.server_config(instance, POSTGRES_DEFAULT_PORT)

    @classmethod
    def _get_connection(cls, config: Dict[str, Any]) -> Any:
        """Open a psycopg2 connection with the resolved configuration."""
        cls.require_driver(_psycopg2_available, "psycopg2")
        cls.require_config(config, "database_host", "database_name")
        try:
            return psycopg2.connect(
                host=config["database_host"],
                dbname=config["database_name"],
                port=config["database_port"],
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
        return cls.run_sql(connection, query, cursor_factory=psycopg2.extras.DictCursor)

    @classmethod
    async def get_schema(cls, instance: ProviderInstanceModel, **kwargs: Any) -> str:
        """Introspect table definitions and foreign-key relations."""
        connection = cls._get_connection(cls.bond_instance(instance).config)
        sql_export: List[str] = []
        key_relations: List[str] = []
        try:
            cursor = connection.cursor(cursor_factory=psycopg2.extras.DictCursor)
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
                    table_columns: Dict[str, List[str]] = {}
                    for row in cursor.fetchall():
                        piece = f"{row['column_name']} {row['data_type']}"
                        if row["column_default"]:
                            piece += f" DEFAULT {row['column_default']}"
                        if row["is_nullable"] == "NO":
                            piece += " NOT NULL"
                        table_columns.setdefault(row["table_name"], []).append(piece)
                    for table_name, parts in table_columns.items():
                        sql_export.append(
                            f'CREATE TABLE "{schema_name}"."{table_name}" ('
                            + ", ".join(parts)
                            + ");"
                        )
            finally:
                cursor.close()
        except Exception as exc:
            raise cls.query_failed(exc, "schema query") from exc
        finally:
            connection.close()
        result = "\n\n".join(sql_export + key_relations)
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
        return cls.server_config_issues(_psycopg2_available, "psycopg2")

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
