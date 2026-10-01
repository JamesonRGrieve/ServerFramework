# SPDX-License-Identifier: AGPL-3.0-or-later
"""
InfluxDB database provider for AGInfrastructure.
Provides InfluxDB time-series database connectivity through the Provider
Rotation System. Supports both InfluxDB 1.x and 2.x APIs.

The rotated instance carries the credential in ``api_key`` (the 2.x token, or
the 1.x password); everything else, including the 2.x ``bucket`` and the 1.x
``database_name``, comes from its settings, each falling back to the
environment.
"""

import json
from typing import Any, ClassVar, Dict, List, Optional

from zephyrex.extensions.database.EXT_Database import (
    AbstractDatabaseExtensionProvider as AbstractDatabaseProvider,
    driver_installed,
)
from zephyrex.extensions.ExternalErrors import TransientExternalError
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

has_influxdb1 = driver_installed("influxdb")
has_influxdb2 = driver_installed("influxdb_client")

INFLUXDB_DEFAULT_VERSION = "2"
INFLUXDB_DEFAULT_PORT = 8086
INFLUXDB_V2_REQUIRED = (
    "influxdb_url",
    "influxdb_token",
    "influxdb_org",
    "influxdb_bucket",
)
INFLUXDB_V1_REQUIRED = (
    "database_host",
    "database_name",
    "database_username",
    "database_password",
)


class PRV_InfluxDB(AbstractDatabaseProvider):
    """
    InfluxDB time-series database provider implementation.
    Static/abstract provider compatible with Provider Rotation System.
    Supports both InfluxDB 1.x and 2.x APIs.
    """

    # Provider metadata
    name: ClassVar[str] = "InfluxDB"
    friendly_name: ClassVar[str] = "InfluxDB Time Series Database"
    description: ClassVar[str] = (
        "InfluxDB time-series database provider supporting 1.x and 2.x"
    )

    # Database type for this provider
    db_type: ClassVar[str] = "influxdb"

    # Environment variables this provider needs
    _env: ClassVar[Dict[str, Any]] = {
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

    @classmethod
    def connection_config(
        cls, instance: Optional[ProviderInstanceModel]
    ) -> Dict[str, Any]:
        influxdb_version = cls.resolve_setting(
            instance,
            "influxdb_version",
            "INFLUXDB_VERSION",
            default=INFLUXDB_DEFAULT_VERSION,
        )
        config: Dict[str, Any] = {
            "influxdb_version": influxdb_version,
            "database_host": cls.resolve_setting(
                instance, "database_host", "DATABASE_HOST"
            ),
            "database_port": cls.resolve_port(
                instance, "DATABASE_PORT", INFLUXDB_DEFAULT_PORT
            ),
        }
        if influxdb_version == "2":
            config.update(
                {
                    "influxdb_url": cls.resolve_setting(
                        instance, "influxdb_url", "INFLUXDB_URL"
                    ),
                    "influxdb_token": cls.resolve_setting(
                        instance, "influxdb_token", "INFLUXDB_TOKEN", field="api_key"
                    ),
                    "influxdb_org": cls.resolve_setting(
                        instance, "influxdb_org", "INFLUXDB_ORG"
                    ),
                    "influxdb_bucket": cls.resolve_setting(
                        instance, "bucket", "INFLUXDB_BUCKET"
                    ),
                }
            )
        else:
            config.update(
                {
                    "database_name": cls.resolve_setting(
                        instance, "database_name", "DATABASE_NAME"
                    ),
                    "database_username": cls.resolve_setting(
                        instance, "database_username", "DATABASE_USERNAME"
                    ),
                    "database_password": cls.resolve_setting(
                        instance,
                        "database_password",
                        "DATABASE_PASSWORD",
                        field="api_key",
                    ),
                }
            )
        return config

    @classmethod
    def _get_connection(cls, config: Dict[str, Any]) -> Any:
        """A client for the resolved configuration, verified reachable."""
        if config["influxdb_version"] == "2":
            cls.require_driver(has_influxdb2, "influxdb-client")
            cls.require_config(config, "influxdb_url", "influxdb_token", "influxdb_org")
            from influxdb_client import InfluxDBClient

            try:
                client = InfluxDBClient(
                    url=config["influxdb_url"],
                    token=config["influxdb_token"],
                    org=config["influxdb_org"],
                )
                reachable = client.ping()
            except Exception as exc:
                raise cls.connection_failed(exc) from exc
            if not reachable:
                client.close()
                raise TransientExternalError(
                    f"Error connecting to {cls.friendly_name}", provider=cls.name
                )
            return client

        cls.require_driver(has_influxdb1, "influxdb")
        cls.require_config(config, "database_host")
        from influxdb import InfluxDBClient as LegacyInfluxDBClient

        try:
            legacy_client = LegacyInfluxDBClient(
                host=config["database_host"],
                port=config["database_port"],
                username=config["database_username"],
                password=config["database_password"],
                database=config["database_name"],
            )
            legacy_client.ping()
        except Exception as exc:
            raise cls.connection_failed(exc) from exc
        return legacy_client

    @classmethod
    async def execute_sql(
        cls, instance: ProviderInstanceModel, query: str, **kwargs: Any
    ) -> str:
        """Execute an InfluxQL or Flux query."""
        return await cls.execute_query(instance, query, **kwargs)

    @staticmethod
    def _format_points(points: List[Dict[str, Any]]) -> str:
        output_lines = [
            ", ".join(f"{key}={value}" for key, value in point.items())
            for point in points
            if point
        ]
        return (
            "\n".join(output_lines)
            if output_lines
            else "Query executed successfully. No data returned."
        )

    @classmethod
    async def execute_query(
        cls, instance: ProviderInstanceModel, query: str, **kwargs: Any
    ) -> str:
        """Execute a database-specific query (InfluxQL for 1.x, Flux for 2.x)."""
        if "```" in query:
            # Extract the query from a Markdown code block.
            for part in query.split("```"):
                if "from(" in part or "SELECT" in part.upper():
                    query = part.strip()
                    break
        query = query.strip()
        logger.debug("Executing InfluxDB query: %s", query)

        config = cls.bond_instance(instance).config
        client = cls._get_connection(config)
        try:
            if config["influxdb_version"] == "2":
                bucket = config["influxdb_bucket"]
                # Scope a bare pipeline to the configured bucket.
                if (
                    "from(bucket:" not in query
                    and bucket
                    and not query.startswith("from(")
                ):
                    query = f'from(bucket:"{bucket}") |> {query}'
                points = [
                    {
                        key: value
                        for key, value in record.values.items()
                        if not key.startswith("_")
                    }
                    for table in client.query_api().query(query)
                    for record in table.records
                ]
            else:
                points = [
                    dict(point) for series in client.query(query) for point in series
                ]
        except Exception as exc:
            raise cls.query_failed(exc, "InfluxDB query") from exc
        finally:
            client.close()
        return cls._format_points(points)

    @classmethod
    async def get_schema(cls, instance: ProviderInstanceModel, **kwargs: Any) -> str:
        """Get the schema of the InfluxDB database."""
        config = cls.bond_instance(instance).config
        if config["influxdb_version"] == "2":
            cls.require_config(config, "influxdb_bucket")
        client = cls._get_connection(config)
        try:
            if config["influxdb_version"] == "2":
                bucket = config["influxdb_bucket"]
                measurements_query = f"""
                import "influxdata/influxdb/schema"
                schema.measurements(bucket: "{bucket}")
                """
                schema_info = [f"InfluxDB 2.x Bucket: {bucket}", "Measurements:"]
                for table in client.query_api().query(measurements_query):
                    for record in table.records:
                        if record.get_value():
                            schema_info.append(f"  - {record.get_value()}")
                return "\n".join(schema_info)

            schema_info = [
                f"InfluxDB 1.x Database: {config['database_name'] or 'N/A'}",
                "Measurements:",
            ]
            for series in client.query("SHOW MEASUREMENTS"):
                for point in series:
                    if "name" not in point:
                        continue
                    measurement = point["name"]
                    schema_info.append(f"  - {measurement}")
                    for field_series in client.query(
                        f'SHOW FIELD KEYS FROM "{measurement}"'
                    ):
                        for field_point in field_series:
                            if "fieldKey" in field_point:
                                schema_info.append(
                                    f"    Field: {field_point['fieldKey']} "
                                    f"({field_point.get('fieldType', 'unknown')})"
                                )
            return "\n".join(schema_info)
        except Exception as exc:
            raise cls.query_failed(exc, "schema query") from exc
        finally:
            client.close()

    @classmethod
    async def chat_with_db(
        cls, instance: ProviderInstanceModel, request: str, **kwargs: Any
    ) -> str:
        """Chat with InfluxDB using natural language query."""
        schema = await cls.get_schema(instance, **kwargs)
        influxdb_version = cls.bond_instance(instance).config["influxdb_version"]
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

    @classmethod
    async def write_data(
        cls, instance: ProviderInstanceModel, data: str, **kwargs: Any
    ) -> str:
        """Write data to the InfluxDB database: line protocol, or (1.x) JSON
        points."""
        logger.debug("Writing data to InfluxDB: %s...", data[:100])
        config = cls.bond_instance(instance).config
        if config["influxdb_version"] == "2":
            cls.require_config(config, "influxdb_bucket", "influxdb_org")
        client = cls._get_connection(config)
        try:
            if config["influxdb_version"] == "2":
                from influxdb_client.client.write_api import SYNCHRONOUS

                client.write_api(write_options=SYNCHRONOUS).write(
                    bucket=config["influxdb_bucket"],
                    org=config["influxdb_org"],
                    record=data,
                )
                return "Data written successfully to InfluxDB 2.x"

            if data.strip().startswith(("[", "{")):
                client.write_points(json.loads(data))
            else:
                client.write_points(data.splitlines(), protocol="line")
            return "Data written successfully to InfluxDB 1.x"
        except Exception as exc:
            raise cls.query_failed(exc, "InfluxDB write") from exc
        finally:
            client.close()

    @classmethod
    def validate_config(cls) -> List[str]:
        """Validate the environment-configured InfluxDB connection."""
        config = cls.connection_config(None)
        issues = []
        if config["influxdb_version"] == "2":
            version_label = "InfluxDB 2.x"
            required = INFLUXDB_V2_REQUIRED
            driver_available = has_influxdb2
        else:
            version_label = "InfluxDB 1.x"
            required = INFLUXDB_V1_REQUIRED
            driver_available = has_influxdb1
        if not driver_available:
            issues.append(f"{version_label} client library not installed")
        issues.extend(
            f"{version_label} {field.replace('_', ' ')} not configured"
            for field in required
            if not config.get(field)
        )

        if not issues:
            try:
                cls._get_connection(config).close()
            except TransientExternalError as e:
                issues.append(f"InfluxDB connection test failed: {e}")
        return issues
