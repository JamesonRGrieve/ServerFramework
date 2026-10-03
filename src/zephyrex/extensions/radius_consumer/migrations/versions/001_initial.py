# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for radius_consumer: owns ``radius_servers``,
``radius_identities`` and ``radius_challenges``.

The extension had no migration before. Tables its replaced scaffold models
may have left through the runtime create_all fallback are not touched; they
are the operator's to drop.
"""

from typing import List, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_radius_consumer_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_radius_consumer",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "radius_consumer"}
TABLES = ("radius_challenges", "radius_identities", "radius_servers")


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


def _text(name: str, comment: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.String(), nullable=nullable, comment=comment)


def _server_reference() -> sa.Column:
    return sa.Column(
        "radius_server_id",
        sa.String(),
        sa.ForeignKey("radius_servers.id"),
        nullable=False,
        comment="The ID of the related radius_server",
    )


def _create(name: str, comment: str, *columns: sa.Column) -> bool:
    """Create ``name`` unless it exists; whether it was created."""
    if sa.inspect(op.get_bind()).has_table(name):
        return False
    op.create_table(
        name,
        *columns,
        *_record_columns(),
        sa.PrimaryKeyConstraint("id"),
        comment=comment,
        info=TABLE_INFO,
    )
    op.create_index(f"ix_{name}_deleted_at", name, ["deleted_at"])
    return True


def upgrade() -> None:
    _create(
        "radius_servers",
        "RADIUS services users sign in through",
        _text("name", "Shown on the sign-in page", nullable=False),
        sa.Column(
            "hosts",
            sa.JSON(),
            nullable=False,
            comment="host, host:port or [v6]:port, in fail-over order; "
            "they share this service's users and secret",
        ),
        _text("transport", "radsec (RADIUS over TLS, RFC 6614) or udp", nullable=False),
        _text(
            "shared_secret",
            "Shared secret (write-only); over RadSec it defaults to 'radsec'",
        ),
        sa.Column(
            "timeout_seconds",
            sa.Integer(),
            nullable=False,
            comment="Seconds to wait for each reply",
        ),
        sa.Column(
            "retries",
            sa.Integer(),
            nullable=False,
            comment="UDP retransmissions to a host before failing over",
        ),
        _text("nas_identifier", "NAS-Identifier sent with each request"),
        _text(
            "tls_ca_pem", "RadSec: CA certificate(s) the servers' certificates chain to"
        ),
        _text("tls_client_cert_pem", "RadSec: this server's client certificate"),
        _text("tls_client_key_pem", "RadSec: its private key (write-only)"),
        _text("tls_server_name", "RadSec: the name the servers' certificates carry"),
        sa.Column(
            "is_enabled", sa.Boolean(), nullable=False, comment="Offered for sign-in"
        ),
        sa.Column(
            "jit_create_users",
            sa.Boolean(),
            nullable=False,
            comment="A first sign-in creates the account (when registration is open)",
        ),
    )
    if _create(
        "radius_identities",
        "Accounts RADIUS usernames sign in to",
        _server_reference(),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id"),
            nullable=False,
            comment="The ID of the related user",
        ),
        _text("username", "User-Name the RADIUS server knows", nullable=False),
        sa.Column(
            "last_login_at",
            sa.DateTime(),
            nullable=True,
            comment="Last sign-in through this identity",
        ),
    ):
        # One account per username on a server, among identities not
        # (soft-)deleted, so a deleted identity's username can be relinked.
        live = sa.text("deleted_at IS NULL")
        op.create_index(
            "ux_radius_identities_server_username",
            "radius_identities",
            ["radius_server_id", "username"],
            unique=True,
            sqlite_where=live,
            postgresql_where=live,
        )
    if _create(
        "radius_challenges",
        "Access-Challenges awaiting an answer",
        _server_reference(),
        _text("token_hash", "SHA-256 of the challenge token", nullable=False),
        sa.Column(
            "host_index",
            sa.Integer(),
            nullable=False,
            comment="Which host asked (its index)",
        ),
        _text("username", "Who is signing in", nullable=False),
        _text("state_hex", "The RADIUS State to send back", nullable=False),
        sa.Column(
            "expires_at",
            sa.DateTime(),
            nullable=False,
            comment="When the answer is too late",
        ),
    ):
        op.create_index(
            "ix_radius_challenges_token_hash", "radius_challenges", ["token_hash"]
        )


def downgrade() -> None:
    op.drop_index("ix_radius_challenges_token_hash", table_name="radius_challenges")
    op.drop_index(
        "ux_radius_identities_server_username", table_name="radius_identities"
    )
    for name in TABLES:
        op.drop_index(f"ix_{name}_deleted_at", table_name=name)
        op.drop_table(name)
