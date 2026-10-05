# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every password write meets the published policy.

Register never checked the password rule (only change-password did), so an
account could be created with the password "a"; and a password longer than
bcrypt's 72 bytes reached bcrypt, which raises, so the request failed with
a 500 instead of a refusal.
"""

import uuid

from zephyrex.testing.factories import TEST_PASSWORD, current_if_match

POLICY = "/v1/user/password-policy"
REGISTER = "/v1/user"
POLICY_FAILURE = "Password does not meet the policy"


def _email() -> str:
    return f"policy-{uuid.uuid4().hex[:10]}@example.com"


def _register(server, email: str, password: str):
    return server.post(REGISTER, json={"email": email, "password": password})


def test_the_policy_is_public(server):
    response = server.get(POLICY)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "min_length": 8,
        "max_bytes": 72,
        "require_letter": True,
        "require_digit": True,
    }


def test_register_refuses_a_weak_password_and_creates_nothing(server):
    email = _email()
    response = _register(server, email, "a")
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == {
        "message": POLICY_FAILURE,
        "failed": ["min_length", "require_digit"],
    }
    # Refused before the account row: the address is still free.
    assert _register(server, email, TEST_PASSWORD).status_code == 201


def test_a_password_past_bcrypts_limit_is_refused_not_crashed(server):
    response = _register(server, _email(), "a1" + "x" * 80)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["failed"] == ["max_bytes"]


def test_change_password_uses_the_same_refusal(server, admin_a):
    headers = {"Authorization": f"Bearer {admin_a.jwt}"}
    response = server.patch(
        "/v1/user",
        json={"current_password": TEST_PASSWORD, "new_password": "nodigits"},
        headers={**headers, **current_if_match(server, "/v1/user", headers)},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == {
        "message": POLICY_FAILURE,
        "failed": ["require_digit"],
    }
