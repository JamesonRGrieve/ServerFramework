# SPDX-License-Identifier: AGPL-3.0-or-later
"""``entitlement_purchases``: the units of a product each purchase bought, and
what for.

A product that sells units (seats in a group, credits) is bought in a
quantity, optionally for a subject; each purchase is recorded against the
payment that pays for it, so the units a subject still has paid for can be
counted from the payments' status and refunds.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_entitlement_purchases"
down_revision: Union[str, None] = "001_entitlements_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "entitlements"}
TABLE = "entitlement_purchases"


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id"),
            nullable=True,
            comment="The buyer",
        ),
        sa.Column(
            "payment_id",
            sa.String(),
            nullable=False,
            comment="The payment that pays for it",
        ),
        sa.Column(
            "product_id",
            sa.String(),
            nullable=False,
            comment="The registered product bought",
        ),
        sa.Column(
            "quantity",
            sa.Integer(),
            nullable=False,
            comment="How many units were bought",
        ),
        sa.Column(
            "subject_id",
            sa.String(),
            nullable=True,
            comment="What the units were bought for (a group, a project)",
        ),
        sa.Column(
            "created_by_user_id",
            sa.String(),
            sa.ForeignKey("users.id"),
            nullable=True,
            comment="Optional foreign key to UserModel",
        ),
        sa.Column(
            "updated_by_user_id",
            sa.String(),
            sa.ForeignKey("users.id"),
            nullable=True,
            comment="Optional foreign key to UserModel",
        ),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        comment="Units of a product bought, and what for",
        info=TABLE_INFO,
    )
    op.create_index(
        "ix_entitlement_purchases_product_subject", TABLE, ["product_id", "subject_id"]
    )
    op.create_index("ix_entitlement_purchases_deleted_at", TABLE, ["deleted_at"])


def downgrade() -> None:
    op.drop_table(TABLE)
