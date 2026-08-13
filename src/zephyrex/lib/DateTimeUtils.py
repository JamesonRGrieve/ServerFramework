# SPDX-License-Identifier: AGPL-3.0-or-later
"""Datetime helper utilities shared across extensions."""

from __future__ import annotations

from datetime import datetime, timezone


def ensure_utc(dt: datetime) -> datetime:
    """Normalise *dt* to a timezone-aware UTC datetime.

    * If *dt* is naive (``tzinfo is None``), assume UTC and attach the
      ``timezone.utc`` tzinfo.
    * If *dt* is already aware, return it unchanged.

    This replaces the ``if dt.tzinfo is None: dt = dt.replace(tzinfo=
    timezone.utc)`` two-liner that was duplicated across many extension
    BLL modules.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
