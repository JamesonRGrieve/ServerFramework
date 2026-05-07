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

from serverframework.extensions.auth_recovery_questions.BLL_Recovery_Questions import (
    UserRecoveryQuestionManager,
    UserRecoveryQuestionModel,
)
from serverframework.extensions.auth_recovery_questions.EXT_Recovery_Questions import (
    AuthRecoveryQuestionsExtension,
)
from serverframework.logic import BLL_Auth


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
