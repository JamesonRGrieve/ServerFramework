# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whose environment a provider instance reads, across extensions.

A setting's environment variable (``InstanceSetting(env=...)``, or the
``env_var`` a provider passes ``resolve_setting``) is a default for the
operator's instances only: root- and system-scoped ones, and lookups with
no instance at all. A user's or team's instance that lacks a value never
takes the operator's: it never pays with the operator's Stripe account,
texts from the operator's Twilio number, or connects to the server's own
database. Only the scope decides, whoever made the instance.

The operator's ``Root_<Provider>`` instances are seeded root-scoped, and the
migration ``provider_instance_root_scope`` moves the ones seeded before in
the user scope, leaving a user's own instance of the same name alone.

Real apps, real instances made through the managers that check them, real
setting rows: nothing here is mocked."""

import importlib.util
import os
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Dict, Iterator, List, Tuple, Type

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticProvider,
    InstanceSetting,
)
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai.PRV_OpenAI import PRV_OpenAI_AI
from zephyrex.extensions.cloud.PRV_S3 import PRV_S3_Cloud
from zephyrex.extensions.database.PRV_Postgres import PRV_Postgres
from zephyrex.extensions.messaging.PRV_Slack import PRV_Slack_Messaging
from zephyrex.extensions.payment.EXT_Payment import EXT_Payment
from zephyrex.extensions.payment.PRV_Stripe_Payment import PRV_Stripe_Payment
from zephyrex.extensions.sms.PRV_Twilio import PRV_Twilio_SMS
from zephyrex.extensions.source.PRV_GitHub import PRV_GitHub_Source
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceModel,
    reads_environment_settings,
    root_instance_name,
)
from zephyrex.testing.factories import provider_instance_as

EXTENSIONS = (
    "payment",
    "sms",
    "cloud",
    "ai",
    "source",
    "messaging",
    "database",
)
# One provider of each extension, named so a failure says which.
REPRESENTATIVES = [
    PRV_Stripe_Payment,
    PRV_Twilio_SMS,
    PRV_S3_Cloud,
    PRV_OpenAI_AI,
    PRV_GitHub_Source,
    PRV_Slack_Messaging,
]
IDS = [provider.name for provider in REPRESENTATIVES]
OPERATOR_SCOPES = ["root", "system"]
# The variables the server's own database is reached with.
SERVER_DATABASE = {
    "database_host": "DATABASE_HOST",
    "database_name": "DATABASE_NAME",
    "database_username": "DATABASE_USERNAME",
    "database_password": "DATABASE_PASSWORD",
}

MIGRATION_NAME = "provider_instance_root_scope"
MIGRATION = (
    Path(__file__).parents[1]
    / "database"
    / "migrations"
    / "versions"
    / f"{MIGRATION_NAME}.py"
)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(MIGRATION_NAME, MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def env_backed(provider: Type[AbstractStaticProvider]) -> List[InstanceSetting]:
    """The provider's settings an environment variable backs."""
    return [declared for declared in provider.instance_settings if declared.env]


def operator_value(variable: str) -> str:
    return f"operator:{variable}"


def loaded_providers(model_registry: Any) -> List[Type[AbstractStaticProvider]]:
    """Every provider of the app's extensions that reads the environment."""
    return [
        provider
        for providers in model_registry.extension_registry.extension_providers.values()
        for provider in providers
        if env_backed(provider)
    ]


class TestWhoseEnvironment(ExtensionServerMixin):
    extension_class = EXT_Payment

    @pytest.fixture(scope="module")
    def server(self) -> Iterator[TestClient]:
        """One app holding a provider-bearing extension of each kind."""
        from conftest import CORE_COMPANION_EXTENSIONS

        from zephyrex.app import instance
        from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

        prepare_test_registry()
        worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
        names = list(EXTENSIONS) + [
            c for c in CORE_COMPANION_EXTENSIONS if c not in EXTENSIONS
        ]
        app = instance(
            db_prefix=f"test.provider_env_scope.{worker_id}",
            extensions=",".join(names),
        )
        yield TestClient(app)

    @pytest.fixture
    def operator_environment(
        self, model_registry: Any, set_env: Callable[[str, str], None]
    ) -> None:
        """Every variable any loaded provider reads set, each to a value
        naming it, and the server database's too."""
        for provider in loaded_providers(model_registry):
            for declared in env_backed(provider):
                set_env(declared.env or "", operator_value(declared.env or ""))
        for variable in SERVER_DATABASE.values():
            set_env(variable, operator_value(variable))
        set_env("DATABASE_PORT", "6543")

    @pytest.fixture
    def instance_in(
        self, model_registry: Any, admin_a: Any, team_a: Any
    ) -> Callable[[Type[AbstractStaticProvider], str], ProviderInstanceModel]:
        """A new instance of a provider in a scope, made by its owner: a
        user's by that user, a team's by its admin, the operator's by ROOT."""

        def _make(
            provider: Type[AbstractStaticProvider], scope: str
        ) -> ProviderInstanceModel:
            if scope == "user":
                return provider_instance_as(
                    model_registry, provider.name, requester_id=admin_a.id, scope=scope
                )
            if scope == "team":
                return provider_instance_as(
                    model_registry,
                    provider.name,
                    requester_id=admin_a.id,
                    scope=scope,
                    team_id=team_a.id,
                )
            return provider_instance_as(model_registry, provider.name, scope=scope)

        return _make

    @pytest.mark.parametrize("provider", REPRESENTATIVES, ids=IDS)
    def test_each_representative_reads_the_environment(self, provider):
        """So none of the tests below passes for having nothing to read."""
        assert env_backed(provider)

    @pytest.mark.parametrize("provider", REPRESENTATIVES, ids=IDS)
    @pytest.mark.parametrize("scope", OPERATOR_SCOPES)
    def test_the_operators_instance_defaults_to_the_environment(
        self, operator_environment, instance_in, provider, scope
    ):
        instance = instance_in(provider, scope)
        for declared in env_backed(provider):
            assert provider.setting(instance, declared.key) == operator_value(
                declared.env or ""
            ), declared.key

    @pytest.mark.parametrize("provider", REPRESENTATIVES, ids=IDS)
    @pytest.mark.parametrize("scope", ["user", "team"])
    def test_a_users_or_teams_instance_never_reads_the_environment(
        self, operator_environment, instance_in, provider, scope
    ):
        instance = instance_in(provider, scope)
        assert instance.scope == scope
        for declared in env_backed(provider):
            assert (
                provider.setting(instance, declared.key) == declared.default
            ), declared.key

    def test_stripe_never_charges_the_operators_account_for_a_user(
        self, operator_environment, instance_in
    ):
        """The hole as reported: a user's Stripe instance with no key of its
        own authenticated as the operator's account, and verified webhooks
        with the operator's signing secret."""
        mine = instance_in(PRV_Stripe_Payment, "user")
        assert PRV_Stripe_Payment.setting(mine, "api_key") is None
        assert PRV_Stripe_Payment.setting(mine, "webhook_secret") is None
        operator = instance_in(PRV_Stripe_Payment, "root")
        assert PRV_Stripe_Payment.setting(operator, "api_key") == operator_value(
            "STRIPE_API_KEY"
        )

    def test_an_instances_own_value_comes_first_in_every_scope(
        self, model_registry, operator_environment, admin_a
    ):
        for scope in ("user", "root"):
            instance = provider_instance_as(
                model_registry,
                PRV_Stripe_Payment.name,
                {"webhook_secret": f"whsec_{scope}"},
                requester_id=admin_a.id if scope == "user" else None,
                scope=scope,
                api_key=f"sk_test_{scope}",
            )
            assert PRV_Stripe_Payment.setting(instance, "api_key") == (
                f"sk_test_{scope}"
            )
            assert PRV_Stripe_Payment.setting(instance, "webhook_secret") == (
                f"whsec_{scope}"
            )

    @pytest.mark.parametrize("scope", ["user", "team"])
    def test_no_loaded_provider_lends_a_user_the_environment(
        self, model_registry, operator_environment, instance_in, scope
    ):
        """Every provider of every loaded extension that reads the
        environment, not just the representatives."""
        providers = loaded_providers(model_registry)
        assert {p.name for p in REPRESENTATIVES} <= {p.name for p in providers}
        borrowed: List[Tuple[str, str]] = []
        for provider in providers:
            instance = instance_in(provider, scope)
            for declared in env_backed(provider):
                if provider.setting(instance, declared.key) != declared.default:
                    borrowed.append((provider.name, declared.key))
        assert borrowed == []

    def test_a_users_database_never_reaches_the_servers_own(
        self, operator_environment, instance_in
    ):
        """A database provider reads ``DATABASE_*`` through resolve_setting:
        a user's PostgreSQL instance with no host of its own connected to
        the server's database with the server's password."""
        for scope in ("user", "team"):
            config = PRV_Postgres.connection_config(instance_in(PRV_Postgres, scope))
            assert all(config[key] is None for key in SERVER_DATABASE), scope
        for scope in OPERATOR_SCOPES:
            config = PRV_Postgres.connection_config(instance_in(PRV_Postgres, scope))
            assert {key: config[key] for key in SERVER_DATABASE} == {
                key: operator_value(variable)
                for key, variable in SERVER_DATABASE.items()
            }
            assert config["database_port"] == 6543

    @pytest.mark.parametrize("provider", REPRESENTATIVES, ids=IDS)
    def test_an_environment_only_lookup_reads_the_environment(
        self, operator_environment, provider
    ):
        for declared in env_backed(provider):
            assert provider.setting(None, declared.key) == operator_value(
                declared.env or ""
            )


class TestSeededRootInstances(ExtensionServerMixin):
    """The operator's ``Root_<Provider>`` instances, as the framework seeds
    them and as the migration moves the ones seeded before."""

    extension_class = EXT_Payment

    @staticmethod
    def seeded(model_registry: Any, provider_name: str) -> ProviderInstanceModel:
        return TestSeededRootInstances.read(
            model_registry, name=root_instance_name(provider_name)
        )

    @staticmethod
    def read(model_registry: Any, **identity: Any) -> ProviderInstanceModel:
        return ProviderInstanceModel.model_validate(
            ProviderInstanceManager(
                model_registry=model_registry, requester_id=env("ROOT_ID")
            ).get(**identity),
            from_attributes=True,
        )

    def test_every_seeded_root_instance_is_root_scoped(self, model_registry):
        """They were seeded in the default scope, ``user``, and so only
        email's own rule kept them reading the environment."""
        names = [
            provider.name
            for providers in model_registry.extension_registry.extension_providers.values()
            for provider in providers
        ]
        providers = [
            provider
            for found in model_registry.extension_registry.extension_providers.values()
            for provider in found
        ]
        assert PRV_Stripe_Payment.name in names
        for provider in providers:
            if not reads_environment_settings(provider):
                continue  # none is seeded (test_erp: no Root_ for ERPNext)
            instance = self.seeded(model_registry, provider.name)
            assert (instance.scope, instance.user_id, instance.team_id) == (
                "root",
                None,
                None,
            ), provider.name
            assert instance.created_by_user_id == env("ROOT_ID")

    def test_the_seeded_root_instance_reads_the_environment(
        self, model_registry, set_env
    ):
        set_env("STRIPE_API_KEY", operator_value("STRIPE_API_KEY"))
        instance = self.seeded(model_registry, PRV_Stripe_Payment.name)
        assert PRV_Stripe_Payment.setting(instance, "api_key") == operator_value(
            "STRIPE_API_KEY"
        )

    def test_the_migration_moves_an_old_seeded_instance_and_no_users(
        self, model_registry, admin_a, set_env
    ):
        """In the app's own schema: the seeded Root_Stripe as the old seeder
        left it (user scope) is moved to root; a user's instance named
        Root_Stripe stays theirs, in their scope."""
        set_env("STRIPE_API_KEY", operator_value("STRIPE_API_KEY"))
        seeded = self.seeded(model_registry, PRV_Stripe_Payment.name)
        users = provider_instance_as(
            model_registry,
            PRV_Stripe_Payment.name,
            requester_id=admin_a.id,
            scope="user",
        )
        engine = model_registry.DB.manager.get_setup_engine()
        instances = sa.table(
            "provider_instances", sa.column("id"), sa.column("name"), sa.column("scope")
        )

        def rewrite(instance_id: Any, **values: str) -> None:
            with engine.begin() as conn:
                conn.execute(
                    instances.update()
                    .where(instances.c.id == str(instance_id))
                    .values(**values)
                )

        rewrite(users.id, name=root_instance_name(PRV_Stripe_Payment.name))
        rewrite(seeded.id, scope="user")
        try:
            old = self.read(model_registry, id=seeded.id)
            assert old.scope == "user"
            assert PRV_Stripe_Payment.setting(old, "api_key") is None

            with engine.begin() as conn:
                moved = _migration().rescope(conn, "user", "root")

            assert str(seeded.id) in moved and str(users.id) not in moved
            assert self.read(model_registry, id=users.id).scope == "user"
            now = self.read(model_registry, id=seeded.id)
            assert now.scope == "root"
            assert PRV_Stripe_Payment.setting(now, "api_key") == operator_value(
                "STRIPE_API_KEY"
            )
        finally:
            rewrite(users.id, name=users.name)
            rewrite(seeded.id, scope="root")


def _instance_row(
    row_id: str,
    name: str,
    provider_id: str,
    scope: str = "user",
    user_id: Any = None,
    team_id: Any = None,
    creator: Any = None,
) -> Dict[str, Any]:
    return {
        "id": row_id,
        "name": name,
        "provider_id": provider_id,
        "scope": scope,
        "user_id": user_id,
        "team_id": team_id,
        "created_by_user_id": creator or env("ROOT_ID"),
    }


class TestTheMigration:
    """``provider_instance_root_scope`` over a database holding the tables as
    they stand at its revision."""

    _metadata = sa.MetaData()
    _providers = sa.Table(
        "providers",
        _metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
    )
    _instances = sa.Table(
        "provider_instances",
        _metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("provider_id", sa.String, nullable=False),
        sa.Column("scope", sa.String, nullable=False),
        sa.Column("user_id", sa.String),
        sa.Column("team_id", sa.String),
        sa.Column("created_by_user_id", sa.String),
    )

    @pytest.fixture
    def legacy(self, tmp_path: Path) -> Iterator[sa.Engine]:
        engine = sa.create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
        self._metadata.create_all(engine)
        root = env("ROOT_ID")
        with engine.begin() as conn:
            conn.execute(
                self._providers.insert(),
                [
                    {"id": "p_stripe", "name": "stripe"},
                    {"id": "p_sns", "name": "amazon_sns"},
                    {"id": "p_twilio", "name": "twilio"},
                ],
            )
            conn.execute(
                self._instances.insert(),
                [
                    _instance_row("seeded", "Root_Stripe", "p_stripe"),
                    _instance_row("seeded_two_words", "Root_AmazonSns", "p_sns"),
                    _instance_row(
                        "seeded_root_user", "Root_Twilio", "p_twilio", user_id=root
                    ),
                    _instance_row(
                        "users_namesake",
                        "Root_Stripe",
                        "p_stripe",
                        user_id="alice",
                        creator="alice",
                    ),
                    _instance_row(
                        "unowned_by_a_user", "Root_Stripe", "p_stripe", creator="alice"
                    ),
                    _instance_row(
                        "teams_namesake", "Root_Stripe", "p_stripe", team_id="t1"
                    ),
                    _instance_row("other_providers_name", "Root_Stripe", "p_twilio"),
                    _instance_row("roots_own", "Stripe billing", "p_stripe"),
                    _instance_row("prefix_only", "Root_StripeX", "p_stripe"),
                    _instance_row(
                        "rescoped_by_operator",
                        "Root_Stripe",
                        "p_stripe",
                        scope="system",
                    ),
                ],
            )
        yield engine
        engine.dispose()

    def _scopes(self, engine: sa.Engine) -> Dict[str, str]:
        with engine.connect() as conn:
            return {
                row.id: row.scope
                for row in conn.execute(
                    sa.select(self._instances.c.id, self._instances.c.scope)
                )
            }

    @staticmethod
    def _run(engine: sa.Engine, step: str) -> None:
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        module = _migration()
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                getattr(module, step)()

    def test_only_the_seeded_instances_move_to_root(self, legacy):
        before = self._scopes(legacy)
        self._run(legacy, "upgrade")
        after = self._scopes(legacy)
        moved = {"seeded", "seeded_two_words", "seeded_root_user"}
        assert {i for i in after if after[i] != before[i]} == moved
        assert all(after[i] == "root" for i in moved)

    def test_it_is_idempotent(self, legacy):
        self._run(legacy, "upgrade")
        first = self._scopes(legacy)
        self._run(legacy, "upgrade")
        assert self._scopes(legacy) == first

    def test_downgrade_puts_the_seeded_instances_back(self, legacy):
        before = self._scopes(legacy)
        self._run(legacy, "upgrade")
        self._run(legacy, "downgrade")
        assert self._scopes(legacy) == before

    def test_without_the_tables_it_does_nothing(self, tmp_path):
        engine = sa.create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
        try:
            self._run(engine, "upgrade")
            self._run(engine, "downgrade")
            assert not sa.inspect(engine).has_table("provider_instances")
        finally:
            engine.dispose()

    def test_the_frozen_name_is_the_seeders(self):
        for name in ("stripe", "amazon_sns", "mcp_streamable_http", "GraphQL"):
            assert _migration().seeded_instance_name(name) == root_instance_name(name)
