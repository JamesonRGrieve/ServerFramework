# SPDX-License-Identifier: AGPL-3.0-or-later
"""Migration ``provider_instance_settings_planted``: the one-time cleanup of
provider instance settings planted before 6acbda48 checked writes.

Run here against a real SQLite database holding the tables as they stand at
this revision. That the rule reads the real app schema alike (fixtures,
roles and memberships made by the managers) is shown in
``logic/BLL_Providers_instance_access_test.py``."""

import importlib.util
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Iterator, List, NamedTuple, Optional

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from zephyrex.lib.Environment import env

MIGRATION_NAME = "provider_instance_settings_planted"
MIGRATION = Path(__file__).parent / "migrations" / "versions" / f"{MIGRATION_NAME}.py"
SECRET_VALUE = "planted-secret-value"
EARLIER = datetime(2026, 1, 1)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(MIGRATION_NAME, MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_metadata = sa.MetaData()
_settings = sa.Table(
    "provider_instance_settings",
    _metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("provider_instance_id", sa.String, nullable=False),
    sa.Column("key", sa.String, nullable=False),
    sa.Column("value", sa.String),
    sa.Column("created_by_user_id", sa.String),
    sa.Column("deleted_at", sa.DateTime),
    sa.Column("deleted_by_user_id", sa.String),
)
_instances = sa.Table(
    "provider_instances",
    _metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("scope", sa.String, nullable=False),
    sa.Column("user_id", sa.String),
    sa.Column("team_id", sa.String),
    sa.Column("created_by_user_id", sa.String),
)
_roles = sa.Table(
    "roles",
    _metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("parent_id", sa.String),
)
_teams = sa.Table(
    "teams",
    _metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("deleted_at", sa.DateTime),
)
_user_teams = sa.Table(
    "user_teams",
    _metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("user_id", sa.String),
    sa.Column("team_id", sa.String),
    sa.Column("role_id", sa.String),
    sa.Column("enabled", sa.Boolean, nullable=False),
    sa.Column("expires_at", sa.DateTime),
    sa.Column("deleted_at", sa.DateTime),
)

OWNER, CREATOR, ADMIN, SUPERADMIN, MEMBER = (
    "owner",
    "creator",
    "admin",
    "superadmin",
    "member",
)
FORMER_ADMIN, EXPIRED_ADMIN, DISABLED_ADMIN, STRANGER = (
    "former_admin",
    "expired_admin",
    "disabled_admin",
    "stranger",
)


@pytest.fixture
def legacy(tmp_path: Path) -> Iterator[sa.Engine]:
    """A database with the tables the cleanup reads, holding one instance of
    each scope and every kind of author."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    _metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            _roles.insert(),
            [
                {"id": "r_user", "name": "user", "parent_id": None},
                {"id": "r_admin", "name": "admin", "parent_id": "r_user"},
                {"id": "r_super", "name": "superadmin", "parent_id": "r_admin"},
            ],
        )
        conn.execute(_teams.insert(), [{"id": "t1", "deleted_at": None}])
        conn.execute(
            _user_teams.insert(),
            [
                _membership("m1", ADMIN, "r_admin"),
                _membership("m2", SUPERADMIN, "r_super"),
                _membership("m3", MEMBER, "r_user"),
                _membership("m4", FORMER_ADMIN, "r_admin", deleted_at=now),
                _membership(
                    "m5", EXPIRED_ADMIN, "r_admin", expires_at=now - timedelta(days=1)
                ),
                _membership("m6", DISABLED_ADMIN, "r_admin", enabled=False),
            ],
        )
        conn.execute(
            _instances.insert(),
            [
                _instance("i_user", "user", user_id=OWNER, creator=OWNER),
                _instance("i_team", "team", team_id="t1", creator=CREATOR),
                _instance("i_root", "root", user_id=env("ROOT_ID")),
                _instance("i_system", "system", user_id=OWNER),
            ],
        )
    yield engine
    engine.dispose()


def _membership(
    row_id: str,
    user_id: str,
    role_id: str,
    enabled: bool = True,
    expires_at: Optional[datetime] = None,
    deleted_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    return {
        "id": row_id,
        "user_id": user_id,
        "team_id": "t1",
        "role_id": role_id,
        "enabled": enabled,
        "expires_at": expires_at,
        "deleted_at": deleted_at,
    }


def _instance(
    row_id: str,
    scope: str,
    user_id: Optional[str] = None,
    team_id: Optional[str] = None,
    creator: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": row_id,
        "scope": scope,
        "user_id": user_id,
        "team_id": team_id,
        "created_by_user_id": creator or env("ROOT_ID"),
    }


def _write(engine: sa.Engine, **rows: tuple) -> None:
    """Settings ``{id: (instance id, author id)}``, each holding the same
    secret-looking value."""
    with engine.begin() as conn:
        for setting_id, (instance_id, author_id) in rows.items():
            conn.execute(
                _settings.insert().values(
                    id=setting_id,
                    provider_instance_id=instance_id,
                    key="api_base",
                    value=SECRET_VALUE,
                    created_by_user_id=author_id,
                )
            )


def _upgrade(engine: sa.Engine) -> None:
    module = _migration()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def _rows(engine: sa.Engine) -> Dict[str, sa.Row]:
    with engine.connect() as conn:
        return {row.id: row for row in conn.execute(sa.select(_settings))}


def _live(engine: sa.Engine) -> List[str]:
    return sorted(i for i, row in _rows(engine).items() if row.deleted_at is None)


class TestTheCleanup:
    def test_it_removes_plants_and_keeps_what_configurers_wrote(self, legacy):
        root, system = env("ROOT_ID"), env("SYSTEM_ID")
        _write(
            legacy,
            kept_root=("i_user", root),
            kept_system=("i_team", system),
            kept_owner=("i_user", OWNER),
            kept_creator=("i_team", CREATOR),
            kept_team_admin=("i_team", ADMIN),
            kept_superadmin=("i_team", SUPERADMIN),
            kept_root_on_root=("i_root", root),
            kept_system_on_system=("i_system", system),
            plant_by_stranger=("i_user", STRANGER),
            plant_by_member=("i_team", MEMBER),
            plant_by_former_admin=("i_team", FORMER_ADMIN),
            plant_by_expired_admin=("i_team", EXPIRED_ADMIN),
            plant_by_disabled_admin=("i_team", DISABLED_ADMIN),
            plant_on_root=("i_root", OWNER),
            plant_on_system_by_its_owner=("i_system", OWNER),
            plant_with_no_author=("i_user", None),
        )

        _upgrade(legacy)

        rows = _rows(legacy)
        assert _live(legacy) == sorted(i for i in rows if i.startswith("kept_"))
        for setting_id, row in rows.items():
            assert row.value == SECRET_VALUE, "soft-deleted, not rewritten"
            if setting_id.startswith("plant_"):
                assert row.deleted_by_user_id == root

    def test_a_row_already_deleted_is_left_as_it_was(self, legacy):
        _write(legacy, gone=("i_user", STRANGER))
        with legacy.begin() as conn:
            conn.execute(
                _settings.update().values(deleted_at=EARLIER, deleted_by_user_id=OWNER)
            )

        _upgrade(legacy)

        row = _rows(legacy)["gone"]
        assert (row.deleted_at, row.deleted_by_user_id) == (EARLIER, OWNER)

    def test_a_setting_with_no_instance_is_left_alone(self, legacy):
        _write(legacy, orphan=("i_missing", STRANGER))
        _upgrade(legacy)
        assert _live(legacy) == ["orphan"]

    def test_it_is_idempotent(self, legacy):
        _write(legacy, plant=("i_user", STRANGER), kept=("i_user", OWNER))
        _upgrade(legacy)
        first = _rows(legacy)

        _upgrade(legacy)

        assert _rows(legacy) == first
        assert _live(legacy) == ["kept"]

    def test_it_logs_ids_per_instance_never_values(self, legacy, caplog):
        _write(
            legacy,
            plant_a=("i_user", STRANGER),
            plant_b=("i_user", MEMBER),
            plant_c=("i_root", OWNER),
        )
        with caplog.at_level(logging.INFO, logger=MIGRATION_NAME):
            _upgrade(legacy)

        removed = [r.getMessage() for r in caplog.records if "i_user" in r.getMessage()]
        assert removed == [
            "Removed 2 planted setting(s) from provider instance i_user: "
            "plant_a, plant_b"
        ]
        assert any("i_root: plant_c" in r.getMessage() for r in caplog.records)
        assert all(SECRET_VALUE not in r.getMessage() for r in caplog.records)

    def test_without_the_settings_table_it_does_nothing(self, tmp_path):
        engine = sa.create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
        try:
            _upgrade(engine)
            assert not sa.inspect(engine).has_table("provider_instance_settings")
        finally:
            engine.dispose()


class Role(NamedTuple):
    id: str
    name: str
    parent_id: Optional[str]


class TestAdminRoles:
    """The roles that make a team member an admin of its records, ranked
    as the edit rule ranks them: by depth, keyed by name."""

    def test_admin_and_the_roles_above_it(self):
        roles = [
            Role("r_user", "user", None),
            Role("r_admin", "admin", "r_user"),
            Role("r_super", "superadmin", "r_admin"),
        ]
        assert _migration().admin_role_ids(roles) == {"r_admin", "r_super"}

    def test_a_custom_role_at_admin_rank_counts(self):
        """As the edit rule ranks them: a role extending ``user`` sits at
        the admin's depth (a team's ``mod``, say), so it counts."""
        roles = [
            Role("r_user", "user", None),
            Role("r_admin", "admin", "r_user"),
            Role("r_lead", "lead", "r_user"),
        ]
        assert _migration().admin_role_ids(roles) == {"r_admin", "r_lead"}

    def test_no_admin_role_means_no_admins(self):
        assert _migration().admin_role_ids([Role("r_user", "user", None)]) == set()
