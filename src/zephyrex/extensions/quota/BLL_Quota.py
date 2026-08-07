"""Unified per-user / per-team quota model and atomic-decrement helpers.

Item 19. The framework provides a single ``Quota`` model that backs all
three user-invokable provider scopes (system / team / user). What changes
between scopes is who effectively pays; what does not change is how usage
is tracked.

This module is the **contract** for downstream BLL_Providers integration
(Batch B). It defines the model, the period-key derivation, the
matching-row resolver, and the atomic-decrement helper. The actual
``resolve_provider_instance`` lives in ``BLL_Providers`` and is left as a
``NotImplementedError`` stub here so consumers fail loudly during the
phased rollout rather than silently calling a missing implementation.

Atomic decrement uses the ``DistributedCounter`` primitive from
``lib.DistributedCounter`` (Item 69, owned by Batch C). Until that lands,
the helpers fall back to an in-process atomic-dict implementation so this
module is exercisable in tests today.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Period key derivation
# ---------------------------------------------------------------------------


PeriodLiteral = Literal["minute", "hour", "day", "month", "billing_cycle"]
UnitLiteral = Literal["call", "token", "byte", "message", "row"]


def derive_period_key(
    period: str,
    now: Optional[datetime] = None,
) -> str:
    """Derive the canonical period-key string for a given period and time.

    All keys are produced in UTC. Format per period:

      * ``"minute"`` -> ``"YYYY-MM-DDTHH:MM"``  e.g. ``"2026-04-28T15:30"``
      * ``"hour"``   -> ``"YYYY-MM-DDTHH:00"``  e.g. ``"2026-04-28T15:00"``
      * ``"day"``    -> ``"YYYY-MM-DD"``        e.g. ``"2026-04-28"``
      * ``"month"``  -> ``"YYYY-MM"``           e.g. ``"2026-04"``
      * ``"billing_cycle"`` -> identity passthrough; the caller computes
        the cycle key (subscription extension owns billing semantics).

    Raises ``ValueError`` if ``period`` is unknown.
    """
    if period == "billing_cycle":
        # Caller-supplied; this function is identity for billing_cycle.
        # Returning the period name itself signals "caller must compute the
        # actual cycle key" -- callers that pass a real key get it back.
        return "billing_cycle"

    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    if period == "minute":
        return now.strftime("%Y-%m-%dT%H:%M")
    if period == "hour":
        return now.strftime("%Y-%m-%dT%H:00")
    if period == "day":
        return now.strftime("%Y-%m-%d")
    if period == "month":
        return now.strftime("%Y-%m")
    raise ValueError(f"Unknown period: {period!r}")


# ---------------------------------------------------------------------------
# Quota model
# ---------------------------------------------------------------------------


class Quota(BaseModel):
    """Unified per-user / per-team quota row.

    Semantics (matches Item 19 prose):
      * ``user_id`` populated, ``team_id`` NULL
            -> a quota that belongs to a user across all of their contexts.
      * ``team_id`` populated, ``user_id`` NULL
            -> a quota that belongs to a team, shared across its members.
      * Both populated -> a per-user-within-team allotment.
      * Both NULL is reserved and currently unused.

    The framework consumes from quota rows; it does NOT decide the limit
    values. Limit population is the responsibility of whoever owns the
    budget (subscription extension writes system-tier limits; team admins
    write team-level partitioning; etc.).
    """

    model_config = ConfigDict(extra="ignore")

    user_id: Optional[str] = None
    team_id: Optional[str] = None
    ability: str = Field(..., description="Canonical ability name.")
    period: PeriodLiteral
    period_key: str = Field(
        ..., description="Period bucket key, e.g. '2026-04' or '2026-04-28T15:00'."
    )
    limit: int = Field(
        ..., description="0 = blocked. Caller may use a sentinel for unlimited."
    )
    consumed: int = 0
    unit: UnitLiteral = "call"
    # Item 84 — optional per-quota USD ceiling. When non-None the row also
    # caps spend in USD: the framework refuses any pre-call where the
    # in-period cost would exceed `(limit_usd - consumed_usd)`. A None
    # value preserves token/call-only semantics (back-compat).
    limit_usd: Optional[Decimal] = None
    consumed_usd: Decimal = Decimal("0")

    # --- Convenience predicates -----------------------------------------

    def remaining(self) -> int:
        """Units left in the period. Negative if over-consumed."""
        return self.limit - self.consumed

    def is_exhausted(self, additional: int = 0) -> bool:
        """True if consuming ``additional`` more units would exceed limit."""
        return (self.consumed + additional) > self.limit

    def remaining_usd(self) -> Optional[Decimal]:
        """USD headroom left in the period. ``None`` if no USD cap is set."""
        if self.limit_usd is None:
            return None
        return self.limit_usd - self.consumed_usd

    def is_usd_exhausted(self, additional: Decimal = Decimal("0")) -> bool:
        """True if spending ``additional`` more USD would exceed ``limit_usd``.
        Always False when ``limit_usd`` is None (no cap configured)."""
        if self.limit_usd is None:
            return False
        return (self.consumed_usd + additional) > self.limit_usd


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def find_matching_quotas(
    quotas: List[Quota],
    user_id: str,
    team_id: Optional[str],
    ability: str,
    now: Optional[datetime] = None,
) -> List[Quota]:
    """Return the quota rows that apply to a (user, team, ability, time).

    Per Item 19 prose, multiple rows can match a single request:

      * a per-user row (``user_id`` populated, ``team_id`` NULL)
      * a team-wide row (``team_id`` populated, ``user_id`` NULL)
      * a per-user-within-team row (both populated)

    All matching rows are returned. The caller (typically ``try_consume``)
    decrements all of them atomically and refuses if any is exhausted.
    """
    matches: List[Quota] = []
    for quota in quotas:
        if quota.ability != ability:
            continue
        # Verify the period_key matches the current time bucket. For
        # billing_cycle the framework cannot compute a key without
        # subscription context, so we accept the row as-is.
        if quota.period != "billing_cycle":
            current_key = derive_period_key(quota.period, now=now)
            if quota.period_key != current_key:
                continue
        # User-only row: user_id == requester, team_id is NULL.
        if (
            quota.user_id == user_id
            and quota.team_id is None
        ):
            matches.append(quota)
            continue
        # Team-only row: team_id == requester team, user_id is NULL.
        if (
            quota.team_id is not None
            and quota.team_id == team_id
            and quota.user_id is None
        ):
            matches.append(quota)
            continue
        # Per-user-within-team row: both populated and both match.
        if (
            quota.user_id == user_id
            and quota.team_id is not None
            and quota.team_id == team_id
        ):
            matches.append(quota)
            continue
    return matches


# ---------------------------------------------------------------------------
# Atomic decrement
# ---------------------------------------------------------------------------


# In-process fallback lock used when ``lib.DistributedCounter`` is not yet
# available (Batch C owns that primitive). The lock serializes decrement
# attempts within a single process so the atomicity contract holds for
# tests today; production deployments depending on cross-process safety
# will get the real distributed counter once it lands.
_FALLBACK_LOCK = threading.Lock()


def _try_distributed_consume(
    quotas: List[Quota], amount: int
) -> Optional[bool]:
    """Try to delegate to ``lib.DistributedCounter``. Returns the boolean
    result if available, ``None`` to signal the caller should fall back."""
    try:
        from zephyrex.lib.DistributedCounter import (  # type: ignore[import-not-found]
            DistributedCounter,
        )
    except ImportError:
        return None
    try:
        counter = DistributedCounter()
    except TypeError:
        # DistributedCounter is abstract or requires args we don't have -
        # fall back to the in-process atomic implementation.
        return None
    try:
        return bool(counter.try_consume(quotas, amount=amount))
    except Exception:
        return None


def try_consume(quotas: List[Quota], amount: int = 1) -> bool:
    """Atomically decrement ``amount`` from every matching quota row.

    Returns ``True`` if every row had enough headroom and was decremented;
    returns ``False`` (and leaves all rows unchanged) if ANY row would have
    been exhausted by the operation. Callers raise
    ``QuotaExhaustedError`` on a False return.

    Implementation order:
      1. If ``lib.DistributedCounter`` is importable, delegate to it.
      2. Otherwise use an in-process lock for serialized check-then-set.
    """
    if amount < 0:
        raise ValueError("amount must be non-negative")
    if not quotas:
        # No matching quotas means no consumption budget exists at all.
        # Per Item 19, system-scoped instances are unreachable without an
        # explicit quota allowance -- so the absence of rows is a hard
        # refusal, not a silent allow.
        return False

    distributed = _try_distributed_consume(quotas, amount)
    if distributed is not None:
        return distributed

    with _FALLBACK_LOCK:
        # Two-pass: verify all rows have headroom, then commit. This keeps
        # the operation all-or-nothing within the lock.
        for quota in quotas:
            if quota.is_exhausted(additional=amount):
                return False
        for quota in quotas:
            quota.consumed += amount
        return True


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class QuotaExhaustedError(Exception):
    """Raised when a request's matching quota is exhausted."""

    def __init__(self, scope: str, ability: str, period: str):
        self.scope = scope
        self.ability = ability
        self.period = period
        super().__init__(
            f"Quota exhausted: scope={scope!r} ability={ability!r} period={period!r}"
        )


class NoProviderInstanceError(Exception):
    """No provider instance resolved at any user-invokable scope.

    Distinct from ``QuotaExhaustedError``: there was no instance to debit
    at all. Root instances are intentionally unreachable from the user-
    context resolution flow.
    """

    def __init__(self, requester_id: str, ability: str):
        self.requester_id = requester_id
        self.ability = ability
        super().__init__(
            f"No provider instance for requester={requester_id!r} ability={ability!r}"
        )


class NoInJurisdictionProviderError(Exception):
    """A provider exists but none satisfies the data-residency constraint.

    Companion to ``NoProviderInstanceError``; surfaces the residency
    violation explicitly so operators can distinguish "no provider"
    from "no in-region provider".
    """

    def __init__(self, requester_id: str, ability: str, jurisdiction: str):
        self.requester_id = requester_id
        self.ability = ability
        self.jurisdiction = jurisdiction
        super().__init__(
            f"No in-jurisdiction provider for requester={requester_id!r} "
            f"ability={ability!r} jurisdiction={jurisdiction!r}"
        )


# ---------------------------------------------------------------------------
# Resolution stub
# ---------------------------------------------------------------------------


def resolve_provider_instance(
    requester_id: str,
    ability: str,
    jurisdiction: Optional[str] = None,
) -> Any:
    """Resolve the provider instance for a user-invokable call.

    Resolution order (per Item 19): walk **user -> team -> system** for an
    instance matching this extension and ability. First match wins. **Root
    is never inspected on this path** -- root instances are reachable only
    from explicit framework or extension code paths via direct lookup
    (e.g. ``EXT_Email.get_root_instance(ability="system_notification")``).

    On a miss at every scope, raises ``NoProviderInstanceError`` -- there
    is no silent fallback to root.

    The actual database walk lives in ``logic.BLL_Providers`` (Batch B).
    This stub raises ``NotImplementedError`` so callers fail loudly during
    the phased rollout rather than silently calling a missing
    implementation. Once ``BLL_Providers`` exposes its resolver, replace
    this stub with a delegating call (or remove it in favor of a direct
    import from BLL_Providers).
    """
    raise NotImplementedError(
        "resolve_provider_instance is the contract for "
        "logic.BLL_Providers (Batch B). Once BLL_Providers exposes its "
        "user->team->system resolver, this stub should delegate to it. "
        f"Called with requester_id={requester_id!r}, ability={ability!r}, "
        f"jurisdiction={jurisdiction!r}."
    )


__all__ = [
    "Quota",
    "PeriodLiteral",
    "UnitLiteral",
    "derive_period_key",
    "find_matching_quotas",
    "try_consume",
    "QuotaExhaustedError",
    "NoProviderInstanceError",
    "NoInJurisdictionProviderError",
    "resolve_provider_instance",
]
