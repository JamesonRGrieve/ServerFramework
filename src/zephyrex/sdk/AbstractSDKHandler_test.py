# SPDX-License-Identifier: AGPL-3.0-or-later
"""The SDK's own HTTP path: a handler built without a custom client sends
its requests through a real ``httpx.Client`` carrying its timeout and TLS
verification. Requests are answered by ``httpx.MockTransport`` at the
transport layer, so everything above the socket is the real code path."""

from typing import Any, Dict, List

import httpx
import pytest

from zephyrex.sdk.AbstractSDKHandler import AbstractSDKHandler, ResourceConfig

BASE_URL = "https://api.example.test"
TIMEOUT_SECONDS = 7


class _Handler(AbstractSDKHandler):
    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        return {}


def _route_through(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[List[Dict[str, Any]], List[httpx.Request]]:
    """Make every ``httpx.Client`` the handler builds a real client whose
    transport answers in-process; return the constructor options each
    client got and the requests that went out."""
    client_options: List[Dict[str, Any]] = []
    sent: List[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"ok": True})

    class _TransportClient(httpx.Client):
        def __init__(self, **kwargs: Any) -> None:
            client_options.append(kwargs)
            super().__init__(transport=httpx.MockTransport(answer), **kwargs)

    monkeypatch.setattr(httpx, "Client", _TransportClient)
    return client_options, sent


def test_a_request_without_a_custom_client_reaches_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``verify`` used to be passed to ``Client.request``, which has no such
    parameter, so every real SDK call raised before anything was sent."""
    _, sent = _route_through(monkeypatch)
    handler = _Handler(base_url=BASE_URL, token="jwt")

    result = handler.get("/v1/user", params={"limit": 5})

    assert result == {"ok": True}
    assert len(sent) == 1
    assert sent[0].method == "GET"
    assert str(sent[0].url) == f"{BASE_URL}/v1/user?limit=5"
    assert sent[0].headers["Authorization"] == "Bearer jwt"


def test_the_client_carries_the_handlers_timeout_and_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_options, sent = _route_through(monkeypatch)
    handler = _Handler(base_url=BASE_URL, timeout=TIMEOUT_SECONDS, verify_ssl=False)

    handler.post("/v1/user", data={"user": {"email": "a@example.test"}})

    assert client_options == [{"timeout": TIMEOUT_SECONDS, "verify": False}]
    assert sent[0].extensions["timeout"] == httpx.Timeout(TIMEOUT_SECONDS).as_dict()


def test_verification_is_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client_options, _ = _route_through(monkeypatch)

    _Handler(base_url=BASE_URL).get("/v1")

    assert client_options[0]["verify"] is True
