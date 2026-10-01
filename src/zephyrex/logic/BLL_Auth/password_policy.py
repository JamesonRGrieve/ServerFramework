# SPDX-License-Identifier: AGPL-3.0-or-later
"""The password rule every credential write enforces, and clients read.

The aim is to refuse trivially weak passwords ("a", "12345"), not to police
every dictionary word: defence in depth on top of bcrypt, lockout and MFA.
``max_bytes`` is bcrypt's input limit, counted in UTF-8 bytes; past it the
hash call raises instead of hashing. ``GET /v1/user/password-policy`` serves
this policy so a client can check a password before submitting it.
"""

from typing import List, Optional

from fastapi import HTTPException, status
from pydantic import BaseModel

# bcrypt hashes at most this many bytes of a password.
BCRYPT_MAX_BYTES = 72


class PasswordPolicy(BaseModel):
    min_length: int
    max_bytes: int
    require_letter: bool
    require_digit: bool


PASSWORD_POLICY = PasswordPolicy(
    min_length=8, max_bytes=BCRYPT_MAX_BYTES, require_letter=True, require_digit=True
)


def failed_rules(
    password: Optional[object], policy: PasswordPolicy = PASSWORD_POLICY
) -> List[str]:
    """The names of the rules ``password`` breaks, in policy order."""
    text = password if isinstance(password, str) else ""
    failed = []
    if len(text.strip()) == 0 or len(text) < policy.min_length:
        failed.append("min_length")
    if len(text.encode()) > policy.max_bytes:
        failed.append("max_bytes")
    if policy.require_letter and not any(c.isalpha() for c in text):
        failed.append("require_letter")
    if policy.require_digit and not any(c.isdigit() for c in text):
        failed.append("require_digit")
    return failed


def enforce_password_policy(password: Optional[object]) -> None:
    """Raise 422, naming the broken rules, unless ``password`` meets the policy."""
    failed = failed_rules(password)
    if failed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "Password does not meet the policy", "failed": failed},
        )
