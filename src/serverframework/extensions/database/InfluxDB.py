import json
from datetime import datetime, timezone
from typing import Optional

from serverframework.lib.Logging import logger

try:
    import influxdb
    from influxdb import InfluxDBClient
except ImportError:
    import subprocess
    import sys

    subprocess.check_call([sys.executable, "-m", "pip", "install", "influxdb"])
    import influxdb
    from influxdb import InfluxDBClient

try:
    # For InfluxDB 2.x
    import influxdb_client
    from influxdb_client import InfluxDBClient as InfluxDBClient2
    from influxdb_client.client.write_api import SYNCHRONOUS

    has_influxdb2 = True
except ImportError:
    has_influxdb2 = False

from serverframework.extensions.database.EXT_Database import (
    AbstractDatabaseExtensionProvider as AbstractDatabaseProvider,
)


class InfluxDBProvider(AbstractDatabaseProvider):
    """
    InfluxDB time-series database provider implementation.
    Supports both InfluxDB 1.x and 2.x APIs.
    """

    def __init__(
        self,
        api_key: str = "",
        api_uri: str = "",
        database_host: str = "localhost",
        database_port: int = 8086,
        database_name: str = "",
        database_username: str = "",
        database_password: str = "",
        influxdb_version: str = "1",  # "1" or "2"
        influxdb_org: str = "",  # For InfluxDB 2.x
        influxdb_token: str = "",  # For InfluxDB 2.x
        influxdb_bucket: str = "",  # For InfluxDB 2.x (equivalent to database in 1.x)
        extension_id: Optional[str] = None,
        **kwargs,
    ):
        self.influxdb_version = influxdb_version
        self.influxdb_org = influxdb_org
        self.influxdb_token = influxdb_token
        self.influxdb_bucket = influxdb_bucket

        super().__init__(
            api_key=api_key,
            api_uri=api_uri,
            database_host=database_host,
            database_port=database_port,
            database_name=database_name,
            database_username=database_username,
            database_password=database_password,
            extension_id=extension_id,
            **kwargs,
        )

        # Override commands dictionary for InfluxDB-specific commands
        self.commands = {
            "Execute InfluxDB Query": self.execute_query,
            "Get InfluxDB Schema": self.get_schema,
            "Chat with InfluxDB": self.chat_with_db,
            "Write Data to InfluxDB": self.write_data,
        }

        # Register InfluxDB-specific abilities
        self.register_ability("time_series")
        self.register_ability("metrics")

        # Note: _configure_provider is called by parent AbstractExtensionProvider class

    def _configure_provider(self, **kwargs) -> None:
        """Configure the InfluxDB provider"""
        if self.influxdb_version == "2" and not has_influxdb2:
            logger.error("InfluxDB 2.x client library not installed")
            return

        try:
            # Test connection during configuration
            client = self.get_connection()
            if client:
                client.close() if hasattr(client, "close") else None
                logger.debug("InfluxDB provider configured successfully")
            else:
                logger.warning(
                    "InfluxDB provider configuration failed - connection test unsuccessful"
                )
        except Exception as e:
            logger.error(f"Failed to configure InfluxDB provider: {e}")

    def get_connection(self):
        try:
            # Connect based on InfluxDB version
            if self.influxdb_version == "2":
                if not has_influxdb2:
                    logger.error("InfluxDB 2.x client library not installed")
                    return None

                # InfluxDB 2.x connection
                return InfluxDBClient2(
                    url=f"http://{self.database_host}:{self.database_port}",
                    token=self.influxdb_token,
                    org=self.influxdb_org,
                )
            else:
                # InfluxDB 1.x connection
                return InfluxDBClient(
                    host=self.database_host,
                    port=self.database_port,
                    username=self.database_username,
                    password=self.database_password,
                    database=self.database_name,
                )
        except Exception as e:
            logger.error(f"Error connecting to InfluxDB. Error: {str(e)}")
            return None

    async def execute_query(self, query: str, retry_count: int = 0) -> str:
        """
        Execute an InfluxDB query (InfluxQL for 1.x or Flux for 2.x).
        """
        # Clean up the query
        if "```" in query:
            # Extract query from code block
            lines = query.split("\n")
            in_code_block = False
            query_lines = []
            for line in lines:
                if line.strip().startswith("```"):
                    in_code_block = not in_code_block
                    continue
                if in_code_block:
                    query_lines.append(line)
            query = "\n".join(query_lines)

        query = query.strip()
        logger.debug(f"Executing InfluxDB query: {query}")

        client = self.get_connection()
        if not client:
            return "Error connecting to InfluxDB"

        try:
            if self.influxdb_version == "2":
                # InfluxDB 2.x - Use Flux query language
                query_api = client.query_api()
                result = query_api.query(query=query, org=self.influxdb_org)

                # Format the result as JSON
                formatted_results = []
                for table in result:
                    table_data = {"table": table.columns, "records": []}
                    for record in table.records:
                        table_data["records"].append(record.values)
                    formatted_results.append(table_data)

                client.close()
                return json.dumps(formatted_results, indent=2)

            else:
                # InfluxDB 1.x - Use InfluxQL
                result = client.query(query, database=self.database_name)

                # Format the result as JSON
                formatted_results = []
                for series in result.raw.get("series", []):
                    series_data = {
                        "name": series.get("name"),
                        "columns": series.get("columns", []),
                        "values": series.get("values", []),
                    }
                    formatted_results.append(series_data)

                client.close()
                return json.dumps(formatted_results, indent=2)

        except Exception as e:
            logger.error(f"Error executing InfluxDB query: {str(e)}")

            # Attempt to fix the query if it's invalid and we haven't retried too many times
            if hasattr(self, "ApiClient") and self.ApiClient and retry_count < 3:
                new_query = self.ApiClient.prompt_agent(
                    agent_name=self.agent_name,
                    prompt_name="Validate InfluxDB Query",
                    prompt_args={
                        "schema": await self.get_schema(),
                        "query": query,
                        "influxdb_version": self.influxdb_version,
                    },
                )
                return await self.execute_query(
                    query=new_query, retry_count=retry_count + 1
                )

            return f"Error executing InfluxDB query: {str(e)}"

    async def write_data(self, data: str) -> str:
        """
        Write data points to InfluxDB.
        Data should be formatted as JSON in the InfluxDB line protocol format.
        """
        if "```json" in data:
            data = data.split("```json")[1].split("```")[0]
        data = data.strip()

        logger.debug(f"Writing data to InfluxDB: {data}")

        try:
            # Parse the data JSON
            data_points = json.loads(data)

            client = self.get_connection()
            if not client:
                return "Error connecting to InfluxDB"

            # Write data based on InfluxDB version
            if self.influxdb_version == "2":
                # InfluxDB 2.x
                write_api = client.write_api(write_options=SYNCHRONOUS)

                # Determine bucket to use (parameter or default)
                bucket = data_points.get("bucket", self.influxdb_bucket)
                if not bucket:
                    return "Error: No bucket specified for writing data"

                # Extract points from the data
                points = data_points.get("points", [])
                if not points:
                    return "Error: No data points specified"

                # Write the points
                write_api.write(bucket=bucket, org=self.influxdb_org, record=points)
                client.close()
                return f"Successfully wrote {len(points)} points to InfluxDB bucket '{bucket}'"

            else:
                # InfluxDB 1.x
                # Determine database to use (parameter or default)
                database = data_points.get("database", self.database_name)
                if not database:
                    return "Error: No database specified for writing data"

                # Extract points from the data
                points = data_points.get("points", [])
                if not points:
                    return "Error: No data points specified"

                # Write the points
                client.write_points(points, database=database)
                client.close()
                return f"Successfully wrote {len(points)} points to InfluxDB database '{database}'"

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in data: {str(e)}")
            return f"Error: Invalid JSON data format: {str(e)}"

        except Exception as e:
            logger.error(f"Error writing data to InfluxDB: {str(e)}")
            return f"Error writing data to InfluxDB: {str(e)}"

    async def execute_sql(self, query: str) -> str:
        """
        SQL is not directly supported for InfluxDB, but we can translate SQL-like queries to InfluxQL/Flux.
        """
        if hasattr(self, "ApiClient") and self.ApiClient:
            influxdb_query = self.ApiClient.prompt_agent(
                agent_name=self.agent_name,
                prompt_name="SQL to InfluxDB",
                prompt_args={
                    "schema": await self.get_schema(),
                    "sql_query": query,
                    "influxdb_version": self.influxdb_version,
                },
            )
            return await self.execute_query(query=influxdb_query)
        else:
            if self.influxdb_version == "2":
                return "InfluxDB 2.x uses Flux query language, not SQL. Please use the 'Execute InfluxDB Query' command with Flux syntax or 'Chat with InfluxDB'."
            else:
                return "InfluxDB 1.x uses InfluxQL, not SQL. Please use the 'Execute InfluxDB Query' command with InfluxQL syntax or 'Chat with InfluxDB'."

    async def get_schema(self) -> str:
        logger.debug(
            f"Getting schema for InfluxDB {'bucket' if self.influxdb_version == '2' else 'database'} '{self.database_name or self.influxdb_bucket}'"
        )

        client = self.get_connection()
        if not client:
            return "Error connecting to InfluxDB"

        try:
            schema_info = []

            if self.influxdb_version == "2":
                # InfluxDB 2.x
                # Get buckets if no specific bucket is specified
                if not self.influxdb_bucket:
                    buckets_api = client.buckets_api()
                    buckets = buckets_api.find_buckets().buckets
                    schema_info.append(
                        f"Available buckets: {', '.join([b.name for b in buckets])}"
                    )

                # Get measurements and fields for the specified bucket
                bucket = self.influxdb_bucket
                if bucket:
                    query_api = client.query_api()

                    # Query for all measurements
                    measurements_query = f"""
                    import "influxdata/influxdb/schema"
                    schema.measurements(bucket: "{bucket}")
                    """
                    measurements_result = query_api.query(
                        query=measurements_query, org=self.influxdb_org
                    )

                    measurements = []
                    for table in measurements_result:
                        for record in table.records:
                            measurements.append(record.values.get("_value"))

                    # For each measurement, get field keys and tag keys
                    for measurement in measurements:
                        field_keys_query = f"""
                        import "influxdata/influxdb/schema"
                        schema.fieldKeys(bucket: "{bucket}", measurement: "{measurement}")
                        """
                        field_keys_result = query_api.query(
                            query=field_keys_query, org=self.influxdb_org
                        )

                        field_keys = []
                        for table in field_keys_result:
                            for record in table.records:
                                field_keys.append(record.values.get("_value"))

                        tag_keys_query = f"""
                        import "influxdata/influxdb/schema"
                        schema.tagKeys(bucket: "{bucket}", measurement: "{measurement}")
                        """
                        tag_keys_result = query_api.query(
                            query=tag_keys_query, org=self.influxdb_org
                        )

                        tag_keys = []
                        for table in tag_keys_result:
                            for record in table.records:
                                tag_keys.append(record.values.get("_value"))

                        # Add to schema info
                        schema_info.append(f"Measurement: {measurement}")
                        schema_info.append(f"  Fields: {', '.join(field_keys)}")
                        schema_info.append(f"  Tags: {', '.join(tag_keys)}")

            else:
                # InfluxDB 1.x
                # Get databases if no specific database is specified
                if not self.database_name:
                    databases = client.get_list_database()
                    schema_info.append(
                        f"Available databases: {', '.join([db['name'] for db in databases])}"
                    )

                # Get measurements for the specified database
                database = self.database_name
                if database:
                    client.switch_database(database)

                    # Get all measurements
                    measurements = client.get_list_measurements()

                    # For each measurement, get field keys and tag keys
                    for measurement in measurements:
                        measurement_name = measurement["name"]

                        # Get field keys
                        field_keys_query = f"SHOW FIELD KEYS FROM {measurement_name}"
                        field_keys_result = client.query(field_keys_query)

                        field_keys = []
                        for point in field_keys_result.get_points():
                            field_keys.append(
                                f"{point['fieldKey']} ({point['fieldType']})"
                            )

                        # Get tag keys
                        tag_keys_query = f"SHOW TAG KEYS FROM {measurement_name}"
                        tag_keys_result = client.query(tag_keys_query)

                        tag_keys = []
                        for point in tag_keys_result.get_points():
                            tag_keys.append(point["tagKey"])

                        # Add to schema info
                        schema_info.append(f"Measurement: {measurement_name}")
                        schema_info.append(f"  Fields: {', '.join(field_keys)}")
                        schema_info.append(f"  Tags: {', '.join(tag_keys)}")

                        # Get recent time range
                        try:
                            time_query = f"SELECT first(*)::field, last(*)::field FROM {measurement_name}"
                            time_result = client.query(time_query)

                            # Add time range info if available
                            if len(time_result) > 0:
                                first_point = next(time_result.get_points(), None)
                                last_point = list(time_result.get_points())[-1]

                                if (
                                    first_point
                                    and "time" in first_point
                                    and last_point
                                    and "time" in last_point
                                ):
                                    schema_info.append(
                                        f"  Time Range: {first_point['time']} to {last_point['time']}"
                                    )
                        except Exception as e:
                            logger.debug(f"Error getting time range: {str(e)}")

            client.close()

            if not schema_info:
                return "No schema information available"

            return "\n".join(schema_info)

        except Exception as e:
            logger.error(f"Error getting InfluxDB schema: {str(e)}")
            return f"Error getting InfluxDB schema: {str(e)}"

    async def chat_with_db(self, request: str) -> str:
        # Get the schema for the database
        schema = await self.get_schema()

        # Generate InfluxDB query based on the schema and natural language query
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        if hasattr(self, "ApiClient") and self.ApiClient:
            # Choose the appropriate query language based on InfluxDB version
            query_language = "Flux" if self.influxdb_version == "2" else "InfluxQL"

            influxdb_query = self.ApiClient.prompt_agent(
                agent_name=self.agent_name,
                prompt_name="Think About It",
                prompt_args={
                    "user_input": f"""### Task
Generate an {query_language} query to answer the following:
`{request}`
### Database Schema
The query will run on InfluxDB {self.influxdb_version}.x with the following schema:
{schema}
### {query_language} Query
Follow these steps to create the {query_language} Query:
1. Only use the measurements, fields, and tags present in the database schema
2. The current date is {date}
3. Format dates in RFC3339 format (e.g., 2023-06-15T10:00:00Z)
4. Use appropriate time ranges based on the request
5. Consider using aggregation functions (mean, sum, count, etc.) for time-series data
6. Remember that InfluxDB is a time-series database, so most queries will involve time ranges
7. {'Use Flux pipe-forward syntax (|>) for InfluxDB 2.x' if self.influxdb_version == '2' else 'Use InfluxQL SQL-like syntax for InfluxDB 1.x'}
In the <answer> block, provide the {query_language} query that will retrieve the information requested in the task.""",
                    "disable_commands": True,
                    "log_output": False,
                    "browse_links": False,
                    "websearch": False,
                    "analyze_user_input": False,
                    "conversation_name": self.conversation_name,
                },
            )
            # Execute the query
            return await self.execute_query(query=influxdb_query)
        else:
            return "ApiClient not available for natural language processing"

    def get_db_type(self) -> str:
        return f"InfluxDB {self.influxdb_version}.x"

    def validate_config(self) -> bool:
        """Validate the provider configuration"""
        if self.influxdb_version == "2":
            if not has_influxdb2:
                logger.error("InfluxDB 2.x client library not available")
                return False

            if not self.influxdb_token:
                logger.error("InfluxDB 2.x token not provided")
                return False

            if not self.influxdb_org:
                logger.warning(
                    "InfluxDB 2.x organization not provided - may cause issues"
                )
                return False
        else:
            if not self.database_username or not self.database_password:
                logger.warning(
                    "InfluxDB 1.x credentials not provided - may cause issues"
                )
                return False

        if not self.database_host:
            logger.error("InfluxDB host not provided")
            return False

        return True
