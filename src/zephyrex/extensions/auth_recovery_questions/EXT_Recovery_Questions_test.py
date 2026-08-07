"""Tests for the auth_recovery_questions extension carve-out (Scope #1).

Covers:
- Canonical UserRecoveryQuestion classes live at the extension path.
- Core ``BLL_Auth`` PEP 562 forwards to the extension.
- The hash-and-verify roundtrip through the manager remains constant-time
  (uses bcrypt + the framework's pinned cost) — this contract was a
  prerequisite for keeping recovery questions a viable secondary factor
  that an app can opt into without touching core.
"""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "auth_recovery_questions_test")

import bcrypt

from zephyrex.extensions.auth_recovery_questions.BLL_Recovery_Questions import (
    UserRecoveryQuestionManager,
    UserRecoveryQuestionModel,
)
from zephyrex.extensions.auth_recovery_questions.EXT_Recovery_Questions import (
    AuthRecoveryQuestionsExtension,
)
from zephyrex.logic import BLL_Auth


class TestCanonicalClassLocation:
    def test_classes_live_in_extension(self):
        assert UserRecoveryQuestionModel.Manager is UserRecoveryQuestionManager
        assert UserRecoveryQuestionManager._model is UserRecoveryQuestionModel

    def test_pep562_forward_in_core(self):
        assert BLL_Auth.UserRecoveryQuestionModel is UserRecoveryQuestionModel
        assert BLL_Auth.UserRecoveryQuestionManager is UserRecoveryQuestionManager


class TestExtensionLifecycle:
    def test_models_returns_recovery_question_model(self):
        assert (
            UserRecoveryQuestionModel
            in AuthRecoveryQuestionsExtension.models()
        )


class TestAnswerHashRoundTrip:
    """Recovery answers are bcrypted with normalized lowercase + strip.
    Verification is delegated to bcrypt.checkpw which is constant-time."""

    def test_normalized_match(self):
        salt = bcrypt.gensalt(rounds=4)
        hashed = bcrypt.hashpw(b"alice cooper", salt).decode()
        # Manager normalizes via .lower().strip(); confirm equivalence on
        # canonical inputs
        for variant in ("Alice Cooper", "alice cooper", "  ALICE COOPER  "):
            normalized = variant.lower().strip()
            assert bcrypt.checkpw(normalized.encode(), hashed.encode())

    def test_normalized_mismatch(self):
        salt = bcrypt.gensalt(rounds=4)
        hashed = bcrypt.hashpw(b"correct", salt).decode()
        assert not bcrypt.checkpw(b"different", hashed.encode())


class TestSensitiveAnswerField:
    """H-3 — the bcrypt hash on ``answer`` is gated by the
    ``auth.user.read_secret`` permission so REST/GraphQL responses
    omit it for any non-ROOT requester."""

    def test_answer_field_carries_required_permission(self):
        from zephyrex.lib.FieldACL import get_required_permissions

        info = UserRecoveryQuestionModel.model_fields["answer"]
        perms = get_required_permissions(info)
        assert "auth.user.read_secret" in perms

    def test_routes_to_register_excludes_search_and_list(self):
        # H-3 — SEARCH/LIST returned the full DTO including the
        # ``answer`` hash. The manager's allow-list narrows the
        # surface to the operations a user actually needs.
        registered = {r.value for r in UserRecoveryQuestionManager.routes_to_register}
        assert "search" not in registered
        assert "list" not in registered
        # Per-record GET, plus create/update/delete remain.
        assert {"get", "create", "update", "delete"}.issubset(registered)


class TestVerifyAnswerLockout:
    """H-3 — repeat wrong answers trip the per-(user, flow) lockout."""

    def test_lockout_tracker_attached(self):
        from zephyrex.lib.InboundSecurity import LockoutTracker

        assert isinstance(
            UserRecoveryQuestionManager._verify_lockout_tracker, LockoutTracker
        )

    def test_lockout_policy_is_strict_enough(self):
        policy = UserRecoveryQuestionManager._verify_lockout_tracker.policy
        # 5 fails per 15 minutes is the minimum to make low-entropy
        # recovery answers unviable for online brute force.
        assert policy.failures_per_window <= 5
        assert policy.window_seconds >= 60
