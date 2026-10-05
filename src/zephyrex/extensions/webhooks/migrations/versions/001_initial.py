# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for webhooks: owns ``webhook_subscriptions`` (users'
outbound endpoints, secrets encrypted) and ``webhook_deliveries`` (the
queue and log of what was sent to them).

Outbound delivery was held in process memory before, so no table of it
exists to carry over.
"""

from typing import List, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_webhooks_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_webhooks",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "webhooks"}
TABLES = ("webhook_deliveries", "webhook_subscriptions")


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


def _text(name: str, comment: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.String(), nullable=nullable, comment=comment)


def _when(name: str, comment: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(), nullable=nullable, comment=comment)


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
        "webhook_subscriptions",
        "An endpoint a user has outbound webhook events delivered to",
        _text("user_id", "The ID of the related user", nullable=False),
        _text("target_url", "Where deliveries are POSTed", nullable=False),
        _text(
            "event_types",
            "Space-separated event types delivered; * for all",
            nullable=False,
        ),
        _text("secret", "The HMAC key deliveries are signed with (encrypted)"),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            comment="Whether events are delivered",
        ),
    )
    _create(
        "webhook_deliveries",
        "Outbound webhook deliveries: queued, sent, or dead-lettered",
        _text(
            "webhook_subscription_id",
            "The ID of the related webhook subscription",
            nullable=False,
        ),
        _text("event_type", "The event delivered", nullable=False),
        _text("payload", "The JSON body POSTed, exactly as signed", nullable=False),
        _text("status", "pending, delivered or dead", nullable=False),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, comment="Sends tried so far"
        ),
        _when("next_attempt_at", "When it is next due", nullable=False),
        _text("last_error", "Why the last send failed"),
        _when("delivered_at", "When it was accepted"),
    )
    if not _has_due_index():
        op.create_index(
            "ix_webhook_deliveries_due",
            "webhook_deliveries",
            ["status", "next_attempt_at"],
        )


def _has_due_index() -> bool:
    return any(
        index["name"] == "ix_webhook_deliveries_due"
        for index in sa.inspect(op.get_bind()).get_indexes("webhook_deliveries")
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("webhook_deliveries") and _has_due_index():
        op.drop_index("ix_webhook_deliveries_due", table_name="webhook_deliveries")
    for name in TABLES:
        if sa.inspect(op.get_bind()).has_table(name):
            op.drop_index(f"ix_{name}_deleted_at", table_name=name)
            op.drop_table(name)
