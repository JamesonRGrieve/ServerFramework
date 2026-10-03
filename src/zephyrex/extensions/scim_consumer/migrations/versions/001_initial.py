# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for scim_consumer: ``scim_connections`` (identity
providers and their token digests), ``scim_resources`` (the users and
teams each provisioned) and ``scim_provisioning_logs``. The extension
had no migration before."""

from typing import List, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_scim_consumer_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_scim_consumer",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "scim_consumer"}
TABLES = ("scim_provisioning_logs", "scim_resources", "scim_connections")


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


def _connection_column() -> sa.Column:
    return sa.Column(
        "scim_connection_id",
        sa.String(),
        sa.ForeignKey("scim_connections.id"),
        nullable=False,
        comment="The connection",
    )


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
    op.create_index(f"ix_{name}_deleted_at", name, ["deleted_at"])


def upgrade() -> None:
    _create(
        "scim_connections",
        "SCIM 2.0 identity-provider connections provisioning into this server",
        sa.Column(
            "role_id",
            sa.String(),
            sa.ForeignKey("roles.id"),
            nullable=True,
            comment="Members' role in provisioned teams",
        ),
        sa.Column("name", sa.String(), nullable=False, comment="The name"),
        sa.Column(
            "token_hash",
            sa.String(),
            nullable=True,
            comment="SHA-256 digest of the bearer token",
        ),
        sa.Column("auto_create_users", sa.Boolean(), nullable=False),
        sa.Column("link_existing_users", sa.Boolean(), nullable=False),
        sa.Column("delete_on_deprovision", sa.Boolean(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
    )
    _create(
        "scim_resources",
        "Users and teams each SCIM connection provisions",
        _connection_column(),
        sa.Column("resource_type", sa.String(), nullable=False),
        sa.Column("local_id", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("deprovisioned_at", sa.DateTime(), nullable=True),
    )
    _create(
        "scim_provisioning_logs",
        "Provisioning requests SCIM connections made",
        _connection_column(),
        sa.Column("resource_type", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("scim_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("error_detail", sa.String(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    for name in TABLES:
        op.drop_index(f"ix_{name}_deleted_at", table_name=name)
        op.drop_table(name)
