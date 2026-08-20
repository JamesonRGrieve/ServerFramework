# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keyless conformance + behavior tests for the ported database providers.

Covers the six providers salvaged from the pre-zephyrex AGInfrastructure fork
and rewritten into the static ``AbstractDatabaseExtensionProvider`` format:
PostgreSQL, MySQL, MariaDB, MSSQL, MongoDB, GraphQL. No live database or driver
is required — the tests exercise metadata, classification, config validation,
and the graceful no-connection paths.
"""

import inspect

import pytest

from zephyrex.extensions.database.EXT_Database import (
    AbstractDatabaseExtensionProvider,
)
from zephyrex.extensions.database.PRV_GraphQL import PRV_GraphQL
from zephyrex.extensions.database.PRV_MariaDB import PRV_MariaDB
from zephyrex.extensions.database.PRV_MongoDB import PRV_MongoDB
from zephyrex.extensions.database.PRV_MSSQL import PRV_MSSQL
from zephyrex.extensions.database.PRV_MySQL import PRV_MySQL
from zephyrex.extensions.database.PRV_Postgres import PRV_Postgres

# (provider class, expected db_type, expected classification)
RELATIONAL = [
    (PRV_Postgres, "postgresql", "relational"),
    (PRV_MySQL, "mysql", "relational"),
    (PRV_MariaDB, "mariadb", "relational"),
    (PRV_MSSQL, "mssql", "relational"),
]
NON_RELATIONAL = [
    (PRV_MongoDB, "mongodb", "document"),
    (PRV_GraphQL, "graphql", "graph"),
]
ALL = RELATIONAL + NON_RELATIONAL
ALL_IDS = [c.__name__ for c, _, _ in ALL]


@pytest.mark.parametrize("cls,db_type,classification", ALL, ids=ALL_IDS)
class TestSalvagedDatabaseProviderConformance:
    def test_is_static_database_provider(self, cls, db_type, classification):
        assert issubclass(cls, AbstractDatabaseExtensionProvider)
        # Static provider — no instance constructor should be required.
        assert not inspect.iscoroutinefunction(cls.bond_instance)

    def test_metadata_populated(self, cls, db_type, classification):
        assert cls.name
        assert cls.friendly_name
        assert cls.description
        assert cls.db_type == db_type

    def test_classification_and_abilities(self, cls, db_type, classification):
        assert classification in cls.get_db_classifications()
        abilities = cls.get_abilities()
        assert "database" in abilities
        assert "data_storage" in abilities

    def test_provider_info_shape(self, cls, db_type, classification):
        info = cls.get_provider_info()
        assert set(info) >= {"name", "friendly_name", "description", "type"}
        assert info["type"] == db_type

    def test_validate_config_returns_list(self, cls, db_type, classification):
        issues = cls.validate_config()
        assert isinstance(issues, list)

    def test_bond_instance_accepts_config(self, cls, db_type, classification):
        # Should never raise on a plain dict, even with no real credentials.
        cls.bond_instance({"database_host": "", "database_name": ""})
        assert isinstance(cls._connection_config, dict)

    async def test_execute_sql_graceful_without_connection(
        self, cls, db_type, classification
    ):
        cls.bond_instance({})
        result = await cls.execute_sql("SELECT 1")
        assert isinstance(result, str)
        assert result  # non-empty, no exception

    async def test_get_schema_graceful_without_connection(
        self, cls, db_type, classification
    ):
        cls.bond_instance({})
        result = await cls.get_schema()
        assert isinstance(result, str)

    async def test_chat_with_db_returns_string(self, cls, db_type, classification):
        cls.bond_instance({})
        result = await cls.chat_with_db("show me everything")
        assert isinstance(result, str)
        assert result


class TestRelationalExecuteQuery:
    @pytest.mark.parametrize("cls", [c for c, _, _ in RELATIONAL])
    async def test_execute_query_aliases_execute_sql(self, cls):
        cls.bond_instance({})
        result = await cls.execute_query("SELECT 1")
        assert isinstance(result, str)

    @pytest.mark.parametrize("cls", [c for c, _, _ in RELATIONAL])
    async def test_write_data_requires_insert(self, cls):
        cls.bond_instance({})
        result = await cls.write_data("not an insert statement")
        assert isinstance(result, str)
        assert "INSERT" in result


class TestDocumentAndGraphRedirectSql:
    async def test_mongodb_execute_sql_redirects(self):
        PRV_MongoDB.bond_instance({})
        result = await PRV_MongoDB.execute_sql("SELECT 1")
        assert "does not support SQL" in result

    async def test_graphql_execute_sql_redirects(self):
        PRV_GraphQL.bond_instance({})
        result = await PRV_GraphQL.execute_sql("SELECT 1")
        assert "does not support SQL" in result

    async def test_mongodb_execute_query_rejects_bad_json(self):
        PRV_MongoDB.bond_instance({"database_host": "localhost"})
        # pymongo may be absent (connection error) OR present (JSON error) —
        # either way the call returns a string, never raises.
        result = await PRV_MongoDB.execute_query("this is not json")
        assert isinstance(result, str)


class TestDriverGuards:
    """Providers must degrade gracefully when the optional driver is absent."""

    def test_mariadb_shares_mysql_driver(self):
        # DRY: MariaDB is a metadata override of the MySQL provider.
        assert issubclass(PRV_MariaDB, PRV_MySQL)
        assert PRV_MariaDB.db_type == "mariadb"

    @pytest.mark.parametrize("cls", [c for c, _, _ in ALL])
    def test_validate_config_reports_missing_driver_or_config(self, cls):
        # With nothing configured, validate_config must surface at least one
        # issue (missing driver and/or missing host) rather than claim healthy.
        cls.bond_instance({})
        assert len(cls.validate_config()) >= 1
