# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for ldap_provider: owns ``ldap_service_accounts``,
the identities legacy applications bind to the directory as.

Before this, ldap_provider had no migration; a deployment may hold the
tables of the replaced ``LDAPDirectoryEntryModel`` and
``LDAPProviderConfigModel`` from the runtime create_all fallback.
Nothing ever read them (the
directory is now computed from users and teams, its configuration is
the environment), so they are left for the operator to drop.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_ldap_provider_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_ldap_provider",)
depends_on: Union[str, Sequence[str], None] = None

TABLE = "ldap_service_accounts"


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("name", sa.String(), nullable=False, comment="The name"),
        sa.Column(
            "description", sa.String(), nullable=True, comment="What uses this account"
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            comment="Whether the account may bind",
        ),
        sa.Column(
            "secret_hash",
            sa.String(),
            nullable=True,
            comment="bcrypt hash of the bind secret",
        ),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        comment="Service accounts that bind to this server's LDAP directory",
        info={"extension": "ldap_provider"},
    )
    op.create_index(f"ix_{TABLE}_name", TABLE, ["name"])
    op.create_index(f"ix_{TABLE}_deleted_at", TABLE, ["deleted_at"])


def downgrade() -> None:
    op.drop_index(f"ix_{TABLE}_deleted_at", table_name=TABLE)
    op.drop_index(f"ix_{TABLE}_name", table_name=TABLE)
    op.drop_table(TABLE)
