# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for forward_auth_consumer: owns
``forward_auth_identities``, the forward-auth identities linked to local
users.

The verifier endpoints are provider instances and their settings, not a
table of this extension's.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_forward_auth_consumer_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_forward_auth_consumer",)
depends_on: Union[str, Sequence[str], None] = None

TABLE = "forward_auth_identities"


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id"),
            nullable=False,
            comment="The ID of the related user",
        ),
        sa.Column(
            "provider_instance_id",
            sa.String(),
            nullable=False,
            comment="The verifier instance that vouches for it",
        ),
        sa.Column(
            "identity",
            sa.String(),
            nullable=False,
            comment="The user the verifier names, as its user header gives it",
        ),
        sa.Column(
            "last_login_at",
            sa.DateTime(),
            nullable=True,
            comment="When the identity last signed in",
        ),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        comment="Forward-auth identities linked to local users",
        info={"extension": "forward_auth_consumer"},
    )
    op.create_index(
        f"ix_{TABLE}_instance_identity", TABLE, ["provider_instance_id", "identity"]
    )
    op.create_index(f"ix_{TABLE}_user_id", TABLE, ["user_id"])
    op.create_index(f"ix_{TABLE}_deleted_at", TABLE, ["deleted_at"])


def downgrade() -> None:
    for index in ("deleted_at", "user_id", "instance_identity"):
        op.drop_index(f"ix_{TABLE}_{index}", table_name=TABLE)
    op.drop_table(TABLE)
