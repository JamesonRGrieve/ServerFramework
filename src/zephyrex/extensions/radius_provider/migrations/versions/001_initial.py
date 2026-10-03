# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for radius_provider: owns ``radius_clients`` (the NAS
clients answered, with their encrypted shared secrets) and
``radius_team_policies`` (reply attributes per team).

The replaced scaffold had no migration; a deployment may hold its
``radius_nas_clients`` (shared secrets in plain text) and
``radius_provider_configs`` tables from the runtime create_all fallback. They
belong to the replaced code and are left for the operator to drop.
"""

from typing import List, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_radius_provider_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_radius_provider",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {
    "source_module": "extensions.radius_provider.BLL_RADIUSProvider",
    "extension": "radius_provider",
}
TABLES = ("radius_team_policies", "radius_clients")


def _record_columns() -> List[sa.Column]:
    return [
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
    ]


def _create(name: str, comment: str, *columns: sa.Column) -> None:
    if sa.inspect(op.get_bind()).has_table(name):
        return
    op.create_table(
        name,
        *columns,
        *_record_columns(),
        sa.PrimaryKeyConstraint("id"),
        comment=comment,
        info=TABLE_INFO,
    )
    op.create_index(f"ix_{name}_deleted_at", name, ["deleted_at"])


def upgrade() -> None:
    _create(
        "radius_clients",
        "NAS clients the RADIUS authenticator answers",
        sa.Column("name", sa.String(), nullable=False, comment="What the NAS is"),
        sa.Column(
            "address",
            sa.String(),
            nullable=False,
            comment="Source address or network (CIDR) the NAS sends from",
        ),
        sa.Column(
            "shared_secret",
            sa.String(),
            nullable=True,
            comment="Shared secret (write-only, encrypted)",
        ),
        sa.Column(
            "required_team_id",
            sa.String(),
            nullable=True,
            comment="Only members of this team are accepted through it",
        ),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            nullable=False,
            comment="Whether its requests are answered",
        ),
    )
    _create(
        "radius_team_policies",
        "RADIUS reply attributes per team",
        sa.Column(
            "team_id",
            sa.String(),
            sa.ForeignKey("teams.id"),
            nullable=False,
            comment="The ID of the related team",
        ),
        sa.Column(
            "vlan_id", sa.Integer(), nullable=True, comment="VLAN to place them on"
        ),
        sa.Column(
            "filter_id",
            sa.String(),
            nullable=True,
            comment="Filter-Id the NAS applies",
        ),
        sa.Column(
            "session_timeout_seconds",
            sa.Integer(),
            nullable=True,
            comment="Session-Timeout, in seconds",
        ),
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            comment="Lowest wins when a user's teams have several",
        ),
    )


def downgrade() -> None:
    for name in TABLES:
        if sa.inspect(op.get_bind()).has_table(name):
            op.drop_index(f"ix_{name}_deleted_at", table_name=name)
            op.drop_table(name)
