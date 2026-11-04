# SPDX-License-Identifier: AGPL-3.0-or-later
"""The generated REST routes' contracts, driven through a real FastAPI app
over an in-memory manager: what a client may set, what it gets back, and
which records a route may touch."""

from typing import Any, Dict

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from zephyrex.pydantic2.fastapi import (
    AuthType,
    HTTPMethod,
    register_custom_route,
    static_route,
)

# ---------------------------------------------------------------------------
# Static custom routes: server-derived arguments win over the body (#272)
# ---------------------------------------------------------------------------


class _EchoArgsManager:
    """A static route that reports the arguments it was called with."""

    @static_route("/echo", method=HTTPMethod.POST, auth_type=AuthType.NONE)
    def echo(cls, model_registry, ip_address, authorization, req_uri, note=None):
        return {
            "cls": cls.__name__,
            "registry": model_registry,
            "ip_address": ip_address,
            "authorization": authorization,
            "req_uri": req_uri,
            "note": note,
        }


def _echo_client():
    router = APIRouter()
    register_custom_route(
        router,
        _EchoArgsManager.echo._static_route_config[0],
        lambda: None,
        _EchoArgsManager,
    )
    app = FastAPI()
    app.state.model_registry = "the-app-registry"
    app.include_router(router)
    return TestClient(app)


def test_static_route_body_cannot_override_server_arguments():
    """A body naming the server-injected arguments is ignored for them: the
    method sees the real peer IP and headers, not the client's claims."""
    spoofed: Dict[str, Any] = {
        "ip_address": "10.0.0.1",
        "authorization": "Bearer forged",
        "req_uri": "https://evil.example",
        "model_registry": "forged-registry",
        "cls": "Forged",
        "note": "kept",
    }
    response = _echo_client().post(
        "/echo",
        json=spoofed,
        headers={"Authorization": "Bearer real", "Referer": "https://app.example"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "cls": "_EchoArgsManager",
        "registry": "the-app-registry",
        "ip_address": "testclient",
        "authorization": "Bearer real",
        "req_uri": "https://app.example",
        # An ordinary body field still reaches the method.
        "note": "kept",
    }
