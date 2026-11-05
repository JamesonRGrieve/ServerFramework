# SPDX-License-Identifier: AGPL-3.0-or-later
"""
SQLite database provider for AGInfrastructure.
Provides SQLite database connectivity through the Provider Rotation System.
The database file is the rotated instance's ``api_key`` (else its
``database_file`` setting, else ``DATABASE_FILE``).
"""

import os
import sqlite3
from typing import Any, ClassVar, Dict, List, Optional

from zephyrex.extensions.database.EXT_Database import (
    AbstractDatabaseExtensionProvider as AbstractDatabaseProvider,
)
from zephyrex.extensions.ExternalErrors import TransientExternalError
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

SQLITE_CHAT_GUIDANCE = """To implement natural language querying, you would need to:
1. Parse the request to understand the intent
2. Map the request to SQL based on the schema
3. Execute the generated SQL query

For now, please convert your request to SQL manually and use the execute_sql method."""


class PRV_SQLite(AbstractDatabaseProvider):
    """
    SQLite database provider implementation.
    Static/abstract provider compatible with Provider Rotation System.
    """

    # Provider metadata
    name: ClassVar[str] = "SQLite"
    friendly_name: ClassVar[str] = "SQLite Database"
    description: ClassVar[str] = "SQLite embedded database provider"

    # Database type for this provider
    db_type: ClassVar[str] = "sqlite"

    # Environment variables this provider needs
    _env: ClassVar[Dict[str, Any]] = {
        "DATABASE_FILE": "",
        "DATABASE_TYPE": "sqlite",
    }

    # Provider-specific abilities
    _abilities = {
        "database",
        "sql",
        "data_storage",
        "embedded",
        "file_based",
        "relational_db",
    }

    @classmethod
    def connection_config(
        cls, instance: Optional[ProviderInstanceModel]
    ) -> Dict[str, Any]:
        return {
            "database_file": cls.resolve_setting(
                instance, "database_file", "DATABASE_FILE", field="api_key"
            )
        }

    @classmethod
    def _get_connection(cls, config: Dict[str, Any]) -> sqlite3.Connection:
        """Open the configured database file, creating its directory."""
        cls.require_config(config, "database_file")
        database_file = config["database_file"]
        try:
            db_dir = os.path.dirname(database_file)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            connection = sqlite3.connect(database_file)
        except (OSError, sqlite3.Error) as exc:
            raise cls.connection_failed(exc) from exc
        connection.row_factory = sqlite3.Row
        return connection

    @classmethod
    async def execute_sql(
        cls, instance: ProviderInstanceModel, query: str, **kwargs: Any
    ) -> str:
        """Execute a custom SQL query in the SQLite database."""
        connection = cls._get_connection(cls.bond_instance(instance).config)
        return cls.run_sql(connection, query)

    @classmethod
    async def get_schema(cls, instance: ProviderInstanceModel, **kwargs: Any) -> str:
        """Get the schema of the SQLite database."""
        connection = cls._get_connection(cls.bond_instance(instance).config)
        schemas: List[str] = []
        key_relations: List[str] = []
        index_schemas: List[str] = []
        try:
            cursor = connection.cursor()
            # Tables, excluding SQLite's internal ones.
            cursor.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%';"
            )
            for table_name, create_table_sql in cursor.fetchall():
                if create_table_sql:
                    schemas.append(f"{create_table_sql};")
                cursor.execute(f"PRAGMA foreign_key_list('{table_name}');")
                for fk in cursor.fetchall():
                    key_relations.append(
                        f"-- {table_name}.{fk['from']} can be joined with "
                        f"{fk['table']}.{fk['to']}"
                    )
            cursor.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='index' AND name NOT LIKE 'sqlite_%';"
            )
            # Automatic indexes carry no SQL.
            index_schemas.extend(f"{row[0]};" for row in cursor.fetchall() if row[0])
        except sqlite3.Error as exc:
            raise cls.query_failed(exc, "schema query") from exc
        finally:
            connection.close()

        result = "\n\n".join(schemas + index_schemas + key_relations)
        return result if result.strip() else "No schema information available"

    @classmethod
    async def chat_with_db(
        cls, instance: ProviderInstanceModel, request: str, **kwargs: Any
    ) -> str:
        """Chat with the SQLite database using natural language query."""
        return await cls.schema_guidance(
            instance, request, SQLITE_CHAT_GUIDANCE, **kwargs
        )

    @classmethod
    def validate_config(cls) -> List[str]:
        """Validate the environment-configured SQLite database file."""
        database_file = cls.connection_config(None)["database_file"]
        if not database_file:
            return ["SQLite database file not provided"]

        issues = []
        db_dir = os.path.dirname(database_file)
        if db_dir and not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir, exist_ok=True)
            except OSError as e:
                issues.append(f"Cannot create directory for SQLite database: {e}")
        if db_dir and not os.access(db_dir, os.W_OK):
            issues.append(f"Directory not writable for SQLite database: {db_dir}")
        if not issues:
            try:
                cls._get_connection({"database_file": database_file}).close()
            except TransientExternalError as e:
                issues.append(f"SQLite connection test failed: {e}")
        return issues

    @classmethod
    async def execute_query(
        cls, instance: ProviderInstanceModel, query: str, **kwargs: Any
    ) -> str:
        """Execute a database-specific query (alias for execute_sql for SQLite)."""
        return await cls.execute_sql(instance, query, **kwargs)

    @classmethod
    async def write_data(
        cls, instance: ProviderInstanceModel, data: str, **kwargs: Any
    ) -> str:
        """Write data to the SQLite database (an INSERT statement)."""
        return await cls.insert_data(instance, data, **kwargs)
