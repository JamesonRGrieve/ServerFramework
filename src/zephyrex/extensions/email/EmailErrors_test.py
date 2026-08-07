"""Tests for `extensions.email.EmailErrors` (Item 88)."""

from __future__ import annotations

import pytest

from zephyrex.extensions.email.EmailErrors import (
    AuthExternalError,
    BaseExternalError,
    EmailAttachmentTraversalError,
    EmailHeaderInjectionError,
    EmailMalformedAddressError,
    EmailNonAsciiAddressError,
    EmailPayloadTooLargeError,
    EmailValidationError,
    InvalidInputExternalError,
    RateLimitExternalError,
    TransientExternalError,
    map_upstream_status,
    map_validation_error,
)


class TestErrorHierarchy:
    def test_validation_subclass_chain(self):
        assert issubclass(EmailHeaderInjectionError, EmailValidationError)
        assert issubclass(EmailValidationError, InvalidInputExternalError)
        assert issubclass(InvalidInputExternalError, BaseExternalError)

    def test_concrete_validation_errors_are_invalid_input(self):
        for cls in (
            EmailHeaderInjectionError,
            EmailPayloadTooLargeError,
            EmailMalformedAddressError,
            EmailAttachmentTraversalError,
            EmailNonAsciiAddressError,
        ):
            assert issubclass(cls, InvalidInputExternalError), cls.__name__


class TestMapValidationError:
    def test_crlf_to_header_injection(self):
        err = map_validation_error("Failed to send email: rejected CRLF in subject")
        assert isinstance(err, EmailHeaderInjectionError)

    def test_nul_to_header_injection(self):
        err = map_validation_error("Failed to send email: rejected NUL byte in input")
        assert isinstance(err, EmailHeaderInjectionError)

    def test_subject_too_large(self):
        err = map_validation_error("Failed to send email: subject exceeds 998 octets")
        assert isinstance(err, EmailPayloadTooLargeError)

    def test_body_too_large(self):
        err = map_validation_error("Failed to send email: body exceeds 10 MiB cap")
        assert isinstance(err, EmailPayloadTooLargeError)

    def test_invalid_recipient(self):
        err = map_validation_error("Failed to send email: invalid recipient address")
        assert isinstance(err, EmailMalformedAddressError)

    def test_path_traversal(self):
        err = map_validation_error(
            "Failed to send email: rejected path traversal in attachment"
        )
        assert isinstance(err, EmailAttachmentTraversalError)

    def test_non_absolute_path(self):
        err = map_validation_error(
            "Failed to send email: attachment path must be absolute"
        )
        assert isinstance(err, EmailAttachmentTraversalError)

    def test_non_ascii(self):
        err = map_validation_error(
            "Failed to send email: non-ASCII recipient (suspected homograph)"
        )
        assert isinstance(err, EmailNonAsciiAddressError)

    def test_unrecognized_falls_back_to_base(self):
        err = map_validation_error("Failed to send email: something weird")
        assert isinstance(err, EmailValidationError)


class TestMapUpstreamStatus:
    def test_429_to_rate_limit(self):
        err = map_upstream_status(429, "throttled")
        assert isinstance(err, RateLimitExternalError)

    def test_401_to_auth(self):
        err = map_upstream_status(401, "bad key")
        assert isinstance(err, AuthExternalError)

    def test_403_to_auth(self):
        assert isinstance(map_upstream_status(403, ""), AuthExternalError)

    def test_400_to_invalid_input(self):
        err = map_upstream_status(400, "bad request")
        assert isinstance(err, InvalidInputExternalError)
        # 4xx other than 401/403/429 should NOT be auth/rate-limit
        assert not isinstance(err, AuthExternalError)
        assert not isinstance(err, RateLimitExternalError)

    def test_503_to_transient(self):
        err = map_upstream_status(503, "down")
        assert isinstance(err, TransientExternalError)

    def test_unknown_to_base(self):
        err = map_upstream_status(999, "weird")
        assert isinstance(err, BaseExternalError)
