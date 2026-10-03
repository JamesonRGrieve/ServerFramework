# SPDX-License-Identifier: AGPL-3.0-or-later
"""Payments and subscriptions recorded per user, and the account that holds
a user's customer record.

``payments`` and ``payment_subscriptions`` mirror what each provider holds
for the user they were made for. ``users.payment_instance_id`` names the
account (provider instance) of ``users.external_payment_id``: a customer
id means nothing at another account. A link made before this has no
account, so it no longer counts; ``customer_create`` makes a new one.
"""

from typing import List, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_payment_records"
down_revision: Union[str, None] = "ee80e1858d77"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "payment"}
PAYMENTS = "payments"
SUBSCRIPTIONS = "payment_subscriptions"


def _text(name: str, comment: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.String(), nullable=nullable, comment=comment)


def _source_columns() -> List[sa.Column]:
    return [
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id"),
            nullable=False,
            comment="The ID of the related user",
        ),
        _text("provider", "The provider (stripe, square, …)", nullable=False),
        _text("provider_instance_id", "The account it is on", nullable=False),
        _text("external_id", "The provider's id for it", nullable=False),
        sa.Column(
            "refreshed_at",
            sa.DateTime(),
            nullable=False,
            comment="When the provider was last read",
        ),
    ]


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


def _create(name: str, comment: str, *columns: sa.Column) -> None:
    if sa.inspect(op.get_bind()).has_table(name):
        return
    op.create_table(
        name,
        *_source_columns(),
        *columns,
        *_record_columns(),
        sa.PrimaryKeyConstraint("id"),
        comment=comment,
        info=TABLE_INFO,
    )
    op.create_index(f"ix_{name}_deleted_at", name, ["deleted_at"])


def upgrade() -> None:
    _create(
        PAYMENTS,
        "Payments (a local mirror of each provider's payment)",
        _text(
            "status",
            "pending, authorized, succeeded, failed, canceled, refunded",
            nullable=False,
        ),
        _text("provider_status", "The provider's own status"),
        _text("amount", "The amount (decimal, major units)", nullable=False),
        _text("currency", "ISO 4217 currency code", nullable=False),
        _text("amount_refunded", "Refunded so far"),
        _text("customer_id", "The provider's customer"),
        _text("description", "What it is for"),
    )
    _create(
        SUBSCRIPTIONS,
        "Subscriptions (a local mirror of each provider's subscription)",
        _text("plan_id", "The plan subscribed to"),
        _text("customer_id", "The provider's customer"),
        _text("status", "The provider's status for it", nullable=False),
        sa.Column(
            "active", sa.Boolean(), nullable=False, comment="Whether it is paid up now"
        ),
        sa.Column(
            "current_period_end",
            sa.DateTime(),
            nullable=True,
            comment="When the period paid for ends",
        ),
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean(),
            nullable=False,
            comment="Whether it ends when the period does",
        ),
    )
    present = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    if "payment_instance_id" not in present:
        op.add_column(
            "users",
            sa.Column(
                "payment_instance_id",
                sa.String(),
                nullable=True,
                comment="The payment account (provider instance) holding it",
            ),
        )


def downgrade() -> None:
    present = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    if "payment_instance_id" in present:
        with op.batch_alter_table("users") as batch:
            batch.drop_column("payment_instance_id")
    for name in (SUBSCRIPTIONS, PAYMENTS):
        if sa.inspect(op.get_bind()).has_table(name):
            op.drop_index(f"ix_{name}_deleted_at", table_name=name)
            op.drop_table(name)
