# SPDX-License-Identifier: AGPL-3.0-or-later
"""The password rule: which passwords break which rules."""

import bcrypt
import pytest
from fastapi import HTTPException

from zephyrex.logic.BLL_Auth.password_policy import (
    BCRYPT_MAX_BYTES,
    PASSWORD_POLICY,
    enforce_password_policy,
    failed_rules,
    password_matches,
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
        ("password²", ["require_digit"]),  # a superscript is not a digit
        ("pässwörd٣", []),  # Arabic-Indic three is a decimal digit (Nd)
    ],
    ids=[
        "ok",
        "short",
        "no-digit",
        "no-letter",
        "blank",
        "missing",
        "over-bcrypt",
        "superscript",
        "other-script",
    ],
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


def test_password_matches_its_hash_only():
    hashed = bcrypt.hashpw(b"correcthorse9", bcrypt.gensalt(rounds=4))
    assert password_matches("correcthorse9", hashed) is True
    assert password_matches("correcthorse9", hashed.decode()) is True
    assert password_matches("wronghorse9", hashed) is False


def test_an_overlong_password_matches_nothing_rather_than_raising():
    """bcrypt 5 raises ValueError past 72 bytes; the check reads it as a
    wrong password, even one whose first 72 bytes are the right one."""
    password = "a1" + "x" * 70
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4))
    assert password_matches(password, hashed) is True
    assert password_matches(password + "x", hashed) is False
    assert password_matches("é" * 37, hashed) is False  # 74 bytes, 37 characters
