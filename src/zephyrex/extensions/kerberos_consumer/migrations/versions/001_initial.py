# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for kerberos_consumer: owns ``kerberos_principals``,
the Kerberos principals linked to local users.

The service keytabs are provider instance settings (encrypted, write-only),
not a table of this extension's.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_kerberos_consumer_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_kerberos_consumer",)
depends_on: Union[str, Sequence[str], None] = None

TABLE = "kerberos_principals"


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
            "principal",
            sa.String(),
            nullable=False,
            comment="Realm-qualified Kerberos principal (alice@EXAMPLE.COM)",
        ),
        sa.Column(
            "realm", sa.String(), nullable=False, comment="The principal's realm"
        ),
        sa.Column(
            "last_login_at",
            sa.DateTime(),
            nullable=True,
            comment="When the principal last signed in",
        ),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        comment="Kerberos principals linked to local users",
        info={"extension": "kerberos_consumer"},
    )
    op.create_index(f"ix_{TABLE}_principal", TABLE, ["principal"])
    op.create_index(f"ix_{TABLE}_deleted_at", TABLE, ["deleted_at"])


def downgrade() -> None:
    op.drop_index(f"ix_{TABLE}_deleted_at", table_name=TABLE)
    op.drop_index(f"ix_{TABLE}_principal", table_name=TABLE)
    op.drop_table(TABLE)
