# SPDX-License-Identifier: AGPL-3.0-or-later
"""The top-level SDK facade against a real test server."""

from zephyrex.sdk.SDK import SDK
from zephyrex.testing.factories import create_user


def _sdk(server, token=None) -> SDK:
    return SDK(base_url="http://testserver", token=token, http_client=server)


def test_health_check_reports_a_reachable_api_healthy(server, admin_a):
    """health_check passed a keyword AbstractSDKHandler.get did not accept, so
    every call raised inside its try and reported the API unhealthy."""
    result = _sdk(server, token=admin_a.jwt).health_check()

    assert result["status"] == "healthy", result
    assert result["api_accessible"] is True


def test_logout_revokes_the_server_session(server):
    """logout called a method the auth SDK does not have; the AttributeError
    was logged and swallowed, so the session stayed valid until it expired."""
    user = create_user(server)
    sdk = _sdk(server, token=user.jwt)

    sdk.logout()

    assert sdk.token is None
    assert sdk.auth.token is None
    after = server.get("/v1/user", headers={"Authorization": f"Bearer {user.jwt}"})
    assert after.status_code == 401, after.text
    assert after.json()["detail"] == "Session has been revoked"
