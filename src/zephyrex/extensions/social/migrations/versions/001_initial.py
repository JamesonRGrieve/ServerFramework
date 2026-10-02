# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for social: owns the ``social_publications`` table,
the record of posts the extension published.

Before this, social had no migration; a deployment may hold
``social_accounts``, ``social_posts``, ``social_media`` and
``social_metrics`` from the runtime create_all fallback. Those belonged to
the replaced fake providers (and ``social_accounts`` kept OAuth tokens in
plain text); this migration leaves them for the operator to drop.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_social_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_social",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "social"}


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("social_publications"):
        return
    op.create_table(
        "social_publications",
        sa.Column(
            "provider",
            sa.String(),
            nullable=False,
            comment="The platform's provider (x, threads, …)",
        ),
        sa.Column(
            "provider_instance_id",
            sa.String(),
            nullable=False,
            comment="The provider instance (account) that published",
        ),
        sa.Column(
            "platform_post_id",
            sa.String(),
            nullable=False,
            comment="The platform's id for the post",
        ),
        sa.Column(
            "url", sa.String(), nullable=True, comment="The post's address, when known"
        ),
        sa.Column("content", sa.String(), nullable=False, comment="The text published"),
        sa.Column("media_urls", sa.JSON(), nullable=True, comment="Media attached"),
        sa.Column(
            "published_at",
            sa.DateTime(),
            nullable=False,
            comment="When the platform accepted it",
        ),
        sa.Column(
            "team_id",
            sa.String(),
            sa.ForeignKey("teams.id"),
            nullable=True,
            comment="Optional foreign key to TeamModel",
        ),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id"),
            nullable=True,
            comment="Optional foreign key to UserModel",
        ),
        sa.Column(
            "created_by_user_id",
            sa.String(),
            sa.ForeignKey("users.id"),
            nullable=True,
            comment="Optional foreign key to UserModel",
        ),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        comment=(
            "Posts the social extension published: platform, account (provider "
            "instance), the platform's post id and address, content and media"
        ),
        info=TABLE_INFO,
    )


def downgrade() -> None:
    op.drop_table("social_publications")
