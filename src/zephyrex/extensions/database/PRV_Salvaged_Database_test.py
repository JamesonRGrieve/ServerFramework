# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keyless conformance + behavior tests for the ported database providers.

Covers the six providers salvaged from the pre-zephyrex AGInfrastructure fork
and rewritten into the static ``AbstractDatabaseExtensionProvider`` format:
PostgreSQL, MySQL, MariaDB, MSSQL, MongoDB, GraphQL. No live database is
required — the tests exercise metadata, classification, instance-first
connection settings, config validation, and the typed failures raised when a
provider cannot connect (which is what lets the rotation fail over).
"""

import inspect

import pytest

from zephyrex.extensions.database.EXT_Database import (
    AbstractDatabaseExtensionProvider,
    DatabaseConnection,
)
from zephyrex.extensions.database.PRV_GraphQL import PRV_GraphQL
from zephyrex.extensions.database.PRV_MariaDB import PRV_MariaDB
from zephyrex.extensions.database.PRV_MongoDB import PRV_MongoDB
from zephyrex.extensions.database.PRV_MSSQL import PRV_MSSQL
from zephyrex.extensions.database.PRV_MySQL import PRV_MySQL
from zephyrex.extensions.database.PRV_Postgres import PRV_Postgres
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)

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
# Providers whose settings name a networked database server.
SERVER_PROVIDERS = [PRV_Postgres, PRV_MySQL, PRV_MariaDB, PRV_MSSQL, PRV_MongoDB]
# An address nothing listens on: connecting fails fast.
UNREACHABLE_HOST = "127.0.0.1"
UNREACHABLE_PORT = "1"


@pytest.fixture
def unreachable_instance(provider_instance):
    """An instance of ``cls`` pointed at a closed local port."""

    def _create(cls):
        return provider_instance(
            cls,
            api_key="secret",
            settings={
                "database_name": "appdb",
                "database_host": UNREACHABLE_HOST,
                "database_port": UNREACHABLE_PORT,
                "graphql_endpoint": f"http://{UNREACHABLE_HOST}:{UNREACHABLE_PORT}/graphql",
            },
        )

    return _create


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

    def test_bond_instance_resolves_the_instance(
        self, cls, db_type, classification, unreachable_instance
    ):
        instance = unreachable_instance(cls)

        connection = cls.bond_instance(instance)

        assert isinstance(connection, DatabaseConnection)
        assert connection.model is instance
        assert connection.config == cls.connection_config(instance)

    async def test_get_schema_unreachable_raises_transient(
        self, cls, db_type, classification, unreachable_instance
    ):
        # Missing driver or closed port: either way the provider cannot serve
        # this instance, so the rotation must fail over, never read an error
        # string as a schema.
        with pytest.raises(TransientExternalError) as raised:
            await cls.get_schema(unreachable_instance(cls))
        assert raised.value.provider == cls.name

    async def test_chat_with_db_unreachable_raises_transient(
        self, cls, db_type, classification, unreachable_instance
    ):
        # chat_with_db embeds the schema; a failed schema fetch must fail
        # over rather than be embedded in the reply.
        with pytest.raises(TransientExternalError):
            await cls.chat_with_db(unreachable_instance(cls), "show me everything")


class TestServerConnectionConfig:
    """Instance values win over env; env fills what the instance leaves out."""

    @pytest.mark.parametrize("cls", SERVER_PROVIDERS, ids=lambda c: c.__name__)
    def test_instance_values_win_over_env(self, cls, provider_instance, set_env):
        set_env("DATABASE_HOST", "env-host")
        set_env("DATABASE_PORT", "1111")
        set_env("DATABASE_USERNAME", "env-user")
        set_env("DATABASE_PASSWORD", "env-password")
        set_env("DATABASE_NAME", "env_db")
        instance = provider_instance(
            cls,
            api_key="instance-password",
            settings={
                "database_host": "instance-host",
                "database_port": "2222",
                "database_name": "instance_db",
                "database_username": "instance-user",
            },
        )

        config = cls.connection_config(instance)

        assert config["database_host"] == "instance-host"
        assert config["database_port"] == 2222
        assert config["database_name"] == "instance_db"
        assert config["database_username"] == "instance-user"
        assert config["database_password"] == "instance-password"

    @pytest.mark.parametrize("cls", SERVER_PROVIDERS, ids=lambda c: c.__name__)
    def test_env_fallback(self, cls, provider_instance, set_env):
        set_env("DATABASE_HOST", "env-host")
        set_env("DATABASE_PORT", "1111")
        set_env("DATABASE_USERNAME", "env-user")
        set_env("DATABASE_PASSWORD", "env-password")
        set_env("DATABASE_NAME", "env_db")
        instance = provider_instance(cls)

        config = cls.connection_config(instance)

        assert config["database_host"] == "env-host"
        assert config["database_port"] == 1111
        assert config["database_name"] == "env_db"
        assert config["database_username"] == "env-user"
        assert config["database_password"] == "env-password"

    @pytest.mark.parametrize("cls", SERVER_PROVIDERS, ids=lambda c: c.__name__)
    def test_model_name_never_names_the_database(self, cls, provider_instance, set_env):
        # The generic seed gives Root_* instances model_name=<provider name>;
        # that is an AI model name column, not a database name.
        set_env("DATABASE_NAME", "env_db")
        seeded_style = provider_instance(cls, model_name=cls.name)

        assert cls.connection_config(seeded_style)["database_name"] == "env_db"

        set_env("DATABASE_NAME", "")
        assert cls.connection_config(seeded_style)["database_name"] is None

    def test_seeded_postgres_instance_ignores_model_name(
        self, provider_instance, set_env
    ):
        set_env("DATABASE_NAME", "appdb")
        seeded_style = provider_instance(
            PRV_Postgres,
            model_name="PostgreSQL",
            settings={"database_host": "db.internal"},
        )

        config = PRV_Postgres.bond_instance(seeded_style).config

        assert config["database_name"] == "appdb"
        assert "PostgreSQL" not in config.values()

    def test_non_numeric_port_is_a_configuration_failure(
        self, provider_instance, set_env
    ):
        instance = provider_instance(PRV_Postgres, settings={"database_port": "abc"})

        with pytest.raises(TransientExternalError, match="port is not a number"):
            PRV_Postgres.connection_config(instance)

    def test_mssql_odbc_driver_setting(self, provider_instance, set_env):
        set_env("MSSQL_ODBC_DRIVER", "ODBC Driver 17 for SQL Server")
        instance = provider_instance(PRV_MSSQL, settings={"odbc_driver": "FreeTDS"})

        assert PRV_MSSQL.connection_config(instance)["odbc_driver"] == "FreeTDS"
        assert (
            PRV_MSSQL.connection_config(provider_instance(PRV_MSSQL))["odbc_driver"]
            == "ODBC Driver 17 for SQL Server"
        )

    def test_mongodb_connection_string_setting(self, provider_instance, set_env):
        set_env("MONGODB_CONNECTION_STRING", "mongodb://env-host/envdb")
        instance = provider_instance(
            PRV_MongoDB, settings={"connection_string": "mongodb://instance/db"}
        )

        assert (
            PRV_MongoDB.connection_config(instance)["connection_string"]
            == "mongodb://instance/db"
        )

    def test_graphql_bearer_token_from_the_instance(self, provider_instance, set_env):
        set_env("GRAPHQL_API_KEY", "env-token")
        instance = provider_instance(
            PRV_GraphQL,
            api_key="instance-token",
            settings={"graphql_endpoint": "https://api.example/graphql"},
        )

        config = PRV_GraphQL.connection_config(instance)

        assert config["graphql_endpoint"] == "https://api.example/graphql"
        assert config["graphql_headers"]["Authorization"] == "Bearer instance-token"

    def test_graphql_endpoint_built_from_env_host(self, provider_instance, set_env):
        set_env("GRAPHQL_ENDPOINT", "")
        set_env("DATABASE_HOST", "gql-host")
        set_env("DATABASE_PORT", "4001")

        config = PRV_GraphQL.connection_config(provider_instance(PRV_GraphQL))

        assert config["graphql_endpoint"] == "http://gql-host:4001/graphql"


class TestRelationalFailures:
    @pytest.mark.parametrize("cls", [c for c, _, _ in RELATIONAL])
    async def test_execute_sql_unreachable_raises_transient(
        self, cls, unreachable_instance
    ):
        with pytest.raises(TransientExternalError) as raised:
            await cls.execute_sql(unreachable_instance(cls), "SELECT 1")
        assert raised.value.provider == cls.name

    @pytest.mark.parametrize("cls", [c for c, _, _ in RELATIONAL])
    async def test_unconfigured_database_raises_transient(
        self, cls, provider_instance, set_env
    ):
        set_env("DATABASE_HOST", "")
        instance = provider_instance(cls, settings={"database_name": "appdb"})

        with pytest.raises(TransientExternalError):
            await cls.execute_sql(instance, "SELECT 1")

    @pytest.mark.parametrize("cls", [c for c, _, _ in RELATIONAL])
    async def test_execute_query_aliases_execute_sql(self, cls, unreachable_instance):
        # The aliasing IS the contract: execute_query must fail exactly as
        # execute_sql does for the same instance and query.
        instance = unreachable_instance(cls)
        with pytest.raises(TransientExternalError) as via_sql:
            await cls.execute_sql(instance, "SELECT 1")
        with pytest.raises(TransientExternalError) as via_query:
            await cls.execute_query(instance, "SELECT 1")
        assert str(via_query.value) == str(via_sql.value)

    @pytest.mark.parametrize("cls", [c for c, _, _ in RELATIONAL])
    async def test_write_data_requires_insert(self, cls, unreachable_instance):
        with pytest.raises(InvalidInputExternalError, match="INSERT"):
            await cls.write_data(unreachable_instance(cls), "not an insert statement")


class TestDocumentAndGraphRefuseSql:
    @pytest.mark.parametrize("cls", [PRV_MongoDB, PRV_GraphQL])
    async def test_execute_sql_is_refused(self, cls, unreachable_instance):
        with pytest.raises(InvalidInputExternalError, match="does not support SQL"):
            await cls.execute_sql(unreachable_instance(cls), "SELECT 1")

    async def test_mongodb_execute_query_rejects_bad_json(self, unreachable_instance):
        # The envelope is validated before connecting, so a bad one is the
        # caller's error whether or not the server is reachable.
        with pytest.raises(InvalidInputExternalError, match="valid JSON"):
            await PRV_MongoDB.execute_query(
                unreachable_instance(PRV_MongoDB), "this is not json"
            )

    async def test_mongodb_execute_query_requires_a_collection(
        self, unreachable_instance
    ):
        with pytest.raises(InvalidInputExternalError, match="collection"):
            await PRV_MongoDB.execute_query(
                unreachable_instance(PRV_MongoDB), '{"operation": "find"}'
            )

    async def test_mongodb_execute_query_rejects_unknown_operations(
        self, unreachable_instance
    ):
        with pytest.raises(InvalidInputExternalError, match="unsupported operation"):
            await PRV_MongoDB.execute_query(
                unreachable_instance(PRV_MongoDB),
                '{"collection": "users", "operation": "drop"}',
            )

    async def test_mongodb_valid_envelope_unreachable_raises_transient(
        self, unreachable_instance
    ):
        with pytest.raises(TransientExternalError):
            await PRV_MongoDB.execute_query(
                unreachable_instance(PRV_MongoDB),
                '{"collection": "users", "operation": "find"}',
            )


class TestDriverGuards:
    """Providers must report what is missing when nothing is configured."""

    def test_mariadb_shares_mysql_driver(self):
        # DRY: MariaDB is a metadata override of the MySQL provider.
        assert issubclass(PRV_MariaDB, PRV_MySQL)
        assert PRV_MariaDB.db_type == "mariadb"

    @pytest.mark.parametrize("cls", [c for c, _, _ in ALL])
    def test_validate_config_reports_missing_driver_or_config(self, cls, set_env):
        # With nothing configured, validate_config must surface at least one
        # issue (missing driver and/or missing host) rather than claim healthy.
        for name in ("DATABASE_HOST", "DATABASE_NAME", "GRAPHQL_ENDPOINT"):
            set_env(name, "")
        set_env("MONGODB_CONNECTION_STRING", "")
        assert len(cls.validate_config()) >= 1

    @pytest.mark.parametrize("cls", SERVER_PROVIDERS, ids=lambda c: c.__name__)
    def test_validate_config_names_the_missing_settings(self, cls, set_env):
        set_env("DATABASE_HOST", "")
        set_env("DATABASE_NAME", "")
        set_env("MONGODB_CONNECTION_STRING", "")

        issues = " ".join(cls.validate_config()).lower()

        assert "host" in issues
        assert "database name not configured" in issues
