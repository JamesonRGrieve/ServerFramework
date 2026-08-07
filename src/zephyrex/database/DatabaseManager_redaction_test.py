"""H-1 — connection-string passwords must not appear in logs.

``DatabaseManager`` previously logged ``database_uri`` verbatim on a
successful connect. PostgreSQL/MySQL/MariaDB/MSSQL URIs embed
``user:password@host``; the redaction helper now strips the password
before the URI reaches a log line.
"""

from __future__ import annotations


def test_redact_db_uri_strips_postgres_password():
    from zephyrex.database.DatabaseManager import _redact_db_uri

    redacted = _redact_db_uri(
        "postgresql+asyncpg://app:s3cret@db.example:5432/app"
    )
    assert "s3cret" not in redacted
    assert "app:" in redacted  # username is fine to log
    assert "***" in redacted
    assert "db.example:5432" in redacted


def test_redact_db_uri_passthrough_sqlite():
    from zephyrex.database.DatabaseManager import _redact_db_uri

    # SQLite URIs carry no secret; pass through unchanged.
    assert (
        _redact_db_uri("sqlite:///./test.db") == "sqlite:///./test.db"
    )
    assert _redact_db_uri("sqlite:///:memory:") == "sqlite:///:memory:"


def test_redact_db_uri_handles_none():
    from zephyrex.database.DatabaseManager import _redact_db_uri

    assert _redact_db_uri(None) == ""


def test_redact_db_uri_handles_garbage():
    from zephyrex.database.DatabaseManager import _redact_db_uri

    # On any parse error, the redactor must NOT fall back to returning
    # the original string — that would re-expose the password.
    out = _redact_db_uri("not://a:valid::uri@@")
    assert "valid" not in out or "***" in out
