# SPDX-License-Identifier: AGPL-3.0-or-later
"""The signed inbound endpoint, called the way a mail server calls it: real
HTTP requests to the app, carrying a raw RFC 5322 message signed with the
secret set on a real provider instance, and every way a delivery is refused.

Before this, nothing fed the inbound hook point: no endpoint took mail."""

import hmac
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.email.BLL_InboundEmail import (
    INBOUND_PREFIX,
    INBOUND_RATE_LIMIT,
)
from zephyrex.extensions.email.EXT_EMail import EXT_EMail
from zephyrex.extensions.email.InboundEmail import (
    MAX_INBOUND_EMAIL_BYTES,
    InboundEmail,
)
from zephyrex.extensions.email.InboundTestSupport import (
    listening,
    operator_instance,
    raw_message,
)
from zephyrex.extensions.email.InboundEndpoint import (
    MAX_ENVELOPE_RECIPIENTS,
    MIN_SIGNING_SECRET_LENGTH,
    RECIPIENTS_HEADER,
    SIGNING_SECRET_SETTING,
    capped_body,
    envelope_recipients,
    inbound_signature,
    verified,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.SecretEncryption import decrypt_secret
from zephyrex.lib.SignedRequests import (
    REPLAY_WINDOW_SECONDS,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    UNSIGNED_DETAIL,
)
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceSettingModel,
)

SECRET = "s" * MIN_SIGNING_SECRET_LENGTH + "-operator-secret"


def signed_headers(
    body: bytes,
    recipients: str = "",
    *,
    secret: str = SECRET,
    timestamp: Optional[int] = None,
) -> Dict[str, str]:
    moment = str(int(time.time()) if timestamp is None else timestamp)
    return {
        "Content-Type": "message/rfc822",
        TIMESTAMP_HEADER: moment,
        RECIPIENTS_HEADER: recipients,
        SIGNATURE_HEADER: inbound_signature(secret, moment, recipients, body),
    }


def assert_unsigned(answered: Any) -> None:
    """The one refusal every unsigned delivery gets (the app's error body
    carries the detail as its message)."""
    assert answered.status_code == 401, answered.text
    assert answered.json()["detail"]["message"] == UNSIGNED_DETAIL


@pytest.fixture
def received() -> Iterator[List[InboundEmail]]:
    with listening() as seen:
        yield seen


# -- the signing format operators implement ----------------------------------


def test_the_signature_is_the_documented_hmac():
    body = b"Subject: x\r\n\r\nbody"
    expected = hmac.new(
        SECRET.encode(), b"1700000000\na@example.org,b@example.org\n" + body, "sha256"
    ).hexdigest()
    assert (
        inbound_signature(SECRET, "1700000000", "a@example.org,b@example.org", body)
        == f"sha256={expected}"
    )


def test_recipients_and_body_cannot_be_re_split_under_one_signature():
    """With a separator a recipient may contain, 'a@x.org' + '.body' and
    'a@x' + 'org.body' would sign the same bytes."""
    assert inbound_signature(SECRET, "1", "a@x.org", b"body") != inbound_signature(
        SECRET, "1", "a@x", b"org\nbody"
    )


def test_verified_refuses_a_stale_a_future_and_a_malformed_timestamp():
    body = b"m"
    now = 1_700_000_000.0
    for moment in (
        str(int(now) - REPLAY_WINDOW_SECONDS - 1),
        str(int(now) + REPLAY_WINDOW_SECONDS + 1),
        "-5",
        "soon",
    ):
        headers = {
            TIMESTAMP_HEADER.lower(): moment,
            SIGNATURE_HEADER.lower(): inbound_signature(SECRET, moment, "", body),
        }
        assert verified(SECRET, headers, body, now) is None
    fresh = str(int(now) - REPLAY_WINDOW_SECONDS)
    good = inbound_signature(SECRET, fresh, "", body)
    headers = {TIMESTAMP_HEADER.lower(): fresh, SIGNATURE_HEADER.lower(): good}
    assert verified(SECRET, headers, body, now) == good


def test_verified_refuses_a_timestamp_of_digits_that_are_not_ascii():
    """'²' is a digit to ``str.isdigit`` but not to ``int``: it was a 500
    (a ValueError) where every unsigned delivery is a 401."""
    body = b"m"
    for moment in ("²", "1700000000²"):
        headers = {
            TIMESTAMP_HEADER.lower(): moment,
            SIGNATURE_HEADER.lower(): inbound_signature(SECRET, moment, "", body),
        }
        assert verified(SECRET, headers, body, 1_700_000_000.0) is None


def test_envelope_recipients_are_bare_addresses():
    assert envelope_recipients(" a@example.org, B@Example.org ,") == [
        "a@example.org",
        "B@Example.org",
    ]
    assert envelope_recipients("") == []
    for bad in (
        "Name <a@example.org>",
        "no-at-sign",
        "a b@example.org",
        "@example.org",
        "a@",
        "ü@example.org",
        "a@" + "x" * 260 + ".org",
    ):
        with pytest.raises(ValueError):
            envelope_recipients(bad)
    with pytest.raises(ValueError):
        envelope_recipients(
            ",".join(f"r{n}@example.org" for n in range(MAX_ENVELOPE_RECIPIENTS + 1))
        )


async def test_a_body_streaming_past_the_cap_is_refused_as_it_arrives() -> None:
    """No Content-Length: the cap holds as the chunks come in."""
    chunks = [b"x" * 600, b"x" * 600, b"never read"]
    read: List[bytes] = []

    async def receive() -> Dict[str, Any]:
        chunk = chunks.pop(0)
        read.append(chunk)
        return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}

    request = Request(
        {"type": "http", "method": "POST", "path": "/", "headers": []}, receive
    )
    with pytest.raises(HTTPException) as refused:
        await capped_body(request, limit=1000)
    assert refused.value.status_code == 413
    assert b"never read" not in read


async def test_a_declared_length_past_the_cap_is_refused_unread() -> None:
    async def receive() -> Dict[str, Any]:
        raise AssertionError("the body was read")

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"content-length", str(MAX_INBOUND_EMAIL_BYTES + 1).encode())],
        },
        receive,
    )
    with pytest.raises(HTTPException) as refused:
        await capped_body(request)
    assert refused.value.status_code == 413


# -- the endpoint --------------------------------------------------------------


class TestInboundEndpoint(ExtensionServerMixin):
    extension_class = EXT_EMail

    def _deliver(
        self, server: Any, instance_id: str, body: bytes, headers: Dict[str, str]
    ) -> Any:
        return server.post(
            f"{INBOUND_PREFIX}/{instance_id}", content=body, headers=headers
        )

    def test_a_signed_message_reaches_the_inbound_listeners(
        self, server, model_registry, received
    ):
        instance = operator_instance(model_registry, {SIGNING_SECRET_SETTING: SECRET})
        body = raw_message(subject="Signed", to="desk@example.org")
        answered = self._deliver(
            server,
            instance.id,
            body,
            signed_headers(body, "Hidden@Example.org, desk@example.org"),
        )
        assert answered.status_code == 200, answered.text
        [message] = received
        assert answered.json() == {"message_id": message.message_id, "listeners": 1}
        assert message.subject == "Signed"
        assert message.sender == "sender@example.net"
        # The envelope recipients (a Bcc the headers never show) come first.
        assert message.recipients == ["hidden@example.org", "desk@example.org"]

    def test_the_secret_is_write_only_and_encrypted(self, model_registry):
        instance = operator_instance(model_registry, {SIGNING_SECRET_SETTING: SECRET})
        [row] = ProviderInstanceSettingModel.DB(model_registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            return_type="dto",
            override_dto=ProviderInstanceSettingModel,
            provider_instance_id=instance.id,
            key=SIGNING_SECRET_SETTING,
        )
        assert row.write_only is True
        assert row.value != SECRET and decrypt_secret(row.value) == SECRET
        assert SECRET not in str(row.model_dump())

    @pytest.mark.parametrize(
        "tamper",
        ["signature", "body", "recipients", "secret", "stale", "future", "missing"],
    )
    def test_a_badly_signed_delivery_is_refused_saying_nothing(
        self, server, model_registry, received, tamper
    ):
        instance = operator_instance(model_registry, {SIGNING_SECRET_SETTING: SECRET})
        body = raw_message()
        headers = signed_headers(body, "desk@example.org")
        sent = body
        if tamper == "signature":
            headers[SIGNATURE_HEADER] = "sha256=" + "0" * 64
        elif tamper == "body":
            sent = body.replace(b"The body.", b"Another body.")
        elif tamper == "recipients":
            headers[RECIPIENTS_HEADER] = "desk@example.org, thief@example.net"
        elif tamper == "secret":
            headers = signed_headers(body, "desk@example.org", secret=SECRET + "x")
        elif tamper == "stale":
            headers = signed_headers(
                body,
                "desk@example.org",
                timestamp=int(time.time()) - REPLAY_WINDOW_SECONDS - 5,
            )
        elif tamper == "future":
            headers = signed_headers(
                body,
                "desk@example.org",
                timestamp=int(time.time()) + REPLAY_WINDOW_SECONDS + 5,
            )
        else:
            del headers[SIGNATURE_HEADER]
        assert_unsigned(self._deliver(server, instance.id, sent, headers))
        assert received == []

    def test_a_replayed_delivery_is_refused(self, server, model_registry, received):
        instance = operator_instance(model_registry, {SIGNING_SECRET_SETTING: SECRET})
        body = raw_message()
        headers = signed_headers(body)
        assert self._deliver(server, instance.id, body, headers).status_code == 200
        assert_unsigned(self._deliver(server, instance.id, body, headers))
        assert len(received) == 1

    @pytest.mark.parametrize(
        "which", ["unknown", "user_scoped", "disabled", "deleted", "no_secret", "weak"]
    )
    def test_only_an_operator_instance_with_a_strong_secret_takes_mail(
        self, server, model_registry, received, which
    ):
        """Every one is answered exactly as a bad signature is, so the
        endpoint tells no one which instances exist or accept mail."""
        settings = {SIGNING_SECRET_SETTING: SECRET}
        if which == "no_secret":
            settings = {}
        if which == "weak":
            settings = {SIGNING_SECRET_SETTING: "s" * (MIN_SIGNING_SECRET_LENGTH - 1)}
        instance = operator_instance(
            model_registry, settings, scope="user" if which == "user_scoped" else "root"
        )
        instances = ProviderInstanceManager(
            model_registry=model_registry, requester_id=env("ROOT_ID")
        )
        if which == "disabled":
            instances.update(id=instance.id, enabled=False)
        if which == "deleted":
            instances.delete(id=instance.id)
        target = str(uuid.uuid4()) if which == "unknown" else instance.id
        body = raw_message()
        secret = settings.get(SIGNING_SECRET_SETTING, SECRET)
        assert_unsigned(
            self._deliver(server, target, body, signed_headers(body, secret=secret))
        )
        assert received == []

    def test_a_session_does_not_stand_in_for_a_signature(
        self, server, model_registry, admin_a, received
    ):
        instance = operator_instance(model_registry, {SIGNING_SECRET_SETTING: SECRET})
        answered = self._deliver(
            server,
            instance.id,
            raw_message(),
            {
                "Content-Type": "message/rfc822",
                "Authorization": f"Bearer {admin_a.jwt}",
            },
        )
        assert answered.status_code == 401, answered.text
        assert received == []

    def test_an_oversized_message_is_refused(self, server, model_registry, received):
        instance = operator_instance(model_registry, {SIGNING_SECRET_SETTING: SECRET})
        answered = server.post(
            f"{INBOUND_PREFIX}/{instance.id}",
            content=b"x",
            headers={
                "Content-Type": "message/rfc822",
                "Content-Length": str(MAX_INBOUND_EMAIL_BYTES + 1),
            },
        )
        assert answered.status_code == 413, answered.text
        assert received == []

    @pytest.mark.parametrize("problem", ["empty", "recipients"])
    def test_a_signed_but_malformed_delivery_is_a_bad_request(
        self, server, model_registry, received, problem
    ):
        instance = operator_instance(model_registry, {SIGNING_SECRET_SETTING: SECRET})
        body = b"" if problem == "empty" else raw_message()
        recipients = "" if problem == "empty" else "Desk <desk@example.org>"
        answered = self._deliver(
            server, instance.id, body, signed_headers(body, recipients)
        )
        assert answered.status_code == 400, answered.text
        assert received == []

    def test_deliveries_are_rate_limited(self, server):
        allowed = int(INBOUND_RATE_LIMIT.split("/")[0])
        target = f"{INBOUND_PREFIX}/{uuid.uuid4()}"
        statuses = [
            server.post(target, content=b"m", headers={}).status_code
            for _ in range(allowed + 1)
        ]
        assert statuses[:allowed] == [401] * allowed
        assert statuses[allowed] == 429

    def test_a_cross_site_post_is_not_refused_as_forged(
        self, server, model_registry, received
    ):
        """A mail server is another origin with no session: the cross-site
        write guard must let its signed delivery through."""
        instance = operator_instance(model_registry, {SIGNING_SECRET_SETTING: SECRET})
        body = raw_message()
        headers = {**signed_headers(body), "Origin": "https://mail.example.net"}
        answered = self._deliver(server, instance.id, body, headers)
        assert answered.status_code == 200, answered.text
        assert len(received) == 1
