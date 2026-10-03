# SPDX-License-Identifier: AGPL-3.0-or-later
"""Authentication closes every database session it opens, and decodes JWTs
with PyJWT itself.

Login opened a session it never used or closed, then "closed" a second one
it opened just for that; the wrong-password path queried old credentials on
a session nothing closed; the user access check opened one per call and
left it; and the login response committed a brand-new empty session. Each
held a pooled connection, so repeated failed logins could drain the pool.
"""

import base64
import importlib
from typing import Any, List

import jwt as pyjwt
import pytest

from zephyrex.testing.factories import TEST_PASSWORD, create_user


class _SessionLedger:
    """Wraps a DatabaseManager's get_session and records each close."""

    def __init__(self, manager: Any) -> None:
        self._get_session = manager.get_session
        self.opened = 0
        self.closed = 0

    def get_session(self) -> Any:
        session = self._get_session()
        self.opened += 1
        close = session.close

        def counted_close() -> None:
            self.closed += 1
            close()

        session.close = counted_close
        return session


@pytest.fixture
def ledger(server, monkeypatch) -> _SessionLedger:
    manager = server.app.state.model_registry.DB.manager
    tracked = _SessionLedger(manager)
    monkeypatch.setattr(manager, "get_session", tracked.get_session)
    return tracked


def _basic(email: str, password: str) -> dict:
    token = base64.b64encode(f"{email}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_logins_close_every_session_they_open(server, ledger):
    user = create_user(server)
    for password, status in ((TEST_PASSWORD, 200), ("wrong-password-1", 401)):
        response = server.post(
            "/v1/user/authorize", headers=_basic(user.email, password)
        )
        assert response.status_code == status, response.text
        server.get("/v1/user", headers={"Authorization": f"Bearer {user.jwt}"})
    assert ledger.opened > 0
    assert ledger.closed == ledger.opened


@pytest.mark.parametrize(
    "module",
    [
        "zephyrex.logic.BLL_Auth.user",
        "zephyrex.lib.SingleUseToken",
        "zephyrex.extensions.oauth_consumer.IdentityProvider",
    ],
)
def test_tokens_are_decoded_by_pyjwt_itself(module):
    """No wrapper sits between the framework and PyJWT's decode."""
    assert importlib.import_module(module).jwt is pyjwt


def test_the_dependencies_module_carries_no_jwt_wrapper():
    dependencies = importlib.import_module("zephyrex.lib.Dependencies")
    assert not hasattr(dependencies, "JWT")
    assert not hasattr(dependencies, "jwt")
