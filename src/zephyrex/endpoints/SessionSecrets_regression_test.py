# SPDX-License-Identifier: AGPL-3.0-or-later
"""A session is listed without its secrets: ``session_key`` (the JWT's jti)
and ``refresh_token_hash`` used to be serialised by GET /v1/session and on
the GraphQL session type."""

import uuid

SECRETS = ("session_key", "refresh_token_hash", "sessionKey", "refreshTokenHash")


def _signed_in(server):
    """A fresh user's login token (creating the user logs them in)."""
    from conftest import create_user

    user = create_user(server, email=f"sessions_{uuid.uuid4().hex[:8]}@example.com")
    return user.jwt


def test_rest_session_rows_carry_no_secrets(server):
    token = _signed_in(server)
    listed = server.get("/v1/session", headers={"Authorization": f"Bearer {token}"})
    assert listed.status_code == 200, listed.text
    rows = listed.json()["sessions"]
    assert rows
    for row in rows:
        assert not set(SECRETS) & set(row), row
        assert {"id", "is_active", "revoked", "expires_at"} <= set(row)


def test_the_graphql_session_type_has_no_secret_fields(server):
    schema = str(server.app.state.model_registry.gql)
    start = schema.index("type SessionType {")
    session_type = schema[start : schema.index("}", start)]
    for secret in ("sessionKey", "refreshTokenHash"):
        assert secret not in session_type
