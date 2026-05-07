"""Tests for the H-4 ``SecurityHeadersMiddleware`` and M-1
``BodySizeLimitMiddleware`` defaults.

These tests run the middleware directly against a stub ASGI app so the
test does not depend on the rest of the framework bootstrap. The
production code wires both middlewares unconditionally in
``app.build_app``.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Tuple

import pytest

from serverframework.lib.InboundSecurity import (
    BodySizeLimitMiddleware,
    DEFAULT_MAX_BODY_BYTES,
    SecurityHeadersMiddleware,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Captured:
    """Capture every ASGI message produced by a middleware run."""

    def __init__(self) -> None:
        self.messages: List[Dict[str, Any]] = []

    async def send(self, message: Dict[str, Any]) -> None:
        self.messages.append(message)

    @property
    def status(self) -> int:
        for m in self.messages:
            if m.get("type") == "http.response.start":
                return m["status"]
        return -1

    @property
    def headers(self) -> Dict[str, str]:
        """Headers are returned with title-cased keys so case-insensitive
        lookup mirrors HTTP semantics (RFC 9110 §5.1)."""
        for m in self.messages:
            if m.get("type") == "http.response.start":
                return {
                    k.decode("latin-1").title(): v.decode("latin-1")
                    for k, v in m.get("headers", [])
                }
        return {}


async def _stub_app_200(scope, receive, send):
    """Minimal ASGI app — emits an empty 200 response with one body chunk."""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"server", b"stub"),  # so we can verify Server stripping
            ],
        }
    )
    await send({"type": "http.response.body", "body": b"{}"})


def _make_receive(chunks: List[bytes]):
    """Build a ``receive`` callable that yields each chunk in turn."""
    iterator = iter(chunks)

    async def receive():
        try:
            body = next(iterator)
            return {"type": "http.request", "body": body, "more_body": True}
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}

    return receive


# ---------------------------------------------------------------------------
# SecurityHeadersMiddleware
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_security_headers_default_set():
    captured = _Captured()
    mw = SecurityHeadersMiddleware(_stub_app_200)
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
    await mw(scope, lambda: None, captured.send)

    h = captured.headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert "Referrer-Policy" in h
    assert "Permissions-Policy" in h
    assert "Content-Security-Policy" in h
    # Server header should be stripped by default (defense-in-depth — the
    # version banner gives an attacker a free fingerprint).
    assert "Server" not in h


@pytest.mark.asyncio
async def test_security_headers_no_hsts_outside_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    captured = _Captured()
    mw = SecurityHeadersMiddleware(_stub_app_200)
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
    await mw(scope, lambda: None, captured.send)
    assert "Strict-Transport-Security" not in captured.headers


@pytest.mark.asyncio
async def test_security_headers_hsts_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    captured = _Captured()
    mw = SecurityHeadersMiddleware(_stub_app_200)
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
    await mw(scope, lambda: None, captured.send)
    assert "Strict-Transport-Security" in captured.headers


@pytest.mark.asyncio
async def test_security_headers_does_not_overwrite_existing(monkeypatch):
    """If an upstream middleware already set ``X-Frame-Options``, the
    security middleware MUST NOT overwrite it (operators may have
    deliberately scoped CSP / framing per-route)."""

    async def app_with_frame(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/plain"),
                    (b"x-frame-options", b"SAMEORIGIN"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    captured = _Captured()
    mw = SecurityHeadersMiddleware(app_with_frame)
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
    await mw(scope, lambda: None, captured.send)
    assert captured.headers["X-Frame-Options"] == "SAMEORIGIN"


# ---------------------------------------------------------------------------
# BodySizeLimitMiddleware
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_body_size_limit_rejects_oversize_content_length():
    captured = _Captured()
    mw = BodySizeLimitMiddleware(_stub_app_200, max_bytes=100)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/x",
        "headers": [(b"content-length", b"500")],
    }
    await mw(scope, _make_receive([b""]), captured.send)
    assert captured.status == 413
    body_chunks = [
        m["body"]
        for m in captured.messages
        if m.get("type") == "http.response.body"
    ]
    assert b"exceeds maximum" in body_chunks[0]


@pytest.mark.asyncio
async def test_body_size_limit_allows_small_content_length():
    captured = _Captured()
    mw = BodySizeLimitMiddleware(_stub_app_200, max_bytes=1024)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/x",
        "headers": [(b"content-length", b"50")],
    }
    await mw(scope, _make_receive([b"x" * 50]), captured.send)
    assert captured.status == 200


@pytest.mark.asyncio
async def test_body_size_limit_streams_running_total():
    """Without ``Content-Length`` (chunked), the middleware tallies bytes
    as they arrive and aborts once the running total crosses the cap.

    The test app must actually consume the body so the middleware's
    ``receive`` wrapper sees the chunks; the 413 short-circuit fires
    when the running total crosses the cap."""

    async def consuming_app(scope, receive, send):
        # Pull every chunk; the middleware's wrapper observes each one.
        while True:
            msg = await receive()
            if msg.get("type") == "http.disconnect":
                return
            if not msg.get("more_body"):
                break
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b"{}"})

    captured = _Captured()
    mw = BodySizeLimitMiddleware(consuming_app, max_bytes=10)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/x",
        "headers": [],  # no content-length → streamed body path
    }
    await mw(
        scope, _make_receive([b"abcdef", b"ghijkl"]), captured.send
    )
    assert captured.status == 413


@pytest.mark.asyncio
async def test_body_size_limit_disabled_via_zero():
    captured = _Captured()
    mw = BodySizeLimitMiddleware(_stub_app_200, max_bytes=0)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/x",
        "headers": [(b"content-length", b"99999999")],
    }
    await mw(scope, _make_receive([b""]), captured.send)
    assert captured.status == 200


def test_default_max_body_bytes_constant_present():
    assert DEFAULT_MAX_BODY_BYTES > 0
