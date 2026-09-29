"""Tests for `extensions.email.EmailErrors` (Item 88)."""

from __future__ import annotations

import pytest

from zephyrex.extensions.email.EmailErrors import (
    EmailAttachmentTraversalError,
    EmailHeaderInjectionError,
    EmailMalformedAddressError,
    EmailNonAsciiAddressError,
    EmailPayloadTooLargeError,
    EmailValidationError,
    map_validation_error,
)
from zephyrex.extensions.ExternalErrors import (
    BaseExternalError,
    InvalidInputExternalError,
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
