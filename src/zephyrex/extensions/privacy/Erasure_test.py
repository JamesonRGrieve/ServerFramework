# SPDX-License-Identifier: AGPL-3.0-or-later
"""An account deleting itself erases what extensions hold about it first:
every registered eraser runs, a failed one keeps the account (so the user
can try again), and deleting stops before anything is erased when the
request's If-Match is stale."""

from typing import Any, Dict, List

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.auth_session.BLL_Session import SessionModel
from zephyrex.extensions.privacy import Erasure
from zephyrex.extensions.privacy.EXT_Privacy import EXT_Privacy
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth.user import UserCredentialModel
from zephyrex.testing.factories import create_user, current_if_match


class TestErasureOnAccountDeletion(ExtensionServerMixin):
    extension_class = EXT_Privacy

    @staticmethod
    def _headers(user: Any) -> Dict[str, str]:
        return {"Authorization": f"Bearer {user.jwt}"}

    @pytest.fixture
    def erased(self) -> Any:
        """A test eraser recording whom it erased, removed afterwards."""
        calls: List[str] = []
        state = {"fail": False}

        def eraser(user_id: str, model_registry: Any) -> None:
            assert model_registry is not None
            if state["fail"]:
                raise RuntimeError("storage unavailable")
            calls.append(user_id)

        # Named for this extension, which the test server loads.
        saved = Erasure._ERASERS.get("privacy")
        Erasure.register_eraser("privacy", eraser)
        yield calls, state
        Erasure._ERASERS.pop("privacy", None)
        if saved is not None:
            Erasure._ERASERS["privacy"] = saved

    def test_a_failed_eraser_keeps_the_account(self, server, erased):
        calls, state = erased
        user = create_user(server)
        state["fail"] = True
        headers = {
            **self._headers(user),
            **current_if_match(server, "/v1/user", self._headers(user)),
        }
        response = server.delete("/v1/user", headers=headers)
        assert response.status_code == 500, response.text
        assert "kept" in response.json()["detail"]
        assert server.get("/v1/user", headers=self._headers(user)).status_code == 200
        assert calls == []

    def test_a_stale_request_erases_nothing(self, server, erased):
        calls, _ = erased
        user = create_user(server)
        stale = {**self._headers(user), "If-Match": '"2000-01-01T00:00:00"'}
        response = server.delete("/v1/user", headers=stale)
        assert response.status_code == 412, response.text
        assert calls == []

    def test_deleting_an_account_erases_it_everywhere_first(self, server, erased):
        calls, _ = erased
        user = create_user(server)
        headers = {
            **self._headers(user),
            **current_if_match(server, "/v1/user", self._headers(user)),
        }
        response = server.delete("/v1/user", headers=headers)
        assert response.status_code in (200, 204), response.text
        assert calls == [user.id]

    def test_deleting_an_account_erases_its_sign_in_records(self, server, erased):
        """Its sessions are revoked, so a token stops working at once, and
        deleted with its credentials: nothing of them stays live to keep the
        account's row from being purged."""
        user = create_user(server)
        headers = {
            **self._headers(user),
            **current_if_match(server, "/v1/user", self._headers(user)),
        }
        assert server.delete("/v1/user", headers=headers).status_code in (200, 204)

        registry = server.app.state.model_registry
        base = registry.DB.manager.Base
        for model in (SessionModel, UserCredentialModel):
            db = model.DB(base)
            # ROOT sees deleted rows too: count only the live ones.
            live = db.list(
                requester_id=env("ROOT_ID"),
                model_registry=registry,
                filters=[db.user_id == user.id, db.deleted_at.is_(None)],
            )
            assert not live, model.__name__
        SessionDB = SessionModel.DB(base)
        sessions = SessionDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            filters=[SessionDB.user_id == user.id],
        )
        assert sessions and all(row["revoked"] for row in sessions)

    def test_an_extension_the_app_did_not_load_erases_nothing(self, server, erased):
        calls, _ = erased
        ran: List[str] = []
        Erasure.register_eraser(
            "not_loaded_here", lambda user_id, model_registry: ran.append(user_id)
        )
        try:
            user = create_user(server)
            headers = {
                **self._headers(user),
                **current_if_match(server, "/v1/user", self._headers(user)),
            }
            assert server.delete("/v1/user", headers=headers).status_code in (200, 204)
            assert ran == [] and calls == [user.id]
        finally:
            Erasure._ERASERS.pop("not_loaded_here", None)
