# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Typed email-extension errors.

Item 88 — migration from `str`-encoded failures to a typed error hierarchy.

Concrete email providers (`SendgridProvider`, `StalwartProvider`,
`Smtp2goProvider`) raise these on send-path failures instead of returning
strings like ``"Failed to send email: rejected CRLF in subject"``. The
legacy `send_email(...) -> str` shim survives one release as a
deprecation-aliased wrapper that catches these exceptions and re-stringifies
them; new callers use `send(EmailMessage)` and `with pytest.raises(...)`.
"""

from __future__ import annotations

from zephyrex.extensions.ExternalErrors import (
    BaseExternalError,
    InvalidInputExternalError,
)

__all__ = [
    "EmailHeaderInjectionError",
    "EmailPayloadTooLargeError",
    "EmailMalformedAddressError",
    "EmailAttachmentTraversalError",
    "EmailAttachmentNotFoundError",
    "EmailNonAsciiAddressError",
    "EmailMissingFromAddressError",
    "EmailValidationError",
    "NotSupportedError",
    "map_validation_error",
]


class NotSupportedError(BaseExternalError):
    """Raised by an `AbstractEmailProviderInstance` ability when the bonded
    provider lacks the capability (e.g. SendGrid asked for `list_threads`).

    Per Item 95: callers should branch on `capability in instance.capabilities`
    rather than catching this; it is the safety-net for accidental calls."""

    def __init__(self, provider: str = "", capability: str = "") -> None:
        msg = (
            f"Provider {provider!r} does not support capability {capability!r}"
            if provider or capability
            else "Capability not supported by this provider"
        )
        super().__init__(msg)
        self.provider = provider
        self.capability = capability


# ---------------------------------------------------------------------------
# Typed validation errors (Item 88)
# ---------------------------------------------------------------------------


class EmailValidationError(InvalidInputExternalError):
    """Base class for input-validation failures from `_validate_send_inputs`
    and `_validate_message`. All concrete validation errors inherit from this
    so callers may catch the broad family or a specific subclass."""


class EmailHeaderInjectionError(EmailValidationError):
    """Raised when CRLF or NUL is detected in a recipient, subject, body,
    custom header, or attachment filename — the classic header-injection
    smuggling attempt."""


class EmailPayloadTooLargeError(EmailValidationError):
    """Raised when the subject exceeds RFC 5322 §2.1.1 (998 octets) or the
    body exceeds the 10 MiB cap, or an attachment's bytes exceed the cap."""


class EmailMalformedAddressError(EmailValidationError):
    """Raised when a recipient/sender/cc/bcc/reply-to/from address fails
    `parseaddr`-shape validation (missing local or domain, multiple `@`,
    empty input)."""


class EmailNonAsciiAddressError(EmailValidationError):
    """Raised when an address contains non-ASCII characters; legitimate
    IDN domains must be Punycode-encoded by the caller."""


class EmailAttachmentTraversalError(EmailValidationError):
    """Raised when an attachment path is not absolute, contains a `..`
    traversal segment, or contains a NUL byte."""


class EmailAttachmentNotFoundError(EmailValidationError):
    """Raised when an attachment file path does not resolve to a real
    file at send time."""


class EmailMissingFromAddressError(EmailValidationError):
    """Raised when the provider has no configured `from` address and the
    message did not supply one."""


# ---------------------------------------------------------------------------
# Validation-string -> typed-error mapping
# ---------------------------------------------------------------------------


# The legacy validators returned a single `str` per failure. We map the
# distinguishing substring so the typed exception carries the right type.
# Listed in priority order (more-specific first); the first matching prefix
# wins.
_VALIDATION_PREFIXES = (
    ("rejected CRLF", EmailHeaderInjectionError),
    ("rejected NUL", EmailHeaderInjectionError),
    ("rejected path traversal", EmailAttachmentTraversalError),
    ("attachment path must be absolute", EmailAttachmentTraversalError),
    ("invalid attachment", EmailAttachmentTraversalError),
    ("non-ASCII", EmailNonAsciiAddressError),
    ("subject exceeds", EmailPayloadTooLargeError),
    ("body exceeds", EmailPayloadTooLargeError),
    ("attachment exceeds", EmailPayloadTooLargeError),
    ("invalid recipient", EmailMalformedAddressError),
    ("invalid to", EmailMalformedAddressError),
    ("invalid cc", EmailMalformedAddressError),
    ("invalid bcc", EmailMalformedAddressError),
    ("invalid reply_to", EmailMalformedAddressError),
    ("invalid from", EmailMalformedAddressError),
    ("from_email", EmailMissingFromAddressError),
)


def map_validation_error(message: str) -> EmailValidationError:
    """Map a legacy validation string to its typed `EmailValidationError`.

    Used by the `send` shim that delegates to `_validate_send_inputs` /
    `_validate_message` (which still return `str`). The returned exception
    carries the original message so operators see the same diagnostic in
    logs as before; only the *type* has shifted.
    """
    text = (message or "").lower()
    # The validator strings always start with "Failed to send email: ".
    body = text.replace("failed to send email:", "").strip()
    for prefix, exc_class in _VALIDATION_PREFIXES:
        if prefix.lower() in body:
            return exc_class(message)
    return EmailValidationError(message)


def extract_status_code(message: str) -> int | None:
    """Pull the first HTTP/SMTP-style status code (1xx–5xx) out of an error
    string, if any.

    The pattern is deliberately `\\b([1-5]\\d{2})\\b`, not a bare `\\d{3}`: a
    real status code is always 1xx–5xx, so restricting the first digit avoids
    matching an incidental 3-digit run in the message (e.g. the ``998`` in
    "subject exceeds 998 octets") as if it were a status.

    Lives here (a provider-neutral module) rather than in a specific provider
    module so email providers can share it without importing each other — a
    cross-provider import at module top let discovery observe a half-imported
    provider and drop it from the cached provider set.
    """
    import re

    m = re.search(r"\b([1-5]\d{2})\b", message)
    return int(m.group(1)) if m else None
