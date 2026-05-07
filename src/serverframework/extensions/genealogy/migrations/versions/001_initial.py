"""Initial migration for genealogy — owns ``persons`` and ``relationships``.

FK enforcement is non-negotiable for the relationship graph:
``person_id`` and ``target_person_id`` are FK-constrained to
``persons.id``. Endpoint-existence (NOT NULL) is enforced for the
standalone case; downstream extensions that widen the table with
additional endpoint kinds are responsible for relaxing nullability and
adding the corresponding CHECK constraint as part of their migration.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "001_genealogy_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("genealogy",)
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _existing_index_names(table: str) -> set:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table)}


def upgrade() -> None:
    if not _has_table("persons"):
        op.create_table(
            "persons",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(255), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("birth_date", sa.DateTime(), nullable=True),
            sa.Column("death_date", sa.DateTime(), nullable=True),
            sa.Column("gender", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            comment=(
                "An actor identity. Used standalone for genealogy and as the "
                "humanoid subject of RPG Characters via Character.person_id."
            ),
            info={
                "source_module": "serverframework.extensions.genealogy.BLL_Genealogy",
                "extension": "genealogy",
            },
        )

    persons_idx = _existing_index_names("persons")
    if "ix_persons_name" not in persons_idx:
        op.create_index("ix_persons_name", "persons", ["name"])
    if "ix_persons_birth" not in persons_idx:
        op.create_index("ix_persons_birth", "persons", ["birth_date"])

    if not _has_table("relationships"):
        op.create_table(
            "relationships",
            sa.Column("id", sa.String(36), nullable=False),
            # Standalone-genealogy endpoints — both NOT NULL by default.
            # Downstream extensions that widen the table relax these to
            # nullable and replace the existence guarantee with a CHECK
            # over the union of endpoint columns.
            sa.Column("person_id", sa.String(36), nullable=False),
            sa.Column("target_person_id", sa.String(36), nullable=False),
            sa.Column("kind", sa.String(64), nullable=True),
            sa.Column("discriminator", sa.String(64), nullable=True),
            sa.Column("intensity", sa.Float(), nullable=True),
            sa.Column("qualifier", sa.String(255), nullable=True),
            sa.Column("valid_from", sa.DateTime(), nullable=True),
            sa.Column("valid_to", sa.DateTime(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["person_id"], ["persons.id"]),
            sa.ForeignKeyConstraint(["target_person_id"], ["persons.id"]),
            comment=(
                "Directed labelled edge. Default endpoints are Persons. "
                "Downstream extensions may inject additional endpoint "
                "columns and are responsible for the corresponding "
                "ALTER TABLE plus endpoint-XOR CHECK constraint."
            ),
            info={
                "source_module": "serverframework.extensions.genealogy.BLL_Genealogy",
                "extension": "genealogy",
            },
        )

    rel_idx = _existing_index_names("relationships")
    if "ix_rel_subject_person" not in rel_idx:
        op.create_index("ix_rel_subject_person", "relationships", ["person_id"])
    if "ix_rel_target_person" not in rel_idx:
        op.create_index(
            "ix_rel_target_person", "relationships", ["target_person_id"]
        )
    if "ix_rel_kind" not in rel_idx:
        op.create_index("ix_rel_kind", "relationships", ["kind"])
    if "ix_rel_validity" not in rel_idx:
        op.create_index(
            "ix_rel_validity", "relationships", ["valid_from", "valid_to"]
        )


def downgrade() -> None:
    rel_idx = _existing_index_names("relationships")
    for name in (
        "ix_rel_validity",
        "ix_rel_kind",
        "ix_rel_target_person",
        "ix_rel_subject_person",
    ):
        if name in rel_idx:
            op.drop_index(name, table_name="relationships")
    if _has_table("relationships"):
        op.drop_table("relationships")

    persons_idx = _existing_index_names("persons")
    for name in ("ix_persons_birth", "ix_persons_name"):
        if name in persons_idx:
            op.drop_index(name, table_name="persons")
    if _has_table("persons"):
        op.drop_table("persons")
