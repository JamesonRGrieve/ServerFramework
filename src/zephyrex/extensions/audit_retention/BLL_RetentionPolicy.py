"""RetentionPolicy contract for audit-log retention and archival (Item 56).

Each audit-event class declares a ``retention: ClassVar[RetentionPolicy]``
that the scheduled retention service consults nightly. The policy
carries a window (``"30d"``, ``"1y"``, ``"7y"``, ``"forever"``), an
archival target identifier (an opaque string interpreted by the
deployment's archive backend — e.g. ``"s3://bucket/audit/"``,
``"gs://bucket/audit/"``, ``"file:///var/audit/"``, or ``"none"`` for
purge-without-archive), and an optional ``legal_hold`` field that
prevents purge regardless of the window for as long as it is set.

The retention service (a ``ScheduledService`` from Item 28) walks every
entity tagged with a non-default policy nightly, archives expired rows
to the configured target as compressed integrity-checked artifacts, and
then purges from the live audit table. **Archival is non-skippable for
events with non-``none`` archival targets** — the purge step refuses to
run if archival did not succeed for any event in the batch.

Cross-references:
  - Item 28 — `ScheduledService` is the runtime that drives the
    nightly retention pass.
  - Item 43 — object-storage abstract provider supplies the archive
    target adapters (S3, GCS, Azure Blob, local).
  - Item 82 — PII / right-to-erasure orchestration interacts with
    retention windows; legal hold trumps both.

This module ships the type surface only. The runtime archive + purge
service plus the audit-of-the-audit emission is a separate landing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Retention windows
# ---------------------------------------------------------------------------

# Recognized window suffixes. Values are integer seconds.
_WINDOW_SUFFIX_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 24 * 60 * 60,
    "w": 7 * 24 * 60 * 60,
    "mo": 30 * 24 * 60 * 60,  # 30-day month is the framework convention
    "y": 365 * 24 * 60 * 60,  # 365-day year is the framework convention
}

_WINDOW_RE = re.compile(r"^(\d+)(s|m|h|d|w|mo|y)$")

FOREVER = "forever"


def parse_window(window: str) -> Optional[int]:
    """Parse a retention window string to seconds.

    Returns:
        The window length in seconds, or ``None`` for the special
        ``"forever"`` value (meaning "never expires"). Invalid windows
        raise ``ValueError``.

    Examples:
        ``parse_window("30d")`` returns ``2_592_000``.
        ``parse_window("1y")`` returns ``31_536_000``.
        ``parse_window("forever")`` returns ``None``.
        ``parse_window("90d")`` returns ``7_776_000``.
    """
    if window == FOREVER:
        return None
    match = _WINDOW_RE.match(window)
    if not match:
        raise ValueError(
            f"Invalid retention window {window!r}; expected '<number><suffix>' "
            f"with suffix in {sorted(_WINDOW_SUFFIX_SECONDS.keys())} or "
            f"the literal {FOREVER!r}"
        )
    quantity, suffix = match.groups()
    return int(quantity) * _WINDOW_SUFFIX_SECONDS[suffix]


# ---------------------------------------------------------------------------
# Archive target
# ---------------------------------------------------------------------------

ARCHIVE_NONE = "none"


def is_archived_target(target: str) -> bool:
    """Return True iff ``target`` denotes a non-``none`` archive target.

    The retention service uses this to decide whether to refuse the
    purge step on archive failure.
    """
    return target != ARCHIVE_NONE


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetentionPolicy:
    """Item 56 — per-class retention contract.

    Fields:
        window: retention window string. Recognized formats:
            ``"<number><suffix>"`` where suffix is one of
            ``s``/``m``/``h``/``d``/``w``/``mo``/``y``, or the literal
            ``"forever"``. Examples: ``"30d"``, ``"1y"``, ``"7y"``,
            ``"forever"``. Default ``"30d"``.
        archive_to: opaque target identifier. The framework recognizes
            ``"none"`` to mean "purge without archive" (only valid for
            short-window non-regulated logs); any other value is passed
            verbatim to the deployment's archive backend (Item 43's
            object-storage abstract). Default ``"none"``.
        legal_hold: optional reason string. While non-None, the
            retention service refuses to purge entries of this class
            regardless of the window — used for regulatory holds
            ("subpoena_2026_q1") that must outlast normal retention.
            Default ``None``.

    The dataclass is frozen so a shared policy instance cannot be
    mutated by one caller and quietly change retention behavior for
    other callers that share the same default policy.
    """

    window: str = "30d"
    archive_to: str = ARCHIVE_NONE
    legal_hold: Optional[str] = None

    def __post_init__(self) -> None:
        # Eagerly validate the window so an invalid declaration raises
        # at class-definition time rather than at the first nightly
        # retention pass against the live audit table.
        parse_window(self.window)

    def window_seconds(self) -> Optional[int]:
        """Return the window in seconds, or ``None`` for ``forever``."""
        return parse_window(self.window)

    def is_indefinite(self) -> bool:
        """Return True iff this policy never expires
        (``window="forever"``)."""
        return self.window == FOREVER

    def archives(self) -> bool:
        """Return True iff entries are archived before purge (rather
        than purge-without-archive)."""
        return is_archived_target(self.archive_to)

    def under_legal_hold(self) -> bool:
        """Return True iff a legal hold is currently in effect.

        The retention service consults this on every pass — purge is
        suppressed for as long as the hold is non-None. Releasing the
        hold is a deliberate operator action that emits its own audit
        event (audit-of-the-audit, with its own typically-``forever``
        retention policy)."""
        return self.legal_hold is not None


def gdpr_default() -> RetentionPolicy:
    """A reasonable GDPR-defensible default: 1-year retention with
    object-storage archive. Operators override per-class as their
    regulatory regime requires."""
    return RetentionPolicy(window="1y", archive_to="object_store://audit/")


def hipaa_default() -> RetentionPolicy:
    """HIPAA-defensible default: 6-year retention with archive (HIPAA
    minimum is 6 years from creation or last effective date)."""
    return RetentionPolicy(window="6y", archive_to="object_store://audit/")


def sox_default() -> RetentionPolicy:
    """Sarbanes-Oxley default: 7-year retention with archive."""
    return RetentionPolicy(window="7y", archive_to="object_store://audit/")


def short_lived_default() -> RetentionPolicy:
    """30-day no-archive default for short-lived non-regulated logs."""
    return RetentionPolicy(window="30d", archive_to=ARCHIVE_NONE)


def forever_default() -> RetentionPolicy:
    """Indefinite retention, used for the audit-of-the-audit (every
    retention pass emits its own event whose retention is itself
    typically ``forever``)."""
    return RetentionPolicy(window=FOREVER, archive_to="object_store://audit/")


__all__ = [
    "ARCHIVE_NONE",
    "FOREVER",
    "RetentionPolicy",
    "forever_default",
    "gdpr_default",
    "hipaa_default",
    "is_archived_target",
    "parse_window",
    "short_lived_default",
    "sox_default",
]
