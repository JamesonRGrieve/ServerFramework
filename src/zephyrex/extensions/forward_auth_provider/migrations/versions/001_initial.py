# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for forward_auth_provider: owns ``forward_auth_rules``
(who may reach a host and path behind the proxy).

Before this, the extension had no migration; a deployment may hold a
``forward_auth_rules`` table from the runtime create_all fallback with the
scaffold's columns (path_pattern, required_roles, ...). The guard leaves it
in place; the operator drops it so this one can be created.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_forward_auth_provider_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_forward_auth_provider",)
depends_on: Union[str, Sequence[str], None] = None

TABLE = "forward_auth_rules"
TABLE_INFO = {"extension": "forward_auth_provider"}


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("name", sa.String(), nullable=False, comment="The name"),
        sa.Column(
            "host",
            sa.String(),
            nullable=True,
            comment="Host the rule covers (no port); empty: every host",
        ),
        sa.Column(
            "path_prefix",
            sa.String(),
            nullable=False,
            comment="Path the rule covers, with everything below it",
        ),
        sa.Column(
            "required_team_id",
            sa.String(),
            nullable=True,
            comment="Team whose members alone may pass; empty: anyone signed in",
        ),
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            comment="Higher wins between rules for the same host and path",
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            comment="Whether the rule is applied",
        ),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        comment="Who may reach a host and path behind the proxy",
        info=TABLE_INFO,
    )
    op.create_index(f"ix_{TABLE}_deleted_at", TABLE, ["deleted_at"])


def downgrade() -> None:
    op.drop_table(TABLE)
