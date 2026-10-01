# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for PRV_InfluxDB provider.

Per AGENTS.md no-mock pillar (Item 72): tests that exercise live SDK
calls or the rotation/transport layer are tagged
``@pytest.mark.external_api(provider="influxdb")``. They run end-to-end
against a real InfluxDB instance when ``INFLUXDB_URL`` + ``INFLUXDB_TOKEN``
are set, and auto-xfail otherwise (Item 15).

Connection settings resolve from real provider instances of a built database
app. Tests that toggle whether a client library is installed are tagged
``@pytest.mark.unit`` and swap the module's driver flag with ``monkeypatch``.
"""

import pytest

from zephyrex.extensions.database import PRV_InfluxDB as influx_module
from zephyrex.extensions.database.EXT_Database import EXT_Database
from zephyrex.extensions.database.PRV_InfluxDB import PRV_InfluxDB
from zephyrex.extensions.ExternalErrors import TransientExternalError


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


class TestPRVInfluxDBConnectionConfig:
    """``connection_config``: the instance's token and settings win, the
    environment fills the rest."""

    def test_connection_config_v2(self, provider_instance, set_env):
        set_env("INFLUXDB_VERSION", "2")
        set_env("INFLUXDB_URL", "http://env:8086")
        set_env("INFLUXDB_TOKEN", "env_token")
        set_env("INFLUXDB_ORG", "env_org")
        set_env("INFLUXDB_BUCKET", "env_bucket")
        instance = provider_instance(
            PRV_InfluxDB,
            api_key="test_token",
            settings={
                "influxdb_url": "http://localhost:8086",
                "influxdb_org": "test_org",
                "bucket": "test_bucket",
            },
        )

        config = PRV_InfluxDB.connection_config(instance)

        assert config["influxdb_version"] == "2"
        assert config["influxdb_url"] == "http://localhost:8086"
        assert config["influxdb_token"] == "test_token"
        assert config["influxdb_org"] == "test_org"
        assert config["influxdb_bucket"] == "test_bucket"

    def test_connection_config_v2_env_fallback(self, provider_instance, set_env):
        set_env("INFLUXDB_VERSION", "2")
        set_env("INFLUXDB_URL", "http://env:8086")
        set_env("INFLUXDB_TOKEN", "env_token")
        set_env("INFLUXDB_ORG", "env_org")
        set_env("INFLUXDB_BUCKET", "env_bucket")

        config = PRV_InfluxDB.connection_config(provider_instance(PRV_InfluxDB))

        assert config["influxdb_url"] == "http://env:8086"
        assert config["influxdb_token"] == "env_token"
        assert config["influxdb_org"] == "env_org"
        assert config["influxdb_bucket"] == "env_bucket"

    def test_model_name_never_names_the_bucket(self, provider_instance, set_env):
        # The generic seed gives Root_InfluxDB model_name="InfluxDB"; that is
        # an AI model name column, not a bucket.
        set_env("INFLUXDB_VERSION", "2")
        set_env("INFLUXDB_BUCKET", "env_bucket")
        seeded_style = provider_instance(PRV_InfluxDB, model_name="InfluxDB")

        config = PRV_InfluxDB.connection_config(seeded_style)

        assert config["influxdb_bucket"] == "env_bucket"

    def test_connection_config_v1(self, provider_instance, set_env):
        set_env("INFLUXDB_VERSION", "2")
        instance = provider_instance(
            PRV_InfluxDB,
            api_key="test_pass",
            model_name="InfluxDB",
            settings={
                "influxdb_version": "1",
                "database_host": "localhost",
                "database_port": "8086",
                "database_name": "test_db",
                "database_username": "test_user",
            },
        )

        config = PRV_InfluxDB.connection_config(instance)

        assert config["influxdb_version"] == "1"
        assert config["database_host"] == "localhost"
        assert config["database_port"] == 8086
        assert config["database_name"] == "test_db"
        assert config["database_username"] == "test_user"
        assert config["database_password"] == "test_pass"

    @pytest.mark.unit
    async def test_v2_missing_library_raises_transient(
        self, provider_instance, monkeypatch
    ):
        monkeypatch.setattr(influx_module, "has_influxdb2", False)
        instance = provider_instance(PRV_InfluxDB, settings={"influxdb_version": "2"})

        with pytest.raises(TransientExternalError, match="influxdb-client"):
            await PRV_InfluxDB.execute_query(instance, "buckets()")

    @pytest.mark.unit
    async def test_v1_missing_library_raises_transient(
        self, provider_instance, monkeypatch
    ):
        monkeypatch.setattr(influx_module, "has_influxdb1", False)
        instance = provider_instance(PRV_InfluxDB, settings={"influxdb_version": "1"})

        with pytest.raises(TransientExternalError, match="influxdb package"):
            await PRV_InfluxDB.execute_query(instance, "SHOW DATABASES")

    @pytest.mark.unit
    async def test_v2_missing_configuration_raises_transient(
        self, provider_instance, monkeypatch, set_env
    ):
        monkeypatch.setattr(influx_module, "has_influxdb2", True)
        set_env("INFLUXDB_URL", "")
        set_env("INFLUXDB_ORG", "")
        instance = provider_instance(
            PRV_InfluxDB,
            api_key="test_token",
            settings={"influxdb_version": "2"},
        )

        with pytest.raises(TransientExternalError) as raised:
            await PRV_InfluxDB.execute_query(instance, "buckets()")

        assert "influxdb_url" in raised.value.message
        assert "influxdb_org" in raised.value.message


class TestPRVInfluxDBValidateConfig:
    """``validate_config`` checks the environment-configured connection."""

    @pytest.mark.unit
    def test_validate_config_v2_missing_fields(self, monkeypatch, set_env):
        """validate_config flags missing required InfluxDB 2.x fields."""
        monkeypatch.setattr(influx_module, "has_influxdb2", True)
        set_env("INFLUXDB_VERSION", "2")
        set_env("INFLUXDB_URL", "")
        set_env("INFLUXDB_TOKEN", "test_token")
        set_env("INFLUXDB_ORG", "")
        set_env("INFLUXDB_BUCKET", "test_bucket")

        issues = PRV_InfluxDB.validate_config()

        assert len(issues) >= 2
        issue_text = " ".join(issues).lower()
        assert "influxdb url not configured" in issue_text
        assert "influxdb org not configured" in issue_text

    @pytest.mark.unit
    def test_validate_config_v1_missing_fields(self, monkeypatch, set_env):
        """validate_config flags missing required InfluxDB 1.x fields."""
        monkeypatch.setattr(influx_module, "has_influxdb1", True)
        set_env("INFLUXDB_VERSION", "1")
        set_env("DATABASE_HOST", "")
        set_env("DATABASE_NAME", "test_db")
        set_env("DATABASE_USERNAME", "")
        set_env("DATABASE_PASSWORD", "test_pass")

        issues = PRV_InfluxDB.validate_config()

        assert len(issues) >= 2
        issue_text = " ".join(issues).lower()
        assert "database host not configured" in issue_text
        assert "database username not configured" in issue_text

    @pytest.mark.unit
    def test_validate_config_missing_libraries(self, monkeypatch, set_env):
        """validate_config flags missing client libraries."""
        monkeypatch.setattr(influx_module, "has_influxdb2", False)
        set_env("INFLUXDB_VERSION", "2")

        issues = PRV_InfluxDB.validate_config()

        assert any("2.x client library not installed" in issue for issue in issues)


class TestPRVInfluxDBLive:
    """Live-sandbox tests — auto-xfailed when INFLUXDB_URL/TOKEN are unset.

    These exercise the real SDK transport and can only run against a
    real InfluxDB instance. Per Item 15 they're tagged
    ``@pytest.mark.external_api(provider="influxdb")`` so CI without
    credentials marks them xfail rather than failing hard.
    """

    @pytest.fixture
    def live_instance(self, provider_instance, sandbox_credentials_for):
        creds = sandbox_credentials_for("influxdb")

        def _create(bucket=None, org=None):
            settings = {"influxdb_version": "2", "influxdb_url": creds["INFLUXDB_URL"]}
            if org:
                settings["influxdb_org"] = org
            if bucket:
                settings["bucket"] = bucket
            return provider_instance(
                PRV_InfluxDB, api_key=creds["INFLUXDB_TOKEN"], settings=settings
            )

        return _create

    @pytest.mark.external_api(provider="influxdb")
    @pytest.mark.asyncio
    async def test_execute_sql_alias(self, live_instance):
        """``execute_sql`` is an alias for ``execute_query``."""
        result = await PRV_InfluxDB.execute_sql(
            live_instance(),
            'from(bucket:"_monitoring") |> range(start: -1h) |> limit(n:1)',
        )
        assert isinstance(result, str)
        # A live query returns key=value rows or the explicit empty-result
        # marker; failures raise typed errors.
        assert result.strip(), result
        assert "error" not in result.lower(), result

    @pytest.mark.external_api(provider="influxdb")
    @pytest.mark.asyncio
    async def test_get_schema_v2(self, live_instance):
        """Schema retrieval against a live InfluxDB 2.x bucket."""
        result = await PRV_InfluxDB.get_schema(live_instance(bucket="_monitoring"))
        assert isinstance(result, str)
        # The schema must name the introspected bucket and list its
        # measurements -- an empty schema string would fail here.
        assert "_monitoring" in result, result
        assert "measurements" in result.lower(), result

    @pytest.mark.external_api(provider="influxdb")
    @pytest.mark.asyncio
    async def test_chat_with_db_v2(self, live_instance):
        """Natural-language guidance for a live InfluxDB 2.x instance."""
        result = await PRV_InfluxDB.chat_with_db(
            live_instance(bucket="_monitoring"), "Show me recent measurements"
        )
        assert "natural language query" in result.lower()
        assert "flux" in result.lower()

    @pytest.mark.external_api(provider="influxdb")
    @pytest.mark.asyncio
    async def test_write_data_v2(self, live_instance):
        """Data write against a live InfluxDB 2.x bucket."""
        result = await PRV_InfluxDB.write_data(
            live_instance(bucket="_monitoring", org="_test_org"),
            "temperature,sensor=1 value=23.5",
        )
        assert isinstance(result, str)
        assert "written successfully" in result.lower(), result
