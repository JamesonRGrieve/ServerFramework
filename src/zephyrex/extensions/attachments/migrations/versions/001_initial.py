# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for attachments: the `assets` and `attachments` tables.

An attachment's asset is a real foreign key (deleting an asset removes its attachments); its
record is polymorphic (`target_entity_type` + `target_entity_id`), so it has no foreign key and is
checked through the record's own manager instead. The Inspector guards make this a no-op on a
database that already has the tables.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_attachments_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_attachments",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {
    "source_module": "zephyrex.extensions.attachments.BLL_Attachments",
    "extension": "attachments",
}


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _audit_columns() -> list:
    return [
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    ]


def upgrade() -> None:
    if not _has_table("assets"):
        op.create_table(
            "assets",
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("content_type", sa.String(), nullable=False),
            sa.Column("size", sa.Integer(), nullable=False),
            sa.Column("sha256", sa.String(), nullable=False),
            sa.Column("provider", sa.String(), nullable=False),
            sa.Column("path", sa.String(), nullable=False),
            *_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
            comment="Uploaded files, stored in cloud storage",
            info=TABLE_INFO,
        )
        op.create_index("ix_assets_deleted_at", "assets", ["deleted_at"])

    if not _has_table("attachments"):
        op.create_table(
            "attachments",
            sa.Column("asset_id", sa.String(), nullable=False),
            sa.Column("target_entity_type", sa.String(), nullable=False),
            sa.Column("target_entity_id", sa.String(), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
            *_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
            comment="Assets attached to records by type and id",
            info=TABLE_INFO,
        )
        op.create_index("ix_attachments_deleted_at", "attachments", ["deleted_at"])
        op.create_index("ix_attachments_asset_id", "attachments", ["asset_id"])
        op.create_index(
            "ix_attachments_target",
            "attachments",
            ["target_entity_type", "target_entity_id"],
        )


def downgrade() -> None:
    if _has_table("attachments"):
        op.drop_index("ix_attachments_target", table_name="attachments")
        op.drop_index("ix_attachments_asset_id", table_name="attachments")
        op.drop_index("ix_attachments_deleted_at", table_name="attachments")
        op.drop_table("attachments")
    if _has_table("assets"):
        op.drop_index("ix_assets_deleted_at", table_name="assets")
        op.drop_table("assets")
