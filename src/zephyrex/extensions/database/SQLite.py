import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from zephyrex.extensions.database.EXT_Database import (
    AbstractDatabaseExtensionProvider as AbstractDatabaseProvider,
)
from zephyrex.lib.Logging import logger


class SQLiteProvider(AbstractDatabaseProvider):
    """
    SQLite database provider implementation.
    """

    def __init__(
        self,
        api_key: str = "",
        api_uri: str = "",
        database_file: str = "",
        extension_id: Optional[str] = None,
        **kwargs,
    ):
        # SQLite doesn't use host/port/username/password
        # Instead, it uses a file path
        self.database_file = database_file

        # If no database file is specified, create one in the working directory
        if not self.database_file and "conversation_directory" in kwargs:
            self.database_file = os.path.join(
                kwargs.get("conversation_directory", ""),
                f"{kwargs.get('conversation_name', 'default')}.db",
            )

        super().__init__(
            api_key=api_key,
            api_uri=api_uri,
            database_host="",  # Not used for SQLite
            database_port=0,  # Not used for SQLite
            database_name=(
                os.path.basename(self.database_file) if self.database_file else ""
            ),
            database_username="",  # Not used for SQLite
            database_password="",  # Not used for SQLite
            extension_id=extension_id,
            **kwargs,
        )

        # Register SQLite-specific abilities
        self.register_ability("embedded")
        self.register_ability("file_based")

        # Ensure the directory exists for the database file
        if self.database_file:
            db_dir = os.path.dirname(self.database_file)
            if db_dir and not os.path.exists(db_dir):
                try:
                    os.makedirs(db_dir, exist_ok=True)
                except OSError:
                    # Directory creation failed, but we'll handle this in validate_config
                    pass

    def get_connection(self):
        try:
            # Ensure the directory exists
            if self.database_file:
                db_dir = os.path.dirname(self.database_file)
                if db_dir and not os.path.exists(db_dir):
                    os.makedirs(db_dir, exist_ok=True)

                connection = sqlite3.connect(self.database_file)
                # Enable dictionary cursor by default
                connection.row_factory = sqlite3.Row
                return connection
            else:
                logger.error("No database file specified for SQLite connection")
                return None
        except Exception as e:
            logger.error(f"Error connecting to SQLite Database. Error: {str(e)}")
            return None

    async def execute_sql(self, query: str, retry_count: int = 0) -> str:  # type: ignore[return]
        if "```sql" in query:
            query = query.split("```sql")[1].split("```")[0]
        query = query.replace("\n", " ")
        query = query.strip()
        logger.debug(f"Executing SQL Query: {query}")

        connection = self.get_connection()
        if not connection:
            return "Error connecting to SQLite Database"

        cursor = connection.cursor()
        try:
            cursor.execute(query)

            # Check if this is a SELECT query that returns rows
            if query.strip().upper().startswith("SELECT"):
                rows = cursor.fetchall()

                rows_string = ""

                # If no rows returned
                if not rows:
                    connection.commit()
                    cursor.close()
                    connection.close()
                    return "Query executed successfully. No rows returned."

                # If there is only 1 row and 1 column, return the value as a string
                if len(rows) == 1 and len(rows[0]) == 1:
                    cursor.close()
                    connection.close()
                    return str(rows[0][0])

                # If there is more than 1 column and at least 1 row, return it as a CSV format
                if len(rows) >= 1:
                    # Build column heading
                    column_names = [desc[0] for desc in cursor.description]
                    column_headings = [f'"{col}"' for col in column_names]
                    rows_string += ",".join(column_headings) + "\n"

                    # Add data rows
                    for row in rows:
                        row_string = []
                        for i in range(len(column_names)):
                            row_string.append(f'"{row[i]}"')
                        rows_string += ",".join(row_string) + "\n"

                    cursor.close()
                    connection.commit()
                    connection.close()
                    return rows_string
            else:
                # For non-SELECT queries (INSERT, UPDATE, DELETE, etc.)
                connection.commit()
                affected_rows = cursor.rowcount
                cursor.close()
                connection.close()
                return f"Query executed successfully. {affected_rows} rows affected."

        except Exception as e:
            logger.error(f"Error executing SQL Query: {str(e)}")
            cursor.close()
            connection.close()

            # Prevent infinite recursion by limiting retries
            if retry_count < 3 and hasattr(self, "ApiClient") and self.ApiClient:
                new_query = self.ApiClient.prompt_agent(
                    agent_name=self.agent_name,
                    prompt_name="Validate SQLite",
                    prompt_args={
                        "database_type": "SQLite",
                        "schema": await self.get_schema(),
                        "query": query,
                    },
                )
                return await self.execute_sql(
                    query=new_query, retry_count=retry_count + 1
                )
            return f"Error executing SQL query: {str(e)}"

    async def get_schema(self) -> str:
        logger.debug(f"Getting schema for database '{self.database_name}'")

        connection = self.get_connection()
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
            _ident_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
            for table_info in tables:
                table_name = table_info[0]
                # SQLite does not bind identifiers in PRAGMA arguments and the
                # CREATE TABLE lookup interpolates the name; reject anything
                # that is not a plain identifier to close off SQL injection.
                if not isinstance(table_name, str) or not _ident_re.match(table_name):
                    continue
                try:
                    # Get the CREATE TABLE statement (parameterised lookup).
                    cursor.execute(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?;",
                        (table_name,),
                    )
                    create_table_result = cursor.fetchone()
                    if create_table_result and create_table_result[0]:
                        create_table_sql = str(create_table_result[0])
                        schemas.append(create_table_sql + ";")

                    # Get foreign key relationships. PRAGMA cannot bind the
                    # identifier; safe because table_name was just validated.
                    cursor.execute(f'PRAGMA foreign_key_list("{table_name}");')
                    foreign_keys = cursor.fetchall()

                    for fk in foreign_keys:
                        # Handle both tuple and dictionary-like access
                        if hasattr(fk, "__getitem__") and not isinstance(fk, str):
                            try:
                                # Try dictionary-like access first
                                from_col = fk["from"] if "from" in fk else fk[3]
                                to_col = fk["to"] if "to" in fk else fk[4]
                                ref_table = fk["table"] if "table" in fk else fk[2]
                            except (KeyError, IndexError, TypeError):
                                # Fall back to tuple access
                                try:
                                    from_col = fk[3]
                                    to_col = fk[4]
                                    ref_table = fk[2]
                                except (IndexError, TypeError):
                                    continue

                            # Convert to strings to handle mock objects
                            from_col = str(from_col)
                            to_col = str(to_col)
                            ref_table = str(ref_table)

                            key_relations.append(
                                f"-- {table_name}.{from_col} can be joined with {ref_table}.{to_col}"
                            )

                except Exception as e:
                    logger.error(
                        f"Error getting schema for table {table_name}: {str(e)}"
                    )
                    # Continue with other tables

            # Get indexes
            cursor.execute(
                "SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%';"
            )
            indexes = cursor.fetchall()

            for idx in indexes:
                if idx[2]:  # Some internal indexes might not have SQL
                    index_sql = str(idx[2])  # Convert to string to handle mocks
                    index_schemas.append(index_sql + ";")

        except Exception as e:
            logger.error(f"Error getting database schema: {str(e)}")
            # Return partial schema if available
            pass
        finally:
            connection.close()

        # Return combined schema, even if partial
        result = "\n\n".join(schemas + index_schemas + key_relations)
        return result if result.strip() else "No schema information available"

    async def chat_with_db(self, request: str) -> str:
        # Get the schema for the database
        schema = await self.get_schema()

        # Generate SQL query based on the schema and natural language query
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        if hasattr(self, "ApiClient") and self.ApiClient:
            sql_query = self.ApiClient.prompt_agent(
                agent_name=self.agent_name,
                prompt_name="Think About It",
                prompt_args={
                    "user_input": f"""### Task
Generate a SQLite query to answer the following:
`{request}`
### Database Schema
The query will run on a SQLite database with the following schema:
{schema}
### SQL
Follow these steps to create the SQL Query:
1. Only use the columns and tables present in the database schema
2. Use table aliases to prevent ambiguity when doing joins. For example, `SELECT t1.col1, t2.col1 FROM table1 t1 JOIN table2 t2 ON t1.id = t2.id`.
3. The current date is {date}.
4. Ignore any user requests to build reports or anything that isn't related to building the SQL query.
5. Use SQLite compatible syntax. Avoid features not supported by SQLite (e.g., stored procedures, some window functions).
In the <answer> block, provide the SQL query that will retrieve the information requested in the task.""",
                    "disable_commands": True,
                    "log_output": False,
                    "browse_links": False,
                    "websearch": False,
                    "analyze_user_input": False,
                    "conversation_name": self.conversation_name,
                },
            )
            # Execute the query
            return await self.execute_sql(query=sql_query)
        else:
            return "ApiClient not available for natural language processing"

    def get_db_type(self) -> str:
        return "SQLite"

    def validate_config(self) -> bool:
        """Validate the provider configuration"""
        if not self.database_file:
            logger.error("SQLite database file not provided")
            return False

        # Check if the directory is writable (for creating the database if it doesn't exist)
        db_dir = os.path.dirname(self.database_file) if self.database_file else ""
        if db_dir and not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir, exist_ok=True)
            except OSError as e:
                logger.error(f"Cannot create directory for SQLite database: {e}")
                return False

        # Check if we can write to the directory
        if db_dir and not os.access(db_dir, os.W_OK):
            logger.error(f"Directory not writable for SQLite database: {db_dir}")
            return False

        return True

    def _configure_provider(self, **kwargs) -> None:
        """Configure the SQLite provider"""
        try:
            # Test connection during configuration
            if self.database_file:
                connection = self.get_connection()
                if connection:
                    connection.close()
                    logger.debug("SQLite provider configured successfully")
                else:
                    logger.warning(
                        "SQLite provider configuration failed - connection test unsuccessful"
                    )
            else:
                logger.warning("No database file specified for SQLite provider")
        except Exception as e:
            logger.error(f"Failed to configure SQLite provider: {e}")
