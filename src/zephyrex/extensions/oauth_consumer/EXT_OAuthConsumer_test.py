# SPDX-License-Identifier: AGPL-3.0-or-later
"""The extension itself: its migration creates exactly the models' tables,
it registers its grant, it refuses to run without encryption, and it makes
the operator's sign-in instance at startup when an issuer is configured."""

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException

from zephyrex import ExtensionLoadError
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.oauth_consumer.BLL_OAuthConsumer import (
    GRANT_TYPE,
    OAuthIdentityModel,
    OAuthLoginStateModel,
    configured_providers,
    public_names,
)
from zephyrex.extensions.oauth_consumer.EXT_OAuthConsumer import (
    EXT_OAuthConsumer,
    ensure_sign_in_instance,
    live_instances,
)
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import PasswordlessGrantRegistry
from zephyrex.logic.BLL_Providers import ProviderInstanceManager, ProviderManager

_MIGRATION = Path(__file__).parent / "migrations" / "versions" / "001_initial.py"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("oauth_consumer_initial", _MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _shape(table: sa.Table) -> dict:
    return {c.name: (c.type.python_type, c.nullable) for c in table.columns}


class TestMigration(ExtensionServerMixin):
    extension_class = EXT_OAuthConsumer

    def test_upgrade_creates_the_models_tables_and_downgrade_drops_them(self, server):
        base = server.app.state.model_registry.DB.manager.Base
        engine = sa.create_engine("sqlite://")
        migration = _load_migration()
        assert migration.branch_labels == ("ext_oauth_consumer",)
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                inspector = sa.inspect(connection)
                for model in (OAuthIdentityModel, OAuthLoginStateModel):
                    table = model.DB(base).__table__
                    created = sa.Table(
                        table.name, sa.MetaData(), autoload_with=connection
                    )
                    assert _shape(created) == _shape(table), table.name
                    assert {i["name"] for i in inspector.get_indexes(table.name)} == {
                        i.name for i in table.indexes
                    }
                migration.upgrade()
                migration.downgrade()
                assert not sa.inspect(connection).get_table_names()


class TestExtension:
    def test_the_grant_is_registered(self):
        assert GRANT_TYPE in PasswordlessGrantRegistry.list_grant_types()

    def test_it_refuses_to_run_without_an_encryption_key(self, set_env):
        set_env("FRAMEWORK_FERNET_KEY", "")
        set_env("MFA_FERNET_KEY", "")
        set_env("ALLOW_PLAINTEXT_SECRETS", "false")
        assert EXT_OAuthConsumer.on_initialize() is False
        assert EXT_OAuthConsumer.validate_config()

    def test_it_runs_with_one(self):
        assert EXT_OAuthConsumer.on_initialize() is True
        assert EXT_OAuthConsumer.validate_config() == []

    @pytest.mark.parametrize("ability", sorted(EXT_OAuthConsumer._abilities))
    def test_each_declared_ability_is_implemented(self, ability):
        assert callable(getattr(EXT_OAuthConsumer, ability))


class TestSignInWithoutTheProvider:
    """Seeding off (SEED_DATA=false) leaves no oidc provider row: with an
    issuer set the server must not start with nobody able to sign in."""

    @staticmethod
    def _no_provider(manager_class: Any, requester_id: str) -> Any:
        def get(**_: Any) -> Any:
            raise HTTPException(status_code=404, detail="Provider not found")

        return SimpleNamespace(get=get)

    def test_with_an_issuer_it_refuses_to_start(self, set_env, monkeypatch):
        set_env("OIDC_CONSUMER_ISSUER_URL", ISSUER)
        monkeypatch.setattr(EXT_OAuthConsumer, "as_requester", self._no_provider)
        with pytest.raises(ExtensionLoadError, match="SEED_DATA"):
            EXT_OAuthConsumer.on_start()

    def test_without_one_it_starts(self, set_env, monkeypatch):
        set_env("OIDC_CONSUMER_ISSUER_URL", "")
        monkeypatch.setattr(EXT_OAuthConsumer, "as_requester", self._no_provider)
        EXT_OAuthConsumer.on_start()


ISSUER = "https://auth.example/application/o/app/"


class TestSignInInstance(ExtensionServerMixin):
    extension_class = EXT_OAuthConsumer

    @staticmethod
    def _instances(server, name: str) -> list:
        oidc = EXT_OAuthConsumer.as_requester(ProviderManager, env("ROOT_ID")).get(
            name="oidc"
        )
        return live_instances(server.app.state.model_registry, oidc.id, name)

    def test_without_an_issuer_nothing_is_made(self, server, set_env):
        set_env("OIDC_CONSUMER_ISSUER_URL", "")
        assert ensure_sign_in_instance(EXT_OAuthConsumer.as_requester) is None
        assert self._instances(server, "authentik") == []

    def test_with_an_issuer_the_root_instance_is_made_once(self, server, set_env):
        set_env("OIDC_CONSUMER_ISSUER_URL", ISSUER)
        set_env("OIDC_CONSUMER_INSTANCE", "")
        assert ensure_sign_in_instance(EXT_OAuthConsumer.as_requester) == "authentik"
        assert ensure_sign_in_instance(EXT_OAuthConsumer.as_requester) == "authentik"
        made = self._instances(server, "authentik")
        assert len(made) == 1
        assert made[0].scope == "root"

    def test_the_instance_is_named_by_the_environment(self, server, set_env):
        set_env("OIDC_CONSUMER_ISSUER_URL", ISSUER)
        set_env("OIDC_CONSUMER_INSTANCE", "zephyrex-sso")
        assert ensure_sign_in_instance(EXT_OAuthConsumer.as_requester) == "zephyrex-sso"
        assert len(self._instances(server, "zephyrex-sso")) == 1

    def test_a_deleted_instance_is_made_again(self, server, set_env):
        set_env("OIDC_CONSUMER_ISSUER_URL", ISSUER)
        set_env("OIDC_CONSUMER_INSTANCE", "gone-sso")
        ensure_sign_in_instance(EXT_OAuthConsumer.as_requester)
        (first,) = self._instances(server, "gone-sso")
        EXT_OAuthConsumer.as_requester(ProviderInstanceManager, env("ROOT_ID")).delete(
            first.id
        )
        assert self._instances(server, "gone-sso") == []
        ensure_sign_in_instance(EXT_OAuthConsumer.as_requester)
        (again,) = self._instances(server, "gone-sso")
        assert again.id != first.id

    def test_the_sign_in_instance_supersedes_the_seeded_root_one(self, server, set_env):
        """Both read OIDC_CONSUMER_*: once the sign-in instance is there, the
        seeded Root_Oidc is no second button, and the sign-in instance is
        listed by its own name (its redirect URI's), not as "oidc"."""
        registry = server.app.state.model_registry
        set_env("OIDC_CONSUMER_ISSUER_URL", ISSUER)
        set_env("OIDC_CONSUMER_CLIENT_ID", "client")
        set_env("OIDC_CONSUMER_INSTANCE", "zx-sign-in")

        def oidc_names() -> list:
            configured = configured_providers(registry)
            listed = public_names(configured)
            return sorted(
                listed[str(c.instance.id)]
                for c in configured
                if c.provider.name == "oidc"
            )

        assert "Root_Oidc" in oidc_names()
        ensure_sign_in_instance(EXT_OAuthConsumer.as_requester)
        # Other tests' instances share this app; only these two matter here.
        after = oidc_names()
        assert "Root_Oidc" not in after
        assert "zx-sign-in" in after
