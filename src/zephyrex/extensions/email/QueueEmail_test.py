# SPDX-License-Identifier: AGPL-3.0-or-later
"""EXT_EMail.queue_email sends in the background and never loses a failure:
the caller returns at once, and a send that fails outright is logged."""

import io
import threading

from loguru import logger as loguru_logger

from zephyrex.extensions.email.EXT_EMail import EXT_EMail

# Mail goes out on a background thread; this bounds the wait for it.
_DELIVERY_TIMEOUT_SECONDS = 10


def test_a_queued_email_is_sent(monkeypatch):
    sent = []
    done = threading.Event()

    async def send_email(recipient, subject, body):
        sent.append((recipient, subject, body))
        done.set()

    monkeypatch.setattr(EXT_EMail, "send_email", send_email)
    EXT_EMail.queue_email("a@example.com", "Hello", "Body")

    assert done.wait(timeout=_DELIVERY_TIMEOUT_SECONDS)
    assert sent == [("a@example.com", "Hello", "Body")]


def test_a_failed_send_is_logged(monkeypatch):
    logged = threading.Event()
    buffer = io.StringIO()

    def sink(message):
        buffer.write(str(message))
        logged.set()

    async def send_email(recipient, subject, body):
        raise RuntimeError("no email provider is configured")

    monkeypatch.setattr(EXT_EMail, "send_email", send_email)
    sink_id = loguru_logger.add(sink, level="ERROR", format="{message}")
    try:
        EXT_EMail.queue_email("b@example.com", "Hello", "Body")
        assert logged.wait(timeout=_DELIVERY_TIMEOUT_SECONDS)
    finally:
        loguru_logger.remove(sink_id)
    assert "b@example.com" in buffer.getvalue()
    assert "no email provider is configured" in buffer.getvalue()
