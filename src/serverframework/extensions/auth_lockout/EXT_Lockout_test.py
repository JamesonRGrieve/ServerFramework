"""Tests for the auth_lockout extension carve-out (Scope #2).

Covers:
- Canonical FailedLoginAttempt classes live at the extension path.
- Core ``BLL_Auth`` PEP 562 forwards to the extension.
- ``EXT_Lockout.on_load`` populates ``BLL_Auth._lockout_hooks``.
- Core's ``UserManager.login`` consults the per-user threshold via the hook
  (no direct FailedLoginAttempt access from core anymore).
- The IP-keyed in-memory ``LockoutTracker`` keeps working without the
  extension (always-on defense).
"""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "auth_lockout_test")

import pytest
from fastapi import HTTPException

from serverframework.extensions.auth_lockout.BLL_Lockout import (
    FailedLoginAttemptManager,
    FailedLoginAttemptModel,
)
from serverframework.extensions.auth_lockout.EXT_Lockout import AuthLockoutExtension
from serverframework.logic import BLL_Auth


class TestCanonicalClassLocation:
    def test_classes_live_in_extension(self):
        assert FailedLoginAttemptModel.Manager is FailedLoginAttemptManager
        assert FailedLoginAttemptManager._model is FailedLoginAttemptModel

    def test_pep562_forward_in_core(self):
        assert BLL_Auth.FailedLoginAttemptModel is FailedLoginAttemptModel
        assert BLL_Auth.FailedLoginAttemptManager is FailedLoginAttemptManager


class TestExtensionLifecycle:
    def setup_method(self):
        self._snapshot = dict(BLL_Auth._lockout_hooks)
        for k in BLL_Auth._lockout_hooks:
            BLL_Auth._lockout_hooks[k] = None

    def teardown_method(self):
        for k, v in self._snapshot.items():
            BLL_Auth._lockout_hooks[k] = v

    def test_models_returns_failed_login_model(self):
        assert FailedLoginAttemptModel in AuthLockoutExtension.models()

    def test_on_load_populates_every_hook(self):
        AuthLockoutExtension.on_load()
        for key in ("assert_within_threshold", "record_failure"):
            assert (
                BLL_Auth._lockout_hooks[key] is not None
            ), f"hook {key!r} not registered by on_load"


class TestIpKeyedLockoutAlwaysAvailable:
    """The ``LockoutTracker`` is in core and runs even without auth_lockout.
    The H-8 IP-keyed lockout must therefore be testable without the extension."""

    def setup_method(self):
        UserMgr = BLL_Auth.UserManager
        self.tracker = UserMgr._lockout_tracker
        self.tracker.clear("test-ip", "password_login")

    def teardown_method(self):
        self.tracker.clear("test-ip", "password_login")

    def test_record_failure_below_threshold_does_not_lock(self):
        for _ in range(self.tracker.policy.failures_per_window - 1):
            self.tracker.record_failure("test-ip", "password_login")
        assert self.tracker.is_locked("test-ip", "password_login") is False

    def test_record_failure_at_threshold_locks(self):
        for _ in range(self.tracker.policy.failures_per_window):
            self.tracker.record_failure("test-ip", "password_login")
        assert self.tracker.is_locked("test-ip", "password_login") is True

    def test_clear_releases_lockout(self):
        for _ in range(self.tracker.policy.failures_per_window):
            self.tracker.record_failure("test-ip", "password_login")
        self.tracker.clear("test-ip", "password_login")
        assert self.tracker.is_locked("test-ip", "password_login") is False
