# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTTP dispatch tests for ``@custom_route`` (``register_custom_routes``).

Every ``AbstractBLLManager`` method is hook-wrapped, so these tests drive
tagged methods over real HTTP against a real model registry and database:
typed bodies, path and query parameters, ``async`` methods, per-route
authentication, and hooks firing on the dispatched call. The real
``auth_api_keys`` and ``auth_notifications`` routes are then exercised
end-to-end through their extension servers.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Iterator, List, Optional

import pytest
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.auth_api_keys.EXT_Auth_APIKeys import EXT_Auth_APIKeys
from zephyrex.extensions.auth_notifications.EXT_Auth_Notifications import (
    EXT_Auth_Notifications,
)
from zephyrex.lib.CustomRoute import ExposeIn, custom_route, register_custom_routes
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    HookContext,
    HookRegistry,
)

PROBE = "/v1/custom-route-probe"
API_KEYS = "/v1/auth/api-keys"
NOTIFICATIONS = "/v1/notifications"
USER_NOTIFICATIONS = "/v1/user-notifications"


def _bearer(credential: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {credential}"}


class ProbeInput(BaseModel):
    text: str


class ProbeOutput(BaseModel):
    text: str
    requester_id: Optional[str] = None
    item_id: Optional[str] = None
    limit: Optional[int] = None


class CustomRouteProbeManager(AbstractBLLManager):
    """A hook-wrapped manager whose routes echo what dispatch handed them."""

    # Assigned by ``AbstractBLLManager.__init_subclass__``; declared for typing.
    _hook_registry: ClassVar[HookRegistry]

    @custom_route(
        method="POST",
        path="/echo",
        input_model=ProbeInput,
        output_model=ProbeOutput,
        authentication_type="jwt",
    )
    def echo(self, body: ProbeInput) -> ProbeOutput:
        return ProbeOutput(text=body.text, requester_id=self.requester_id)

    @custom_route(
        method="POST",
        path="/async-echo",
        input_model=ProbeInput,
        output_model=ProbeOutput,
        authentication_type="jwt",
    )
    async def async_echo(self, body: ProbeInput) -> ProbeOutput:
        return ProbeOutput(text=body.text, requester_id=self.requester_id)

    @custom_route(
        method="POST",
        path="/splat",
        input_model=ProbeInput,
        output_model=ProbeOutput,
        authentication_type="session",
    )
    def splat(self, text: str) -> Dict[str, Any]:
        return {"text": text, "requester_id": self.requester_id}

    @custom_route(
        method="GET",
        path="/items/{item_id}",
        output_model=ProbeOutput,
        authentication_type="jwt",
    )
    def item(self, item_id: str, limit: int = 10) -> Dict[str, Any]:
        return {"text": "item", "item_id": item_id, "limit": limit}

    @custom_route(
        method="POST",
        path="/public",
        input_model=ProbeInput,
        output_model=ProbeOutput,
        authentication_type="none",
    )
    def public(self, body: ProbeInput) -> ProbeOutput:
        return ProbeOutput(text=body.text, requester_id=self.requester_id)

    @custom_route(
        method="GET",
        path="/cookie",
        output_model=ProbeOutput,
        authentication_type="none",
    )
    def cookie(self, request: Request) -> ProbeOutput:
        return ProbeOutput(text=request.cookies.get("probe", "none"))

    @custom_route(
        method="GET",
        path="/raw",
        authentication_type="none",
        expose_in=(ExposeIn.REST,),
        response_class=PlainTextResponse,
    )
    def raw(self, wrong: bool = False) -> Any:
        return {"not": "a response"} if wrong else PlainTextResponse("raw body")


@pytest.fixture(scope="module")
def probe(model_registry: Any) -> TestClient:
    """An app serving only the probe routes, bound to the real registry."""
    app = FastAPI()
    app.state.model_registry = model_registry
    router = APIRouter(prefix=PROBE)
    assert register_custom_routes(router, CustomRouteProbeManager) == 7
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def probe_hooks() -> Iterator[HookRegistry]:
    """The probe manager's hook registry, emptied after each test."""
    registry = CustomRouteProbeManager._hook_registry
    yield registry
    registry.clear()


class TestCustomRouteDispatch:
    def test_typed_body_reaches_the_method(self, probe, admin_a):
        response = probe.post(
            f"{PROBE}/echo", json={"text": "hello"}, headers=_bearer(admin_a.jwt)
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "text": "hello",
            "requester_id": admin_a.id,
            "item_id": None,
            "limit": None,
        }

    def test_body_fields_splat_onto_named_parameters(self, probe, admin_a):
        response = probe.post(
            f"{PROBE}/splat", json={"text": "fields"}, headers=_bearer(admin_a.jwt)
        )
        assert response.status_code == 200, response.text
        assert response.json()["text"] == "fields"
        assert response.json()["requester_id"] == admin_a.id

    def test_invalid_body_is_422(self, probe, admin_a):
        response = probe.post(
            f"{PROBE}/echo", json={"wrong": 1}, headers=_bearer(admin_a.jwt)
        )
        assert response.status_code == 422, response.text

    def test_malformed_json_is_400(self, probe, admin_a):
        response = probe.post(
            f"{PROBE}/echo",
            content=b"{not json",
            headers={**_bearer(admin_a.jwt), "Content-Type": "application/json"},
        )
        assert response.status_code == 400, response.text

    def test_path_and_query_parameters_reach_the_method(self, probe, admin_a):
        response = probe.get(
            f"{PROBE}/items/abc-123",
            params={"limit": "3"},
            headers=_bearer(admin_a.jwt),
        )
        assert response.status_code == 200, response.text
        assert response.json()["item_id"] == "abc-123"
        assert response.json()["limit"] == 3

    def test_query_parameter_default_applies(self, probe, admin_a):
        response = probe.get(f"{PROBE}/items/abc", headers=_bearer(admin_a.jwt))
        assert response.status_code == 200, response.text
        assert response.json()["limit"] == 10

    def test_query_parameter_is_validated_against_its_annotation(self, probe, admin_a):
        response = probe.get(
            f"{PROBE}/items/abc",
            params={"limit": "many"},
            headers=_bearer(admin_a.jwt),
        )
        assert response.status_code == 422, response.text

    def test_async_method_result_is_returned(self, probe, admin_a):
        response = probe.post(
            f"{PROBE}/async-echo", json={"text": "later"}, headers=_bearer(admin_a.jwt)
        )
        assert response.status_code == 200, response.text
        assert response.json()["text"] == "later"
        assert response.json()["requester_id"] == admin_a.id

    def test_a_request_parameter_receives_the_incoming_request(self, probe):
        response = probe.get(f"{PROBE}/cookie", headers={"Cookie": "probe=seen"})
        assert response.status_code == 200, response.text
        assert response.json()["text"] == "seen"
        # ...and is never mistaken for a query parameter.
        assert probe.get(f"{PROBE}/cookie").json()["text"] == "none"

    def test_a_response_class_route_answers_with_its_own_response(self, probe):
        response = probe.get(f"{PROBE}/raw")
        assert response.status_code == 200, response.text
        assert response.text == "raw body"
        assert response.headers["content-type"].startswith("text/plain")

    def test_a_response_class_route_returning_anything_else_fails(self, probe):
        assert probe.get(f"{PROBE}/raw", params={"wrong": "true"}).status_code == 500

    def test_jwt_route_without_credentials_is_401(self, probe):
        response = probe.post(f"{PROBE}/echo", json={"text": "anon"})
        assert response.status_code == 401, response.text

    def test_session_route_without_credentials_is_401(self, probe):
        response = probe.post(f"{PROBE}/splat", json={"text": "anon"})
        assert response.status_code == 401, response.text

    def test_none_route_works_without_credentials(self, probe):
        response = probe.post(f"{PROBE}/public", json={"text": "open"})
        assert response.status_code == 200, response.text
        assert response.json()["text"] == "open"
        assert response.json()["requester_id"] is None

    def test_before_hook_fires_and_can_rewrite_arguments(
        self, probe, probe_hooks, admin_a
    ):
        seen: List[str] = []

        def shout(context: HookContext) -> None:
            seen.append(context.kwargs["body"].text)
            context.kwargs["body"] = ProbeInput(
                text=context.kwargs["body"].text.upper()
            )

        probe_hooks.register_hook(CustomRouteProbeManager, "echo", "before", shout)
        response = probe.post(
            f"{PROBE}/echo", json={"text": "quiet"}, headers=_bearer(admin_a.jwt)
        )
        assert response.status_code == 200, response.text
        assert seen == ["quiet"]
        assert response.json()["text"] == "QUIET"

    def test_before_hook_fires_for_async_method(self, probe, probe_hooks, admin_a):
        seen: List[str] = []
        probe_hooks.register_hook(
            CustomRouteProbeManager,
            "async_echo",
            "before",
            lambda context: seen.append(context.kwargs["body"].text),
        )
        response = probe.post(
            f"{PROBE}/async-echo", json={"text": "hooked"}, headers=_bearer(admin_a.jwt)
        )
        assert response.status_code == 200, response.text
        assert seen == ["hooked"]

    def test_after_hook_can_replace_the_result(self, probe, probe_hooks, admin_a):
        def replace(context: HookContext) -> None:
            context.set_result(ProbeOutput(text="replaced"))

        probe_hooks.register_hook(CustomRouteProbeManager, "echo", "after", replace)
        response = probe.post(
            f"{PROBE}/echo", json={"text": "original"}, headers=_bearer(admin_a.jwt)
        )
        assert response.status_code == 200, response.text
        assert response.json()["text"] == "replaced"

    def test_blocking_before_hook_error_maps_to_its_status(
        self, probe, probe_hooks, admin_a
    ):
        def deny(context: HookContext) -> None:
            raise HTTPException(status_code=403, detail="denied by hook")

        probe_hooks.register_hook(CustomRouteProbeManager, "echo", "before", deny)
        response = probe.post(
            f"{PROBE}/echo", json={"text": "x"}, headers=_bearer(admin_a.jwt)
        )
        assert response.status_code == 403, response.text


class TestRegistrationContract:
    def test_path_placeholder_without_parameter_is_rejected(self):
        class Broken(AbstractBLLManager):
            @custom_route(method="GET", path="/{missing}", output_model=ProbeOutput)
            def lookup(self) -> ProbeOutput:
                return ProbeOutput(text="never")

        with pytest.raises(TypeError, match="missing"):
            register_custom_routes(APIRouter(), Broken)

    def test_body_parameter_on_bodiless_route_is_rejected(self):
        class Broken(AbstractBLLManager):
            @custom_route(method="GET", path="/read", output_model=ProbeOutput)
            def read(self, body: ProbeInput) -> ProbeOutput:
                return ProbeOutput(text=body.text)

        with pytest.raises(TypeError, match="body"):
            register_custom_routes(APIRouter(), Broken)


@pytest.mark.ep
@pytest.mark.auth
class TestAPIKeyRoutesOverHTTP(ExtensionServerMixin):
    extension_class = EXT_Auth_APIKeys

    @staticmethod
    def _issue(server: TestClient, jwt: str, name: str) -> Dict[str, Any]:
        response = server.post(
            f"{API_KEYS}/issue", json={"name": name}, headers=_bearer(jwt)
        )
        assert response.status_code == 200, response.text
        issued: Dict[str, Any] = response.json()
        return issued

    @staticmethod
    def _validate(server: TestClient, raw: str) -> Dict[str, Any]:
        response = server.post(f"{API_KEYS}/validate", json={"api_key": raw})
        assert response.status_code == 200, response.text
        verdict: Dict[str, Any] = response.json()
        return verdict

    def test_issue_returns_the_raw_key(self, server, admin_a):
        issued = self._issue(server, admin_a.jwt, "http issued")
        assert issued["name"] == "http issued"
        assert issued["id"]
        assert issued["key"]

    def test_issue_without_credentials_is_401(self, server):
        response = server.post(f"{API_KEYS}/issue", json={"name": "anon"})
        assert response.status_code == 401, response.text

    def test_validate_needs_no_credentials(self, server, admin_a):
        issued = self._issue(server, admin_a.jwt, "to validate")
        verdict = self._validate(server, issued["key"])
        assert verdict["valid"] is True
        assert verdict["user_id"] == admin_a.id

    def test_validate_rejects_an_unknown_key(self, server):
        assert self._validate(server, "not-a-real-key")["valid"] is False

    def test_rotate_replaces_the_key(self, server, admin_a):
        issued = self._issue(server, admin_a.jwt, "to rotate")
        response = server.post(
            f"{API_KEYS}/rotate",
            json={"key_id": issued["id"]},
            headers=_bearer(admin_a.jwt),
        )
        assert response.status_code == 200, response.text
        rotated = response.json()
        assert rotated["key"] != issued["key"]
        assert self._validate(server, rotated["key"])["valid"] is True
        assert self._validate(server, issued["key"])["valid"] is False

    def test_rotate_another_users_key_is_forbidden(self, server, admin_a, admin_b):
        issued = self._issue(server, admin_a.jwt, "not yours")
        response = server.post(
            f"{API_KEYS}/rotate",
            json={"key_id": issued["id"]},
            headers=_bearer(admin_b.jwt),
        )
        assert response.status_code == 403, response.text
        assert self._validate(server, issued["key"])["valid"] is True


@pytest.mark.ep
@pytest.mark.auth
class TestNotificationRoutesOverHTTP(ExtensionServerMixin):
    extension_class = EXT_Auth_Notifications

    @staticmethod
    def _deliver(server: TestClient, user: Any, title: str) -> str:
        """Create a notification for ``user`` and its delivery row; return the row id."""
        created = server.post(
            NOTIFICATIONS,
            json={
                "notification": {
                    "title": title,
                    "content": f"Body of {title}",
                    "user_id": user.id,
                }
            },
            headers=_bearer(user.jwt),
        )
        assert created.status_code == 201, created.text
        delivered = server.post(
            USER_NOTIFICATIONS,
            json={
                "user_notification": {
                    "notification_id": created.json()["notification"]["id"],
                    "user_id": user.id,
                }
            },
            headers=_bearer(user.jwt),
        )
        assert delivered.status_code == 201, delivered.text
        row_id: str = delivered.json()["user_notification"]["id"]
        return row_id

    @staticmethod
    def _state(server: TestClient, user: Any, row_id: str) -> Dict[str, Any]:
        fetched = server.get(
            f"{USER_NOTIFICATIONS}/{row_id}", headers=_bearer(user.jwt)
        )
        assert fetched.status_code == 200, fetched.text
        state: Dict[str, Any] = fetched.json()["user_notification"]
        return state

    def test_mark_read_and_acknowledge(self, server, admin_a):
        row_id = self._deliver(server, admin_a, "http read")

        read = server.patch(
            f"{USER_NOTIFICATIONS}/{row_id}/read", json={}, headers=_bearer(admin_a.jwt)
        )
        assert read.status_code == 200, read.text
        assert read.json()["id"] == row_id
        assert read.json()["read"] is True
        assert read.json()["read_at"] is not None

        acknowledged = server.patch(
            f"{USER_NOTIFICATIONS}/{row_id}/acknowledge",
            json={},
            headers=_bearer(admin_a.jwt),
        )
        assert acknowledged.status_code == 200, acknowledged.text
        assert acknowledged.json()["acknowledged"] is True
        assert acknowledged.json()["acknowledged_at"] is not None

        state = self._state(server, admin_a, row_id)
        assert state["read"] is True
        assert state["acknowledged"] is True

    def test_other_user_cannot_mark_or_acknowledge(self, server, admin_a, admin_b):
        row_id = self._deliver(server, admin_a, "http not yours")
        for action in ("read", "acknowledge"):
            response = server.patch(
                f"{USER_NOTIFICATIONS}/{row_id}/{action}",
                json={},
                headers=_bearer(admin_b.jwt),
            )
            assert response.status_code == 403, response.text

        state = self._state(server, admin_a, row_id)
        assert state["read"] is False
        assert state["acknowledged"] is False

    def test_mark_read_without_credentials_is_401(self, server, admin_a):
        row_id = self._deliver(server, admin_a, "http anon")
        response = server.patch(f"{USER_NOTIFICATIONS}/{row_id}/read", json={})
        assert response.status_code == 401, response.text

    def test_a_custom_save_is_held_to_if_match(self, server, admin_a):
        """A custom route writing the record its path names honours
        If-Match like a generic PUT: stale is 412 with the record as it
        stands and nothing written; the current version saves."""
        row_id = self._deliver(server, admin_a, "http versioned")
        fetched = server.get(
            f"{USER_NOTIFICATIONS}/{row_id}", headers=_bearer(admin_a.jwt)
        )
        etag = fetched.headers["etag"]

        stale = server.patch(
            f"{USER_NOTIFICATIONS}/{row_id}/read",
            json={},
            headers={**_bearer(admin_a.jwt), "If-Match": '"1970-01-01T00:00:00"'},
        )
        assert stale.status_code == 412, stale.text
        assert stale.json()["current"]["id"] == row_id
        assert stale.headers["etag"] == etag
        assert self._state(server, admin_a, row_id)["read"] is False

        read = server.patch(
            f"{USER_NOTIFICATIONS}/{row_id}/read",
            json={},
            headers={**_bearer(admin_a.jwt), "If-Match": etag},
        )
        assert read.status_code == 200, read.text
        # The read moved the version on: the old ETag is now stale.
        again = server.patch(
            f"{USER_NOTIFICATIONS}/{row_id}/acknowledge",
            json={},
            headers={**_bearer(admin_a.jwt), "If-Match": etag},
        )
        assert again.status_code == 412, again.text
        assert self._state(server, admin_a, row_id)["acknowledged"] is False

    def test_a_custom_save_without_if_match_is_428_when_required(
        self, server, admin_a, monkeypatch
    ):
        from zephyrex.lib import Environment
        from zephyrex.lib.Preconditions import IF_MATCH_REQUIRED_SETTING

        row_id = self._deliver(server, admin_a, "http required")
        monkeypatch.setenv(IF_MATCH_REQUIRED_SETTING, "true")
        monkeypatch.setattr(Environment.settings, IF_MATCH_REQUIRED_SETTING, "true")
        refused = server.patch(
            f"{USER_NOTIFICATIONS}/{row_id}/read",
            json={},
            headers=_bearer(admin_a.jwt),
        )
        assert refused.status_code == 428, refused.text
        assert refused.json() == {"detail": "If-Match required"}
        assert self._state(server, admin_a, row_id)["read"] is False

    def test_mark_unknown_row_is_404(self, server, admin_a):
        response = server.patch(
            f"{USER_NOTIFICATIONS}/00000000-0000-0000-0000-000000000000/read",
            json={},
            headers=_bearer(admin_a.jwt),
        )
        assert response.status_code == 404, response.text
