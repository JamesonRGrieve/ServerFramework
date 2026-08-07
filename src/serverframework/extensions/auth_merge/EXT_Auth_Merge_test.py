"""Tests for the auth_merge extension.

Covers:
- Canonical model/manager wiring.
- Extension lifecycle and self-merge guard.
- The merge-handler registry (registration, ordering, error isolation).
"""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "auth_merge_test")

import pytest
from fastapi import HTTPException

from serverframework.extensions.auth_merge.BLL_Auth_Merge import (
    MergeContext,
    UserMergeManager,
    UserMergeModel,
    _run_merge_handlers,
    list_merge_handlers,
    register_merge_handler,
    unregister_merge_handler,
)
from serverframework.extensions.auth_merge.EXT_Auth_Merge import EXT_Auth_Merge


class TestCanonicalWiring:
    def test_model_manager_round_trip(self):
        assert UserMergeModel.Manager is UserMergeManager
        assert UserMergeManager._model is UserMergeModel

    def test_extension_metadata(self):
        assert EXT_Auth_Merge.name == "auth_merge"
        assert "auth_session" in EXT_Auth_Merge.extension_dependencies
        assert "user_merge" in EXT_Auth_Merge.get_abilities()


class TestLifecycle:
    def test_on_initialize_returns_true(self):
        assert EXT_Auth_Merge.on_initialize() is True

    def test_validate_config_returns_list(self):
        issues = EXT_Auth_Merge.validate_config()
        assert isinstance(issues, list)


class TestValidation:
    def test_self_merge_rejected(self):
        manager = UserMergeManager.__new__(UserMergeManager)
        manager.model_registry = None
        with pytest.raises(HTTPException) as exc_info:
            manager._validate("user-1", "user-1")
        assert exc_info.value.status_code == 400
        assert "themselves" in exc_info.value.detail


class TestMergeHandlerRegistry:
    def setup_method(self):
        # Other extensions (auth_notifications, oauth_consumer,
        # auth_api_keys, auth_recovery_questions) register handlers at
        # ``EXT.on_initialize`` time. When those lifecycle hooks run
        # earlier in the worker process, the global ``_HANDLERS`` dict
        # carries the registrations into this test class — and they
        # crash on ``model_registry=None`` payloads, leaking errors
        # into ``_run_merge_handlers``'s return. Snapshot and restore
        # the registry around each test so this class only sees the
        # handlers it explicitly registers.
        from serverframework.extensions.auth_merge.BLL_Auth_Merge import (
            _HANDLERS,
        )

        self._handlers_snapshot = dict(_HANDLERS)
        _HANDLERS.clear()
        self._test_handlers = []

    def teardown_method(self):
        for name in self._test_handlers:
            unregister_merge_handler(name)
        from serverframework.extensions.auth_merge.BLL_Auth_Merge import (
            _HANDLERS,
        )

        _HANDLERS.clear()
        _HANDLERS.update(self._handlers_snapshot)

    def _register(self, name, fn):
        register_merge_handler(name, fn)
        self._test_handlers.append(name)

    def test_register_and_list(self):
        self._register("test-a", lambda ctx: None)
        self._register("test-b", lambda ctx: None)
        names = list_merge_handlers()
        assert "test-a" in names
        assert "test-b" in names

    def test_handlers_receive_context(self):
        captured = {}

        def handler(ctx):
            captured["ctx"] = ctx

        self._register("test-capture", handler)
        ctx = MergeContext(
            initiating_user_id="i",
            target_user_id="t",
            requester_id="r",
            model_registry=None,
        )
        errors = _run_merge_handlers(ctx)
        assert errors == {}
        assert captured["ctx"].initiating_user_id == "i"
        assert captured["ctx"].target_user_id == "t"
        assert captured["ctx"].requester_id == "r"

    def test_handler_failure_is_isolated(self):
        def failing(ctx):
            raise RuntimeError("boom")

        succeeded = {"ran": False}

        def succeeding(ctx):
            succeeded["ran"] = True

        # Register failing first, then succeeding — confirm the second
        # handler still ran despite the first raising.
        self._register("test-fails", failing)
        self._register("test-succeeds", succeeding)
        ctx = MergeContext(
            initiating_user_id="i",
            target_user_id="t",
            requester_id="r",
            model_registry=None,
        )
        errors = _run_merge_handlers(ctx)
        assert "test-fails" in errors
        assert "boom" in errors["test-fails"]
        assert "test-succeeds" not in errors
        assert succeeded["ran"] is True

    def test_unregister_removes_handler(self):
        called = {"count": 0}

        def handler(ctx):
            called["count"] += 1

        register_merge_handler("test-remove", handler)
        unregister_merge_handler("test-remove")
        ctx = MergeContext(
            initiating_user_id="i",
            target_user_id="t",
            requester_id="r",
            model_registry=None,
        )
        _run_merge_handlers(ctx)
        assert called["count"] == 0

    def test_register_overwrites(self):
        first_called = {"v": False}
        second_called = {"v": False}

        def first(ctx):
            first_called["v"] = True

        def second(ctx):
            second_called["v"] = True

        register_merge_handler("test-same", first)
        register_merge_handler("test-same", second)
        self._test_handlers.append("test-same")
        ctx = MergeContext(
            initiating_user_id="i",
            target_user_id="t",
            requester_id="r",
            model_registry=None,
        )
        _run_merge_handlers(ctx)
        assert first_called["v"] is False
        assert second_called["v"] is True


class TestParticipatingExtensions:
    """Confirm every extension that should participate self-registers on
    initialize."""

    def test_auth_notifications_registers(self):
        from serverframework.extensions.auth_notifications.EXT_Auth_Notifications import (
            EXT_Auth_Notifications,
        )

        EXT_Auth_Notifications.on_initialize()
        assert "auth_notifications" in list_merge_handlers()

    def test_oauth_consumer_registers(self):
        os.environ.setdefault("ALLOW_PLAINTEXT_SECRETS", "true")
        from serverframework.extensions.oauth_consumer.EXT_OAuthConsumer import (
            EXT_OAuthConsumer,
        )

        EXT_OAuthConsumer.on_initialize()
        assert "oauth_consumer" in list_merge_handlers()

    def test_auth_api_keys_registers(self):
        from serverframework.extensions.auth_api_keys.EXT_Auth_APIKeys import (
            EXT_Auth_APIKeys,
        )

        EXT_Auth_APIKeys.on_initialize()
        assert "auth_api_keys" in list_merge_handlers()

    def test_auth_recovery_questions_registers(self):
        from serverframework.extensions.auth_recovery_questions.EXT_Recovery_Questions import (
            AuthRecoveryQuestionsExtension,
        )

        AuthRecoveryQuestionsExtension.on_initialize()
        assert "auth_recovery_questions" in list_merge_handlers()


class TestAuthorization:
    """`merge_users` must refuse to drive a merge that the requester is
    not party to. The `_assert_can_merge` helper is the gate."""

    def _manager_with_requester(self, requester_id: str) -> UserMergeManager:
        manager = UserMergeManager.__new__(UserMergeManager)
        manager.model_registry = None

        class _Stub:
            id = requester_id

        manager.requester = _Stub()
        return manager

    def test_non_root_third_party_caller_rejected(self):
        manager = self._manager_with_requester("not-a-participant")
        with pytest.raises(HTTPException) as exc_info:
            manager._assert_can_merge("user-a", "user-b", consent_token=None)
        assert exc_info.value.status_code == 403

    def test_target_caller_rejected(self):
        # C-1 — even the user being absorbed cannot drive the merge.
        manager = self._manager_with_requester("user-b")
        with pytest.raises(HTTPException) as exc_info:
            manager._assert_can_merge("user-a", "user-b", consent_token=None)
        assert exc_info.value.status_code == 403

    def test_initiating_caller_without_consent_rejected(self):
        # C-1 — the initiating user alone cannot drive the merge; they
        # must present a consent token minted by the target.
        manager = self._manager_with_requester("user-a")
        with pytest.raises(HTTPException) as exc_info:
            manager._assert_can_merge("user-a", "user-b", consent_token=None)
        assert exc_info.value.status_code == 403
        assert "consent" in exc_info.value.detail.lower()

    def test_initiating_caller_with_valid_consent_allowed(self):
        from serverframework.extensions.auth_merge.BLL_Auth_Merge import (
            _mint_merge_consent_token,
        )
        from serverframework.lib.ReplayCache import (
            InMemoryReplayCache,
            set_replay_cache,
        )

        # Pristine replay cache so a previously-burned jti from another
        # test cannot leak in.
        set_replay_cache(InMemoryReplayCache())
        manager = self._manager_with_requester("user-a")
        token = _mint_merge_consent_token(
            target_user_id="user-b", initiating_user_id="user-a"
        )
        manager._assert_can_merge("user-a", "user-b", consent_token=token)

    def test_consent_token_is_single_use(self):
        from serverframework.extensions.auth_merge.BLL_Auth_Merge import (
            _mint_merge_consent_token,
        )
        from serverframework.lib.ReplayCache import (
            InMemoryReplayCache,
            set_replay_cache,
        )

        set_replay_cache(InMemoryReplayCache())
        manager = self._manager_with_requester("user-a")
        token = _mint_merge_consent_token(
            target_user_id="user-b", initiating_user_id="user-a"
        )
        # First use accepted.
        manager._assert_can_merge("user-a", "user-b", consent_token=token)
        # Second use refused (jti burned).
        with pytest.raises(HTTPException) as exc_info:
            manager._assert_can_merge("user-a", "user-b", consent_token=token)
        assert exc_info.value.status_code == 403
        assert "redeemed" in exc_info.value.detail.lower()

    def test_consent_token_target_mismatch_rejected(self):
        from serverframework.extensions.auth_merge.BLL_Auth_Merge import (
            _mint_merge_consent_token,
        )
        from serverframework.lib.ReplayCache import (
            InMemoryReplayCache,
            set_replay_cache,
        )

        set_replay_cache(InMemoryReplayCache())
        manager = self._manager_with_requester("user-a")
        # Token signed for target=user-b, but submission claims user-c.
        token = _mint_merge_consent_token(
            target_user_id="user-b", initiating_user_id="user-a"
        )
        with pytest.raises(HTTPException) as exc_info:
            manager._assert_can_merge("user-a", "user-c", consent_token=token)
        assert exc_info.value.status_code == 403

    def test_consent_token_initiator_mismatch_rejected(self):
        from serverframework.extensions.auth_merge.BLL_Auth_Merge import (
            _mint_merge_consent_token,
        )
        from serverframework.lib.ReplayCache import (
            InMemoryReplayCache,
            set_replay_cache,
        )

        set_replay_cache(InMemoryReplayCache())
        # Token consents to merge into user-a, but the caller is user-c.
        manager = self._manager_with_requester("user-c")
        token = _mint_merge_consent_token(
            target_user_id="user-b", initiating_user_id="user-a"
        )
        with pytest.raises(HTTPException) as exc_info:
            manager._assert_can_merge("user-c", "user-b", consent_token=token)
        assert exc_info.value.status_code == 403

    def test_consent_token_garbage_rejected(self):
        manager = self._manager_with_requester("user-a")
        with pytest.raises(HTTPException) as exc_info:
            manager._assert_can_merge(
                "user-a", "user-b", consent_token="not.a.jwt"
            )
        assert exc_info.value.status_code == 403

    def test_root_bypasses_consent(self):
        from serverframework.lib.Environment import env

        # ROOT_ID may be unset in the test env — only assert when set.
        root_id = env("ROOT_ID")
        if not root_id:
            pytest.skip("ROOT_ID not configured in test environment")
        manager = self._manager_with_requester(root_id)
        # No consent token, ROOT still allowed.
        manager._assert_can_merge("user-a", "user-b", consent_token=None)
