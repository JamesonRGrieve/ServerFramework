# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for entitlements: owns the ``entitlement_seats`` table
(the seats a subscriber shares their plan with)."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_entitlements_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_entitlements",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "entitlements"}


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("entitlement_seats"):
        return
    op.create_table(
        "entitlement_seats",
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id"),
            nullable=True,
            comment="The subscriber giving the seat",
        ),
        sa.Column(
            "seat_user_id",
            sa.String(),
            nullable=False,
            comment="The account the seat is given to",
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
        comment="Seats a subscriber shares their plan with",
        info=TABLE_INFO,
    )
    op.create_index(
        "ix_entitlement_seats_seat_user_id", "entitlement_seats", ["seat_user_id"]
    )
    op.create_index(
        "ix_entitlement_seats_deleted_at", "entitlement_seats", ["deleted_at"]
    )


def downgrade() -> None:
    op.drop_table("entitlement_seats")
