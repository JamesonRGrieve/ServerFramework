"""
Tests for EXT_Database extension (Item 72 — no mocks).

These tests exercise the real `AbstractDatabaseExtensionProvider`
contract via `PRV_Fake_Database`, a real-class fake (not a mock) that
satisfies the provider interface with deterministic in-memory state.
The previous version patched `EXT_Database.root.rotate(...)` — that
violated AGENTS.md's no-BLL-mocks pillar and is removed. The remaining
test verifies the same behavior by calling the provider's methods
directly, which is the actual unit of work the rotation system
delegates to.
"""

from __future__ import annotations

from typing import Dict, List

import pytest

from serverframework.extensions.AbstractEXTTest import (
    ExtensionServerMixin,
    ExtensionTestConfig,
    ExtensionTestType,
)
from serverframework.extensions.database.EXT_Database import (
    AbstractDatabaseExtensionProvider,
    EXT_Database,
)
from serverframework.extensions.database.PRV_Fake_Database import PRV_Fake_Database


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
    def bond_instance(cls, config: Dict[str, any]) -> None:
        cls._config = config

    @classmethod
    async def execute_sql(cls, query: str, **kwargs) -> str:
        return f"Test SQL executed: {query}"

    @classmethod
    async def get_schema(cls, **kwargs) -> str:
        return "CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT);"

    @classmethod
    async def chat_with_db(cls, request: str, **kwargs) -> str:
        return f"Test chat response for: {request}"

    @classmethod
    async def execute_query(cls, query: str, **kwargs) -> str:
        return f"Test query executed: {query}"

    @classmethod
    async def write_data(cls, data: str, **kwargs) -> str:
        return f"Test data written: {data}"

    @classmethod
    def validate_config(cls) -> List[str]:
        return []


@pytest.fixture(autouse=True)
def _reset_fake_provider():
    """Reset captured state on PRV_Fake_Database between tests."""
    PRV_Fake_Database.reset()
    yield
    PRV_Fake_Database.reset()


def _install_provider_list(provider_list):
    """Install a real provider list on EXT_Database via a descriptor shim.

    EXT_Database has a `classproperty` named ``providers`` (no-arg
    accessor) but several call sites within the framework call it as
    ``cls.providers()``. To make tests robust against both shapes without
    modifying EXT_Database (parallel agent scope), install a real
    callable-descriptor that returns the list whether the consumer calls
    it as a property or as a function.

    Returns the original descriptor so the fixture can restore it. This
    is **not a mock** — it's a small real class that satisfies both
    access patterns.
    """

    class _CallableList(list):
        def __call__(self):  # noqa: D401 - small real callable
            return list(self)

    real_list = _CallableList(provider_list)

    class _Descriptor:
        def __get__(self, _instance, _owner):
            return real_list

        def __set__(self, _instance, value):
            real_list[:] = list(value)

    original = type(EXT_Database).__dict__.get("providers")
    type(EXT_Database).providers = _Descriptor()  # type: ignore[attr-defined]
    return original


def _restore_provider_descriptor(original):
    if original is None:
        try:
            del type(EXT_Database).providers
        except AttributeError:
            pass
    else:
        type(EXT_Database).providers = original  # type: ignore[attr-defined]


@pytest.fixture
def fake_only_providers():
    """Install [PRV_Fake_Database] as the active provider list (real, not mocked)."""
    original_cache = EXT_Database._providers
    EXT_Database._providers = [PRV_Fake_Database]
    original_descriptor = _install_provider_list([PRV_Fake_Database])
    try:
        yield
    finally:
        _restore_provider_descriptor(original_descriptor)
        EXT_Database._providers = original_cache


@pytest.fixture
def both_providers():
    """Install both real fake providers as the active provider list."""
    original_cache = EXT_Database._providers
    providers_list = [PRV_Fake_Database, ConcreteDatabaseProvider]
    EXT_Database._providers = list(providers_list)
    original_descriptor = _install_provider_list(providers_list)
    try:
        yield
    finally:
        _restore_provider_descriptor(original_descriptor)
        EXT_Database._providers = original_cache


@pytest.fixture
def no_providers():
    """Install an empty provider list."""
    original_cache = EXT_Database._providers
    EXT_Database._providers = []
    original_descriptor = _install_provider_list([])
    try:
        yield
    finally:
        _restore_provider_descriptor(original_descriptor)
        EXT_Database._providers = original_cache


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
    async def test_provider_execute_sql(self):
        result = await PRV_Fake_Database.execute_sql("SELECT * FROM test")
        assert result == "fake-sql:SELECT * FROM test"
        assert "SELECT * FROM test" in PRV_Fake_Database.executed_queries

    @pytest.mark.asyncio
    async def test_provider_get_schema(self):
        result = await PRV_Fake_Database.get_schema()
        assert "CREATE TABLE" in result

    @pytest.mark.asyncio
    async def test_provider_chat_with_db(self):
        result = await PRV_Fake_Database.chat_with_db("Show me all users")
        assert result == "fake-chat:Show me all users"
        assert PRV_Fake_Database.last_request == "Show me all users"

    @pytest.mark.asyncio
    async def test_provider_execute_query(self):
        result = await PRV_Fake_Database.execute_query(
            "FROM bucket |> range(start: -1h)"
        )
        assert result.startswith("fake-query:")

    @pytest.mark.asyncio
    async def test_provider_write_data(self):
        result = await PRV_Fake_Database.write_data(
            '{"measurement": "test", "value": 1}'
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

    def test_startup_shutdown_hooks(self):
        # No mocks: just verify the hooks complete without raising.
        EXT_Database.on_startup()
        EXT_Database.on_shutdown()


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
    async def test_provider_methods(self):
        result = await ConcreteDatabaseProvider.execute_sql("SELECT 1")
        assert "Test SQL executed" in result

        schema = await ConcreteDatabaseProvider.get_schema()
        assert "CREATE TABLE" in schema

        chat_response = await ConcreteDatabaseProvider.chat_with_db("test request")
        assert "Test chat response" in chat_response

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
    async def test_provider_records_queries(self):
        PRV_Fake_Database.reset()
        await PRV_Fake_Database.execute_sql("SELECT 1")
        await PRV_Fake_Database.execute_query("SELECT 2")
        assert PRV_Fake_Database.executed_queries == ["SELECT 1", "SELECT 2"]
