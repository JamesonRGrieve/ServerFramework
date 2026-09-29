# SPDX-License-Identifier: AGPL-3.0-or-later
"""The top-level SDK facade against a real test server."""

from zephyrex.sdk.SDK import SDK


def _sdk(server, token=None) -> SDK:
    return SDK(base_url="http://testserver", token=token, http_client=server)


def test_health_check_reports_a_reachable_api_healthy(server, admin_a):
    """health_check passed a keyword AbstractSDKHandler.get did not accept, so
    every call raised inside its try and reported the API unhealthy."""
    result = _sdk(server, token=admin_a.jwt).health_check()

    assert result["status"] == "healthy", result
    assert result["api_accessible"] is True
