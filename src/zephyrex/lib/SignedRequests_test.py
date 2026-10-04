# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib
import hmac
import time
import uuid
from typing import Dict, Mapping

import pytest
from fastapi import HTTPException

from zephyrex.lib.SignedRequests import (
    REPLAY_WINDOW_SECONDS,
    SIGNATURE_HEADER,
    SIGNATURE_PREFIX,
    TIMESTAMP_HEADER,
    UNSIGNED_DETAIL,
    SignedRequests,
    fresh,
    hmac_signature,
    lower_case_headers,
    unsigned,
)

SECRET = "a-shared-secret-of-some-length"
CONTEXT_HEADER = "x-context"
NOW = 1_700_000_000.0
BODY = b'{"event": "ping"}'


def _content(timestamp: str, headers: Mapping[str, str], body: bytes) -> bytes:
    """A layout that signs a header besides the timestamp and body."""
    context = headers.get(CONTEXT_HEADER, "")
    return b"\n".join((timestamp.encode(), context.encode(), body))


SIGNING = SignedRequests(replay_scope="test:signed", signed_content=_content)


def signed(
    timestamp: str = str(int(NOW)), context: str = "", body: bytes = BODY
) -> Dict[str, str]:
    """Lower-case headers of a request signed with :data:`SECRET`."""
    headers = {TIMESTAMP_HEADER.lower(): timestamp, CONTEXT_HEADER: context}
    headers[SIGNATURE_HEADER.lower()] = SIGNING.signature(
        SECRET, timestamp, headers, body
    )
    return headers


def signer() -> str:
    """A signer no other test uses: the replay cache is process-wide."""
    return uuid.uuid4().hex


def test_a_signature_is_the_prefixed_hex_hmac_sha256_of_the_content():
    expected = hmac.new(SECRET.encode(), b"content", hashlib.sha256).hexdigest()
    assert hmac_signature(SECRET, b"content") == f"{SIGNATURE_PREFIX}{expected}"


def test_the_endpoints_layout_is_what_is_signed():
    headers = signed(context="ctx")
    assert headers[SIGNATURE_HEADER.lower()] == hmac_signature(
        SECRET, _content(str(int(NOW)), headers, BODY)
    )


def test_a_right_signature_with_a_fresh_timestamp_verifies():
    headers = signed()
    assert SIGNING.verified(SECRET, headers, BODY, NOW) == (
        headers[SIGNATURE_HEADER.lower()]
    )


def test_the_window_holds_its_edges():
    for moment in (int(NOW) - REPLAY_WINDOW_SECONDS, int(NOW) + REPLAY_WINDOW_SECONDS):
        assert SIGNING.verified(SECRET, signed(str(moment)), BODY, NOW) is not None


def test_a_stale_timestamp_is_refused():
    stale = str(int(NOW) - REPLAY_WINDOW_SECONDS - 1)
    assert SIGNING.verified(SECRET, signed(stale), BODY, NOW) is None


def test_a_future_timestamp_is_refused():
    future = str(int(NOW) + REPLAY_WINDOW_SECONDS + 1)
    assert SIGNING.verified(SECRET, signed(future), BODY, NOW) is None


@pytest.mark.parametrize(
    "timestamp", ["", "soon", "-5", "+1700000000", " 1700000000", "1_700_000_000"]
)
def test_a_timestamp_that_is_not_unix_seconds_in_digits_is_refused(timestamp: str):
    assert not fresh(timestamp, NOW)
    assert SIGNING.verified(SECRET, signed(timestamp), BODY, NOW) is None


@pytest.mark.parametrize("timestamp", ["²", "1700000000²", "١٢"])
def test_digits_that_are_not_ascii_are_refused_without_raising(timestamp: str):
    """'²' passes ``str.isdigit`` but not ``int``; Arabic-Indic digits pass
    both. Neither is a timestamp a signer sends."""
    assert not fresh(timestamp, NOW)
    assert SIGNING.verified(SECRET, signed(timestamp), BODY, NOW) is None


def test_a_tampered_body_is_refused():
    assert SIGNING.verified(SECRET, signed(), BODY + b" ", NOW) is None


def test_a_tampered_signed_header_is_refused():
    headers = signed(context="a@example.org")
    headers[CONTEXT_HEADER] = "b@example.org"
    assert SIGNING.verified(SECRET, headers, BODY, NOW) is None


def test_a_tampered_timestamp_is_refused():
    headers = signed()
    headers[TIMESTAMP_HEADER.lower()] = str(int(NOW) - 1)
    assert SIGNING.verified(SECRET, headers, BODY, NOW) is None


def test_a_tampered_or_foreign_signature_is_refused():
    headers = signed()
    headers[SIGNATURE_HEADER.lower()] = SIGNATURE_PREFIX + "0" * 64
    assert SIGNING.verified(SECRET, headers, BODY, NOW) is None
    assert SIGNING.verified("another-secret", signed(), BODY, NOW) is None
    bare = signed()
    bare[SIGNATURE_HEADER.lower()] = bare[SIGNATURE_HEADER.lower()].removeprefix(
        SIGNATURE_PREFIX
    )
    assert SIGNING.verified(SECRET, bare, BODY, NOW) is None


def test_a_missing_signature_or_timestamp_is_refused():
    headers = signed()
    del headers[SIGNATURE_HEADER.lower()]
    assert SIGNING.verified(SECRET, headers, BODY, NOW) is None
    headers = signed()
    del headers[TIMESTAMP_HEADER.lower()]
    assert SIGNING.verified(SECRET, headers, BODY, NOW) is None


def test_accept_takes_a_signed_request_once():
    who = signer()
    headers = signed()
    SIGNING.accept(SECRET, who, headers, BODY, NOW)
    with pytest.raises(HTTPException) as replayed:
        SIGNING.accept(SECRET, who, headers, BODY, NOW)
    assert replayed.value.status_code == 401
    assert replayed.value.detail == UNSIGNED_DETAIL


def test_a_signature_is_used_once_per_signer_and_per_endpoint():
    headers = signed()
    SIGNING.accept(SECRET, signer(), headers, BODY, NOW)
    SIGNING.accept(SECRET, signer(), headers, BODY, NOW)
    other = SignedRequests(replay_scope="test:other", signed_content=_content)
    who = signer()
    SIGNING.accept(SECRET, who, headers, BODY, NOW)
    other.accept(SECRET, who, headers, BODY, NOW)


def test_accept_defaults_to_the_current_time():
    headers = signed(str(int(time.time())))
    SIGNING.accept(SECRET, signer(), headers, BODY)
    with pytest.raises(HTTPException):
        SIGNING.accept(SECRET, signer(), signed(), BODY)


def test_every_refusal_is_the_same_401():
    who = signer()
    stale = signed(str(int(NOW) - REPLAY_WINDOW_SECONDS - 1))
    tampered = signed()
    tampered[SIGNATURE_HEADER.lower()] = SIGNATURE_PREFIX + "0" * 64
    for headers in (stale, tampered, {}):
        with pytest.raises(HTTPException) as refused:
            SIGNING.accept(SECRET, who, headers, BODY, NOW)
        assert (refused.value.status_code, refused.value.detail) == (
            unsigned().status_code,
            unsigned().detail,
        )
    assert (unsigned().status_code, unsigned().detail) == (401, UNSIGNED_DETAIL)


def test_a_refused_request_does_not_use_up_its_signature():
    who = signer()
    headers = signed()
    with pytest.raises(HTTPException):
        SIGNING.accept(SECRET, who, headers, BODY + b"x", NOW)
    SIGNING.accept(SECRET, who, headers, BODY, NOW)


def test_lower_case_headers_keys_by_lower_case_name():
    assert lower_case_headers([("X-Zephyrex-Timestamp", "1"), ("A", "b")]) == {
        "x-zephyrex-timestamp": "1",
        "a": "b",
    }
