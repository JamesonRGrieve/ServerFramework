# SPDX-License-Identifier: AGPL-3.0-or-later
"""provider_instance_settings_planted

Revision ID: provider_instance_settings_planted
Revises: users_email_unique_live

A one-time cleanup of provider instance settings that were planted: rows
written by someone who could not configure their instance. Until 6acbda48 a
setting was checked as ROOT on create, so any user could write ``api_base``,
``url`` or ``verify_url`` onto another user's instance, or onto the
operator's root- and system-scoped ones, and the provider (which reads
settings on the server's behalf) then applied it. Writes are checked as the
requester since then (``ProviderInstanceChildManager``), so no new plant can
be made; this removes the old ones, and after it every live setting applies
whatever later becomes of its author.

A live setting is legitimate when its author:

- is ROOT or SYSTEM; or, on an instance that is not root- or system-scoped,
- is the instance's owner (``user_id``) or creator (``created_by_user_id``),
  or
- is a live admin of the instance's team (``team_id``): an enabled,
  unexpired, undeleted membership in a team that is not deleted, holding the
  admin role or one ranked at or above it. That is the team-admin edit rule
  of ``generate_permission_filter``, frozen here as a migration must keep
  meaning what it meant when it ran.

On a root- or system-scoped instance only ROOT and SYSTEM are legitimate,
whoever the instance names as its user. A row with no author is not
legitimate (no authority can be shown for it). Everything else is planted.

**Approximation.** No point-in-time history of team roles exists, so team
admin status is read as it stands when this runs, not as it stood when the
row was written. A row its author wrote as a team admin who has since left
the team, or been demoted, is removed with the plants; a row written by a
member who was promoted later is kept. Editors who held the instance only
through an acl_rbac Permission row are not recognised either.

Planted rows are soft-deleted, not removed: the table soft-deletes (this
keeps the audit trail, and a wrongly judged row is restored by clearing its
``deleted_at``), and ``ProviderInstanceModel.get_setting`` reads live rows
only. The ids removed are logged per instance, with their count, but never
their values: a setting can be a secret.

Idempotent: a second run finds no live planted rows. Core SQLAlchemy only,
so it runs alike on SQLite and PostgreSQL. Settings whose instance no longer
exists are left alone (no provider reads them).
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

import sqlalchemy as sa
from alembic import op

from zephyrex.lib.Environment import env

revision: str = "provider_instance_settings_planted"
down_revision: Union[str, None] = "users_email_unique_live"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger(__name__)

OPERATOR_SCOPES = frozenset({"root", "system"})
DEFAULT_SCOPE = "user"
ADMIN_ROLE_NAME = "admin"
# As the role hierarchy the edit rule ranks roles by.
MAX_ROLE_DEPTH = 10
# Ids per UPDATE, under every supported backend's bound-parameter limit.
TOMBSTONE_BATCH = 500
SETTINGS_TABLE = "provider_instance_settings"

_settings = sa.table(
    SETTINGS_TABLE,
    sa.column("id", sa.String),
    sa.column("provider_instance_id", sa.String),
    sa.column("created_by_user_id", sa.String),
    sa.column("deleted_at", sa.DateTime),
    sa.column("deleted_by_user_id", sa.String),
)
_instances = sa.table(
    "provider_instances",
    sa.column("id", sa.String),
    sa.column("scope", sa.String),
    sa.column("user_id", sa.String),
    sa.column("team_id", sa.String),
    sa.column("created_by_user_id", sa.String),
)
_roles = sa.table(
    "roles",
    sa.column("id", sa.String),
    sa.column("name", sa.String),
    sa.column("parent_id", sa.String),
)
_user_teams = sa.table(
    "user_teams",
    sa.column("user_id", sa.String),
    sa.column("team_id", sa.String),
    sa.column("role_id", sa.String),
    sa.column("enabled", sa.Boolean),
    sa.column("expires_at", sa.DateTime),
    sa.column("deleted_at", sa.DateTime),
)
_teams = sa.table(
    "teams",
    sa.column("id", sa.String),
    sa.column("deleted_at", sa.DateTime),
)


def admin_role_ids(roles: Iterable[sa.Row]) -> Set[str]:
    """The ids of the roles ranked at or above the admin role. Roles are
    ranked by depth in the ``parent_id`` tree, keyed by name, as
    ``_get_role_hierarchy_map`` ranks them."""
    roles = list(roles)
    level_by_name: Dict[str, int] = {}
    current = [role for role in roles if role.parent_id is None]
    level = 0
    while current and level < MAX_ROLE_DEPTH:
        for role in current:
            level_by_name[role.name] = level
        parent_ids = {role.id for role in current}
        current = [role for role in roles if role.parent_id in parent_ids]
        level += 1
    admin_level = level_by_name.get(ADMIN_ROLE_NAME)
    if admin_level is None:
        return set()
    return {
        str(role.id)
        for role in roles
        if level_by_name.get(role.name, -1) >= admin_level
    }


def _live_team_admins(bind: sa.Connection) -> Set[Tuple[str, str]]:
    """``(user_id, team_id)`` for every live admin membership."""
    admin_ids = admin_role_ids(
        bind.execute(sa.select(_roles.c.id, _roles.c.name, _roles.c.parent_id))
    )
    if not admin_ids:
        return set()
    rows = bind.execute(
        sa.select(_user_teams.c.user_id, _user_teams.c.team_id)
        .join(_teams, _teams.c.id == _user_teams.c.team_id)
        .where(
            _user_teams.c.role_id.in_(sorted(admin_ids)),
            _user_teams.c.enabled == sa.true(),
            sa.or_(
                _user_teams.c.expires_at.is_(None),
                _user_teams.c.expires_at > sa.func.now(),
            ),
            _user_teams.c.deleted_at.is_(None),
            _teams.c.deleted_at.is_(None),
        )
    )
    return {(str(row.user_id), str(row.team_id)) for row in rows}


def is_legitimate(
    author_id: Optional[str],
    scope: Optional[str],
    owner_id: Optional[str],
    team_id: Optional[str],
    creator_id: Optional[str],
    team_admins: Set[Tuple[str, str]],
) -> bool:
    """Whether a setting ``author_id`` wrote speaks for its instance (the
    rule in this module's docstring)."""
    if author_id is None:
        return False
    if author_id in (env("ROOT_ID"), env("SYSTEM_ID")):
        return True
    if (scope or DEFAULT_SCOPE) in OPERATOR_SCOPES:
        return False
    if author_id in (owner_id, creator_id):
        return True
    return team_id is not None and (author_id, team_id) in team_admins


def planted_setting_ids(bind: sa.Connection) -> Dict[str, List[str]]:
    """``{instance id: [setting ids]}`` for every live planted setting."""
    team_admins = _live_team_admins(bind)
    rows = bind.execute(
        sa.select(
            _settings.c.id,
            _settings.c.provider_instance_id,
            _settings.c.created_by_user_id.label("author_id"),
            _instances.c.scope,
            _instances.c.user_id,
            _instances.c.team_id,
            _instances.c.created_by_user_id.label("creator_id"),
        )
        .join(_instances, _instances.c.id == _settings.c.provider_instance_id)
        .where(_settings.c.deleted_at.is_(None))
    )
    planted: Dict[str, List[str]] = defaultdict(list)
    for row in rows:
        if not is_legitimate(
            row.author_id,
            row.scope,
            row.user_id,
            row.team_id,
            row.creator_id,
            team_admins,
        ):
            planted[str(row.provider_instance_id)].append(str(row.id))
    return {instance: sorted(ids) for instance, ids in sorted(planted.items())}


def tombstone(bind: sa.Connection, planted: Dict[str, List[str]]) -> None:
    """Soft-deletes ``planted`` as ROOT (the server's cleanup), logging the
    count and the ids per instance, never the values."""
    deleted_at = datetime.now(timezone.utc)
    for instance_id, setting_ids in planted.items():
        for start in range(0, len(setting_ids), TOMBSTONE_BATCH):
            bind.execute(
                _settings.update()
                .where(
                    _settings.c.id.in_(setting_ids[start : start + TOMBSTONE_BATCH]),
                    _settings.c.deleted_at.is_(None),
                )
                .values(deleted_at=deleted_at, deleted_by_user_id=env("ROOT_ID"))
            )
        logger.warning(
            "Removed %d planted setting(s) from provider instance %s: %s",
            len(setting_ids),
            instance_id,
            ", ".join(setting_ids),
        )


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(SETTINGS_TABLE):
        return
    planted = planted_setting_ids(bind)
    tombstone(bind, planted)
    logger.info(
        "Planted provider instance settings removed: %d on %d instance(s)",
        sum(len(ids) for ids in planted.values()),
        len(planted),
    )


def downgrade() -> None:
    """Nothing is put back: which rows were plants is not recorded beyond
    the upgrade's log. A row judged wrongly is restored by clearing its
    ``deleted_at`` and ``deleted_by_user_id``."""
