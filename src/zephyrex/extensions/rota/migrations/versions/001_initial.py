# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for rota: the ``rotas``, ``rota_nights`` and
``rota_swaps`` tables."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_rota_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_rota",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "rota"}


def _record_columns() -> list:
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
        *columns,
        *_record_columns(),
        sa.PrimaryKeyConstraint("id"),
        comment=comment,
        info=TABLE_INFO,
    )
    op.create_index(f"ix_{name}_created_by_user_id", name, ["created_by_user_id"])


def upgrade() -> None:
    _create(
        "rotas",
        "Repeating rotas of nights, kept by their owner",
        sa.Column(
            "name", sa.String(), nullable=False, comment="What the rota is called"
        ),
        sa.Column(
            "cycle_days",
            sa.Integer(),
            nullable=False,
            comment="How many nights before it repeats",
        ),
        sa.Column(
            "starts_on",
            sa.Date(),
            nullable=False,
            comment="The first night of the first cycle",
        ),
    )
    _create(
        "rota_nights",
        "Who each night of a rota's cycle is with",
        sa.Column("rota_id", sa.String(), nullable=False, comment="The rota"),
        sa.Column(
            "day_index",
            sa.Integer(),
            nullable=False,
            comment="Which night of the cycle, from 0",
        ),
        sa.Column(
            "user_id",
            sa.String(),
            nullable=False,
            comment="The account that night is with",
        ),
    )
    _create(
        "rota_swaps",
        "One-off swaps of a rota's night, agreed by both people",
        sa.Column("rota_id", sa.String(), nullable=False, comment="The rota"),
        sa.Column("night", sa.Date(), nullable=False, comment="The night swapped"),
        sa.Column(
            "from_user_id",
            sa.String(),
            nullable=False,
            comment="Whom the night is with before the swap",
        ),
        sa.Column(
            "to_user_id",
            sa.String(),
            nullable=False,
            comment="Whom it would be with after",
        ),
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            comment="pending, accepted or declined",
        ),
    )


def downgrade() -> None:
    for name in ("rota_swaps", "rota_nights", "rotas"):
        op.drop_index(f"ix_{name}_created_by_user_id", table_name=name)
        op.drop_table(name)
