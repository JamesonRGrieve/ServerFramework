# SPDX-License-Identifier: AGPL-3.0-or-later
"""provider_instance_root_scope

Revision ID: provider_instance_root_scope
Revises: provider_instance_settings_planted

The operator's seeded ``Root_<Provider>`` instances move into the root
scope. Only root- and system-scoped instances may take a setting they lack
from the server's environment (``AbstractStaticProvider.reads_environment``),
so a user's or team's instance never acts with the operator's credentials.
The framework seeded its own instances in the default scope, ``user``; it
seeds them root-scoped since this revision, and this moves the ones already
seeded.

A seeded instance is identified the way the seeder made it, never by a name
prefix a user could also choose:

- its name is ``Root_`` and its provider's name PascalCased by
  ``stringcase.pascalcase`` (``stripe`` -> ``Root_Stripe``), the rule of
  ``BLL_Providers.root_instance_name``, frozen here;
- ROOT created it (the seeder writes as ``seed_creator_id``, ROOT);
- it belongs to no user but ROOT, and to no team; and
- it is in the ``user`` scope (an instance the operator has since put in
  another scope keeps it).

An instance a user made, whatever it is called, is created by that user and
is left alone. Deleted instances are moved too: one restored is the
operator's as before.

Idempotent: a second run finds no seeded instance left in the ``user``
scope. Core SQLAlchemy only, so it runs alike on SQLite and PostgreSQL.

``downgrade`` puts the seeded instances back in the ``user`` scope, where
the code before this revision seeds and expects them.
"""

import logging
from typing import List, Sequence, Union

import sqlalchemy as sa
import stringcase
from alembic import op

from zephyrex.lib.Environment import env

revision: str = "provider_instance_root_scope"
down_revision: Union[str, None] = "provider_instance_settings_planted"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger(__name__)

SEEDED_SCOPE = "user"
ROOT_SCOPE = "root"
INSTANCES_TABLE = "provider_instances"
PROVIDERS_TABLE = "providers"

_instances = sa.table(
    INSTANCES_TABLE,
    sa.column("id", sa.String),
    sa.column("name", sa.String),
    sa.column("provider_id", sa.String),
    sa.column("scope", sa.String),
    sa.column("user_id", sa.String),
    sa.column("team_id", sa.String),
    sa.column("created_by_user_id", sa.String),
)
_providers = sa.table(
    PROVIDERS_TABLE,
    sa.column("id", sa.String),
    sa.column("name", sa.String),
)


def seeded_instance_name(provider_name: str) -> str:
    """The name the seeder gave a provider's ``Root_<Provider>`` instance."""
    return f"Root_{stringcase.pascalcase(provider_name)}"


def seeded_instance_ids(bind: sa.Connection, scope: str) -> List[str]:
    """The ids of the seeded ``Root_<Provider>`` instances in ``scope``."""
    root_id = env("ROOT_ID")
    rows = bind.execute(
        sa.select(
            _instances.c.id, _instances.c.name, _providers.c.name.label("provider")
        )
        .join(_providers, _providers.c.id == _instances.c.provider_id)
        .where(
            _instances.c.scope == scope,
            _instances.c.created_by_user_id == root_id,
            sa.or_(_instances.c.user_id.is_(None), _instances.c.user_id == root_id),
            _instances.c.team_id.is_(None),
        )
    )
    return sorted(
        str(row.id)
        for row in rows
        if row.provider is not None and row.name == seeded_instance_name(row.provider)
    )


def rescope(bind: sa.Connection, from_scope: str, to_scope: str) -> List[str]:
    """Moves the seeded instances in ``from_scope`` to ``to_scope``; their ids."""
    ids = seeded_instance_ids(bind, from_scope)
    if ids:
        bind.execute(
            _instances.update()
            .where(_instances.c.id.in_(ids), _instances.c.scope == from_scope)
            .values(scope=to_scope)
        )
    logger.info(
        "Seeded provider instances moved from the %s scope to the %s scope: %d",
        from_scope,
        to_scope,
        len(ids),
    )
    return ids


def _has_tables(bind: sa.Connection) -> bool:
    inspector = sa.inspect(bind)
    return inspector.has_table(INSTANCES_TABLE) and inspector.has_table(PROVIDERS_TABLE)


def upgrade() -> None:
    bind = op.get_bind()
    if _has_tables(bind):
        rescope(bind, SEEDED_SCOPE, ROOT_SCOPE)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_tables(bind):
        rescope(bind, ROOT_SCOPE, SEEDED_SCOPE)
