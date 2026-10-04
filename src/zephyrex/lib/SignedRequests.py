# SPDX-License-Identifier: AGPL-3.0-or-later
"""Requests signed with a shared secret, as Zephyrex's inbound endpoints
take them (agent webhooks, inbound mail).

A signed request carries :data:`TIMESTAMP_HEADER` (Unix seconds, ASCII
digits) and :data:`SIGNATURE_HEADER`: :data:`SIGNATURE_PREFIX` and the
lower-case hex HMAC-SHA256, keyed by the secret, of the endpoint's signed
content. What is signed differs by endpoint, so each declares its layout
(:data:`SignedContent`); it always holds the timestamp and the body.

A request is accepted only when its signature is right (compared in
constant time), its timestamp is within :data:`REPLAY_WINDOW_SECONDS` of
now, and the same signature has not been accepted for the same signer
within that window (the shared replay cache). Every refusal is the same
401, so the endpoint says nothing about which signers exist or why a
request failed.
"""

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Mapping, Optional, Tuple

from fastapi import HTTPException

from zephyrex.lib.ReplayCache import get_replay_cache

TIMESTAMP_HEADER = "X-Zephyrex-Timestamp"
SIGNATURE_HEADER = "X-Zephyrex-Signature"
SIGNATURE_PREFIX = "sha256="
REPLAY_WINDOW_SECONDS = 300
UNSIGNED_DETAIL = "Signature verification failed"

# What a request signs: from its timestamp (as sent), its headers (keyed
# lower-case) and its raw body.
SignedContent = Callable[[str, Mapping[str, str], bytes], bytes]


def hmac_signature(secret: str, content: bytes) -> str:
    """The :data:`SIGNATURE_HEADER` value of ``content`` signed with
    ``secret``."""
    digest = hmac.new(secret.encode(), content, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def lower_case_headers(headers: Iterable[Tuple[str, str]]) -> Dict[str, str]:
    """``headers`` (name and value pairs) keyed by lower-case name."""
    return {name.lower(): value for name, value in headers}


def unsigned() -> HTTPException:
    """The one answer to every request that is not properly signed."""
    return HTTPException(status_code=401, detail=UNSIGNED_DETAIL)


def fresh(timestamp: str, now: float) -> bool:
    """Whether ``timestamp`` is Unix seconds in ASCII digits within
    :data:`REPLAY_WINDOW_SECONDS` of ``now``."""
    return (
        timestamp.isascii()
        and timestamp.isdigit()
        and abs(now - int(timestamp)) <= REPLAY_WINDOW_SECONDS
    )


@dataclass(frozen=True)
class SignedRequests:
    """One endpoint's signed requests: its ``replay_scope`` (keeping its
    signatures apart from other endpoints') and the layout of what its
    requests sign."""

    replay_scope: str
    signed_content: SignedContent

    def signature(
        self, secret: str, timestamp: str, headers: Mapping[str, str], body: bytes
    ) -> str:
        """The signature of a request: ``headers`` keyed lower-case."""
        return hmac_signature(secret, self.signed_content(timestamp, headers, body))

    def verified(
        self, secret: str, headers: Mapping[str, str], body: bytes, now: float
    ) -> Optional[str]:
        """The request's signature when it is right and its timestamp
        fresh, else None. ``headers`` are keyed lower-case."""
        timestamp = headers.get(TIMESTAMP_HEADER.lower(), "")
        if not fresh(timestamp, now):
            return None
        signature = headers.get(SIGNATURE_HEADER.lower(), "")
        expected = self.signature(secret, timestamp, headers, body)
        if not hmac.compare_digest(expected.encode(), signature.encode()):
            return None
        return signature

    def accept(
        self,
        secret: str,
        signer_id: str,
        headers: Mapping[str, str],
        body: bytes,
        now: Optional[float] = None,
    ) -> None:
        """Accept a request ``signer_id``'s ``secret`` signs, once: refused
        (:func:`unsigned`) unless it verifies and its signature is new for
        the signer within the window. ``headers`` are keyed lower-case;
        ``now`` defaults to the current time."""
        signature = self.verified(
            secret, headers, body, time.time() if now is None else now
        )
        if signature is None or not get_replay_cache().mark_if_unused(
            f"{self.replay_scope}:{signer_id}:{signature}", REPLAY_WINDOW_SECONDS
        ):
            raise unsigned()
