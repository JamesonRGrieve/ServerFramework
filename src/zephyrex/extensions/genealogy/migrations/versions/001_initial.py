# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for genealogy: owns the ``persons`` and
``relationships`` tables.

genealogy shipped without a migration (in zephyrex-rpg), so a deployment's
tables came from the runtime create_all fallback, if at all. The Inspector
guards make this a no-op on a database that already has them.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_genealogy_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_genealogy",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "genealogy"}


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


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
    if not _has_table("persons"):
        op.create_table(
            "persons",
            sa.Column("description", sa.String(), nullable=True),
            sa.Column("name", sa.String(), nullable=True),
            sa.Column(
                "birth_date", sa.DateTime(), nullable=True, comment="Date of birth"
            ),
            sa.Column(
                "death_date",
                sa.DateTime(),
                nullable=True,
                comment="Date of death; NULL = living or unknown",
            ),
            sa.Column(
                "gender", sa.String(), nullable=True, comment="Free-form gender label"
            ),
            *_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
            comment="An actor identity: a genealogy subject, or the person behind another extension's actor.",
            info=TABLE_INFO,
        )
        op.create_index("ix_persons_deleted_at", "persons", ["deleted_at"])

    if not _has_table("relationships"):
        op.create_table(
            "relationships",
            sa.Column(
                "person_id",
                sa.String(),
                sa.ForeignKey("persons.id"),
                nullable=True,
                comment="Subject-side Person endpoint",
            ),
            sa.Column(
                "target_person_id",
                sa.String(),
                nullable=True,
                comment="Object-side Person endpoint",
            ),
            sa.Column("kind", sa.String(), nullable=True, comment="Relationship kind"),
            sa.Column(
                "discriminator",
                sa.String(),
                nullable=True,
                comment="The subject's role in the relationship",
            ),
            sa.Column(
                "intensity",
                sa.Float(),
                nullable=True,
                comment="-1.0 nemesis to +1.0 closest",
            ),
            sa.Column(
                "qualifier", sa.String(), nullable=True, comment="Conditional context"
            ),
            sa.Column(
                "valid_from", sa.DateTime(), nullable=True, comment="NULL = always"
            ),
            sa.Column(
                "valid_to",
                sa.DateTime(),
                nullable=True,
                comment="NULL = still in effect",
            ),
            sa.Column("notes", sa.String(), nullable=True, comment="Free-form notes"),
            *_audit_columns(),
            sa.PrimaryKeyConstraint("id"),
            comment="Directed labelled edge: subject (kind, discriminator) object.",
            info=TABLE_INFO,
        )
        op.create_index("ix_relationships_deleted_at", "relationships", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_relationships_deleted_at", table_name="relationships")
    op.drop_table("relationships")
    op.drop_index("ix_persons_deleted_at", table_name="persons")
    op.drop_table("persons")
