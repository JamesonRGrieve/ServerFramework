"""Initial migration for auth_invitations — owns `invitations`+`invitees`."""

from alembic import op


revision = "001_auth_invitations_initial"
down_revision = None
branch_labels = ("auth_invitations",)
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS invitations (
            id VARCHAR(36) PRIMARY KEY,
            code VARCHAR(255) UNIQUE,
            team_id VARCHAR(36),
            role_id VARCHAR(36),
            user_id VARCHAR(36),
            expires_at TIMESTAMP,
            max_uses INTEGER,
            used_count INTEGER DEFAULT 0,
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
        """
        CREATE TABLE IF NOT EXISTS invitees (
            id VARCHAR(36) PRIMARY KEY,
            invitation_id VARCHAR(36) NOT NULL,
            email VARCHAR(320),
            user_id VARCHAR(36),
            accepted_at TIMESTAMP,
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
        "CREATE INDEX IF NOT EXISTS ix_invitations_team ON invitations(team_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_invitees_invitation ON invitees(invitation_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS invitees")
    op.execute("DROP TABLE IF EXISTS invitations")
