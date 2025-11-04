"""
SQLite database provider for AGInfrastructure.
Provides SQLite database connectivity through the Provider Rotation System.
Fully static implementation compatible with the Provider Rotation System.
"""

import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from extensions.database.EXT_Database import (
    AbstractDatabaseExtensionProvider as AbstractDatabaseProvider,
)
from lib.Environment import env
from lib.Logging import logger


class PRV_SQLite(AbstractDatabaseProvider):
    """
    SQLite database provider implementation.
    Static/abstract provider compatible with Provider Rotation System.
    """

    # Provider metadata
    name: str = "SQLite"
    friendly_name: str = "SQLite Database"
    description: str = "SQLite embedded database provider"

    # Database type for this provider
    db_type: str = "sqlite"

    # Environment variables this provider needs
    _env: Dict[str, Any] = {
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

    # Class-level connection configuration
    _connection_config: Dict[str, Any] = {}

    @classmethod
    def bond_instance(cls, config: Dict[str, Any]) -> None:
        """
        Configure the SQLite provider with the given configuration.
        This is called once during provider initialization.
        """
        try:
            # Extract database file from config
            database_file = config.get("database_file") or env("DATABASE_FILE")

            if not database_file:
                # Create default database file if not specified
                conversation_name = config.get("conversation_name", "default")
                conversation_dir = config.get("conversation_directory", ".")
                # If a POSIX-style directory was provided (contains '/') prefer
                # to preserve the exact string so tests that expect forward
                # slashes (e.g. '/tmp/...') remain stable on Windows.
                if "/" in conversation_dir and os.path.sep != "/":
                    database_file = f"{conversation_dir}/{conversation_name}.db"
                else:
                    database_file = os.path.join(
                        conversation_dir, f"{conversation_name}.db"
                    )

            # Store configuration
            cls._connection_config = {"database_file": database_file, **config}

            # Ensure the directory exists for the database file
            if database_file:
                db_dir = os.path.dirname(database_file)
                if db_dir and not os.path.exists(db_dir):
                    try:
                        os.makedirs(db_dir, exist_ok=True)
                        logger.debug(f"Created directory for SQLite database: {db_dir}")
                    except OSError as e:
                        logger.warning(
                            f"Could not create directory for SQLite database: {e}"
                        )

            logger.debug(f"SQLite provider bonded with database file: {database_file}")

        except Exception as e:
            logger.error(f"Failed to configure SQLite provider: {e}")
            raise

    @classmethod
    def _get_connection(cls):
        """Get a connection to the SQLite database."""
        try:
            database_file = cls._connection_config.get("database_file")
            if not database_file:
                logger.error("No database file configured for SQLite connection")
                return None

            # Ensure the directory exists
            db_dir = os.path.dirname(database_file)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)

            connection = sqlite3.connect(database_file)
            # Enable dictionary cursor by default
            connection.row_factory = sqlite3.Row
            return connection

        except Exception as e:
            logger.error(f"Error connecting to SQLite Database: {e}")
            return None

    @classmethod
    async def execute_sql(cls, query: str, **kwargs) -> str:
        """Execute a custom SQL query in the SQLite database."""
        try:
            # Clean up query format
            if "```sql" in query:
                query = query.split("```sql")[1].split("```")[0]
            query = query.replace("\n", " ").strip()

            logger.debug(f"Executing SQLite query: {query}")

            connection = cls._get_connection()
            if not connection:
                return "Error connecting to SQLite Database"

            cursor = connection.cursor()

            try:
                cursor.execute(query)

                # Check if this is a SELECT query that returns rows
                if query.strip().upper().startswith("SELECT"):
                    rows = cursor.fetchall()

                    # If no rows returned
                    if not rows:
                        connection.commit()
                        return "Query executed successfully. No rows returned."

                    # If there is only 1 row and 1 column, return the value as a string
                    if len(rows) == 1 and len(rows[0]) == 1:
                        return str(rows[0][0])

                    # If there is more than 1 column and at least 1 row, return it as a CSV format
                    if len(rows) >= 1:
                        # Build column heading
                        column_names = [desc[0] for desc in cursor.description]
                        column_headings = [f'"{col}"' for col in column_names]
                        rows_string = ",".join(column_headings) + "\n"

                        # Add data rows
                        for row in rows:
                            row_string = [
                                f'"{row[i]}"' for i in range(len(column_names))
                            ]
                            rows_string += ",".join(row_string) + "\n"

                        return rows_string
                else:
                    # For non-SELECT queries (INSERT, UPDATE, DELETE, etc.)
                    connection.commit()
                    affected_rows = cursor.rowcount
                    return (
                        f"Query executed successfully. {affected_rows} rows affected."
                    )

            finally:
                cursor.close()
                connection.close()

        except Exception as e:
            logger.error(f"Error executing SQLite query: {e}")
            return f"Error executing SQL query: {str(e)}"

    @classmethod
    async def get_schema(cls, **kwargs) -> str:
        """Get the schema of the SQLite database."""
        try:
            logger.debug("Getting SQLite database schema")

            connection = cls._get_connection()
            if not connection:
                return "Error connecting to SQLite Database"

            cursor = connection.cursor()
            schemas = []
            key_relations = []
            index_schemas = []

            try:
                # Get all tables (excluding SQLite internal tables)
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
                )
                tables = cursor.fetchall()

                # Get schema for each table
                for table_info in tables:
                    table_name = table_info[0]
                    try:
                        # Get the CREATE TABLE statement
                        cursor.execute(
                            f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table_name}';"
                        )
                        create_table_result = cursor.fetchone()
                        if create_table_result and create_table_result[0]:
                            create_table_sql = str(create_table_result[0])
                            schemas.append(create_table_sql + ";")

                        # Get foreign key relationships
                        cursor.execute(f"PRAGMA foreign_key_list('{table_name}');")
                        foreign_keys = cursor.fetchall()

                        for fk in foreign_keys:
                            try:
                                # Handle both tuple and dictionary-like access
                                if hasattr(fk, "__getitem__") and not isinstance(
                                    fk, str
                                ):
                                    try:
                                        from_col = fk["from"] if "from" in fk else fk[3]
                                        to_col = fk["to"] if "to" in fk else fk[4]
                                        ref_table = (
                                            fk["table"] if "table" in fk else fk[2]
                                        )
                                    except (KeyError, IndexError, TypeError):
                                        from_col = fk[3]
                                        to_col = fk[4]
                                        ref_table = fk[2]

                                    key_relations.append(
                                        f"-- {table_name}.{from_col} can be joined with {ref_table}.{to_col}"
                                    )
                            except Exception:
                                continue

                    except Exception as e:
                        logger.error(
                            f"Error getting schema for table {table_name}: {e}"
                        )
                        continue

                # Get indexes
                cursor.execute(
                    "SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%';"
                )
                indexes = cursor.fetchall()

                for idx in indexes:
                    if idx[2]:  # Some internal indexes might not have SQL
                        index_sql = str(idx[2])
                        index_schemas.append(index_sql + ";")

            finally:
                connection.close()

            # Return combined schema
            result = "\n\n".join(schemas + index_schemas + key_relations)
            return result if result.strip() else "No schema information available"

        except Exception as e:
            logger.error(f"Error getting SQLite database schema: {e}")
            return f"Error getting database schema: {str(e)}"

    @classmethod
    async def chat_with_db(cls, request: str, **kwargs) -> str:
        """Chat with the SQLite database using natural language query."""
        try:
            # Get the schema for the database
            schema = await cls.get_schema(**kwargs)

            # For now, return a basic response since we don't have ApiClient in static context
            # This could be enhanced with a static AI service integration
            return f"""Natural language query: "{request}"

Database schema:
{schema}

To implement natural language querying, you would need to:
1. Parse the request to understand the intent
2. Map the request to SQL based on the schema
3. Execute the generated SQL query

For now, please convert your request to SQL manually and use the execute_sql method."""

        except Exception as e:
            logger.error(f"Error in SQLite chat_with_db: {e}")
            return f"Error processing natural language query: {str(e)}"

    @classmethod
    def validate_config(cls) -> List[str]:
        """Validate the SQLite provider configuration."""
        issues = []

        try:
            database_file = cls._connection_config.get("database_file") or env(
                "DATABASE_FILE"
            )

            if not database_file:
                issues.append("SQLite database file not provided")
                return issues

            # Check if the directory is writable (for creating the database if it doesn't exist)
            db_dir = os.path.dirname(database_file) if database_file else ""
            if db_dir and not os.path.exists(db_dir):
                try:
                    os.makedirs(db_dir, exist_ok=True)
                except OSError as e:
                    issues.append(f"Cannot create directory for SQLite database: {e}")

            # Check if we can write to the directory
            if db_dir and not os.access(db_dir, os.W_OK):
                issues.append(f"Directory not writable for SQLite database: {db_dir}")

            # Test connection
            try:
                connection = cls._get_connection()
                if connection:
                    connection.close()
                else:
                    issues.append("Cannot establish SQLite database connection")
            except Exception as e:
                issues.append(f"SQLite connection test failed: {e}")

        except Exception as e:
            issues.append(f"SQLite configuration validation error: {e}")

        return issues

    @classmethod
    async def execute_query(cls, query: str, **kwargs) -> str:
        """Execute a database-specific query (alias for execute_sql for SQLite)."""
        return await cls.execute_sql(query, **kwargs)

    @classmethod
    async def write_data(cls, data: str, **kwargs) -> str:
        """Write data to SQLite database (convert to INSERT statement)."""
        try:
            # This is a simplified implementation
            # In practice, you'd want to parse the data format and create proper INSERT statements
            if data.strip().upper().startswith("INSERT"):
                return await cls.execute_sql(data, **kwargs)
            else:
                return "Data writing for SQLite requires INSERT SQL statements"
        except Exception as e:
            logger.error(f"Error writing data to SQLite: {e}")
            return f"Error writing data: {str(e)}"
