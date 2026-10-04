# SPDX-License-Identifier: AGPL-3.0-or-later
"""The inbound mail hook point: real RFC 5322 messages, parsed, and handed to
every listener in turn."""

from email.message import EmailMessage
from typing import Any, List

import pytest

from zephyrex.extensions.email.InboundEmail import (
    MAX_INBOUND_EMAIL_BYTES,
    InboundEmail,
    on_inbound_email,
    receive_inbound_email,
    remove_inbound_email_listener,
)


def raw_message(**headers: str) -> bytes:
    message = EmailMessage()
    for name, value in headers.items():
        message[name.replace("_", "-")] = value
    message.set_content("Plain words.")
    message.add_alternative("<p>Marked-up words.</p>", subtype="html")
    return message.as_bytes()


def test_a_message_is_parsed():
    parsed = InboundEmail.parse(
        raw_message(
            From="Ada <Ada@Example.org>",
            To="One <one@example.net>, two@example.net",
            Cc="three@example.net",
            Subject="Status",
            Message_ID="<m1@example.org>",
        ),
        envelope_recipients=["Four@Example.net", "one@example.net"],
    )
    assert parsed.sender == "ada@example.org"
    assert parsed.recipients == [
        "four@example.net",
        "one@example.net",
        "two@example.net",
        "three@example.net",
    ]
    assert (parsed.subject, parsed.message_id) == ("Status", "<m1@example.org>")
    assert parsed.text.strip() == "Plain words."
    assert parsed.headers["Subject"] == "Status"


def test_an_html_only_message_reads_its_html():
    message = EmailMessage()
    message["From"] = "a@example.org"
    message["To"] = "b@example.org"
    message.set_content("<p>Only markup.</p>", subtype="html")
    assert "Only markup." in InboundEmail.parse(message.as_bytes()).text


def test_a_body_in_an_unknown_charset_is_read_not_refused():
    """Mail is whatever a sender wrote: a body labelled with a charset
    Python does not know raised LookupError out of parse, which the inbound
    endpoint answered with a 500 and the poller dropped."""
    raw = (
        b"From: a@example.org\r\nTo: b@example.org\r\nSubject: Odd\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: text/plain; charset=x-no-such-charset\r\n\r\n"
        b"Words \xff here.\r\n"
    )
    parsed = InboundEmail.parse(raw)
    assert parsed.subject == "Odd"
    assert "Words" in parsed.text and "here." in parsed.text


def test_an_oversized_message_is_refused():
    with pytest.raises(ValueError):
        InboundEmail.parse(b"x" * (MAX_INBOUND_EMAIL_BYTES + 1))


async def test_every_listener_gets_the_message_and_one_failure_spares_the_rest():
    seen: List[Any] = []

    async def failing(registry: Any, message: InboundEmail) -> None:
        raise RuntimeError("listener broke")

    async def recording(registry: Any, message: InboundEmail) -> None:
        seen.append((registry, message.subject))

    on_inbound_email(failing)
    on_inbound_email(recording)
    on_inbound_email(recording)  # registering twice registers once
    try:
        message = InboundEmail.parse(raw_message(From="a@x.org", Subject="Hi"))
        taken = await receive_inbound_email("registry", message)
    finally:
        remove_inbound_email_listener(failing)
        remove_inbound_email_listener(recording)
    assert seen == [("registry", "Hi")]
    assert taken >= 1
