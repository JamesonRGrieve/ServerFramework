# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for ldap_consumer: owns ``ldap_directories`` (the
directories people sign in with) and ``ldap_identities`` (who in which
directory signs in as which local user).

Before this, ldap_consumer had no migration; a deployment may hold the
legacy ``l_d_a_p_server_configs`` and ``user_l_d_a_p_links`` tables from the
runtime create_all fallback (the service password in plain text). They
belong to the replaced code and are left for the operator to drop.
"""

from typing import List, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_ldap_consumer_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_ldap_consumer",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "ldap_consumer"}
IDENTITY_KEY = "ux_ldap_identities_directory_external_id"


def _record_columns() -> List[sa.Column]:
    return [
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    ]


def _text(name: str, comment: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.String(), nullable=nullable, comment=comment)


def _flag(name: str, comment: str) -> sa.Column:
    return sa.Column(name, sa.Boolean(), nullable=False, comment=comment)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("ldap_directories"):
        op.create_table(
            "ldap_directories",
            _text("name", "The name", nullable=False),
            _text("host", "Directory host name or address", nullable=False),
            sa.Column(
                "port",
                sa.Integer(),
                nullable=True,
                comment="Port; 636 for ldaps and 389 otherwise when unset",
            ),
            _text(
                "security",
                "ldaps, or starttls; plain only for a loopback directory with "
                "LDAP_CONSUMER_ALLOW_PLAINTEXT_LOOPBACK=true",
                nullable=False,
            ),
            _text(
                "ca_certificate",
                "PEM CA certificate(s) to trust instead of the system store",
            ),
            _text("bind_dn", "Service account DN (write-only)"),
            _text("bind_password", "Service account password (write-only)"),
            _text("base_dn", "Where people are searched for", nullable=False),
            _text("user_object_filter", "Filter every person matches"),
            _text(
                "username_attribute",
                "Attribute holding the username (sAMAccountName on AD)",
                nullable=False,
            ),
            _text(
                "id_attribute",
                "Attribute holding the stable id (objectGUID on AD)",
                nullable=False,
            ),
            _text("email_attribute", "Email attribute"),
            _text("display_name_attribute", "Display name attribute"),
            _text(
                "group_source",
                "none, member_of (the memberOf attribute) or search",
                nullable=False,
            ),
            _text(
                "group_search_base",
                "Where groups are searched for (group_source=search)",
            ),
            _text("group_object_filter", "Filter every group matches"),
            _text("group_member_attribute", "Group attribute listing member DNs"),
            _flag(
                "link_existing_by_email",
                "Trust the directory's email: a first sign-in links to the "
                "local account with that email",
            ),
            sa.Column(
                "timeout_seconds",
                sa.Integer(),
                nullable=False,
                comment="Connect and operation timeout",
            ),
            _flag("enabled", "Whether people may sign in with it"),
            *_record_columns(),
            sa.PrimaryKeyConstraint("id"),
            comment="Directories (LDAP, AD) people sign in with",
            info=TABLE_INFO,
        )
        op.create_index(
            "ix_ldap_directories_deleted_at", "ldap_directories", ["deleted_at"]
        )
    if not inspector.has_table("ldap_identities"):
        op.create_table(
            "ldap_identities",
            _text(
                "ldap_directory_id",
                "The ID of the related ldap_directory",
                nullable=False,
            ),
            _text("user_id", "The ID of the related user", nullable=False),
            _text(
                "external_id",
                "The entry's stable id (entryUUID, objectGUID)",
                nullable=False,
            ),
            _text("dn", "The entry's DN at last sign-in"),
            _text("username", "Directory username"),
            _text("email", "Directory email"),
            sa.Column(
                "groups", sa.JSON(), nullable=True, comment="Group DNs at last sign-in"
            ),
            sa.Column(
                "last_login_at",
                sa.DateTime(),
                nullable=True,
                comment="Last successful directory sign-in",
            ),
            *_record_columns(),
            sa.ForeignKeyConstraint(["ldap_directory_id"], ["ldap_directories.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            comment="Local users' identities in sign-in directories",
            info=TABLE_INFO,
        )
        op.create_index(
            "ix_ldap_identities_deleted_at", "ldap_identities", ["deleted_at"]
        )
        # One live link per directory account, even when two first sign-ins
        # race; a deleted link leaves the account free to link again.
        op.create_index(
            IDENTITY_KEY,
            "ldap_identities",
            ["ldap_directory_id", "external_id"],
            unique=True,
            sqlite_where=sa.text("deleted_at IS NULL"),
            postgresql_where=sa.text("deleted_at IS NULL"),
        )


def downgrade() -> None:
    op.drop_table("ldap_identities")
    op.drop_table("ldap_directories")
