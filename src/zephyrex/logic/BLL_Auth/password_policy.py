# SPDX-License-Identifier: AGPL-3.0-or-later
"""The password rule every credential write enforces, and clients read.

The aim is to refuse trivially weak passwords ("a", "12345"), not to police
every dictionary word: defence in depth on top of bcrypt, lockout and MFA.
``max_bytes`` is bcrypt's input limit, counted in UTF-8 bytes; past it the
hash call raises instead of hashing. ``GET /v1/user/password-policy`` serves
this policy so a client can check a password before submitting it.
``password_matches`` is the check every password input goes through, so an
over-long one reads as a wrong password instead of a bcrypt error.
"""

from typing import List, Optional

import bcrypt
from fastapi import HTTPException, status
from pydantic import BaseModel

# bcrypt hashes at most this many bytes of a password.
BCRYPT_MAX_BYTES = 72

# What an over-long password is checked as instead, so refusing it costs the
# same bcrypt time as checking a real one. Its result is never used.
_OVERLONG_STAND_IN = b"x"


    min_length: int
    max_bytes: int
    require_letter: bool
    require_digit: bool


PASSWORD_POLICY = PasswordPolicy(
    min_length=8, max_bytes=BCRYPT_MAX_BYTES, require_letter=True, require_digit=True
)


    """The names of the rules ``password`` breaks, in policy order."""
    text = password if isinstance(password, str) else ""
    failed = []
    if len(text.strip()) == 0 or len(text) < policy.min_length:
        failed.append("min_length")
    if len(text.encode()) > policy.max_bytes:
        failed.append("max_bytes")
    if policy.require_letter and not any(c.isalpha() for c in text):
        failed.append("require_letter")
    # A decimal digit (Unicode Nd): isdigit() would also take superscripts.
    if policy.require_digit and not any(c.isdecimal() for c in text):
        failed.append("require_digit")
    return failed


    """Raise 422, naming the broken rules, unless ``password`` meets the policy."""
    failed = failed_rules(password)
    if failed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "Password does not meet the policy", "failed": failed},
        )


    """Whether ``password`` is the one ``password_hash`` was made from.

    A password past bcrypt's input limit never matches: bcrypt 5 raises on it
    rather than checking it, and every write refuses one. The stand-in is
    checked against the hash instead, so the refusal takes the time a real
    check takes and reads to the caller as a wrong password.
    """
    hashed = password_hash.encode() if isinstance(password_hash, str) else password_hash
    candidate = password.encode()
    if len(candidate) > BCRYPT_MAX_BYTES:
        return False
