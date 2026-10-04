# SPDX-License-Identifier: AGPL-3.0-or-later
"""Inbound mailbox cursors: ``email_inbound_mailboxes`` keeps how far the
IMAP poller has read each mailbox (the UIDVALIDITY and the highest UID
taken), so no message is delivered twice. Created only where it does not
exist yet.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_email_inbound_mailboxes"
down_revision: Union[str, None] = "b122a471c66e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "email_inbound_mailboxes"
INFO = {"source_module": "extensions.email.BLL_InboundEmail", "extension": "email"}


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column(
            "provider_instance_id",
            sa.String(),
            nullable=False,
            comment="The provider instance whose mailbox it is",
        ),
        sa.Column("mailbox", sa.String(), nullable=False, comment="The mailbox polled"),
        sa.Column(
            "uid_validity",
            sa.String(),
            nullable=True,
            comment="The UIDVALIDITY the cursor was read under",
        ),
        sa.Column(
            "last_uid",
            sa.String(),
            nullable=False,
            comment="The highest UID taken from it",
        ),
        sa.Column(
            "last_polled_at",
            sa.DateTime(),
            nullable=True,
            comment="When it was last polled",
        ),
        sa.Column(
            "last_error",
            sa.String(),
            nullable=True,
            comment="Why the last poll failed, if it did",
        ),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        comment="How far the inbound IMAP poller has read each mailbox",
        info=INFO,
    )
    op.create_index(f"ix_{TABLE}_deleted_at", TABLE, ["deleted_at"])
    op.create_index(
        f"ix_{TABLE}_instance_mailbox", TABLE, ["provider_instance_id", "mailbox"]
    )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE):
        op.drop_index(f"ix_{TABLE}_instance_mailbox", table_name=TABLE)
        op.drop_index(f"ix_{TABLE}_deleted_at", table_name=TABLE)
        op.drop_table(TABLE)
