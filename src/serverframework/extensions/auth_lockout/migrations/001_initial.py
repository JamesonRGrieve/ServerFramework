"""Initial migration for auth_lockout — owns `failed_login_attempts`."""

from alembic import op


revision = "001_auth_lockout_initial"
down_revision = None
branch_labels = ("auth_lockout",)
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS failed_login_attempts (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36),
            ip_address VARCHAR(64),
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
        "CREATE INDEX IF NOT EXISTS ix_failed_login_attempts_user_created "
        "ON failed_login_attempts(user_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS failed_login_attempts")
