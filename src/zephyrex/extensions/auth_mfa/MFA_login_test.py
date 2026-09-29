# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two-step login for a user with a verified second factor: POST
/v1/user/authorize answers with a challenge and no session, and POST
/v1/user/authorize/mfa trades the challenge plus a current code for the
normal login response."""

import base64
import time
import uuid
from typing import Any, Dict, List, Tuple

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.auth_mfa.BLL_Auth_MFA import (
    MultifactorMethodManager,
    MultifactorMethodType,
)
from zephyrex.extensions.auth_mfa.EXT_Auth_MFA import EXT_Auth_MFA
from zephyrex.lib.SingleUseToken import issue_single_use_token
from zephyrex.logic.BLL_Auth.user import MFA_CHALLENGE_AUDIENCE
from zephyrex.testing.factories import create_user

pyotp = pytest.importorskip("pyotp")

AUTHORIZE = "/v1/user/authorize"
AUTHORIZE_MFA = "/v1/user/authorize/mfa"
PASSWORD = "testpassword"


def _password_login(server: Any, email: str) -> Any:
    credentials = base64.b64encode(f"{email}:{PASSWORD}".encode()).decode()
    return server.post(AUTHORIZE, headers={"Authorization": f"Basic {credentials}"})


def _enrolled_user(server: Any) -> Tuple[Any, Any, List[str]]:
    """A fresh user with an enrolled TOTP method and recovery codes. The
    enrolment code is spent, so callers log in with the next window's."""
    user = create_user(
        server, email=f"mfa_{uuid.uuid4().hex[:8]}@example.com", password=PASSWORD
    )
    manager = MultifactorMethodManager(
        requester_id=user.id, model_registry=server.app.state.model_registry
    )
    method = manager.create(method_type=MultifactorMethodType.TOTP)
    secret = manager.totp_provisioning_route(method.id)["secret"]
    totp = pyotp.TOTP(secret)
    assert manager.verify_mfa_code(method.id, totp.now())
    recovery = manager.generate_recovery_codes_route(method.id, {"count": 2})
    return user, totp, recovery


def _next_code(totp: Any) -> str:
    code: str = totp.at(time.time() + totp.interval)
    return code


def _challenge(server: Any, email: str) -> Dict[str, Any]:
    response = _password_login(server, email)
    assert response.status_code == 200, response.text
    body: Dict[str, Any] = response.json()
    return body


class TestMFALogin(ExtensionServerMixin):
    extension_class = EXT_Auth_MFA

    def test_password_alone_yields_a_challenge_not_a_session(self, server):
        user, _, _ = _enrolled_user(server)
        body = _challenge(server, user.email)

        assert body["mfa_required"] is True
        assert body["challenge_token"]
        assert [m["method_type"] for m in body["methods"]] == ["totp"]
        assert "token" not in body and "session_key" not in body
        # The challenge is not a bearer token.
        me = server.get(
            "/v1/user",
            headers={"Authorization": f"Bearer {body['challenge_token']}"},
        )
        assert me.status_code == 401, me.text

    def test_a_current_code_completes_the_login_once(self, server):
        user, totp, _ = _enrolled_user(server)
        challenge = _challenge(server, user.email)["challenge_token"]

        wrong = server.post(
            AUTHORIZE_MFA, json={"challenge_token": challenge, "code": "000000"}
        )
        assert wrong.status_code == 401, wrong.text

        done = server.post(
            AUTHORIZE_MFA,
            json={"challenge_token": challenge, "code": _next_code(totp)},
        )
        assert done.status_code == 200, done.text
        login = done.json()
        assert login["user"]["id"] == user.id
        me = server.get(
            "/v1/user", headers={"Authorization": f"Bearer {login['token']}"}
        )
        assert me.status_code == 200, me.text

        again = server.post(
            AUTHORIZE_MFA,
            json={"challenge_token": challenge, "code": totp.now()},
        )
        assert again.status_code == 401, again.text

    def test_a_recovery_code_completes_the_login(self, server):
        user, _, recovery = _enrolled_user(server)
        challenge = _challenge(server, user.email)["challenge_token"]

        done = server.post(
            AUTHORIZE_MFA,
            json={"challenge_token": challenge, "code": recovery[0].lower()},
        )
        assert done.status_code == 200, done.text
        assert done.json()["token"]

    def test_only_a_challenge_token_is_accepted(self, server):
        user, totp, _ = _enrolled_user(server)
        foreign = issue_single_use_token(
            audience="zephyrex:test:another_purpose",
            subject=user.id,
            ttl_seconds=60,
        )
        for challenge in ("", "not-a-token", foreign):
            response = server.post(
                AUTHORIZE_MFA,
                json={"challenge_token": challenge, "code": _next_code(totp)},
            )
            assert response.status_code == 401, response.text

    def test_an_expired_challenge_is_refused(self, server):
        user, totp, _ = _enrolled_user(server)
        expired = issue_single_use_token(
            audience=MFA_CHALLENGE_AUDIENCE, subject=user.id, ttl_seconds=-120
        )
        response = server.post(
            AUTHORIZE_MFA, json={"challenge_token": expired, "code": _next_code(totp)}
        )
        assert response.status_code == 401, response.text

    def test_an_unenrolled_method_does_not_gate_login(self, server):
        user = create_user(
            server, email=f"mfa_{uuid.uuid4().hex[:8]}@example.com", password=PASSWORD
        )
        MultifactorMethodManager(
            requester_id=user.id, model_registry=server.app.state.model_registry
        ).create(method_type=MultifactorMethodType.TOTP)

        body = _challenge(server, user.email)
        assert "mfa_required" not in body
        assert body["token"]
