# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for ecommerce: owns ``store_orders``,
``store_products`` and ``store_returns``, the local mirror of each
store's records.

Before this, ecommerce had no migration; a deployment may hold the legacy
``ecommerce_providers`` (store credentials and OAuth tokens in plain
text), ``ecommerce_orders``, ``ecommerce_products`` and
``ecommerce_returns`` tables from the runtime create_all fallback. They
belong to the replaced code and are left for the operator to drop.
"""

from typing import List, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_ecommerce_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_ecommerce",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "ecommerce"}
TABLES = ("store_returns", "store_products", "store_orders")


def _source_columns() -> List[sa.Column]:
    return [
        sa.Column(
            "provider",
            sa.String(),
            nullable=False,
            comment="The platform (shopify, etsy, …)",
        ),
        sa.Column(
            "provider_instance_id",
            sa.String(),
            nullable=False,
            comment="The store it came from",
        ),
        sa.Column(
            "synced_at",
            sa.DateTime(),
            nullable=False,
            comment="When it was last read from the store",
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
    ]


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


def _create(name: str, comment: str, *columns: sa.Column) -> None:
    if sa.inspect(op.get_bind()).has_table(name):
        return
    op.create_table(
        name,
        *_source_columns(),
        *columns,
        *_record_columns(),
        sa.PrimaryKeyConstraint("id"),
        comment=comment,
        info=TABLE_INFO,
    )
    op.create_index(f"ix_{name}_deleted_at", name, ["deleted_at"])


def _text(name: str, comment: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.String(), nullable=nullable, comment=comment)


def upgrade() -> None:
    _create(
        "store_orders",
        "Orders read from each store (a local mirror)",
        _text("platform_order_id", "The platform's order id", nullable=False),
        _text("order_number", "The number customers see"),
        _text("status", "pending, processing, shipped, delivered, …", nullable=False),
        _text("customer_name", "Customer"),
        _text("customer_email", "Customer's email"),
        _text("total", "Order total (decimal)"),
        _text("currency", "ISO currency code"),
        sa.Column(
            "order_date", sa.DateTime(), nullable=True, comment="When it was placed"
        ),
        sa.Column("shipping_address", sa.JSON(), nullable=True, comment="Ship to"),
        sa.Column("line_items", sa.JSON(), nullable=True, comment="Lines ordered"),
    )
    _create(
        "store_products",
        "Products read from each store (a local mirror)",
        _text("platform_product_id", "The platform's product id", nullable=False),
        _text("sku", "Stock keeping unit"),
        _text("title", "Title"),
        _text("description", "Description"),
        _text("price", "Price (decimal)"),
        _text("currency", "ISO currency code"),
        sa.Column(
            "quantity",
            sa.Integer(),
            nullable=True,
            comment="Stock, where the store says",
        ),
        sa.Column("images", sa.JSON(), nullable=True, comment="Image addresses"),
        sa.Column("is_active", sa.Boolean(), nullable=False, comment="Listed for sale"),
        _text("url", "The product's page"),
    )
    _create(
        "store_returns",
        "Returns read from each store (a local mirror)",
        _text("platform_return_id", "The platform's return id", nullable=False),
        _text("platform_order_id", "The order returned"),
        _text("status", "requested, approved, rejected, refunded, …", nullable=False),
        _text("reason", "Why"),
        _text("refund", "Refund (decimal)"),
        _text("currency", "ISO currency code"),
        sa.Column("line_items", sa.JSON(), nullable=True, comment="Lines returned"),
    )


def downgrade() -> None:
    for name in TABLES:
        op.drop_index(f"ix_{name}_deleted_at", table_name=name)
        op.drop_table(name)
