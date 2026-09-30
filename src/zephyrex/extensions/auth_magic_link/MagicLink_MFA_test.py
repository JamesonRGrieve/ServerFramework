# SPDX-License-Identifier: AGPL-3.0-or-later
"""A magic link proves the email only: for a user with a verified second
factor it yields the MFA challenge, not a session, exactly as a password
login does."""

import os
import time
import uuid
from typing import Any, Iterator, List

import pytest
from fastapi.testclient import TestClient

from conftest import CORE_COMPANION_EXTENSIONS
from zephyrex.extensions.auth_magic_link.BLL_Auth_MagicLink import (
    clear_send_listeners,
    register_send_listener,
)
from zephyrex.extensions.auth_mfa.BLL_Auth_MFA import (
    MultifactorMethodManager,
    MultifactorMethodType,
)
from zephyrex.lib.SessionCookies import SESSION_COOKIE
from zephyrex.testing.factories import create_user

pyotp = pytest.importorskip("pyotp")


@pytest.fixture(scope="module")
def server() -> TestClient:
    from zephyrex.app import instance
    from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

    prepare_test_registry()
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    app = instance(
        db_prefix=f"test.magiclink_mfa.{worker}",
        extensions=",".join(
            ["auth_magic_link", "auth_mfa", *CORE_COMPANION_EXTENSIONS]
        ),
    )
    # Secure session cookies only travel over https.
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def inbox() -> Iterator[List[str]]:
    tokens: List[str] = []
    clear_send_listeners()
    register_send_listener(
        lambda email, magic_link_url, raw_token: tokens.append(raw_token)
    )
    yield tokens
    clear_send_listeners()


def _link_login(server: Any, email: str, inbox: List[str]) -> Any:
    requested = server.post("/v1/auth/magic-link/request", json={"email": email})
    assert requested.status_code in (200, 202), requested.text
    return server.post(
        "/v1/auth/magic-link/verify", json={"token": inbox[-1], "email": email}
    )


def test_an_mfa_user_gets_the_challenge_and_no_session(server, inbox):
    user = create_user(server, email=f"link_mfa_{uuid.uuid4().hex[:8]}@example.com")
    manager = MultifactorMethodManager(
        requester_id=user.id, model_registry=server.app.state.model_registry
    )
    method = manager.create(method_type=MultifactorMethodType.TOTP)
    totp = pyotp.TOTP(manager.totp_provisioning_route(method.id)["secret"])
    assert manager.verify_mfa_code(method.id, totp.now())
    server.cookies.clear()

    verified = _link_login(server, user.email, inbox)
    assert verified.status_code == 200, verified.text
    body = verified.json()
    assert body["mfa_required"] is True
    assert body["token"] is None and body["session_key"] is None
    assert SESSION_COOKIE not in server.cookies

    completed = server.post(
        "/v1/user/authorize/mfa",
        json={
            "challenge_token": body["challenge_token"],
            "code": totp.at(time.time() + totp.interval),
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["user"]["id"] == user.id
    assert server.cookies[SESSION_COOKIE] == completed.json()["token"]


def test_without_the_email_extension_the_miss_is_logged(server, inbox):
    """This app does not load the email extension: the request still
    answers normally, and the undeliverable link is logged, not dropped
    silently."""
    import io

    from loguru import logger as loguru_logger

    user = create_user(server, email=f"nomail_{uuid.uuid4().hex[:8]}@example.com")
    # Signed out, as a browser asking for a link is.
    server.cookies.clear()
    buffer = io.StringIO()
    sink = loguru_logger.add(buffer, level="WARNING", format="{message}")
    try:
        requested = server.post(
            "/v1/auth/magic-link/request", json={"email": user.email}
        )
    finally:
        loguru_logger.remove(sink)
    assert requested.status_code in (200, 202), requested.text
    assert "cannot be delivered" in buffer.getvalue()


def test_a_user_without_mfa_is_signed_in_by_the_link(server, inbox):
    user = create_user(server, email=f"link_{uuid.uuid4().hex[:8]}@example.com")
    server.cookies.clear()

    verified = _link_login(server, user.email, inbox)
    assert verified.status_code == 200, verified.text
    body = verified.json()
    assert body["mfa_required"] is False and body["token"]
    assert server.cookies[SESSION_COOKIE] == body["token"]
