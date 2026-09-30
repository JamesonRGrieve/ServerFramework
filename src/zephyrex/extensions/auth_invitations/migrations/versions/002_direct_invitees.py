# SPDX-License-Identifier: AGPL-3.0-or-later
"""Give every direct invitation an invitee row.

A direct invitation (``invitations.user_id`` set) used to have no invitee
row, so its recipient had nothing to answer it with: PATCH
/v1/invitation/{id} takes an invitee id or a code, and a direct invitation
may have no code. New direct invitations get their row when created; this
backfills one for each unrevoked direct invitation that lacks it.

Additive data only: the downgrade leaves the rows, which the previous code
reads as ordinary invitees.
"""

import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_auth_invitations_direct_invitees"
down_revision: Union[str, None] = "001_auth_invitations_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_invitations = sa.table(
    "invitations",
    sa.column("id", sa.String),
    sa.column("user_id", sa.String),
    sa.column("created_by_user_id", sa.String),
    sa.column("deleted_at", sa.DateTime),
)
_invitees = sa.table(
    "invitees",
    sa.column("id", sa.String),
    sa.column("invitation_id", sa.String),
    sa.column("user_id", sa.String),
    sa.column("email", sa.String),
    sa.column("created_at", sa.DateTime),
    sa.column("created_by_user_id", sa.String),
)
_users = sa.table(
    "users",
    sa.column("id", sa.String),
    sa.column("email", sa.String),
)


def upgrade() -> None:
    bind = op.get_bind()
    has_row = (
        sa.select(_invitees.c.id)
        .where(
            _invitees.c.invitation_id == _invitations.c.id,
            sa.or_(
                _invitees.c.user_id == _invitations.c.user_id,
                sa.func.lower(_invitees.c.email) == sa.func.lower(_users.c.email),
            ),
        )
        .exists()
    )
    missing = bind.execute(
        sa.select(
            _invitations.c.id,
            _invitations.c.user_id,
            _invitations.c.created_by_user_id,
            _users.c.email,
        )
        .join(_users, _users.c.id == _invitations.c.user_id)
        .where(_invitations.c.deleted_at.is_(None), ~has_row)
    ).all()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if missing:
        op.bulk_insert(
            _invitees,
            [
                {
                    "id": str(uuid.uuid4()),
                    "invitation_id": invitation_id,
                    "user_id": user_id,
                    "email": email.lower().strip(),
                    "created_at": now,
                    "created_by_user_id": created_by,
                }
                for invitation_id, user_id, created_by, email in missing
            ],
        )


def downgrade() -> None:
    pass
