# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for health: owns the ``health_activities``,
``health_meals``, ``health_weights`` and ``health_sleeps`` tables, a
user's own health log.

Before this, health had no migration; a deployment may hold the legacy
``health_providers`` (with provider credentials in plain text) and
``*_records`` tables from the runtime create_all fallback. They belong
to the replaced code and are left for the operator to drop.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_health_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_health",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "health"}


def _owner() -> sa.Column:
    return sa.Column(
        "user_id",
        sa.String(),
        sa.ForeignKey("users.id"),
        nullable=True,
        comment="Optional foreign key to UserModel",
    )


def _record_columns() -> list:
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
        _owner(),
        *columns,
        *_record_columns(),
        sa.PrimaryKeyConstraint("id"),
        comment=comment,
        info=TABLE_INFO,
    )
    op.create_index(f"ix_{name}_deleted_at", name, ["deleted_at"])


def _number(name: str, comment: str, kind: type = sa.Float) -> sa.Column:
    return sa.Column(name, kind(), nullable=True, comment=comment)


def upgrade() -> None:
    _create(
        "health_activities",
        "A user's logged physical activities",
        sa.Column(
            "performed_at",
            sa.DateTime(),
            nullable=False,
            comment="When the activity started",
        ),
        sa.Column("kind", sa.String(), nullable=False, comment="What was done"),
        sa.Column(
            "duration_minutes",
            sa.Integer(),
            nullable=False,
            comment="How long it lasted",
        ),
        _number("calories_burned", "kcal"),
        _number("steps", "Steps taken", sa.Integer),
        _number("distance_km", "Distance covered"),
        _number("heart_rate_avg", "Average beats per minute", sa.Integer),
        _number("heart_rate_max", "Peak beats per minute", sa.Integer),
        sa.Column("notes", sa.String(), nullable=True, comment="Free-form notes"),
    )
    _create(
        "health_meals",
        "A user's logged meals and their nutrients",
        sa.Column(
            "eaten_at", sa.DateTime(), nullable=False, comment="When it was eaten"
        ),
        sa.Column("kind", sa.String(), nullable=False, comment="Which meal"),
        sa.Column("food", sa.String(), nullable=False, comment="What was eaten"),
        _number("serving_size", "How much"),
        sa.Column("serving_unit", sa.String(), nullable=True, comment="g, ml, cup, …"),
        sa.Column("calories", sa.Float(), nullable=False, comment="kcal"),
        _number("protein_g", "Protein, grams"),
        _number("carbs_g", "Carbohydrate, grams"),
        _number("fat_g", "Fat, grams"),
        _number("fiber_g", "Fibre, grams"),
        _number("sugar_g", "Sugar, grams"),
        _number("sodium_mg", "Sodium, milligrams"),
    )
    _create(
        "health_weights",
        "A user's weigh-ins",
        sa.Column(
            "measured_at", sa.DateTime(), nullable=False, comment="When it was measured"
        ),
        sa.Column(
            "weight_kg", sa.Float(), nullable=False, comment="Body weight, kilograms"
        ),
        _number("body_fat_percent", "Body fat, percent"),
        sa.Column("notes", sa.String(), nullable=True, comment="Free-form notes"),
    )
    _create(
        "health_sleeps",
        "A user's nights of sleep",
        sa.Column(
            "bedtime",
            sa.DateTime(),
            nullable=False,
            comment="When the night's sleep began",
        ),
        sa.Column("wake_time", sa.DateTime(), nullable=False, comment="When it ended"),
        sa.Column(
            "duration_minutes",
            sa.Integer(),
            nullable=False,
            comment="Bed to wake (kept by the server)",
        ),
        _number("deep_minutes", "Deep sleep", sa.Integer),
        _number("light_minutes", "Light sleep", sa.Integer),
        _number("rem_minutes", "REM sleep", sa.Integer),
        _number("awake_minutes", "Awake in bed", sa.Integer),
        _number("quality", "Quality score, 0-100", sa.Integer),
    )


def downgrade() -> None:
    for name in (
        "health_sleeps",
        "health_weights",
        "health_meals",
        "health_activities",
    ):
        op.drop_index(f"ix_{name}_deleted_at", table_name=name)
        op.drop_table(name)
