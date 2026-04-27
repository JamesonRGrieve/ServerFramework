"""Security tests for the logging subsystem.

These tests assert that the framework's logger redacts known-sensitive
keyword arguments and structured-log fields. A sloppy extension author who
calls ``logger.info("user logged in", password=password)`` should not leak
the password to stdout — the framework must scrub it.

Most of these tests are EXPECTED FAIL today: ``src/lib/Logging.py`` does
not install any redaction filter (see file contents — only a single
``logger.add(sys.stdout, level=log_level, filter=format_with_timezone)``).
Surfacing these failures is the audit's intent.
"""

from __future__ import annotations

import io
import sys

import pytest
from loguru import logger as loguru_logger

from lib.Logging import logger


SENSITIVE_KEYS = (
    "password",
    "current_password",
    "new_password",
    "token",
    "jwt",
    "api_key",
    "secret",
    "client_secret",
    "refresh_token",
    "authorization",
)


@pytest.fixture
def captured_log_output():
    """Attach a temporary loguru sink to capture log records, then detach.

    Yields the StringIO buffer; tests read ``buffer.getvalue()``.
    """
    buffer = io.StringIO()
    sink_id = loguru_logger.add(buffer, level="DEBUG", format="{message} | {extra}")
    try:
        yield buffer
    finally:
        loguru_logger.remove(sink_id)


@pytest.mark.security
@pytest.mark.parametrize("key", SENSITIVE_KEYS)
def test_log_redacts_sensitive_kwarg(captured_log_output, key):
    """``logger.info(..., <sensitive>=<value>)`` must NOT contain the value.

    EXPECTED FAIL today — no redaction filter is installed in lib/Logging.py.
    Surfaces the gap.
    """
    canary = "CANARY-VALUE-THAT-MUST-NEVER-APPEAR-IN-LOGS"
    # Bind the sensitive kwarg via loguru's structured-extra mechanism.
    logger.bind(**{key: canary}).info(f"event with sensitive {key}")

    output = captured_log_output.getvalue()
    assert canary not in output, (
        f"Logger leaked sensitive value bound to '{key}': "
        f"value {canary!r} appeared in log output. "
        "lib/Logging.py needs a redaction filter for sensitive kwargs."
    )


@pytest.mark.security
def test_log_redacts_authorization_header_in_request_dump(captured_log_output):
    """A request object's Authorization header must not appear in logs."""
    bearer = "Bearer secret-token-CANARY-12345"
    logger.bind(headers={"Authorization": bearer}).info("incoming request")

    output = captured_log_output.getvalue()
    assert "secret-token-CANARY-12345" not in output, (
        "Authorization-header value leaked into logs. "
        "lib/Logging.py needs a request-header sanitizer."
    )


@pytest.mark.security
def test_log_redacts_password_in_message_string(captured_log_output):
    """A password embedded in the log message body must be scrubbed.

    Catches the case where a developer interpolates a password directly
    into the log string instead of passing it as a kwarg.
    """
    canary = "literal-password-CANARY-67890"
    logger.info(f"login attempt with password={canary}")

    output = captured_log_output.getvalue()
    assert canary not in output, (
        "Logger emitted a literal password embedded in message text. "
        "lib/Logging.py needs a regex-based scrubber for inline secrets."
    )
