# SPDX-License-Identifier: AGPL-3.0-or-later
"""Meta's Graph API helper: reading an error body's code, and a real call
with a refused token typed as an auth failure (xfails offline)."""

from typing import ClassVar

import httpx
import pytest

from zephyrex.extensions.ExternalErrors import AuthExternalError
from zephyrex.lib.MetaGraph import FACEBOOK_GRAPH, graph_error_code, meta_graph
from zephyrex.lib.ProviderHTTPClient import ClientPolicy, ProviderHTTPClient


class GraphTestProvider:
    name: ClassVar[str] = "graph_test"

    @classmethod
    def http(cls) -> ProviderHTTPClient:
        return ProviderHTTPClient(
            policy=ClientPolicy(timeout=15), provider_name=cls.name
        )


def _online() -> bool:
    try:
        httpx.head("https://graph.facebook.com", timeout=5)
        return True
    except httpx.HTTPError:
        return False


@pytest.mark.parametrize(
    "payload, code",
    [
        ('{"error": {"code": 190, "type": "OAuthException"}}', 190),
        ('{"error": {"code": 100}}', 100),
        ('{"error": {}}', None),
        ("not json", None),
        (None, None),
    ],
)
def test_error_codes(payload, code):
    assert graph_error_code(payload) == code


def test_the_version_is_current():
    """v21.0 expires 2027-01-21; the helper must not pin an old version."""
    assert FACEBOOK_GRAPH.endswith("/v26.0")


@pytest.mark.xfail(not _online(), reason="graph.facebook.com is unreachable")
async def test_a_refused_token_is_an_auth_failure():
    with pytest.raises(AuthExternalError):
        await meta_graph(GraphTestProvider, "GET", "me", "not-a-real-token")
