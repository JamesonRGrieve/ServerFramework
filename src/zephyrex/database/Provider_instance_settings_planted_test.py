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
    sa.Column("expires_at", sa.DateTime),
    sa.Column("deleted_at", sa.DateTime),
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

# acl_rbac's grants, kept apart: the cleanup must also run without them.
_acl_metadata = sa.MetaData()
_permissions = sa.Table(
    "permissions",
    _acl_metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("user_id", sa.String),
    sa.Column("team_id", sa.String),
    sa.Column("role_id", sa.String),
    sa.Column("resource_type", sa.String, nullable=False),
    sa.Column("resource_id", sa.String, nullable=False),
    sa.Column("can_view", sa.Boolean, nullable=False, default=False),
    sa.Column("can_edit", sa.Boolean, nullable=False, default=False),
    sa.Column("enabled", sa.Boolean, nullable=False, default=True),
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
FORMER_ADMIN, EXPIRED_ADMIN, DISABLED_ADMIN, STRANGER, MODERATOR = (
    "former_admin",
    "expired_admin",
    "disabled_admin",
    "stranger",
    "moderator",
)
USER_ROLE, ADMIN_ROLE, SUPER_ROLE = (
    env("USER_ROLE_ID"),
    env("ADMIN_ROLE_ID"),
    env("SUPERADMIN_ROLE_ID"),
)


class Role(NamedTuple):
    id: str
    name: str
    parent_id: Optional[str]
    deleted_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


def _add_roles(conn: sa.Connection, *roles: Role) -> None:
    conn.execute(_roles.insert(), [role._asdict() for role in roles])


@pytest.fixture
def legacy(tmp_path: Path) -> Iterator[sa.Engine]:
    """A database with the tables the cleanup reads, holding one instance of
    each scope and every kind of author."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    _metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        _add_roles(
            conn,
            Role(USER_ROLE, "user", None),
            Role(ADMIN_ROLE, "admin", USER_ROLE),
            Role(SUPER_ROLE, "superadmin", ADMIN_ROLE),
            Role("r_mod", "mod", USER_ROLE),
        )
        conn.execute(_teams.insert(), [{"id": "t1", "deleted_at": None}])
        conn.execute(
            _user_teams.insert(),
            [
                _membership("m1", ADMIN, ADMIN_ROLE),
                _membership("m2", SUPERADMIN, SUPER_ROLE),
                _membership("m3", MEMBER, USER_ROLE),
                _membership("m4", FORMER_ADMIN, ADMIN_ROLE, deleted_at=now),
                _membership(
                    "m5", EXPIRED_ADMIN, ADMIN_ROLE, expires_at=now - timedelta(days=1)
                ),
                _membership("m6", DISABLED_ADMIN, ADMIN_ROLE, enabled=False),
                _membership("m7", MODERATOR, "r_mod"),
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
            plant_by_moderator=("i_team", MODERATOR),
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

    def test_an_acl_editors_settings_are_kept_and_revoked_grants_are_not(self, legacy):
        """A live Permission row granting its user edit on the instance
        makes them its configurer; a revoked (soft-deleted) or lapsed grant,
        one that only lets them view, one on another instance, or a team's
        grant does not. Root- and system-scoped instances stay ROOT's and
        SYSTEM's alone, whatever a grant says."""
        now = datetime.now(timezone.utc)
        _acl_metadata.create_all(legacy)

        def grant(user_id: Optional[str], instance_id: str, **fields: Any) -> dict:
            row = {
                "id": f"g_{user_id or fields.get('team_id')}_{instance_id}",
                "user_id": user_id,
                "team_id": None,
                "role_id": None,
                "resource_type": "provider_instances",
                "resource_id": instance_id,
                "can_view": False,
                "can_edit": True,
                "enabled": True,
                "expires_at": None,
                "deleted_at": None,
            }
            row.update(fields)
            return row

        with legacy.begin() as conn:
            conn.execute(
                _permissions.insert(),
                [
                    grant("editor", "i_user"),
                    grant("editor", "i_root"),
                    grant("editor", "i_system"),
                    grant("revoked", "i_user", deleted_at=now),
                    grant("lapsed", "i_user", expires_at=now - timedelta(days=1)),
                    grant("viewer", "i_user", can_edit=False, can_view=True),
                    grant("elsewhere", "i_team"),
                    grant(None, "i_user", team_id="t1"),
                ],
            )
        _write(
            legacy,
            kept_editor=("i_user", "editor"),
            plant_by_editor_on_root=("i_root", "editor"),
            plant_by_editor_on_system=("i_system", "editor"),
            plant_by_revoked=("i_user", "revoked"),
            plant_by_lapsed=("i_user", "lapsed"),
            plant_by_viewer=("i_user", "viewer"),
            plant_by_editor_elsewhere=("i_user", "elsewhere"),
            plant_by_team_granted=("i_user", MEMBER),
        )

        _upgrade(legacy)

        rows = _rows(legacy)
        assert _live(legacy) == ["kept_editor"]
        assert rows["plant_by_revoked"].deleted_by_user_id == env("ROOT_ID")

    def test_without_the_settings_table_it_does_nothing(self, tmp_path):
        engine = sa.create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
        try:
            _upgrade(engine)
            assert not sa.inspect(engine).has_table("provider_instance_settings")
        finally:
            engine.dispose()


class TestAdminRoles:
    """The roles that make a team member an admin of its records, as the
    edit rule judges them: the admin role and the live roles extending it,
    by ``parent_id`` ancestry by id.

    This replaces ``test_a_custom_role_at_admin_rank_counts``, which held
    the first copy of the rule (depth keyed by name): there a ``lead``
    extending ``user`` counted as an admin. That rule was the privilege
    escalation (database/Team_admin_edit_test.py), so its holder's plants
    are now removed."""

    @staticmethod
    def admins(legacy: sa.Engine, *roles: Role) -> set:
        with legacy.begin() as conn:
            _add_roles(conn, *roles)
            return set(_migration().admin_role_ids(conn))

    def test_admin_and_the_roles_extending_it(self, legacy):
        assert self.admins(
            legacy, Role("r_lead", "lead", ADMIN_ROLE), Role("r_deputy", "x", "r_lead")
        ) == {ADMIN_ROLE, SUPER_ROLE, "r_lead", "r_deputy"}

    def test_a_role_at_the_admins_depth_does_not_count(self, legacy):
        """``r_mod`` (the fixture's) and ``lead`` extend ``user``, where the
        admin role sits; a role named ``user`` there, or one named
        ``admin`` anywhere, ranks nothing by its name."""
        assert self.admins(
            legacy,
            Role("r_lead", "lead", USER_ROLE),
            Role("r_user_named", "user", USER_ROLE),
            Role("r_admin_named", "admin", None),
        ) == {ADMIN_ROLE, SUPER_ROLE}

    def test_a_dead_role_extends_nothing(self, legacy):
        now = datetime.now(timezone.utc)
        assert self.admins(
            legacy,
            Role("r_gone", "gone", ADMIN_ROLE, deleted_at=now),
            Role("r_below_gone", "below", "r_gone"),
            Role("r_lapsed", "lapsed", ADMIN_ROLE, expires_at=now - timedelta(days=1)),
        ) == {ADMIN_ROLE, SUPER_ROLE}

    def test_a_cyclic_tree_ends_the_walk(self, legacy):
        assert self.admins(
            legacy, Role("r_a", "a", "r_b"), Role("r_b", "b", "r_a")
        ) == {ADMIN_ROLE, SUPER_ROLE}

    def test_no_admin_role_means_no_admins(self, tmp_path):
        engine = sa.create_engine(f"sqlite:///{tmp_path / 'roles.db'}")
        try:
            _metadata.create_all(engine)
            with engine.begin() as conn:
                _add_roles(conn, Role(USER_ROLE, "user", None))
                assert _migration().admin_role_ids(conn) == set()
        finally:
            engine.dispose()
