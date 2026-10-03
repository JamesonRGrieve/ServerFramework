# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for saml_provider: owns ``saml_service_providers``
(the SP registrations), ``saml_subjects`` (pairwise NameIDs) and
``saml_pending_requests`` (requests waiting for their user, and the
request ids each SP has used).

Before this, saml_provider had no migration; a deployment may hold the
scaffold's ``s_a_m_l_service_providers`` and ``s_a_m_l_provider_configs``
tables from the runtime create_all fallback. Nothing reads them; they are
left for the operator to drop.
"""

from typing import List, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_saml_provider_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_saml_provider",)
depends_on: Union[str, Sequence[str], None] = None

TABLE_INFO = {"extension": "saml_provider"}
TABLES = ("saml_pending_requests", "saml_subjects", "saml_service_providers")


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


def _create(
    name: str, comment: str, *columns: sa.Column, indexed: Sequence[str] = ()
) -> None:
    if sa.inspect(op.get_bind()).has_table(name):
        return
    op.create_table(
        name,
        *columns,
        *_record_columns(),
        sa.PrimaryKeyConstraint("id"),
        comment=comment,
        info=TABLE_INFO,
    )
    for column in ("deleted_at", *indexed):
        op.create_index(f"ix_{name}_{column}", name, [column])


def _text(name: str, comment: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.String(), nullable=nullable, comment=comment)


def _flag(name: str, comment: str) -> sa.Column:
    return sa.Column(name, sa.Boolean(), nullable=False, comment=comment)


def _service_provider() -> sa.Column:
    return sa.Column(
        "saml_service_provider_id",
        sa.String(),
        sa.ForeignKey("saml_service_providers.id"),
        nullable=False,
        comment="The ID of the related saml service provider",
    )


def upgrade() -> None:
    _create(
        "saml_service_providers",
        "Service Providers registered with the SAML IdP",
        _text("name", "What the SP is called"),
        _text("entity_id", "The SP's SAML entity id"),
        sa.Column(
            "acs_urls",
            sa.JSON(),
            nullable=False,
            comment="Assertion Consumer Service URLs (HTTP-POST), matched exactly",
        ),
        _text("certificate", "The SP's X.509 certificate (PEM)", nullable=True),
        _flag("require_signed_requests", "Refuse an AuthnRequest that is not signed"),
        _flag("encrypt_assertions", "Encrypt assertions to the SP's certificate"),
        _text("name_id_format", "persistent (a pairwise id per user and SP) or email"),
        sa.Column(
            "released_attributes",
            sa.JSON(),
            nullable=False,
            comment="User fields released to the SP",
        ),
        _flag("allow_idp_initiated", "Allow sign-in started at this IdP"),
        _flag("enabled", "Whether the SP may sign users in"),
    )
    _create(
        "saml_subjects",
        "Pairwise SAML NameIDs, one per user and SP",
        _service_provider(),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id"),
            nullable=False,
            comment="The ID of the related user",
        ),
        _text("name_id", "The opaque identifier the SP sees"),
    )
    _create(
        "saml_pending_requests",
        "SAML requests waiting for their user to sign in",
        _service_provider(),
        _text("ticket", "The secret the browser carries back"),
        _text(
            "request_id",
            "The AuthnRequest's ID (none when IdP-initiated)",
            nullable=True,
        ),
        _text("acs_url", "Where the Response goes"),
        _text("relay_state", "Returned to the SP as sent", nullable=True),
        _flag("force_authn", "The SP asked for a fresh sign-in"),
        _flag("is_passive", "The SP asked for no interaction"),
        sa.Column(
            "expires_at",
            sa.DateTime(),
            nullable=False,
            comment="When the request lapses",
        ),
        _flag("login_redirected", "The browser has been sent to sign in once"),
        sa.Column(
            "consumed_at", sa.DateTime(), nullable=True, comment="When it was answered"
        ),
        indexed=("ticket",),
    )


def downgrade() -> None:
    op.drop_index("ix_saml_pending_requests_ticket", table_name="saml_pending_requests")
    for name in TABLES:
        op.drop_index(f"ix_{name}_deleted_at", table_name=name)
        op.drop_table(name)
