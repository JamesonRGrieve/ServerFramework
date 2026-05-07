"""Initial migration for auth_session — owns ``sessions``.

The ``sessions`` table is fully owned by this extension. Without
``auth_session`` enabled, JWTs issued by core ``BLL_Auth`` are stateless
(verified by signature/exp/nbf/aud/iss/jti only); enabling the extension
adds the row that makes a token revocable, the pending-state machine for
device-pairing flows, and the ``grant_type`` audit field.
"""

from alembic import op
import sqlalchemy as sa


revision = "001_auth_session_initial"
down_revision = None
branch_labels = ("auth_session",)
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column(
            "user_id",
            sa.String(),
            nullable=True,
            comment="Foreign key to User",
        ),
        sa.Column(
            "session_key",
            sa.String(),
            nullable=False,
            comment="Unique session identifier used in JWT jti claim",
        ),
        sa.Column(
            "jwt_issued_at",
            sa.DateTime(),
            nullable=False,
            comment="When the JWT was issued",
        ),
        sa.Column(
            "refresh_token_hash",
            sa.String(),
            nullable=True,
            comment="Hash of refresh token if refresh mechanism is enabled",
        ),
        sa.Column(
            "device_type",
            sa.String(),
            nullable=True,
            comment="Type of device (mobile, desktop, api, etc.)",
        ),
        sa.Column(
            "device_name",
            sa.String(),
            nullable=True,
            comment="Name of the device if provided",
        ),
        sa.Column(
            "browser",
            sa.String(),
            nullable=True,
            comment="Browser information from user agent",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            comment="Whether this session is currently active",
        ),
        sa.Column(
            "last_activity",
            sa.DateTime(),
            nullable=False,
            comment="Timestamp of last activity in this session",
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(),
            nullable=False,
            comment="When this session expires",
        ),
        sa.Column(
            "revoked",
            sa.Boolean(),
            nullable=False,
            comment="Whether this session has been explicitly revoked",
        ),
        sa.Column(
            "trust_score",
            sa.Integer(),
            nullable=False,
            comment="Trust level of this session (0-100)",
        ),
        sa.Column(
            "requires_verification",
            sa.Boolean(),
            nullable=False,
            comment="Whether additional verification is required",
        ),
        sa.Column(
            "grant_type",
            sa.String(),
            nullable=True,
            comment=(
                "Identifier of the grant kind that issued this session "
                "(e.g. password, magic_link, device_pairing). NULL when "
                "issued by an ad-hoc tooling path."
            ),
        ),
        sa.Column(
            "pending_state",
            sa.String(),
            nullable=True,
            comment=(
                "Pending approval state for cross-device passwordless "
                "flows: awaiting_approval, approved, or denied."
            ),
        ),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        comment="Active user authentication sessions and related metadata",
        info={"source_module": "serverframework.extensions.auth_session.BLL_Session"},
    )
    op.create_index(
        "ix_sessions_user_active",
        "sessions",
        ["user_id", "is_active"],
    )
    op.create_index(
        "ix_sessions_session_key",
        "sessions",
        ["session_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_sessions_session_key", table_name="sessions")
    op.drop_index("ix_sessions_user_active", table_name="sessions")
    op.drop_table("sessions")
