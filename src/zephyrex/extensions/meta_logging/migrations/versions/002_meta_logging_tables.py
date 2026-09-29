# SPDX-License-Identifier: AGPL-3.0-or-later
"""Create meta_logging's audit_logs and system_logs tables.

The initial revision (6045c769f24b) was an autogenerate run against empty
metadata: its upgrade() is a no-op, so databases stamped at it never got the
extension's tables. This follow-up creates them; the guards make it a no-op
where they already exist.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_meta_logging_tables"
down_revision: Union[str, None] = "6045c769f24b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {
    "source_module": "zephyrex.extensions.meta_logging.BLL_Meta_Logging",
    "extension": "meta_logging",
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
    if not _has_table("audit_logs"):
        op.create_table(
            "audit_logs",
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("action", sa.String(), nullable=False),
            sa.Column("resource_type", sa.String(), nullable=False),
            sa.Column("resource_id", sa.String(), nullable=True),
            sa.Column("ip_address", sa.String(), nullable=True),
            sa.Column("user_agent", sa.String(), nullable=True),
            sa.Column("success", sa.Boolean(), nullable=False),
            sa.Column("error_message", sa.String(), nullable=True),
            sa.Column("additional_data", sa.JSON(), nullable=True),
            sa.Column("privacy_impact", sa.Boolean(), nullable=False),
            sa.Column("data_categories", sa.JSON(), nullable=True),
            *_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
            comment="Audit log entries for security and compliance tracking",
            info=TABLE_INFO,
        )
        op.create_index("ix_audit_logs_deleted_at", "audit_logs", ["deleted_at"])

    if not _has_table("system_logs"):
        op.create_table(
            "system_logs",
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.Column("level", sa.String(), nullable=False),
            sa.Column("component", sa.String(), nullable=False),
            sa.Column("message", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("request_id", sa.String(), nullable=True),
            sa.Column("additional_data", sa.JSON(), nullable=True),
            *_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
            comment="General system logs for debugging and monitoring",
            info=TABLE_INFO,
        )
        op.create_index("ix_system_logs_deleted_at", "system_logs", ["deleted_at"])


def downgrade() -> None:
    for table in ("system_logs", "audit_logs"):
        if _has_table(table):
            op.drop_index(f"ix_{table}_deleted_at", table_name=table)
            op.drop_table(table)
