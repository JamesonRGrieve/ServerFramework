# SPDX-License-Identifier: AGPL-3.0-or-later
"""ProviderHTTPClient.fetch against a local server: the body cap, redirects
followed and each one checked by the SSRF guard (a public page cannot
bounce the server into a private address), redirect loops, and typed
errors."""

import pytest

from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.ProviderHTTPClient import ClientPolicy, ProviderHTTPClient

BIG_BYTES = 2 * 1024 * 1024
CAP = 1000
ROUTES = {
    "/small": (200, {"Content-Type": "text/plain"}, b"hello"),
    "/big": (200, {"Content-Type": "text/plain"}, b"x" * BIG_BYTES),
    "/hop": (302, {"Location": "/small"}, b""),
    "/to-metadata": (
        302,
        {"Location": "http://169.254.169.254/latest/meta-data/"},
        b"",
    ),
    "/loop": (302, {"Location": "/loop"}, b""),
    "/missing": (404, {"Content-Type": "text/plain"}, b"no such page"),
}


@pytest.fixture
def base(local_http_server) -> str:
    return str(local_http_server(ROUTES).base_url)


@pytest.fixture
def client() -> ProviderHTTPClient:
    return ProviderHTTPClient(policy=ClientPolicy(timeout=10), provider_name="fetch")


async def test_a_body(client, base):
    fetched = await client.fetch(f"{base}/small", max_bytes=CAP)
    assert fetched.body == b"hello" and not fetched.truncated
    assert fetched.content_type == "text/plain" and fetched.status == 200


async def test_the_cap_stops_reading(client, base):
    fetched = await client.fetch(f"{base}/big", max_bytes=CAP)
    assert len(fetched.body) == CAP and fetched.truncated


async def test_a_redirect_is_followed(client, base):
    fetched = await client.fetch(f"{base}/hop", max_bytes=CAP)
    assert fetched.url == f"{base}/small" and fetched.body == b"hello"


async def test_a_redirect_into_a_private_address_is_refused(client, base):
    with pytest.raises(InvalidInputExternalError, match="SSRF"):
        await client.fetch(f"{base}/to-metadata", max_bytes=CAP)


async def test_a_redirect_loop_ends(client, base):
    with pytest.raises(InvalidInputExternalError, match="redirected more than"):
        await client.fetch(f"{base}/loop", max_bytes=CAP, max_redirects=3)


async def test_an_error_status_is_typed(client, base):
    with pytest.raises(InvalidInputExternalError) as raised:
        await client.fetch(f"{base}/missing", max_bytes=CAP)
    assert raised.value.upstream_status == 404


async def test_a_private_address_is_refused_without_an_allowance(
    client, local_http_server
):
    server = local_http_server(ROUTES, allow=False)
    with pytest.raises(InvalidInputExternalError, match="SSRF"):
        await client.fetch(f"{server.base_url}/small", max_bytes=CAP)
