# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for ai_tuning: owns ``tuning_jobs``, the local record
of each fine-tuning job a provider runs.

Before this, ai_tuning had no tables; its jobs lived in process memory.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_ai_tuning_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_ai_tuning",)
depends_on: Union[str, Sequence[str], None] = None

TABLE = "tuning_jobs"


def _text(name: str, comment: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.String(), nullable=nullable, comment=comment)


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id"),
            nullable=False,
            comment="The ID of the related user",
        ),
        _text("provider", "The provider (openai_fine_tuning, …)", nullable=False),
        _text("provider_instance_id", "The account it runs on", nullable=False),
        _text("provider_job_id", "The provider's job id", nullable=False),
        _text("base_model", "The model tuned", nullable=False),
        _text("training_file_id", "The training file"),
        _text("validation_file_id", "The validation file"),
        _text("suffix", "Added to the tuned model's name"),
        _text("status", "The provider's status for the job", nullable=False),
        _text("fine_tuned_model", "The tuned model's name, once the job succeeds"),
        _text("error", "Why the job failed"),
        sa.Column(
            "trained_tokens",
            sa.Integer(),
            nullable=True,
            comment="Billable tokens trained",
        ),
        sa.Column(
            "hyperparameters",
            sa.JSON(),
            nullable=True,
            comment="The hyperparameters the job used",
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(),
            nullable=True,
            comment="When the job finished",
        ),
        sa.Column(
            "refreshed_at",
            sa.DateTime(),
            nullable=False,
            comment="When the provider was last read",
        ),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        comment="Fine-tuning jobs (a local mirror of each provider's job)",
        info={"extension": "ai_tuning"},
    )
    op.create_index(f"ix_{TABLE}_deleted_at", TABLE, ["deleted_at"])


def downgrade() -> None:
    op.drop_index(f"ix_{TABLE}_deleted_at", table_name=TABLE)
    op.drop_table(TABLE)
