"""Initial migration for acl_rbac — owns `permissions`."""

from alembic import op


revision = "001_acl_rbac_initial"
down_revision = None
branch_labels = ("acl_rbac",)
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS permissions (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36),
            team_id VARCHAR(36),
            role_id VARCHAR(36),
            resource_type VARCHAR(255) NOT NULL,
            resource_id VARCHAR(36) NOT NULL,
            can_view BOOLEAN DEFAULT 0,
            can_execute BOOLEAN DEFAULT 0,
            can_copy BOOLEAN DEFAULT 0,
            can_edit BOOLEAN DEFAULT 0,
            can_delete BOOLEAN DEFAULT 0,
            can_share BOOLEAN DEFAULT 0,
            expires_at TIMESTAMP,
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
        "CREATE INDEX IF NOT EXISTS ix_permissions_resource "
        "ON permissions(resource_type, resource_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_permissions_user "
        "ON permissions(user_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS permissions")
