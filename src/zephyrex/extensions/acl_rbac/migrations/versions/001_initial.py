"""Initial migration for acl_rbac — owns `permissions`.

The `permissions` table was originally created by core in
`e0b0dc9d5070_initial_schema.py` before the Phase-2 carve-out (PR #144).
On databases that were stamped with that core revision, the table already
exists; the Inspector check below makes the upgrade a no-op in that case.
On fresh databases, core's initial schema is still the file that creates
the table — this revision tracks ownership for any *subsequent* schema
changes the extension makes to the table.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "001_acl_rbac_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("acl_rbac",)
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _existing_index_names(table: str) -> set:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table)}


def upgrade() -> None:
    if not _has_table("permissions"):
        op.create_table(
            "permissions",
            sa.Column("role_id", sa.String(), nullable=True),
            sa.Column("team_id", sa.String(), nullable=True),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("resource_type", sa.String(), nullable=False),
            sa.Column("resource_id", sa.String(), nullable=False),
            sa.Column("can_view", sa.Boolean(), nullable=False),
            sa.Column("can_execute", sa.Boolean(), nullable=False),
            sa.Column("can_copy", sa.Boolean(), nullable=False),
            sa.Column("can_edit", sa.Boolean(), nullable=False),
            sa.Column("can_delete", sa.Boolean(), nullable=False),
            sa.Column("can_share", sa.Boolean(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("created_by_user_id", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            comment="Fine-grained permissions for specific resources and actions",
            info={
                "source_module": "zephyrex.extensions.acl_rbac.BLL_ACL",
                "extension": "acl_rbac",
            },
        )

    existing = _existing_index_names("permissions")
    if "ix_permissions_resource" not in existing:
        op.create_index(
            "ix_permissions_resource",
            "permissions",
            ["resource_type", "resource_id"],
        )
    if "ix_permissions_user" not in existing:
        op.create_index("ix_permissions_user", "permissions", ["user_id"])


def downgrade() -> None:
    existing = _existing_index_names("permissions")
    if "ix_permissions_user" in existing:
        op.drop_index("ix_permissions_user", table_name="permissions")
    if "ix_permissions_resource" in existing:
        op.drop_index("ix_permissions_resource", table_name="permissions")
    if _has_table("permissions"):
        op.drop_table("permissions")
