# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression tests for the social OAuth ``sso_handler`` client-id resolution.

Guards issue #229: the Google/Microsoft/Amazon providers used to resolve their
client id with a stray ``timeout=10`` keyword argument that ``env`` does not
accept. That raised ``TypeError`` while building the token-request payload —
before ``requests.post`` was ever invoked. The surrounding ``except Exception``
swallowed it and returned ``None``, silently killing SSO sign-in (fail-closed).
The 10-second timeout was only ever meant for the HTTP request, not the env
lookup.

Each test drives the real ``env`` (no mock, so the signature mismatch would still
bite) and proves the token request is actually issued with the resolved client
id, rather than the whole handler collapsing to ``None``.
"""

from unittest.mock import patch

import pytest

from zephyrex.extensions.auth_oauth2_client.Amazon import AmazonOAuthProvider
from zephyrex.extensions.auth_oauth2_client.Google import GoogleOAuthProvider
from zephyrex.extensions.auth_oauth2_client.Microsoft import MicrosoftOAuthProvider

# (module_path_for_patching, provider_class, client_id_env_var)
_PROVIDER_CASES = [
    (
        "zephyrex.extensions.auth_oauth2_client.Google",
        GoogleOAuthProvider,
        "GOOGLE_CLIENT_ID",
    ),
    (
        "zephyrex.extensions.auth_oauth2_client.Microsoft",
        MicrosoftOAuthProvider,
        "MICROSOFT_CLIENT_ID",
    ),
    (
        "zephyrex.extensions.auth_oauth2_client.Amazon",
        AmazonOAuthProvider,
        "AWS_CLIENT_ID",
    ),
]


@pytest.mark.parametrize(
    "module_path, provider_cls, client_id_var",
    _PROVIDER_CASES,
    ids=[case[2] for case in _PROVIDER_CASES],
)
def test_sso_handler_resolves_client_id_from_env(
    module_path, provider_cls, client_id_var, monkeypatch
):
    """``sso_handler`` must resolve the client id via ``env`` and issue the
    token request with it — never raise ``TypeError`` and fall through to ``None``."""
    sentinel = f"sentinel-{client_id_var.lower()}"
    monkeypatch.setenv(client_id_var, sentinel)
    monkeypatch.setenv("MAGIC_LINK_URL", "https://example.com/magic")

    # Replace the provider module's ``requests`` with a mock. A non-200 status
    # keeps ``sso_handler`` from constructing an instance (which would make a
    # further HTTP call), while still recording the outbound token request.
    with patch(f"{module_path}.requests") as mock_requests:
        mock_requests.post.return_value.status_code = 500

        result = provider_cls.sso_handler(
            "dummy-code", redirect_uri="https://example.com/callback"
        )

    # Pre-fix, the stray timeout kwarg raised while building the payload, so
    # ``requests.post`` was never reached. Its being called is the proof the
    # regression is gone.
    mock_requests.post.assert_called_once()

    call = mock_requests.post.call_args
    payload = call.kwargs.get("params") or call.kwargs.get("data")
    assert payload is not None, "token request sent without a form/query payload"
    assert payload["client_id"] == sentinel

    # Non-200 upstream response still yields the documented fail-closed None,
    # but only *after* the env lookup succeeded and the request was issued.
    assert result is None
