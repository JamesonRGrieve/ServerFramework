"""Unit tests for the RetentionPolicy contract surface (Item 56)."""

from __future__ import annotations

import pytest

from extensions.RetentionPolicy import (
    ARCHIVE_NONE,
    FOREVER,
    RetentionPolicy,
    forever_default,
    gdpr_default,
    hipaa_default,
    is_archived_target,
    parse_window,
    short_lived_default,
    sox_default,
)


# ---------- parse_window -----------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected_seconds",
    [
        ("1s", 1),
        ("30s", 30),
        ("5m", 300),
        ("1h", 3600),
        ("24h", 86400),
        ("1d", 86400),
        ("7d", 86400 * 7),
        ("1w", 86400 * 7),
        ("30d", 86400 * 30),
        ("1mo", 86400 * 30),
        ("3mo", 86400 * 90),
        ("1y", 86400 * 365),
        ("6y", 86400 * 365 * 6),
        ("7y", 86400 * 365 * 7),
    ],
)
def test_parse_window_valid(raw, expected_seconds):
    assert parse_window(raw) == expected_seconds


def test_parse_window_forever_returns_none():
    assert parse_window("forever") is None


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "30",
        "abc",
        "30days",
        "1.5d",
        "forever_",
        "FOREVER",  # case-sensitive on purpose
        "30dx",
        "0d 1h",
    ],
)
def test_parse_window_rejects_invalid(raw):
    with pytest.raises(ValueError):
        parse_window(raw)


# ---------- is_archived_target -----------------------------------------------


def test_archive_none_is_not_archived():
    assert not is_archived_target(ARCHIVE_NONE)


@pytest.mark.parametrize(
    "target",
    [
        "s3://bucket/audit/",
        "gs://bucket/audit/",
        "file:///var/audit/",
        "object_store://audit/",
        "azure://container/audit/",
    ],
)
def test_non_none_target_is_archived(target):
    assert is_archived_target(target)


# ---------- RetentionPolicy --------------------------------------------------


def test_default_policy_is_30d_no_archive():
    p = RetentionPolicy()
    assert p.window == "30d"
    assert p.archive_to == ARCHIVE_NONE
    assert p.legal_hold is None
    assert not p.archives()
    assert not p.is_indefinite()
    assert not p.under_legal_hold()


def test_window_seconds_round_trip():
    p = RetentionPolicy(window="1y")
    assert p.window_seconds() == 86400 * 365


def test_forever_window_returns_none_seconds():
    p = RetentionPolicy(window="forever")
    assert p.window_seconds() is None
    assert p.is_indefinite()


def test_invalid_window_raises_at_construction():
    # __post_init__ validates eagerly — invalid declarations fail at
    # class-definition / construction time rather than at the first
    # nightly retention pass.
    with pytest.raises(ValueError):
        RetentionPolicy(window="30days")


def test_legal_hold_under_hold():
    p = RetentionPolicy(window="1y", legal_hold="subpoena_2026_q1")
    assert p.under_legal_hold()
    # window is unchanged; the hold trumps it but does not override it
    assert p.window == "1y"


def test_legal_hold_releases_when_none():
    p = RetentionPolicy(window="1y")
    assert not p.under_legal_hold()


def test_archive_classification():
    p1 = RetentionPolicy(window="30d", archive_to=ARCHIVE_NONE)
    p2 = RetentionPolicy(window="1y", archive_to="s3://bucket/audit/")
    assert not p1.archives()
    assert p2.archives()


def test_dataclass_is_frozen():
    p = RetentionPolicy(window="30d")
    with pytest.raises(Exception):  # FrozenInstanceError
        p.window = "1y"  # type: ignore[misc]


# ---------- presets ----------------------------------------------------------


def test_gdpr_default_is_1y_archived():
    p = gdpr_default()
    assert p.window == "1y"
    assert p.archives()


def test_hipaa_default_is_6y_archived():
    p = hipaa_default()
    assert p.window == "6y"
    assert p.archives()


def test_sox_default_is_7y_archived():
    p = sox_default()
    assert p.window == "7y"
    assert p.archives()


def test_short_lived_default_is_30d_no_archive():
    p = short_lived_default()
    assert p.window == "30d"
    assert not p.archives()


def test_forever_default_archives_indefinitely():
    p = forever_default()
    assert p.window == FOREVER
    assert p.is_indefinite()
    assert p.archives()


# ---------- ClassVar attachment ---------------------------------------------


def test_audit_class_can_attach_retention_classvar():
    """Audit-event classes attach a `retention: ClassVar[RetentionPolicy]`
    that the retention service consults nightly. This test verifies the
    surface — the runtime service is a separate landing."""

    class AuditLoginEvent:
        retention = RetentionPolicy(
            window="1y", archive_to="s3://bucket/audit/"
        )

    assert AuditLoginEvent.retention.window == "1y"
    assert AuditLoginEvent.retention.archives()
