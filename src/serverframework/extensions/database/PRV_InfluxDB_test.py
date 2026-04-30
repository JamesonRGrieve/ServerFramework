"""
Tests for PRV_InfluxDB provider.

Per AGENTS.md no-mock pillar (Item 72): tests that exercise live SDK
calls or the rotation/transport layer are tagged
``@pytest.mark.external_api(provider="influxdb")``. They run end-to-end
against a real InfluxDB instance when ``INFLUXDB_URL`` + ``INFLUXDB_TOKEN``
are set, and auto-xfail otherwise (Item 15).

Tests of pure-utility behavior (config-dict shape, validate_config logic
on a real config dict, classification metadata) are tagged
``@pytest.mark.unit`` and may locally import ``unittest.mock`` for
isolation per AGENTS.md.
"""

from typing import Dict

import pytest

from serverframework.extensions.database.PRV_InfluxDB import PRV_InfluxDB
from serverframework.extensions.database.EXT_Database import EXT_Database


class TestPRVInfluxDBMetadata:
    """Pure-metadata tests — no SDK, no mocks."""

    def test_provider_metadata(self):
        """Test static provider metadata"""
        assert PRV_InfluxDB.name == "InfluxDB"
        assert PRV_InfluxDB.friendly_name == "InfluxDB Time Series Database"
        assert PRV_InfluxDB.db_type == "influxdb"
        assert PRV_InfluxDB.extension == EXT_Database

    def test_provider_abilities(self):
        """Test provider abilities"""
        abilities = PRV_InfluxDB.get_abilities()

        assert isinstance(abilities, set)
        assert "database" in abilities
        assert "sql" in abilities
        assert "data_storage" in abilities
        assert "time_series" in abilities
        assert "metrics" in abilities
        assert "time_series_db" in abilities

    def test_provider_env_variables(self):
        """Test provider environment variables"""
        env_vars = PRV_InfluxDB._env

        assert isinstance(env_vars, dict)
        assert "INFLUXDB_URL" in env_vars
        assert "INFLUXDB_TOKEN" in env_vars
        assert "INFLUXDB_ORG" in env_vars
        assert "INFLUXDB_BUCKET" in env_vars
        assert "INFLUXDB_VERSION" in env_vars

    def test_get_db_classifications(self):
        """Test database classifications"""
        classifications = PRV_InfluxDB.get_db_classifications()

        assert isinstance(classifications, set)
        assert "time_series" in classifications

    def test_get_provider_info(self):
        """Test provider information"""
        info = PRV_InfluxDB.get_provider_info()

        assert info["name"] == "InfluxDB"
        assert info["type"] == "influxdb"
        assert "time_series" in info["classification"]
        assert isinstance(info["abilities"], list)


class TestPRVInfluxDBBondInstance:
    """Pure-utility tests for ``bond_instance`` config-dict storage.

    These exercise the deterministic in-memory config bookkeeping that
    runs before any SDK call. No real SDK is invoked; tagged
    ``@pytest.mark.unit`` per AGENTS.md so the local mock import is
    permitted.
    """

    @pytest.mark.unit
    def test_bond_instance_v2(self):
        """Test bonding with InfluxDB 2.x configuration."""
        from unittest.mock import patch

        config = {
            "influxdb_version": "2",
            "influxdb_url": "http://localhost:8086",
            "influxdb_token": "test_token",
            "influxdb_org": "test_org",
            "influxdb_bucket": "test_bucket",
        }

        with patch("serverframework.extensions.database.PRV_InfluxDB.has_influxdb2", True):
            PRV_InfluxDB.bond_instance(config)

            assert PRV_InfluxDB._connection_config["influxdb_version"] == "2"
            assert (
                PRV_InfluxDB._connection_config["influxdb_url"]
                == "http://localhost:8086"
            )
            assert PRV_InfluxDB._connection_config["influxdb_token"] == "test_token"

    @pytest.mark.unit
    def test_bond_instance_v1(self):
        """Test bonding with InfluxDB 1.x configuration."""
        from unittest.mock import MagicMock, patch

        config = {
            "influxdb_version": "1",
            "database_host": "localhost",
            "database_port": "8086",
            "database_name": "test_db",
            "database_username": "test_user",
            "database_password": "test_pass",
        }

        with patch("serverframework.extensions.database.PRV_InfluxDB.influxdb", MagicMock()):
            PRV_InfluxDB.bond_instance(config)

            assert PRV_InfluxDB._connection_config["influxdb_version"] == "1"
            assert PRV_InfluxDB._connection_config["database_host"] == "localhost"
            assert PRV_InfluxDB._connection_config["database_name"] == "test_db"

    @pytest.mark.unit
    def test_bond_instance_v2_missing_library(self):
        """Test bonding with InfluxDB 2.x when library is missing."""
        from unittest.mock import patch

        config = {"influxdb_version": "2"}

        with patch("serverframework.extensions.database.PRV_InfluxDB.has_influxdb2", False):
            with pytest.raises(
                ImportError, match="InfluxDB 2.x client library not installed"
            ):
                PRV_InfluxDB.bond_instance(config)

    @pytest.mark.unit
    def test_bond_instance_v1_missing_library(self):
        """Test bonding with InfluxDB 1.x when library is missing."""
        from unittest.mock import patch

        config = {"influxdb_version": "1"}

        with patch("serverframework.extensions.database.PRV_InfluxDB.influxdb", None):
            with pytest.raises(
                ImportError, match="InfluxDB 1.x client library not installed"
            ):
                PRV_InfluxDB.bond_instance(config)


class TestPRVInfluxDBValidateConfig:
    """Pure-utility tests for the synchronous ``validate_config`` method.

    These exercise input validation on a config dict — they don't talk to
    a live InfluxDB. Tagged ``@pytest.mark.unit`` per AGENTS.md.
    """

    @pytest.mark.unit
    def test_validate_config_v2_missing_fields(self):
        """validate_config flags missing required InfluxDB 2.x fields."""
        from unittest.mock import patch

        config = {
            "influxdb_version": "2",
            "influxdb_url": "",  # Missing
            "influxdb_token": "test_token",
            "influxdb_org": "",  # Missing
            "influxdb_bucket": "test_bucket",
        }
        PRV_InfluxDB._connection_config = config

        with patch("serverframework.extensions.database.PRV_InfluxDB.has_influxdb2", True):
            issues = PRV_InfluxDB.validate_config()

            assert len(issues) >= 2
            issue_text = " ".join(issues).lower()
            assert "influxdb url not configured" in issue_text
            assert "influxdb org not configured" in issue_text

    @pytest.mark.unit
    def test_validate_config_v1_missing_fields(self):
        """validate_config flags missing required InfluxDB 1.x fields."""
        from unittest.mock import MagicMock, patch

        config = {
            "influxdb_version": "1",
            "database_host": "",  # Missing
            "database_name": "test_db",
            "database_username": "",  # Missing
            "database_password": "test_pass",
        }
        PRV_InfluxDB._connection_config = config

        with patch("serverframework.extensions.database.PRV_InfluxDB.influxdb", MagicMock()):
            issues = PRV_InfluxDB.validate_config()

            assert len(issues) >= 2
            issue_text = " ".join(issues).lower()
            assert "database host not configured" in issue_text
            assert "database username not configured" in issue_text

    @pytest.mark.unit
    def test_validate_config_missing_libraries(self):
        """validate_config flags missing client libraries."""
        from unittest.mock import patch

        config = {"influxdb_version": "2"}
        PRV_InfluxDB._connection_config = config

        with patch("serverframework.extensions.database.PRV_InfluxDB.has_influxdb2", False):
            issues = PRV_InfluxDB.validate_config()

            assert len(issues) > 0
            assert any("2.x client library not installed" in issue for issue in issues)


class TestPRVInfluxDBLive:
    """Live-sandbox tests — auto-xfailed when INFLUXDB_URL/TOKEN are unset.

    These exercise the real SDK transport and can only run against a
    real InfluxDB instance. Per Item 15 they're tagged
    ``@pytest.mark.external_api(provider="influxdb")`` so CI without
    credentials marks them xfail rather than failing hard.
    """

    @pytest.mark.external_api(provider="influxdb")
    @pytest.mark.asyncio
    async def test_execute_sql_alias(self, sandbox_credentials_for):
        """``execute_sql`` is an alias for ``execute_query``."""
        creds = sandbox_credentials_for("influxdb")
        config = {
            "influxdb_version": "2",
            "influxdb_url": creds["INFLUXDB_URL"],
            "influxdb_token": creds["INFLUXDB_TOKEN"],
        }
        PRV_InfluxDB.bond_instance(config)
        result = await PRV_InfluxDB.execute_sql(
            'from(bucket:"_monitoring") |> range(start: -1h) |> limit(n:1)'
        )
        assert isinstance(result, str)

    @pytest.mark.external_api(provider="influxdb")
    @pytest.mark.asyncio
    async def test_get_schema_v2(self, sandbox_credentials_for):
        """Schema retrieval against a live InfluxDB 2.x bucket."""
        creds = sandbox_credentials_for("influxdb")
        config = {
            "influxdb_version": "2",
            "influxdb_url": creds["INFLUXDB_URL"],
            "influxdb_token": creds["INFLUXDB_TOKEN"],
            "influxdb_bucket": "_monitoring",
        }
        PRV_InfluxDB.bond_instance(config)
        result = await PRV_InfluxDB.get_schema()
        assert isinstance(result, str)

    @pytest.mark.external_api(provider="influxdb")
    @pytest.mark.asyncio
    async def test_chat_with_db_v2(self, sandbox_credentials_for):
        """Natural-language guidance for a live InfluxDB 2.x instance."""
        creds = sandbox_credentials_for("influxdb")
        config = {
            "influxdb_version": "2",
            "influxdb_url": creds["INFLUXDB_URL"],
            "influxdb_token": creds["INFLUXDB_TOKEN"],
        }
        PRV_InfluxDB.bond_instance(config)
        result = await PRV_InfluxDB.chat_with_db("Show me recent measurements")
        assert "natural language query" in result.lower()
        assert "flux" in result.lower()

    @pytest.mark.external_api(provider="influxdb")
    @pytest.mark.asyncio
    async def test_write_data_v2(self, sandbox_credentials_for):
        """Data write against a live InfluxDB 2.x bucket."""
        creds = sandbox_credentials_for("influxdb")
        config = {
            "influxdb_version": "2",
            "influxdb_url": creds["INFLUXDB_URL"],
            "influxdb_token": creds["INFLUXDB_TOKEN"],
            "influxdb_bucket": "_monitoring",
            "influxdb_org": "_test_org",
        }
        PRV_InfluxDB.bond_instance(config)
        result = await PRV_InfluxDB.write_data("temperature,sensor=1 value=23.5")
        assert isinstance(result, str)
