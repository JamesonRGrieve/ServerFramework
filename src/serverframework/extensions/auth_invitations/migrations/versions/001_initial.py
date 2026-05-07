"""Initial migration for auth_invitations — owns `invitations` and `invitees`.

These tables were originally created by core in
`e0b0dc9d5070_initial_schema.py` before the Phase-2 carve-out (PR #144).
On databases stamped with that core revision the tables already exist;
the Inspector check below makes the upgrade a no-op in that case. On
fresh databases, core's initial schema is still the file that creates
the tables — this revision tracks ownership for any *subsequent* schema
changes the extension makes to them.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "001_auth_invitations_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("auth_invitations",)
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _existing_index_names(table: str) -> set:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table)}


def upgrade() -> None:
    if not _has_table("invitations"):
        op.create_table(
            "invitations",
            sa.Column("role_id", sa.String(), nullable=True),
            sa.Column("team_id", sa.String(), nullable=True),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("code", sa.String(), nullable=True),
            sa.Column("max_uses", sa.Integer(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("created_by_user_id", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            comment="Invitations to join teams, can be direct or via invitation code",
            info={
                "source_module": "serverframework.extensions.auth_invitations.BLL_Invitations",
                "extension": "auth_invitations",
            },
        )

    if not _has_table("invitees"):
        op.create_table(
            "invitees",
            sa.Column("invitation_id", sa.String(), nullable=True),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("email", sa.String(), nullable=False),
            sa.Column("declined_at", sa.DateTime(), nullable=True),
            sa.Column("accepted_at", sa.DateTime(), nullable=True),
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("created_by_user_id", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            comment="Tracks specific individuals invited to join a team",
            info={
                "source_module": "serverframework.extensions.auth_invitations.BLL_Invitations",
                "extension": "auth_invitations",
            },
        )

    invitations_idx = _existing_index_names("invitations")
    if "ix_invitations_team" not in invitations_idx:
        op.create_index("ix_invitations_team", "invitations", ["team_id"])

    invitees_idx = _existing_index_names("invitees")
    if "ix_invitees_invitation" not in invitees_idx:
        op.create_index("ix_invitees_invitation", "invitees", ["invitation_id"])


def downgrade() -> None:
    invitees_idx = _existing_index_names("invitees")
    if "ix_invitees_invitation" in invitees_idx:
        op.drop_index("ix_invitees_invitation", table_name="invitees")

    invitations_idx = _existing_index_names("invitations")
    if "ix_invitations_team" in invitations_idx:
        op.drop_index("ix_invitations_team", table_name="invitations")

    if _has_table("invitees"):
        op.drop_table("invitees")
    if _has_table("invitations"):
        op.drop_table("invitations")
