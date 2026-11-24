# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for auth_privacy: `data_regions`, `data_residency_preferences`.

Generated from the extension's own models. The Inspector guards make it a no-op on a database
that already has the tables (made from the models before this extension shipped migrations).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_auth_privacy_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_auth_privacy",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {
    "source_module": "zephyrex.extensions.auth_privacy.BLL_Auth_Privacy",
    "extension": "auth_privacy",
}


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("data_regions"):
        op.create_table(
            "data_regions",
            sa.Column(
                "name",
                sa.String(),
                nullable=False,
                comment="Region identifier, e.g. 'eu-west-1'",
            ),
            sa.Column(
                "description",
                sa.String(),
                nullable=True,
                comment="Human-readable description",
            ),
            sa.Column(
                "is_available",
                sa.Boolean(),
                nullable=False,
                comment="Whether new data may be placed here",
            ),
            sa.Column(
                "compliance_level",
                sa.String(),
                nullable=True,
                comment="One of 'gdpr', 'hipaa', etc.",
            ),
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("created_by_user_id", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            comment="Catalog of data regions tenants may pin residency to",
            info=TABLE_INFO,
        )
        op.create_index(
            "ix_data_regions_deleted_at", "data_regions", ["deleted_at"], unique=False
        )

    if not _has_table("data_residency_preferences"):
        op.create_table(
            "data_residency_preferences",
            sa.Column(
                "user_id",
                sa.String(),
                nullable=True,
                comment="User this preference belongs to (xor team_id)",
            ),
            sa.Column(
                "team_id",
                sa.String(),
                nullable=True,
                comment="Team this preference belongs to (xor user_id)",
            ),
            sa.Column(
                "data_type",
                sa.String(),
                nullable=False,
                comment="Data category: 'user_data' | 'files' | 'analytics' | ...",
            ),
            sa.Column(
                "region_id",
                sa.String(),
                nullable=False,
                comment="Reference to ``DataRegionModel.id``",
            ),
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("created_by_user_id", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            comment="Per-tenant data-residency pinning",
            info=TABLE_INFO,
        )
        op.create_index(
            "ix_data_residency_preferences_deleted_at",
            "data_residency_preferences",
            ["deleted_at"],
            unique=False,
        )


def downgrade() -> None:
    if _has_table("data_residency_preferences"):
        op.drop_table("data_residency_preferences")
    if _has_table("data_regions"):
        op.drop_table("data_regions")
