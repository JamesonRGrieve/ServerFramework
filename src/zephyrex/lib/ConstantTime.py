# SPDX-License-Identifier: AGPL-3.0-or-later
"""Constant-time equality for secrets, signatures and tokens.

``hmac.compare_digest`` raises ``TypeError`` when either argument is a
``str`` with a non-ASCII character. A value a client sends (a header
Starlette decodes as latin-1, a query parameter, a token claim) can carry
one, so a check that passed it straight through answered a refusal with a
500 (#283). Comparing the UTF-8 bytes keeps the comparison constant-time
and turns any such value into a plain mismatch."""

import hmac


def _as_bytes(value: str | bytes) -> bytes:
    return value.encode("utf-8", "surrogatepass") if isinstance(value, str) else value


def secure_equals(left: str | bytes, right: str | bytes) -> bool:
    """Whether ``left`` and ``right`` are equal, compared in constant time.
    Never raises for text that isn't ASCII."""
    return hmac.compare_digest(_as_bytes(left), _as_bytes(right))
