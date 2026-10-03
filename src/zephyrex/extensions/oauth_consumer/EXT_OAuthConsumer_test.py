# SPDX-License-Identifier: AGPL-3.0-or-later
"""The extension itself: its migration creates exactly the models' tables,
it registers its grant, and it refuses to run without encryption."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.oauth_consumer.BLL_OAuthConsumer import (
    GRANT_TYPE,
    OAuthIdentityModel,
    OAuthLoginStateModel,
)
from zephyrex.extensions.oauth_consumer.EXT_OAuthConsumer import EXT_OAuthConsumer
from zephyrex.logic.BLL_Auth import PasswordlessGrantRegistry

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
