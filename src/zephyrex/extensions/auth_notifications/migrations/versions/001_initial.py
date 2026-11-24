# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for auth_notifications: `user_notifications`, `notifications`.

Generated from the extension's own models. The Inspector guards make it a no-op on a database
that already has the tables (made from the models before this extension shipped migrations).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_auth_notifications_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_auth_notifications",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {
    "source_module": "zephyrex.extensions.auth_notifications.BLL_Auth_Notifications",
    "extension": "auth_notifications",
}


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("user_notifications"):
        op.create_table(
            "user_notifications",
            sa.Column(
                "user_id",
                sa.String(),
                sa.ForeignKey("users.id"),
                nullable=False,
                comment="Foreign key to UserModel",
            ),
            sa.Column(
                "notification_id",
                sa.String(),
                nullable=False,
                comment="Reference to NotificationModel",
            ),
            sa.Column(
                "read",
                sa.Boolean(),
                nullable=False,
                comment="Whether the user has read it",
            ),
            sa.Column("read_at", sa.DateTime(), nullable=True, comment="When read"),
            sa.Column(
                "acknowledged",
                sa.Boolean(),
                nullable=False,
                comment="Whether the user dismissed it",
            ),
            sa.Column(
                "acknowledged_at",
                sa.DateTime(),
                nullable=True,
                comment="When acknowledged",
            ),
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("created_by_user_id", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            comment="Per-user notification delivery state",
            info=TABLE_INFO,
        )
        op.create_index(
            "ix_user_notifications_deleted_at",
            "user_notifications",
            ["deleted_at"],
            unique=False,
        )

    if not _has_table("notifications"):
        op.create_table(
            "notifications",
            sa.Column(
                "team_id",
                sa.String(),
                sa.ForeignKey("teams.id"),
                nullable=True,
                comment="Optional foreign key to TeamModel",
            ),
            sa.Column(
                "user_id",
                sa.String(),
                sa.ForeignKey("users.id"),
                nullable=True,
                comment="Optional foreign key to UserModel",
            ),
            sa.Column(
                "title", sa.String(), nullable=False, comment="Notification title"
            ),
            sa.Column(
                "content", sa.String(), nullable=False, comment="Notification body"
            ),
            sa.Column(
                "reference_type",
                sa.String(),
                nullable=True,
                comment="Type of referenced entity, e.g. 'invitation'",
            ),
            sa.Column(
                "reference_id",
                sa.String(),
                nullable=True,
                comment="ID of referenced entity",
            ),
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("created_by_user_id", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            comment="System and user notifications with team/user scoping",
            info=TABLE_INFO,
        )
        op.create_index(
            "ix_notifications_deleted_at", "notifications", ["deleted_at"], unique=False
        )


def downgrade() -> None:
    if _has_table("notifications"):
        op.drop_table("notifications")
    if _has_table("user_notifications"):
        op.drop_table("user_notifications")
