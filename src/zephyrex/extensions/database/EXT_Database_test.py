# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for EXT_Database extension (Item 72 — no mocks).

These tests exercise the real `AbstractDatabaseExtensionProvider`
contract via `PRV_Fake_Database`, a real-class fake (not a mock) that
satisfies the provider interface with deterministic in-memory state.
The previous version patched `EXT_Database.root.rotate(...)` — that
violated AGENTS.md's no-BLL-mocks pillar and is removed. Provider methods
are called directly with a real provider instance, and the failover tests
drive a real rotation of SQLite instances.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest

from zephyrex.extensions.AbstractEXTTest import (
    ExtensionServerMixin,
    ExtensionTestConfig,
    ExtensionTestType,
)
from zephyrex.extensions.database.EXT_Database import (
    AbstractDatabaseExtensionProvider,
    EXT_Database,
)
from zephyrex.extensions.database.PRV_Fake_Database import PRV_Fake_Database
from zephyrex.extensions.database.PRV_SQLite import PRV_SQLite
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceModel,
    RotationManager,
    RotationModel,
    RotationProviderInstanceManager,
    RotationProviderInstanceModel,
)
from zephyrex.pydantic2.registry import ModelRegistry, classproperty


class ConcreteDatabaseProvider(AbstractDatabaseExtensionProvider):
    """Concrete implementation of AbstractDatabaseExtensionProvider for testing.

    A second real provider (alongside ``PRV_Fake_Database``) used by tests
    that need a distinct instance for ability/metadata assertions.
    """

    name = "test_database"
    friendly_name = "Test Database Provider"
    description = "Test database provider for unit tests"
    db_type = "test"

    _env = {
        "TEST_DATABASE_URL": "test://localhost:1234/testdb",
        "TEST_DATABASE_TOKEN": "test_token",
    }

    _abilities = {
        "database",
        "sql",
        "data_storage",
        "test_ability",
    }

    extension = EXT_Database

    @classmethod
    def connection_config(
        cls, instance: Optional[ProviderInstanceModel]
    ) -> Dict[str, Any]:
        return {"url": cls.resolve_setting(instance, "url", "TEST_DATABASE_URL")}

    @classmethod
    async def execute_sql(
        cls, instance: ProviderInstanceModel, query: str, **kwargs: Any
    ) -> str:
        return f"Test SQL executed: {query}"

    @classmethod
    async def get_schema(cls, instance: ProviderInstanceModel, **kwargs: Any) -> str:
        return "CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT);"

    @classmethod
    async def chat_with_db(
        cls, instance: ProviderInstanceModel, request: str, **kwargs: Any
    ) -> str:
        return f"Test chat response for: {request}"

    @classmethod
    async def execute_query(
        cls, instance: ProviderInstanceModel, query: str, **kwargs: Any
    ) -> str:
        return f"Test query executed: {query}"

    @classmethod
    async def write_data(
        cls, instance: ProviderInstanceModel, data: str, **kwargs: Any
    ) -> str:
        return f"Test data written: {data}"

    @classmethod
    def validate_config(cls) -> List[str]:
        return []


@pytest.fixture
def fake_instance(
    provider_instance: Callable[..., ProviderInstanceModel],
) -> ProviderInstanceModel:
    """A real provider instance of the fake database provider."""
    return provider_instance(PRV_Fake_Database)


@pytest.fixture(autouse=True)
def _reset_fake_provider():
    """Reset captured state on PRV_Fake_Database between tests."""
    PRV_Fake_Database.reset()
    yield
    PRV_Fake_Database.reset()


def _use_providers(monkeypatch, provider_list):
    """Make ``EXT_Database.providers`` return exactly ``provider_list``
    instead of scanning the extension directory."""
    monkeypatch.setattr(
        EXT_Database, "providers", classproperty(lambda _cls: list(provider_list))
    )


@pytest.fixture
def fake_only_providers(monkeypatch):
    """Install [PRV_Fake_Database] as the active provider list (real, not mocked)."""
    _use_providers(monkeypatch, [PRV_Fake_Database])


@pytest.fixture
def both_providers(monkeypatch):
    """Install both real fake providers as the active provider list."""
    _use_providers(monkeypatch, [PRV_Fake_Database, ConcreteDatabaseProvider])


@pytest.fixture
def no_providers(monkeypatch):
    """Install an empty provider list."""
    _use_providers(monkeypatch, [])


class TestEXTDatabase(ExtensionServerMixin):
    """
    Test suite for EXT_Database extension.
    Tests static extension functionality with the Provider Rotation System
    via real fake providers (no mocks per Item 72).
    """

    extension_class = EXT_Database

    test_config = ExtensionTestConfig(
        test_types={
            ExtensionTestType.STRUCTURE,
            ExtensionTestType.METADATA,
            ExtensionTestType.DEPENDENCIES,
            ExtensionTestType.ABILITIES,
            ExtensionTestType.ENVIRONMENT,
            ExtensionTestType.ROTATION,
        },
        expected_abilities={
            "database_query",
            "database_schema",
            "database_chat",
            "sql_execution",
            "data_storage",
            "relational_db",
            "nosql_db",
            "time_series_db",
        },
        expected_env_vars={
            "DATABASE_TYPE": "sqlite",
            "DATABASE_HOST": "",
            "DATABASE_PORT": "",
            "DATABASE_NAME": "",
            "DATABASE_USERNAME": "",
            "DATABASE_PASSWORD": "",
            "DATABASE_FILE": "",
            "INFLUXDB_VERSION": "2",
            "INFLUXDB_ORG": "",
            "INFLUXDB_TOKEN": "",
            "INFLUXDB_BUCKET": "",
        },
    )

    def test_extension_metadata(self):
        assert EXT_Database.name == "database"
        assert EXT_Database.friendly_name == "Database Connectivity"
        assert EXT_Database.version == "1.0.0"
        assert "database connectivity" in EXT_Database.description.lower()

    def test_provider_discovery(self):
        # Test that provider discovery works through filesystem scanning.
        # Clear the cache to force re-discovery.
        EXT_Database._providers = []
        providers = EXT_Database.providers
        assert isinstance(providers, list)

    def test_get_abilities(self, both_providers):
        abilities = EXT_Database.get_abilities()
        for ability in EXT_Database._abilities:
            assert ability in abilities
        for ability in ConcreteDatabaseProvider._abilities:
            assert ability in abilities

    def test_has_ability(self, both_providers):
        assert EXT_Database.has_ability("database_query")
        assert EXT_Database.has_ability("sql_execution")
        assert EXT_Database.has_ability("test_ability")  # From ConcreteDatabaseProvider
        assert not EXT_Database.has_ability("nonexistent_ability")

    def test_get_database_classifications(self):
        all_classifications = EXT_Database.get_database_classifications()
        assert "relational" in all_classifications
        assert "document" in all_classifications
        assert "time_series" in all_classifications
        assert "graph" in all_classifications

        postgres_classifications = EXT_Database.get_database_classifications("postgres")
        assert "relational" in postgres_classifications
        assert postgres_classifications["relational"] == ["postgres"]

    def test_get_default_port(self):
        assert EXT_Database.get_default_port("postgres") == 5432
        assert EXT_Database.get_default_port("mysql") == 3306
        assert EXT_Database.get_default_port("mongodb") == 27017
        assert EXT_Database.get_default_port("influxdb") == 8086
        assert EXT_Database.get_default_port("sqlite") == 0
        assert EXT_Database.get_default_port("unknown") == 0

    def test_get_provider_names(self, both_providers):
        provider_names = EXT_Database.get_provider_names()
        assert "fake_database" in provider_names
        assert "test_database" in provider_names

    def test_validate_config_no_providers(self, no_providers):
        issues = EXT_Database.validate_config()
        assert len(issues) > 0
        assert any(
            "no database providers available" in issue.lower() for issue in issues
        )

    def test_validate_config_with_providers(self, both_providers):
        issues = EXT_Database.validate_config()
        assert isinstance(issues, list)

    # -----------------------------------------------------------------
    # Per-provider method tests (replacing the mock-based rotation tests)
    # -----------------------------------------------------------------
    # The previous version patched EXT_Database.root.rotate to assert the
    # rotation reached the provider. Item 72 replaces that with direct
    # calls against the real PRV_Fake_Database — the actual unit of work
    # the rotation system delegates to. The rotation infrastructure is
    # exercised by integration tests that boot a real ModelRegistry.

    @pytest.mark.asyncio
    async def test_provider_execute_sql(self, fake_instance):
        result = await PRV_Fake_Database.execute_sql(
            fake_instance, "SELECT * FROM test"
        )
        assert result == "fake-sql:SELECT * FROM test"
        assert "SELECT * FROM test" in PRV_Fake_Database.executed_queries

    @pytest.mark.asyncio
    async def test_provider_get_schema(self, fake_instance):
        result = await PRV_Fake_Database.get_schema(fake_instance)
        assert "CREATE TABLE" in result

    @pytest.mark.asyncio
    async def test_provider_chat_with_db(self, fake_instance):
        result = await PRV_Fake_Database.chat_with_db(
            fake_instance, "Show me all users"
        )
        assert result == "fake-chat:Show me all users"
        assert PRV_Fake_Database.last_request == "Show me all users"

    @pytest.mark.asyncio
    async def test_provider_execute_query(self, fake_instance):
        result = await PRV_Fake_Database.execute_query(
            fake_instance, "FROM bucket |> range(start: -1h)"
        )
        assert result.startswith("fake-query:")

    @pytest.mark.asyncio
    async def test_provider_write_data(self, fake_instance):
        result = await PRV_Fake_Database.write_data(
            fake_instance, '{"measurement": "test", "value": 1}'
        )
        assert result.startswith("fake-write:")
        assert PRV_Fake_Database.last_data == '{"measurement": "test", "value": 1}'

    def test_required_permissions(self):
        permissions = EXT_Database.get_required_permissions()
        assert isinstance(permissions, list)
        assert "database:query" in permissions
        assert "database:schema" in permissions
        assert "database:write" in permissions
        assert "database:admin" in permissions

    def test_env_property(self):
        env_vars = EXT_Database.env
        assert isinstance(env_vars, dict)
        assert "DATABASE_TYPE" in env_vars
        assert "DATABASE_HOST" in env_vars
        assert "INFLUXDB_TOKEN" in env_vars

    def test_dependency_properties(self):
        pip_deps = EXT_Database.pip_dependencies
        ext_deps = EXT_Database.ext_dependencies
        sys_deps = EXT_Database.sys_dependencies

        assert isinstance(pip_deps, list)
        assert isinstance(ext_deps, list)
        assert isinstance(sys_deps, list)

        pip_names = [dep.name for dep in pip_deps]
        assert "psycopg2-binary" in pip_names
        assert "pymongo" in pip_names
        assert "influxdb" in pip_names

    def test_gql_requests_transport_dependencies_declared(self):
        """PRV_GraphQL connects over gql's requests transport, which imports
        what gql's ``requests`` extra installs; plain ``gql`` lacks it."""
        from importlib.metadata import requires

        from packaging.requirements import Requirement

        transport = [
            Requirement(r)
            for r in requires("gql") or []
            if Requirement(r).marker
            and Requirement(r).marker.evaluate({"extra": "requests"})
        ]
        assert transport
        declared = {
            Requirement(r).name.replace("_", "-").lower()
            for r in EXT_Database.pip_requirements()
        }
        for requirement in transport:
            assert requirement.name.replace("_", "-").lower() in declared

    def test_startup_shutdown_hooks(self):
        # No mocks: just verify the hooks complete without raising.
        EXT_Database.on_start()
        EXT_Database.on_stop()


class TestAbstractDatabaseExtensionProvider:
    """Test the abstract database extension provider base class via real subclasses."""

    def test_concrete_provider_implementation(self):
        provider = ConcreteDatabaseProvider

        assert hasattr(provider, "bond_instance")
        assert hasattr(provider, "execute_sql")
        assert hasattr(provider, "get_schema")
        assert hasattr(provider, "chat_with_db")

        assert provider.name == "test_database"
        assert provider.db_type == "test"
        assert provider.extension == EXT_Database

    def test_provider_abilities(self):
        abilities = ConcreteDatabaseProvider.get_abilities()
        assert isinstance(abilities, set)
        assert "database" in abilities
        assert "sql" in abilities
        assert "test_ability" in abilities

    def test_provider_info(self):
        info = ConcreteDatabaseProvider.get_provider_info()
        assert info["name"] == "test_database"
        assert info["friendly_name"] == "Test Database Provider"
        assert info["type"] == "test"
        assert isinstance(info["abilities"], list)

    @pytest.mark.asyncio
    async def test_provider_methods(self, fake_instance):
        result = await ConcreteDatabaseProvider.execute_sql(fake_instance, "SELECT 1")
        assert "Test SQL executed" in result

        schema = await ConcreteDatabaseProvider.get_schema(fake_instance)
        assert "CREATE TABLE" in schema

        chat_response = await ConcreteDatabaseProvider.chat_with_db(
            fake_instance, "test request"
        )
        assert "Test chat response" in chat_response

    def test_bond_instance_carries_the_resolved_config(self, provider_instance):
        instance = provider_instance(
            PRV_Fake_Database, settings={"url": "test://db.example:1234/instance"}
        )

        connection = ConcreteDatabaseProvider.bond_instance(instance)

        assert connection.model is instance
        assert connection.config == {"url": "test://db.example:1234/instance"}


class TestConnectionSettingResolution:
    """``resolve_setting``: instance field, then setting, then env, then default."""

    def test_instance_field_wins(self, provider_instance, set_env):
        set_env("DATABASE_PASSWORD", "from-env")
        instance = provider_instance(
            PRV_Fake_Database,
            api_key="from-field",
            settings={"database_password": "from-setting"},
        )

        assert (
            AbstractDatabaseExtensionProvider.resolve_setting(
                instance, "database_password", "DATABASE_PASSWORD", field="api_key"
            )
            == "from-field"
        )

    def test_setting_wins_over_env(self, provider_instance, set_env):
        set_env("DATABASE_PASSWORD", "from-env")
        instance = provider_instance(
            PRV_Fake_Database, settings={"database_password": "from-setting"}
        )

        assert (
            AbstractDatabaseExtensionProvider.resolve_setting(
                instance, "database_password", "DATABASE_PASSWORD", field="api_key"
            )
            == "from-setting"
        )

    def test_env_fallback_then_default(self, provider_instance, set_env):
        instance = provider_instance(PRV_Fake_Database)
        set_env("DATABASE_PASSWORD", "from-env")
        resolve = AbstractDatabaseExtensionProvider.resolve_setting

        assert (
            resolve(instance, "database_password", "DATABASE_PASSWORD", field="api_key")
            == "from-env"
        )
        set_env("DATABASE_PASSWORD", "")
        assert (
            resolve(
                instance,
                "database_password",
                "DATABASE_PASSWORD",
                field="api_key",
                default="fallback",
            )
            == "fallback"
        )


class TestDatabaseRotationFailover:
    """Typed provider errors drive the rotation: an unreachable database
    fails over, a rejected statement surfaces to the caller."""

    async def test_unreachable_instance_fails_over_to_the_next(
        self, provider_instance, rotation_over, tmp_path: Path, monkeypatch
    ):
        # A directory is not an openable database file.
        unreachable = provider_instance(PRV_SQLite, api_key=str(tmp_path))
        reachable = provider_instance(PRV_SQLite, api_key=str(tmp_path / "ok.db"))
        rotation = rotation_over(unreachable, reachable)
        # EXT_Database.root serves the cached rotation for its registry.
        monkeypatch.setattr(EXT_Database, "_root_rotation_cache", rotation)

        assert await EXT_Database.execute_sql("SELECT 42;") == "42"

    async def test_rejected_statement_is_not_retried_elsewhere(
        self, provider_instance, rotation_over, tmp_path: Path, monkeypatch
    ):
        first = provider_instance(PRV_SQLite, api_key=str(tmp_path / "first.db"))
        second = provider_instance(PRV_SQLite, api_key=str(tmp_path / "second.db"))
        monkeypatch.setattr(
            EXT_Database, "_root_rotation_cache", rotation_over(first, second)
        )

        with pytest.raises(InvalidInputExternalError):
            await EXT_Database.execute_sql("NOT A STATEMENT;")
        # The second instance was never tried, so its file was never created.
        assert not (tmp_path / "second.db").exists()

    async def test_rotate_provider_for_uses_only_that_providers_instances(
        self, provider_instance, rotation_over, tmp_path: Path, monkeypatch
    ):
        """An operation on what one provider owns runs on that provider's
        instances only, even when another provider leads the rotation."""
        fake = provider_instance(PRV_Fake_Database)
        sqlite = provider_instance(PRV_SQLite, api_key=str(tmp_path / "owned.db"))
        monkeypatch.setattr(
            EXT_Database, "_root_rotation_cache", rotation_over(fake, sqlite)
        )
        assert await EXT_Database.execute_sql("SELECT 7;") == "fake-sql:SELECT 7;"
        assert (
            await EXT_Database.rotate_provider_for(
                PRV_SQLite.name, "execute_sql", "SELECT 7;"
            )
            == "7"
        )

    async def test_rotate_provider_for_without_that_provider_in_the_rotation(
        self, provider_instance, rotation_over, monkeypatch
    ):
        from fastapi import HTTPException

        monkeypatch.setattr(
            EXT_Database,
            "_root_rotation_cache",
            rotation_over(provider_instance(PRV_Fake_Database)),
        )
        with pytest.raises(HTTPException) as raised:
            await EXT_Database.rotate_provider_for(
                PRV_SQLite.name, "execute_sql", "SELECT 1;"
            )
        assert raised.value.status_code == 503

    async def test_rotate_provider_for_an_unknown_provider(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as raised:
            await EXT_Database.rotate_provider_for("no_such", "execute_sql", "")
        assert raised.value.status_code == 400

    def test_provider_metadata_attributes(self):
        assert hasattr(ConcreteDatabaseProvider, "name")
        assert hasattr(ConcreteDatabaseProvider, "friendly_name")
        assert hasattr(ConcreteDatabaseProvider, "description")
        assert hasattr(ConcreteDatabaseProvider, "db_type")
        assert hasattr(ConcreteDatabaseProvider, "extension")
        assert ConcreteDatabaseProvider.extension == EXT_Database

    def test_provider_abilities_structure(self):
        abilities = ConcreteDatabaseProvider._abilities
        assert isinstance(abilities, set)
        assert "database" in abilities
        assert "sql" in abilities
        assert "data_storage" in abilities

    def test_get_db_type_method(self):
        db_type = ConcreteDatabaseProvider.get_db_type()
        assert db_type == "test"

    def test_provider_info_structure(self):
        info = ConcreteDatabaseProvider.get_provider_info()
        assert isinstance(info, dict)
        assert "name" in info
        assert "friendly_name" in info
        assert "description" in info
        assert "type" in info
        assert "classification" in info
        assert "abilities" in info


class TestPRVFakeDatabase:
    """Direct tests of PRV_Fake_Database (the real fake replacing AsyncMock)."""

    def test_metadata(self):
        assert PRV_Fake_Database.name == "fake_database"
        assert PRV_Fake_Database.extension is EXT_Database

    def test_validate_config_returns_empty(self):
        assert PRV_Fake_Database.validate_config() == []

    def test_reset_clears_state(self):
        PRV_Fake_Database.executed_queries.append("noise")
        PRV_Fake_Database.last_request = "noise"
        PRV_Fake_Database.last_data = "noise"
        PRV_Fake_Database.reset()
        assert PRV_Fake_Database.executed_queries == []
        assert PRV_Fake_Database.last_request == ""
        assert PRV_Fake_Database.last_data == ""

    @pytest.mark.asyncio
    async def test_provider_records_queries(self, fake_instance):
        PRV_Fake_Database.reset()
        await PRV_Fake_Database.execute_sql(fake_instance, "SELECT 1")
        await PRV_Fake_Database.execute_query(fake_instance, "SELECT 2")
        assert PRV_Fake_Database.executed_queries == ["SELECT 1", "SELECT 2"]
