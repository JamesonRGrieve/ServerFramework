# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for proxy_auth_consumer: owns ``user_proxy_auth_links``,
the proxy-asserted identities linked to local users."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_proxy_auth_consumer_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_proxy_auth_consumer",)
depends_on: Union[str, Sequence[str], None] = None

LINKS = "user_proxy_auth_links"
INDEXES = ("identity", "user_id", "deleted_at")


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(LINKS):
        return
    op.create_table(
        LINKS,
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id"),
            nullable=False,
            comment="The ID of the related user",
        ),
        sa.Column(
            "identity",
            sa.String(),
            nullable=False,
            comment="The identity the proxy asserts, exactly",
        ),
        sa.Column(
            "last_login_at",
            sa.DateTime(),
            nullable=True,
            comment="Last successful proxy sign-in",
        ),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        comment="Links a local user to a proxy-asserted identity",
        info={"extension": "proxy_auth_consumer"},
    )
    for column in INDEXES:
        op.create_index(f"ix_{LINKS}_{column}", LINKS, [column])


def downgrade() -> None:
    for column in INDEXES:
        op.drop_index(f"ix_{LINKS}_{column}", table_name=LINKS)
    op.drop_table(LINKS)
