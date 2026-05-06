"""Initial migration — owns the `user_recovery_questions` table.

Extension carve-out from core (Scope #1). The table previously lived in
core migrations; this migration takes ownership without rebuilding it
(idempotent CREATE IF NOT EXISTS).
"""

from alembic import op
import sqlalchemy as sa


revision = "001_auth_recovery_questions_initial"
down_revision = None
branch_labels = ("auth_recovery_questions",)
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_recovery_questions (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            question VARCHAR(512) NOT NULL,
            answer VARCHAR(255) NOT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP,
            deleted_at TIMESTAMP,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_by_user_id VARCHAR(36)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_recovery_questions_user_id "
        "ON user_recovery_questions(user_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_recovery_questions")
