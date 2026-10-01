# SPDX-License-Identifier: AGPL-3.0-or-later
"""The password rule: which passwords break which rules."""

import pytest
from fastapi import HTTPException

from zephyrex.logic.BLL_Auth.password_policy import (
    BCRYPT_MAX_BYTES,
    PASSWORD_POLICY,
    enforce_password_policy,
    failed_rules,
)


@pytest.mark.parametrize(
    "password, failed",
    [
        ("correcthorse9", []),
        ("short1", ["min_length"]),
        ("onlyletters", ["require_digit"]),
        ("1234567890", ["require_letter"]),
        ("        ", ["min_length", "require_letter", "require_digit"]),
        (None, ["min_length", "require_letter", "require_digit"]),
        ("a1" + "é" * 40, ["max_bytes"]),  # 82 UTF-8 bytes in 42 characters
    ],
    ids=["ok", "short", "no-digit", "no-letter", "blank", "missing", "over-bcrypt"],
)
def test_failed_rules(password, failed):
    assert failed_rules(password) == failed


def test_the_byte_limit_is_bcrypts():
    assert PASSWORD_POLICY.max_bytes == BCRYPT_MAX_BYTES == 72
    assert failed_rules("a1" + "x" * 70) == []
    assert failed_rules("a1" + "x" * 71) == ["max_bytes"]


def test_enforce_names_the_broken_rules():
    with pytest.raises(HTTPException) as refused:
        enforce_password_policy("short")
    assert refused.value.status_code == 422
    assert refused.value.detail == {
        "message": "Password does not meet the policy",
        "failed": ["min_length", "require_digit"],
    }
