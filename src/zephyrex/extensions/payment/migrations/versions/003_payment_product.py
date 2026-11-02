# SPDX-License-Identifier: AGPL-3.0-or-later
"""``payments.product_id``: the app product a payment buys.

Entitlements grants a product's plan once a payment for it has succeeded
(and while it is not fully refunded). Existing payments bought no product.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_payment_product"
down_revision: Union[str, None] = "002_payment_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "payment"}
PAYMENTS = "payments"
COLUMN = "product_id"


def _columns() -> set:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(PAYMENTS)}


def upgrade() -> None:
    if COLUMN not in _columns():
        op.add_column(
            PAYMENTS,
            sa.Column(
                COLUMN,
                sa.String(),
                nullable=True,
                comment="The app product it buys (entitlements), set once by the server",
            ),
        )


def downgrade() -> None:
    if COLUMN in _columns():
        with op.batch_alter_table(PAYMENTS) as batch:
            batch.drop_column(COLUMN)
