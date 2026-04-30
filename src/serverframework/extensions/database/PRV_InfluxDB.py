"""
InfluxDB database provider for AGInfrastructure.
Provides InfluxDB time-series database connectivity through the Provider Rotation System.
Fully static implementation compatible with the Provider Rotation System.
Supports both InfluxDB 1.x and 2.x APIs.
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from serverframework.extensions.database.EXT_Database import (
    AbstractDatabaseExtensionProvider as AbstractDatabaseProvider,
)
from serverframework.lib.Environment import env
from serverframework.lib.Logging import logger

try:
    import influxdb
    from influxdb import InfluxDBClient
except ImportError:
    influxdb = None
    InfluxDBClient = None

try:
    # For InfluxDB 2.x
    import influxdb_client
    from influxdb_client import InfluxDBClient as InfluxDBClient2
    from influxdb_client.client.write_api import SYNCHRONOUS

    has_influxdb2 = True
except ImportError:
    has_influxdb2 = False
    influxdb_client = None
    InfluxDBClient2 = None
    SYNCHRONOUS = None


class PRV_InfluxDB(AbstractDatabaseProvider):
    """
    InfluxDB time-series database provider implementation.
    Static/abstract provider compatible with Provider Rotation System.
    Supports both InfluxDB 1.x and 2.x APIs.
    """

    # Provider metadata
    name: str = "InfluxDB"
    friendly_name: str = "InfluxDB Time Series Database"
    description: str = "InfluxDB time-series database provider supporting 1.x and 2.x"

    # Database type for this provider
    db_type: str = "influxdb"

    # Environment variables this provider needs
    _env: Dict[str, Any] = {
        "INFLUXDB_URL": "",
        "INFLUXDB_TOKEN": "",
        "INFLUXDB_ORG": "",
        "INFLUXDB_BUCKET": "",
        "INFLUXDB_VERSION": "2",
        "DATABASE_HOST": "localhost",
        "DATABASE_PORT": "8086",
        "DATABASE_NAME": "",
        "DATABASE_USERNAME": "",
        "DATABASE_PASSWORD": "",
    }

    # Provider-specific abilities
    _abilities = {
        "database",
        "sql",
        "data_storage",
        "time_series",
        "metrics",
        "time_series_db",
    }

    # Class-level connection configuration
    _connection_config: Dict[str, Any] = {}

    @classmethod
    def bond_instance(cls, config: Dict[str, Any]) -> None:
        """
        Configure the InfluxDB provider with the given configuration.
        This is called once during provider initialization.
        """
        try:
            # Determine InfluxDB version
            influxdb_version = config.get("influxdb_version") or env(
                "INFLUXDB_VERSION", "2"
            )

            # Common configuration
            cls._connection_config = {
                "influxdb_version": influxdb_version,
                "database_host": config.get("database_host")
                or env("DATABASE_HOST", "localhost"),
                "database_port": int(
                    config.get("database_port") or env("DATABASE_PORT", "8086")
                ),
                **config,
            }

            if influxdb_version == "2":
                # InfluxDB 2.x configuration
                cls._connection_config.update(
                    {
                        "influxdb_url": config.get("influxdb_url")
                        or env("INFLUXDB_URL"),
                        "influxdb_token": config.get("influxdb_token")
                        or env("INFLUXDB_TOKEN"),
                        "influxdb_org": config.get("influxdb_org")
                        or env("INFLUXDB_ORG"),
                        "influxdb_bucket": config.get("influxdb_bucket")
                        or env("INFLUXDB_BUCKET"),
                    }
                )

                if not has_influxdb2:
                    raise ImportError("InfluxDB 2.x client library not installed")

            else:
                # InfluxDB 1.x configuration
                cls._connection_config.update(
                    {
                        "database_name": config.get("database_name")
                        or env("DATABASE_NAME"),
                        "database_username": config.get("database_username")
                        or env("DATABASE_USERNAME"),
                        "database_password": config.get("database_password")
                        or env("DATABASE_PASSWORD"),
                    }
                )

                if not influxdb:
                    raise ImportError("InfluxDB 1.x client library not installed")

            logger.debug(f"InfluxDB {influxdb_version}.x provider bonded successfully")

        except Exception as e:
            logger.error(f"Failed to configure InfluxDB provider: {e}")
            raise

    @classmethod
    def _get_connection(cls):
        """Get a connection to the InfluxDB database."""
        try:
            config = cls._connection_config
            influxdb_version = config.get("influxdb_version", "2")

            if influxdb_version == "2":
                # InfluxDB 2.x connection
                if not has_influxdb2:
                    logger.error("InfluxDB 2.x client library not available")
                    return None

                url = config.get("influxdb_url")
                token = config.get("influxdb_token")
                org = config.get("influxdb_org")

                if not all([url, token, org]):
                    logger.error("InfluxDB 2.x connection parameters missing")
                    return None

                return InfluxDBClient2(url=url, token=token, org=org)

            else:
                # InfluxDB 1.x connection
                if not influxdb:
                    logger.error("InfluxDB 1.x client library not available")
                    return None

                host = config.get("database_host", "localhost")
                port = config.get("database_port", 8086)
                username = config.get("database_username")
                password = config.get("database_password")
                database = config.get("database_name")

                return InfluxDBClient(
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    database=database,
                )

        except Exception as e:
            logger.error(f"Error connecting to InfluxDB: {e}")
            return None

    @classmethod
    async def execute_sql(cls, query: str, **kwargs) -> str:
        """Execute an InfluxQL or Flux query."""
        return await cls.execute_query(query, **kwargs)

    @classmethod
    async def execute_query(cls, query: str, **kwargs) -> str:
        """Execute a database-specific query (InfluxQL for 1.x, Flux for 2.x)."""
        try:
            # Clean up query format
            if "```" in query:
                # Extract query from code blocks
                parts = query.split("```")
                for part in parts:
                    if "from(" in part or "SELECT" in part.upper():
                        query = part.strip()
                        break

            query = query.strip()
            logger.debug(f"Executing InfluxDB query: {query}")

            client = cls._get_connection()
            if not client:
                return "Error connecting to InfluxDB"

            config = cls._connection_config
            influxdb_version = config.get("influxdb_version", "2")

            try:
                if influxdb_version == "2":
                    # InfluxDB 2.x - use Flux query
                    query_api = client.query_api()
                    bucket = config.get("influxdb_bucket")

                    # If query doesn't specify bucket, add default bucket reference
                    if "from(bucket:" not in query and bucket:
                        query = (
                            f'from(bucket:"{bucket}") |> {query}'
                            if not query.startswith("from(")
                            else query
                        )

                    result = query_api.query(query)

                    # Format results
                    output_lines = []
                    for table in result:
                        for record in table.records:
                            values = []
                            for key, value in record.values.items():
                                if key.startswith("_"):
                                    continue
                                values.append(f"{key}={value}")
                            if values:
                                output_lines.append(", ".join(values))

                    return (
                        "\n".join(output_lines)
                        if output_lines
                        else "Query executed successfully. No data returned."
                    )

                else:
                    # InfluxDB 1.x - use InfluxQL
                    result = client.query(query)

                    # Format results
                    output_lines = []
                    for series in result:
                        for point in series:
                            values = []
                            for key, value in point.items():
                                values.append(f"{key}={value}")
                            output_lines.append(", ".join(values))

                    return (
                        "\n".join(output_lines)
                        if output_lines
                        else "Query executed successfully. No data returned."
                    )

            finally:
                if hasattr(client, "close"):
                    client.close()

        except Exception as e:
            logger.error(f"Error executing InfluxDB query: {e}")
            return f"Error executing query: {str(e)}"

    @classmethod
    async def get_schema(cls, **kwargs) -> str:
        """Get the schema of the InfluxDB database."""
        try:
            logger.debug("Getting InfluxDB database schema")

            client = cls._get_connection()
            if not client:
                return "Error connecting to InfluxDB"

            config = cls._connection_config
            influxdb_version = config.get("influxdb_version", "2")

            try:
                if influxdb_version == "2":
                    # InfluxDB 2.x - list measurements and fields
                    query_api = client.query_api()
                    bucket = config.get("influxdb_bucket")

                    if not bucket:
                        return "No bucket configured for InfluxDB 2.x"

                    # Get measurements
                    measurements_query = f"""
                    import "influxdata/influxdb/schema"
                    schema.measurements(bucket: "{bucket}")
                    """

                    result = query_api.query(measurements_query)
                    measurements = []
                    for table in result:
                        for record in table.records:
                            if hasattr(record, "get_value") and record.get_value():
                                measurements.append(record.get_value())

                    schema_info = [f"InfluxDB 2.x Bucket: {bucket}"]
                    schema_info.append("Measurements:")
                    for measurement in measurements:
                        schema_info.append(f"  - {measurement}")

                    return "\n".join(schema_info)

                else:
                    # InfluxDB 1.x - show measurements and series
                    measurements_result = client.query("SHOW MEASUREMENTS")

                    schema_info = [
                        f"InfluxDB 1.x Database: {config.get('database_name', 'N/A')}"
                    ]
                    schema_info.append("Measurements:")

                    for series in measurements_result:
                        for point in series:
                            if "name" in point:
                                measurement = point["name"]
                                schema_info.append(f"  - {measurement}")

                                # Get field keys for this measurement
                                try:
                                    fields_result = client.query(
                                        f'SHOW FIELD KEYS FROM "{measurement}"'
                                    )
                                    for field_series in fields_result:
                                        for field_point in field_series:
                                            if "fieldKey" in field_point:
                                                schema_info.append(
                                                    f"    Field: {field_point['fieldKey']} ({field_point.get('fieldType', 'unknown')})"
                                                )
                                except Exception:
                                    pass

                    return "\n".join(schema_info)

            finally:
                if hasattr(client, "close"):
                    client.close()

        except Exception as e:
            logger.error(f"Error getting InfluxDB schema: {e}")
            return f"Error getting database schema: {str(e)}"

    @classmethod
    async def chat_with_db(cls, request: str, **kwargs) -> str:
        """Chat with InfluxDB using natural language query."""
        try:
            # Get the schema for context
            schema = await cls.get_schema(**kwargs)

            config = cls._connection_config
            influxdb_version = config.get("influxdb_version", "2")

            # Return basic guidance since we don't have AI service integration in static context
            query_language = "Flux" if influxdb_version == "2" else "InfluxQL"

            return f"""Natural language query: "{request}"

Database schema:
{schema}

To query this InfluxDB {influxdb_version}.x database, you need to write {query_language} queries.

{query_language} Query Guidelines:
""" + (
                """
- Use Flux syntax: from(bucket:"your_bucket") |> range(start: -1h) |> filter(fn: (r) => r._measurement == "measurement_name")
- Time ranges: range(start: -1h), range(start: -1d), range(start: -1w)
- Filters: filter(fn: (r) => r._field == "field_name")
- Aggregations: aggregateWindow(every: 1m, fn: mean)
"""
                if influxdb_version == "2"
                else """
- Use InfluxQL syntax: SELECT field FROM measurement WHERE time > now() - 1h
- Time ranges: WHERE time > now() - 1h, WHERE time > now() - 1d
- Filters: WHERE tag_name = 'value'
- Aggregations: SELECT MEAN(field) FROM measurement GROUP BY time(1m)
"""
            )

        except Exception as e:
            logger.error(f"Error in InfluxDB chat_with_db: {e}")
            return f"Error processing natural language query: {str(e)}"

    @classmethod
    async def write_data(cls, data: str, **kwargs) -> str:
        """Write data to the InfluxDB database."""
        try:
            logger.debug(f"Writing data to InfluxDB: {data[:100]}...")

            client = cls._get_connection()
            if not client:
                return "Error connecting to InfluxDB"

            config = cls._connection_config
            influxdb_version = config.get("influxdb_version", "2")

            try:
                if influxdb_version == "2":
                    # InfluxDB 2.x - use line protocol
                    write_api = client.write_api(write_options=SYNCHRONOUS)
                    bucket = config.get("influxdb_bucket")
                    org = config.get("influxdb_org")

                    if not bucket or not org:
                        return "Bucket or organization not configured for InfluxDB 2.x"

                    write_api.write(bucket=bucket, org=org, record=data)
                    return "Data written successfully to InfluxDB 2.x"

                else:
                    # InfluxDB 1.x - use line protocol or JSON
                    if data.strip().startswith("[") or data.strip().startswith("{"):
                        # JSON format
                        json_data = json.loads(data)
                        client.write_points(json_data)
                    else:
                        # Line protocol format
                        client.write_points_from_dataframe(data)

                    return "Data written successfully to InfluxDB 1.x"

            finally:
                if hasattr(client, "close"):
                    client.close()

        except Exception as e:
            logger.error(f"Error writing data to InfluxDB: {e}")
            return f"Error writing data: {str(e)}"

    @classmethod
    def validate_config(cls) -> List[str]:
        """Validate the InfluxDB provider configuration."""
        issues = []

        try:
            config = cls._connection_config
            influxdb_version = config.get("influxdb_version", "2")

            if influxdb_version == "2":
                # InfluxDB 2.x validation
                required_fields = [
                    "influxdb_url",
                    "influxdb_token",
                    "influxdb_org",
                    "influxdb_bucket",
                ]
                for field in required_fields:
                    if not config.get(field):
                        issues.append(
                            f"InfluxDB 2.x {field.replace('_', ' ')} not configured"
                        )

                if not has_influxdb2:
                    issues.append("InfluxDB 2.x client library not installed")

            else:
                # InfluxDB 1.x validation
                required_fields = [
                    "database_host",
                    "database_name",
                    "database_username",
                    "database_password",
                ]
                for field in required_fields:
                    if not config.get(field):
                        issues.append(
                            f"InfluxDB 1.x {field.replace('_', ' ')} not configured"
                        )

                if not influxdb:
                    issues.append("InfluxDB 1.x client library not installed")

            # Test connection if no configuration issues
            if not issues:
                try:
                    client = cls._get_connection()
                    if client:
                        if hasattr(client, "close"):
                            client.close()
                    else:
                        issues.append("Cannot establish InfluxDB connection")
                except Exception as e:
                    issues.append(f"InfluxDB connection test failed: {e}")

        except Exception as e:
            issues.append(f"InfluxDB configuration validation error: {e}")

        return issues
