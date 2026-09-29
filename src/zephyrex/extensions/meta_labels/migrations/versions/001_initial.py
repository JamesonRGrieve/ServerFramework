# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for meta_labels: owns the `labels` and `label_links` tables.

meta_labels shipped without a migration, so its tables only ever came from the
runtime create_all fallback, which runs before the extension's SQLAlchemy
models are generated and therefore created nothing; every label endpoint then
failed with "no such table". The Inspector guards make this a no-op on a
database that already has the tables.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_meta_labels_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_meta_labels",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {
    "source_module": "zephyrex.extensions.meta_labels.BLL_Meta_Labels",
    "extension": "meta_labels",
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
    if not _has_table("labels"):
        op.create_table(
            "labels",
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("description", sa.String(), nullable=True),
            sa.Column("color", sa.String(), nullable=True),
            *_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
            comment="Polymorphic label catalog",
            info=TABLE_INFO,
        )
        op.create_index("ix_labels_deleted_at", "labels", ["deleted_at"])

    if not _has_table("label_links"):
        op.create_table(
            "label_links",
            sa.Column("label_id", sa.String(), nullable=False),
            sa.Column("target_type", sa.String(), nullable=False),
            sa.Column("target_id", sa.String(), nullable=False),
            *_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
            comment=(
                "Polymorphic label attachment; dedupes the legacy "
                "AgentLabel/PromptLabel/ChainLabel/... join tables"
            ),
            info=TABLE_INFO,
        )
        op.create_index("ix_label_links_deleted_at", "label_links", ["deleted_at"])


def downgrade() -> None:
    if _has_table("label_links"):
        op.drop_index("ix_label_links_deleted_at", table_name="label_links")
        op.drop_table("label_links")
    if _has_table("labels"):
        op.drop_index("ix_labels_deleted_at", table_name="labels")
        op.drop_table("labels")
