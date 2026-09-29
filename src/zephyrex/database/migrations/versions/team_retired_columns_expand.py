# SPDX-License-Identifier: AGPL-3.0-or-later
"""team_retired_columns_expand

Revision ID: team_retired_columns_expand
Revises: d4e5f6a7b8c9
Create Date: 2026-09-29 00:00:00.000000

Expand phase (Item 80) for retiring ``teams.encryption_salt``,
``teams.token`` and ``teams.training_data``. Nothing ever read the salt
(per-team row encryption was never implemented), yet clients could set it
and every team response exposed it; token and training_data were unused.
From this release the application neither reads nor writes these columns.
All three are nullable, so inserts that omit them succeed on either side of
a rolling deploy: this migration makes no structural change.

The paired ``team_retired_columns_contract`` migration, which drops the
three columns, ships in the release after this one has fully rolled out.
"""

from typing import Sequence, Union

revision: str = "team_retired_columns_expand"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Expand: the application stops reading/writing the retired columns."""


def downgrade() -> None:
    """Nothing to undo: the expand made no structural change."""
