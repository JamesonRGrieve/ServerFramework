"""Initial migration for metadata — owns the `metadata` table."""

from alembic import op


revision = "001_metadata_initial"
down_revision = None
branch_labels = ("metadata",)
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36),
            team_id VARCHAR(36),
            key VARCHAR(255) NOT NULL,
            value TEXT,
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
        "CREATE INDEX IF NOT EXISTS ix_metadata_user_key ON metadata(user_id, key)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_metadata_team_key ON metadata(team_id, key)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS metadata")
