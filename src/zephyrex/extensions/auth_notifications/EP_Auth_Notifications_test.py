# SPDX-License-Identifier: AGPL-3.0-or-later
"""Endpoint tests for the auth_notifications extension.

Runs against a real app with ``auth_notifications`` loaded
(``ExtensionServerMixin``). Notification and delivery-row CRUD are exercised
over HTTP. Mark-read and acknowledge are exercised through a real
``UserNotificationManager`` bound to the same registry and database, because
the framework's ``@custom_route`` REST adapter (``lib/CustomRoute.py``) cannot
yet dispatch them: it inspects the hook-wrapped method's
``(self, *args, **kwargs)`` signature (so ``id``/``body`` are never passed)
and does not await ``async`` route methods.
"""

from typing import Any, Dict, Optional

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.auth_notifications.BLL_Auth_Notifications import (
    UserNotificationManager,
)
from zephyrex.extensions.auth_notifications.EXT_Auth_Notifications import (
    EXT_Auth_Notifications,
)
from zephyrex.lib.Environment import env

NOTIFICATIONS = "/v1/notifications"
USER_NOTIFICATIONS = "/v1/user-notifications"


def _bearer(jwt: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


@pytest.mark.ep
@pytest.mark.auth
class TestNotificationEndpoints(ExtensionServerMixin):
    extension_class = EXT_Auth_Notifications

    @staticmethod
    def _create_notification(
        server: Any,
        jwt: str,
        title: str,
        user_id: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> Any:
        payload: Dict[str, Any] = {"title": title, "content": f"Body of {title}"}
        if user_id is not None:
            payload["user_id"] = user_id
        if team_id is not None:
            payload["team_id"] = team_id
        return server.post(
            NOTIFICATIONS, json={"notification": payload}, headers=_bearer(jwt)
        )

    def _deliver(self, server: Any, user: Any, title: str) -> Dict[str, Any]:
        """Create a notification for ``user`` plus their delivery row."""
        created = self._create_notification(server, user.jwt, title, user_id=user.id)
        assert created.status_code == 201, created.text
        notification = created.json()["notification"]
        delivered = server.post(
            USER_NOTIFICATIONS,
            json={
                "user_notification": {
                    "notification_id": notification["id"],
                    "user_id": user.id,
                }
            },
            headers=_bearer(user.jwt),
        )
        assert delivered.status_code == 201, delivered.text
        row: Dict[str, Any] = delivered.json()["user_notification"]
        return row

    @staticmethod
    def _delivery_manager(
        model_registry: Any, requester_id: str
    ) -> UserNotificationManager:
        return UserNotificationManager(
            requester_id=requester_id, model_registry=model_registry
        )

    # -- notifications -----------------------------------------------------

    def test_create_and_list_own_notification(self, server, admin_a, admin_b):
        created = self._create_notification(
            server, admin_a.jwt, "for-self", user_id=admin_a.id
        )
        assert created.status_code == 201, created.text
        notification = created.json()["notification"]
        assert notification["title"] == "for-self"
        assert notification["user_id"] == admin_a.id

        own = server.get(NOTIFICATIONS, headers=_bearer(admin_a.jwt))
        assert own.status_code == 200, own.text
        assert notification["id"] in own.text

        other = server.get(NOTIFICATIONS, headers=_bearer(admin_b.jwt))
        assert other.status_code == 200, other.text
        assert notification["id"] not in other.text

    def test_cannot_create_notification_for_another_user(
        self, server, admin_a, admin_b
    ):
        response = server.post(
            NOTIFICATIONS,
            json={
                "notification": {
                    "title": "impersonated",
                    "content": "x",
                    "user_id": admin_b.id,
                }
            },
            headers=_bearer(admin_a.jwt),
        )
        assert response.status_code == 403, response.text

    def test_create_notification_for_own_team(self, server, admin_a, team_a):
        response = self._create_notification(
            server, admin_a.jwt, "own-team", team_id=team_a.id
        )
        assert response.status_code == 201, response.text
        assert response.json()["notification"]["team_id"] == team_a.id

    def test_cannot_create_notification_for_foreign_team(self, server, admin_a, team_b):
        response = self._create_notification(
            server, admin_a.jwt, "foreign-team", team_id=team_b.id
        )
        assert response.status_code == 403, response.text

        listed = server.get(NOTIFICATIONS, headers=_bearer(admin_a.jwt))
        assert "foreign-team" not in listed.text

    def test_root_may_address_any_team(self, server, team_b):
        response = server.post(
            NOTIFICATIONS,
            json={
                "notification": {
                    "title": "root-broadcast",
                    "content": "x",
                    "team_id": team_b.id,
                }
            },
            headers={"X-API-Key": env("ROOT_API_KEY")},
        )
        assert response.status_code == 201, response.text

    def test_team_notification_not_listed_outside_team(
        self, server, admin_a, team_a, admin_b
    ):
        created = self._create_notification(
            server, admin_a.jwt, "team-a-only", team_id=team_a.id
        )
        assert created.status_code == 201, created.text
        notification_id = created.json()["notification"]["id"]

        outsider = server.get(NOTIFICATIONS, headers=_bearer(admin_b.jwt))
        assert outsider.status_code == 200, outsider.text
        assert notification_id not in outsider.text

    # -- per-user delivery state ------------------------------------------

    def test_list_own_delivery_rows(self, server, admin_a, admin_b):
        row = self._deliver(server, admin_a, "delivered")
        assert row["user_id"] == admin_a.id
        assert row["read"] is False
        assert row["acknowledged"] is False

        own = server.get(USER_NOTIFICATIONS, headers=_bearer(admin_a.jwt))
        assert own.status_code == 200, own.text
        assert row["id"] in own.text

        other = server.get(USER_NOTIFICATIONS, headers=_bearer(admin_b.jwt))
        assert other.status_code == 200, other.text
        assert row["id"] not in other.text

    def test_mark_read_and_acknowledge_own(self, server, model_registry, admin_a):
        row = self._deliver(server, admin_a, "to-read")
        manager = self._delivery_manager(model_registry, admin_a.id)

        read = manager.mark_as_read(row["id"])
        assert read.read is True
        assert read.read_at is not None

        acknowledged = manager.acknowledge(row["id"])
        assert acknowledged.acknowledged is True
        assert acknowledged.acknowledged_at is not None

        fetched = server.get(
            f"{USER_NOTIFICATIONS}/{row['id']}", headers=_bearer(admin_a.jwt)
        )
        assert fetched.status_code == 200, fetched.text
        state = fetched.json()["user_notification"]
        assert state["read"] is True
        assert state["acknowledged"] is True

    def test_other_user_cannot_mark_or_acknowledge(
        self, server, model_registry, admin_a, admin_b
    ):
        row = self._deliver(server, admin_a, "not-yours")
        intruder = self._delivery_manager(model_registry, admin_b.id)

        with pytest.raises(HTTPException) as read_exc:
            intruder.mark_as_read(row["id"])
        assert read_exc.value.status_code == 403

        with pytest.raises(HTTPException) as ack_exc:
            intruder.acknowledge(row["id"])
        assert ack_exc.value.status_code == 403

        fetched = server.get(
            f"{USER_NOTIFICATIONS}/{row['id']}", headers=_bearer(admin_a.jwt)
        )
        state = fetched.json()["user_notification"]
        assert state["read"] is False
        assert state["acknowledged"] is False

    def test_mark_unknown_delivery_row_is_404(self, model_registry, admin_a):
        manager = self._delivery_manager(model_registry, admin_a.id)
        with pytest.raises(HTTPException) as excinfo:
            manager.mark_as_read("00000000-0000-0000-0000-000000000000")
        assert excinfo.value.status_code == 404
