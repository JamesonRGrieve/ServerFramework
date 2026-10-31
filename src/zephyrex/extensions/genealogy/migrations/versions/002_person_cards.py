# SPDX-License-Identifier: AGPL-3.0-or-later
"""Person cards: ``persons.linked_user_id`` (the account a person is),
``persons.visibility`` (how they show to strangers when linked trees
merge) and the ``person_attributes`` table (typed facts on a person's
card).

The Inspector guards make this a no-op on a database whose tables already
have them (from the runtime create_all fallback).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_genealogy_person_cards"
down_revision: Union[str, None] = "001_genealogy_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "genealogy"}


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _audit_columns() -> list:
    return [
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
    ]


def upgrade() -> None:
    columns = {c["name"] for c in _inspector().get_columns("persons")}
    if "linked_user_id" not in columns:
        op.add_column(
            "persons",
            sa.Column(
                "linked_user_id",
                sa.String(),
                nullable=True,
                comment="The account this person is, when they have one",
            ),
        )
        op.create_index("ix_persons_linked_user_id", "persons", ["linked_user_id"])
    if "visibility" not in columns:
        op.add_column(
            "persons",
            sa.Column(
                "visibility",
                sa.String(),
                nullable=True,
                comment="How this person shows to strangers when linked trees merge",
            ),
        )

    if not _inspector().has_table("person_attributes"):
        op.create_table(
            "person_attributes",
            sa.Column(
                "person_id",
                sa.String(),
                sa.ForeignKey("persons.id"),
                nullable=True,
                comment="Optional foreign key to PersonModel",
            ),
            sa.Column("category", sa.String(), nullable=False),
            sa.Column("label", sa.String(), nullable=False),
            sa.Column("value", sa.String(), nullable=False),
            sa.Column("unit", sa.String(), nullable=True),
            sa.Column("position", sa.Integer(), nullable=True),
            *_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
            comment="Typed facts on a person's card",
            info=TABLE_INFO,
        )
        op.create_index(
            "ix_person_attributes_person_id", "person_attributes", ["person_id"]
        )
        op.create_index(
            "ix_person_attributes_deleted_at", "person_attributes", ["deleted_at"]
        )


def downgrade() -> None:
    op.drop_table("person_attributes")
    op.drop_column("persons", "visibility")
    op.drop_index("ix_persons_linked_user_id", table_name="persons")
    op.drop_column("persons", "linked_user_id")
