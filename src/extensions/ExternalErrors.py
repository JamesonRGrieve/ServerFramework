"""
Typed external error hierarchy and rotation policy primitives.

These types are the canonical contract between the Provider Rotation system
(`RotationManager`, `AbstractStaticProvider`) and the rest of the framework
(`AbstractExternalManager`, `AbstractBLLManager`, FastAPI/Strawberry layers).

A `*_via_provider` method that opts into the typed-raise contract (see
`AbstractExternalModel.raises_typed_errors` and the `@idempotent` decorator)
returns the raw payload on success and raises one of the
`BaseExternalError` subclasses on failure. The rotation system reads
`error.failure_class` (implicit from the type) to decide whether to retry,
back off, mark the provider unhealthy, or surface the error directly to the
caller.

Item 1 — Unified result contract / typed external errors.
Item 2 — RotationPolicy dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Optional


class BaseExternalError(Exception):
    """
    Base class for all caller-facing external provider errors.

    Subclasses correspond to the four failure classes the rotation system
    distinguishes. Each subclass carries a class-level `runbook_url` that
    operators can follow when investigating a production incident.

    Attributes:
        message: Human-readable detail of the error.
        provider: Name of the provider that produced the error, when known.
        ability: Name of the ability or operation that was being performed.
        upstream_status: Numeric status code returned by the upstream, if any.
        upstream_payload: Raw payload returned by the upstream, if any.
        runbook_url: Class-level pointer to the operator runbook entry.
    """

    runbook_url: ClassVar[Optional[str]] = None

    def __init__(
        self,
        message: str = "",
        *,
        provider: Optional[str] = None,
        ability: Optional[str] = None,
        upstream_status: Optional[int] = None,
        upstream_payload: Any = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.ability = ability
        self.upstream_status = upstream_status
        self.upstream_payload = upstream_payload
        self.cause = cause

    def __str__(self) -> str:
        parts = [self.message or self.__class__.__name__]
        if self.provider:
            parts.append(f"provider={self.provider}")
        if self.ability:
            parts.append(f"ability={self.ability}")
        if self.upstream_status is not None:
            parts.append(f"status={self.upstream_status}")
        return " | ".join(parts)


class TransientExternalError(BaseExternalError):
    """5xx-shaped failures. Retried per the provider's backoff policy; on
    exhaustion advances to the next provider in the chain."""

    runbook_url: ClassVar[Optional[str]] = (
        "https://docs.serverframework.dev/runbooks/transient-external-error"
    )


class AuthExternalError(BaseExternalError):
    """401 / 403 with auth-failure semantics. Marks the provider unhealthy
    for a cool-down window and advances without consuming retry budget."""

    runbook_url: ClassVar[Optional[str]] = (
        "https://docs.serverframework.dev/runbooks/auth-external-error"
    )


class InvalidInputExternalError(BaseExternalError):
    """4xx-shaped request defects (validation, missing required fields,
    operator-detected bad arguments). Never triggers rotation."""

    runbook_url: ClassVar[Optional[str]] = (
        "https://docs.serverframework.dev/runbooks/invalid-input-external-error"
    )


class RateLimitExternalError(BaseExternalError):
    """429-shaped failures. Honors `Retry-After` (or per-provider parser
    equivalent) and backs off against the same provider with exponential
    delay and jitter; does not advance the chain.

    Carries an optional `retry_after_seconds` indicating how long the caller
    or rotation system should wait before retrying.
    """

    runbook_url: ClassVar[Optional[str]] = (
        "https://docs.serverframework.dev/runbooks/rate-limit-external-error"
    )

    def __init__(
        self,
        message: str = "",
        *,
        retry_after_seconds: Optional[float] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.retry_after_seconds = retry_after_seconds


class PermanentExternalError(BaseExternalError):
    """An upstream response indicating the operation will never succeed
    against any provider in this chain (e.g., resource permanently
    deleted, account permanently suspended). Never retried."""

    runbook_url: ClassVar[Optional[str]] = (
        "https://docs.serverframework.dev/runbooks/permanent-external-error"
    )


# Specialized errors for narrower failure modes -------------------------------


class InvalidPaginationError(InvalidInputExternalError):
    """Raised when a pagination `next_token` is malformed or the embedded
    `query_hash` does not match the new query parameters."""


class UnsupportedOperatorError(InvalidInputExternalError):
    """Raised by a query DSL translator when it is asked to translate an
    operator the upstream does not support."""


class NavigationNotIncludedError(InvalidInputExternalError):
    """Raised when an external navigation property is accessed without
    being named in the request's `include` set, while strict mode is on."""


# RotationPolicy --------------------------------------------------------------


@dataclass
class RotationPolicy:
    """
    Per-provider rotation policy describing how each failure class is
    handled by the rotation system.

    Dispatch behavior (consumed by `RotationManager`, owned by Batch B):

    - `TransientExternalError`: retry up to `transient_max_retries` times
      against the same provider with exponential backoff between
      `transient_base_ms` and `transient_max_ms` (multiplied by a random
      `transient_jitter` factor in `[1 - jitter, 1 + jitter]`). On
      exhaustion, advance to the next provider in the chain.
    - `RateLimitExternalError`: honor `error.retry_after_seconds` if
      present, else fall back to exponential backoff between
      `rate_limit_base_ms` and `rate_limit_max_ms`. Stay on the same
      provider; do not advance the chain.
    - `AuthExternalError`: mark the provider unhealthy for
      `auth_cooldown_seconds`, advance to the next provider, do not
      consume the retry budget.
    - `InvalidInputExternalError` / `PermanentExternalError`: surface
      directly to the caller; do not retry; do not advance.

    `header_parser` is an optional callable that extracts a structured
    rate-limit signal (`retry_after_seconds`, `quota_remaining`, etc.)
    from the upstream response headers when the upstream uses a
    non-standard header shape.
    """

    transient_max_retries: int = 3
    transient_base_ms: int = 100
    transient_max_ms: int = 5000
    transient_jitter: float = 0.1
    rate_limit_base_ms: int = 1000
    rate_limit_max_ms: int = 60000
    auth_cooldown_seconds: int = 300
    header_parser: Optional[Callable[[Any], dict]] = field(default=None, repr=False)


def default_rotation_policy() -> RotationPolicy:
    """Return a `RotationPolicy` populated with the framework defaults.

    Provider authors that want the standard behavior can simply omit
    `rotation_policy = ...` on their class; the framework's RotationManager
    will fall back to this factory.
    """

    return RotationPolicy()


__all__ = [
    "BaseExternalError",
    "TransientExternalError",
    "AuthExternalError",
    "InvalidInputExternalError",
    "RateLimitExternalError",
    "PermanentExternalError",
    "InvalidPaginationError",
    "UnsupportedOperatorError",
    "NavigationNotIncludedError",
    "RotationPolicy",
    "default_rotation_policy",
]
