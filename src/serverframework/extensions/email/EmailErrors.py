"""
Typed email-extension errors.

Item 88 — migration from `str`-encoded failures to a typed error hierarchy.

Concrete email providers (`SendgridProvider`, `StalwartProvider`,
`Smtp2goProvider`) raise these on send-path failures instead of returning
strings like ``"Failed to send email: rejected CRLF in subject"``. The
legacy `send_email(...) -> str` shim survives one release as a
deprecation-aliased wrapper that catches these exceptions and re-stringifies
them; new callers use `send(EmailMessage)` and `with pytest.raises(...)`.

Imports the canonical base classes from `extensions.ExternalErrors` when
available; falls back to a local stub when not. The stub exists so the email
extension can land in isolation — Batch A's canonical hierarchy is the
authoritative source once it is wired in.
"""

from __future__ import annotations

try:
    from serverframework.extensions.ExternalErrors import (
        AuthExternalError,
        BaseExternalError,
        InvalidInputExternalError,
        RateLimitExternalError,
        TransientExternalError,
    )
except Exception:  # pragma: no cover — only when the canonical module is missing
    # Local stub. Batch A will rebase these to the canonical location.
    class BaseExternalError(Exception):
        """Stub base; replaced by `extensions.ExternalErrors.BaseExternalError`."""

    class InvalidInputExternalError(BaseExternalError):
        """Stub; replaced by canonical InvalidInputExternalError."""

    class TransientExternalError(BaseExternalError):
        """Stub; replaced by canonical TransientExternalError."""

    class RateLimitExternalError(BaseExternalError):
        """Stub; replaced by canonical RateLimitExternalError."""

    class AuthExternalError(BaseExternalError):
        """Stub; replaced by canonical AuthExternalError."""


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
    "map_upstream_status",
    "BaseExternalError",
    "InvalidInputExternalError",
    "TransientExternalError",
    "RateLimitExternalError",
    "AuthExternalError",
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


# ---------------------------------------------------------------------------
# Upstream-status mapping (Item 88)
# ---------------------------------------------------------------------------


def map_upstream_status(
    status: int,
    message: str = "",
    *,
    provider: str = "",
    retry_after_seconds: float = None,
) -> BaseExternalError:
    """Map an upstream HTTP-style status code to the typed external-error
    hierarchy.

    Mapping rules per Item 88:
        429        -> `RateLimitExternalError`
        401 / 403  -> `AuthExternalError`
        400-499    -> `InvalidInputExternalError`
        500-599    -> `TransientExternalError`
        other      -> `BaseExternalError`
    """
    kwargs = {}
    if provider:
        kwargs["provider"] = provider
    try:
        if status == 429:
            return RateLimitExternalError(
                message,
                retry_after_seconds=retry_after_seconds,
                **kwargs,
            )
        if status in (401, 403):
            return AuthExternalError(message, **kwargs)
        if 400 <= status < 500:
            return InvalidInputExternalError(message, **kwargs)
        if 500 <= status < 600:
            return TransientExternalError(message, **kwargs)
    except TypeError:
        # Stub classes don't accept the keyword args — fall through to
        # bare construction.
        pass
    if status == 429:
        return RateLimitExternalError(message)
    if status in (401, 403):
        return AuthExternalError(message)
    if 400 <= status < 500:
        return InvalidInputExternalError(message)
    if 500 <= status < 600:
        return TransientExternalError(message)
    return BaseExternalError(message)
