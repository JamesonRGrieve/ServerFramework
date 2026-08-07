"""Tests for the auth_notifications extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "auth_notifications_test")


from zephyrex.extensions.auth_notifications.BLL_Auth_Notifications import (
    NotificationManager,
    NotificationModel,
    UserNotificationManager,
    UserNotificationModel,
)
from zephyrex.extensions.auth_notifications.EXT_Auth_Notifications import (
    EXT_Auth_Notifications,
)


class TestCanonicalWiring:
    def test_notification_manager_round_trip(self):
        assert NotificationModel.Manager is NotificationManager
        assert NotificationManager._model is NotificationModel

    def test_user_notification_manager_round_trip(self):
        assert UserNotificationModel.Manager is UserNotificationManager
        assert UserNotificationManager._model is UserNotificationModel

    def test_extension_metadata(self):
        assert EXT_Auth_Notifications.name == "auth_notifications"
        abilities = EXT_Auth_Notifications.get_abilities()
        assert "notifications_create" in abilities
        assert "notifications_acknowledge" in abilities


class TestLifecycle:
    def test_on_initialize_returns_true(self):
        assert EXT_Auth_Notifications.on_initialize() is True
