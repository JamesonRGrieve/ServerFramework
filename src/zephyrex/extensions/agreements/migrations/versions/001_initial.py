# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for agreements: the ``agreements``,
``agreement_parties`` and ``agreement_terms`` tables."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_agreements_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_agreements",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "agreements"}


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
        "agreements",
        "Written agreements between people",
        sa.Column(
            "title", sa.String(), nullable=False, comment="What the agreement is called"
        ),
        sa.Column(
            "revisit_on", sa.Date(), nullable=True, comment="When to look at it again"
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            comment="Moves on whenever its terms change",
        ),
    )
    _create(
        "agreement_parties",
        "Who is party to each agreement, and what they confirmed",
        sa.Column("agreement_id", sa.String(), nullable=False, comment="The agreement"),
        sa.Column(
            "user_id",
            sa.String(),
            nullable=False,
            comment="The account that is party to it",
        ),
        sa.Column(
            "confirmed_version",
            sa.Integer(),
            nullable=True,
            comment="The version they last confirmed; none until they do",
        ),
    )
    _create(
        "agreement_terms",
        "The terms of each agreement: things agreed, likes and limits",
        sa.Column("agreement_id", sa.String(), nullable=False, comment="The agreement"),
        sa.Column(
            "kind", sa.String(), nullable=False, comment="agreement, like or limit"
        ),
        sa.Column(
            "text",
            sa.String(),
            nullable=False,
            comment="The term, in the parties' own words",
        ),
        sa.Column(
            "position",
            sa.Integer(),
            nullable=False,
            comment="Its place in the agreement",
        ),
        sa.Column(
            "written_by",
            sa.String(),
            nullable=False,
            comment="The party who last wrote it",
        ),
    )


def downgrade() -> None:
    for name in ("agreement_terms", "agreement_parties", "agreements"):
        op.drop_index(f"ix_{name}_created_by_user_id", table_name=name)
        op.drop_table(name)
