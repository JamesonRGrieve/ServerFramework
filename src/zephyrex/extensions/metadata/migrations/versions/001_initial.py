"""Initial migration for metadata — owns the `metadata` table.

The `metadata` table was originally created by core in
`e0b0dc9d5070_initial_schema.py` before the Phase-2 carve-out (PR #144).
On databases stamped with that core revision the table already exists;
the Inspector check below makes the upgrade a no-op in that case. On
fresh databases, core's initial schema is still the file that creates
the table — this revision tracks ownership for any *subsequent* schema
changes the extension makes to it.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "001_metadata_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("metadata",)
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _existing_index_names(table: str) -> set:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table)}


def upgrade() -> None:
    if not _has_table("metadata"):
        op.create_table(
            "metadata",
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("team_id", sa.String(), nullable=True),
            sa.Column("key", sa.String(), nullable=False),
            sa.Column("value", sa.String(), nullable=True),
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("created_by_user_id", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            comment="Unified metadata table for users and teams",
            info={
                "source_module": "zephyrex.extensions.metadata.BLL_Metadata",
                "extension": "metadata",
            },
        )

    existing = _existing_index_names("metadata")
    if "ix_metadata_user_key" not in existing:
        op.create_index("ix_metadata_user_key", "metadata", ["user_id", "key"])
    if "ix_metadata_team_key" not in existing:
        op.create_index("ix_metadata_team_key", "metadata", ["team_id", "key"])


def downgrade() -> None:
    existing = _existing_index_names("metadata")
    if "ix_metadata_team_key" in existing:
        op.drop_index("ix_metadata_team_key", table_name="metadata")
    if "ix_metadata_user_key" in existing:
        op.drop_index("ix_metadata_user_key", table_name="metadata")
    if _has_table("metadata"):
        op.drop_table("metadata")
