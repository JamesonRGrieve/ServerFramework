# SPDX-License-Identifier: AGPL-3.0-or-later
"""The extension's declaration, its abilities against the real directory,
and its migration."""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, Dict

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from zephyrex.extensions.ldap_consumer.conftest import Directory
from zephyrex.extensions.ldap_consumer.EXT_LDAPConsumer import EXT_LDAPConsumer
from zephyrex.lib.Dependencies import EXT_Dependency
from zephyrex.lib.Environment import env

_MIGRATION = Path(__file__).parent / "migrations" / "versions" / "001_initial.py"


def test_the_declaration() -> None:
    assert EXT_LDAPConsumer.name == "ldap_consumer"
    assert EXT_LDAPConsumer.version == "2.0.0"
    assert EXT_LDAPConsumer.pip_requirements() == ["ldap3>=2.9.1"]
    assert sorted(
        (d.name, d.optional)
        for d in EXT_LDAPConsumer.dependencies
        if isinstance(d, EXT_Dependency)
    ) == [("auth_lockout", True), ("auth_session", False)]
    assert EXT_LDAPConsumer.get_abilities() >= {
        "list_ldap_directories",
        "check_ldap_directory",
        "find_ldap_account",
    }


@pytest.fixture(scope="module")
def directory_id(extension_app: Any, directory: Directory) -> str:
    from zephyrex.extensions.ldap_consumer.BLL_LDAPConsumer import (
        LdapDirectoryManager,
    )

    manager = LdapDirectoryManager(
        requester_id=env("ROOT_ID"),
        model_registry=extension_app.state.model_registry,
    )
    created = manager.create(**directory.settings(name="Abilities"))
    return str(created.id)


async def test_the_abilities(directory: Directory, directory_id: str) -> None:
    listed = await EXT_LDAPConsumer.list_ldap_directories()
    assert {
        "id": directory_id,
        "name": "Abilities",
        "host": "localhost",
        "security": "ldaps",
    } in listed
    assert await EXT_LDAPConsumer.check_ldap_directory(directory_id) == {
        "reachable": True,
        "security": "ldaps",
    }
    found: Dict[str, Any] = await EXT_LDAPConsumer.find_ldap_account(
        directory_id, "bob"
    )
    assert found["dn"] == directory.people["bob"].dn
    assert found["email"] == "bob@example.com"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ldap_consumer_001", _MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_matches_the_models(extension_app: Any) -> None:
    base = extension_app.state.model_registry.DB.manager.Base
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE users (id VARCHAR PRIMARY KEY)"))
        migration = _load_migration()
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            inspector = sa.inspect(connection)
            for table in ("ldap_directories", "ldap_identities"):
                migrated = {c["name"]: c for c in inspector.get_columns(table)}
                modelled = base.metadata.tables[table].columns
                assert set(migrated) == {c.name for c in modelled}, table
                for column in modelled:
                    assert migrated[column.name]["nullable"] == column.nullable, (
                        table,
                        column.name,
                    )
            unique = [
                index
                for index in inspector.get_indexes("ldap_identities")
                if index["unique"]
            ]
            assert [i["column_names"] for i in unique] == [
                ["ldap_directory_id", "external_id"]
            ]
            migration.upgrade()
            migration.downgrade()
            assert not sa.inspect(connection).has_table("ldap_identities")
            assert not sa.inspect(connection).has_table("ldap_directories")
