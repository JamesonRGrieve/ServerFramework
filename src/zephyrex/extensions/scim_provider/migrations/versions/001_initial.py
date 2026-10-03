# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for scim_provider: owns ``scim_links`` (each framework
user and team as a SCIM target holds it) and ``scim_sync_logs`` (every
write made to a target).

The scaffold this replaces declared ``scim_targets`` (with a plain-text
bearer token) and ``scim_sync_logs`` models but never shipped a migration
or any code that used them; a database built by the runtime create_all
fallback may hold those tables. A target is now a provider instance whose
token is a write-only setting, so a leftover ``scim_targets`` table is the
operator's to drop. A leftover ``scim_sync_logs`` of the old shape is left
alone by the guard below and must be dropped for this one to be created.
"""

from typing import List, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_scim_provider_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_scim_provider",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {
    "source_module": "zephyrex.extensions.scim_provider.BLL_SCIMProvider",
    "extension": "scim_provider",
}
TABLES = ("scim_sync_logs", "scim_links")


def _record_columns() -> List[sa.Column]:
    return [
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    ]


def _target() -> sa.Column:
    return sa.Column(
        "provider_instance_id",
        sa.String(),
        sa.ForeignKey("provider_instances.id"),
        nullable=False,
        comment="The SCIM target",
    )


def _text(name: str, comment: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.String(), nullable=nullable, comment=comment)


def _create(name: str, comment: str, *columns: sa.Column) -> None:
    if sa.inspect(op.get_bind()).has_table(name):
        return
    op.create_table(
        name,
        _target(),
        *columns,
        *_record_columns(),
        sa.PrimaryKeyConstraint("id"),
        comment=comment,
        info=TABLE_INFO,
    )
    op.create_index(f"ix_{name}_deleted_at", name, ["deleted_at"])
    op.create_index(
        f"ix_{name}_target_local",
        name,
        ["provider_instance_id", "resource_type", "local_id"],
    )


def upgrade() -> None:
    _create(
        "scim_links",
        "Framework users and teams as held by SCIM targets",
        _text("resource_type", "'User' or 'Group'", nullable=False),
        _text("local_id", "The framework user's or team's id", nullable=False),
        _text("remote_id", "Its id on the target"),
        _text("remote_name", "The userName or displayName last pushed"),
        _text("remote_etag", "Its version on the target"),
        _text("pushed_hash", "Digest of what was pushed"),
        sa.Column(
            "pending", sa.Boolean(), nullable=False, comment="Whether a push is due"
        ),
        _text(
            "status",
            "pending, synced, deactivated, deleted or error",
            nullable=False,
        ),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True, comment="Last push"),
        _text("last_error", "Why the last push failed"),
    )
    _create(
        "scim_sync_logs",
        "Every write made to a SCIM target",
        _text("resource_type", "'User' or 'Group'", nullable=False),
        _text(
            "operation",
            "create, link, patch, replace, deactivate, delete or push",
            nullable=False,
        ),
        _text("local_id", "The framework user's or team's id", nullable=False),
        _text("remote_id", "Its id on the target"),
        _text("status", "success or error", nullable=False),
        _text("error_detail", "Why it failed"),
    )


def downgrade() -> None:
    for name in TABLES:
        op.drop_index(f"ix_{name}_target_local", table_name=name)
        op.drop_index(f"ix_{name}_deleted_at", table_name=name)
        op.drop_table(name)
