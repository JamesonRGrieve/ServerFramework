# SPDX-License-Identifier: AGPL-3.0-or-later
"""users_email_unique_live

Revision ID: users_email_unique_live
Revises: provider_instance_setting_secret

One live account per email, held by the database: a partial unique index
on ``users.email`` over the rows that are not soft-deleted
(``uq_users_email_live``; SQLite and PostgreSQL both take the predicate).
Until now only the application checked, and two writers racing, or any
writer that skipped the check, could make two live accounts with one email.

The stored email is the normalized one (NFKC, lowercased, trimmed: what
registration stores and login matches), so the column itself is what is
unique. Before the index is made:

- live accounts whose emails normalize alike are looked for; if there are
  any the upgrade stops, naming their ids, and changes nothing. Nothing is
  ever deleted: an operator resolves them (soft-delete or re-address all but
  one) and upgrades again;
- stored emails not yet in normalized form are rewritten to it. Such an
  account could never sign in by password (login matches the normalized
  form), and without the rewrite the index would let a second account take
  the normalized spelling.

A soft-deleted account keeps its email and does not block a new one.
"""

import unicodedata
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "users_email_unique_live"
down_revision: Union[str, None] = "provider_instance_setting_secret"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "uq_users_email_live"
LIVE_ROWS = "deleted_at IS NULL"

_users = sa.table(
    "users",
    sa.column("id", sa.String),
    sa.column("email", sa.String),
    sa.column("deleted_at", sa.DateTime),
)


def _normalized(email: str) -> str:
    """The stored form of an email, as of this revision (the same rule as
    ``UserManager._normalize_identifier``; frozen here, as a migration must
    keep meaning what it meant when it ran)."""
    return unicodedata.normalize("NFKC", email).lower().strip()


def live_duplicates(rows: Sequence[sa.Row]) -> Dict[str, List[str]]:
    """``{normalized email: [ids]}`` for every email that more than one live
    row holds once normalized."""
    by_email: Dict[str, List[str]] = defaultdict(list)
    for row in rows:
        if row.email is not None and row.deleted_at is None:
            by_email[_normalized(row.email)].append(str(row.id))
    return {email: ids for email, ids in by_email.items() if len(ids) > 1}


def _refusal(duplicates: Dict[str, List[str]]) -> str:
    groups = "; ".join(
        ", ".join(sorted(ids)) for ids in sorted(duplicates.values(), key=sorted)
    )
    return (
        f"Cannot create {INDEX_NAME}: {len(duplicates)} email(s) are held by "
        f"more than one live user (user ids, grouped by email: {groups}). "
        "Nothing was changed. Soft-delete or re-address all but one account "
        "of each group, then run the upgrade again."
    )


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(_users.c.id, _users.c.email, _users.c.deleted_at)
    ).all()
    duplicates = live_duplicates(rows)
    if duplicates:
        raise RuntimeError(_refusal(duplicates))
    for row in rows:
        stored: Optional[str] = row.email
        if stored is not None and stored != _normalized(stored):
            bind.execute(
                _users.update()
                .where(_users.c.id == row.id)
                .values(email=_normalized(stored))
            )
    op.create_index(
        INDEX_NAME,
        "users",
        ["email"],
        unique=True,
        sqlite_where=sa.text(LIVE_ROWS),
        postgresql_where=sa.text(LIVE_ROWS),
    )


def downgrade() -> None:
    """Drops the index; emails stay in their normalized form."""
    op.drop_index(INDEX_NAME, table_name="users")
