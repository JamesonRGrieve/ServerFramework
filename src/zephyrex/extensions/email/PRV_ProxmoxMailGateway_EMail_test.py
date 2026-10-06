# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Proxmox Mail Gateway provider (send relay + REST API surfaces)."""

import hashlib
import hmac
from typing import Any, Dict, Iterator

import httpx
import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.email.EmailTestSupport import email_instance
from zephyrex.extensions.email.EXT_EMail import (
    EXT_EMail,
    Importance,
    subscribe_email_delivery,
    unsubscribe_email_delivery,
)
from zephyrex.extensions.email.MailAPITestServers import PMGTestServer
from zephyrex.extensions.email.PRV_ProxmoxMailGateway_EMail import (
    ProxmoxMailGatewayProvider as PMG,
)
from zephyrex.extensions.email.PRV_ProxmoxMailGateway_EMail import (
    _dispatch_pmg_events,
)
from zephyrex.extensions.FieldMappings import apply_from_external, apply_to_external

TOKEN = "root@pam!tok=secret"
GATEWAY_DATA: Dict[str, Any] = {
    "version": {"version": "8.1"},
    "statistics/mail": {"count_in": 10, "count_out": 5},
    "quarantine/virus": [{"id": "C1"}, {"id": "C2"}],
    "nodes/mail01/tracker": [{"id": "T1"}],
}


def _run(coro):
    import asyncio

    return asyncio.run(coro)


class TestSignature:
    def test_verify_good_bad_and_no_secret(self, monkeypatch):
        monkeypatch.setenv("PMG_WEBHOOK_SECRET", "sek")
        body = b'{"event":"quarantine"}'
        sig = hmac.new(b"sek", body, hashlib.sha256).hexdigest()
        assert PMG.verify_signature({"X-PMG-Signature": sig}, body) is True
        assert PMG.verify_signature({"X-PMG-Signature": "sha256=" + sig}, body) is True
        assert PMG.verify_signature({"X-PMG-Signature": "bad"}, body) is False
        assert PMG.verify_signature({}, body) is False
        monkeypatch.delenv("PMG_WEBHOOK_SECRET", raising=False)
        assert PMG.verify_signature({"X-PMG-Signature": sig}, body) is False


class TestFieldMappings:
    def test_round_trip(self):
        flat = {
            "subject": "Hi",
            "from_address": "a@b.c",
            "from_name": "A B",
            "importance": Importance.HIGH.value,
        }
        ext = apply_to_external(PMG.field_mappings, flat)
        assert ext["subject"] == "Hi"
        assert ext["from"] == "A B <a@b.c>"
        assert ext["x_priority"] == "1"
        inv = apply_from_external(PMG.field_mappings, ext)
        assert inv["from_address"] == "a@b.c"
        assert inv["importance"] == Importance.HIGH.value


class TestRestApi(ExtensionServerMixin):
    """The management API against a gateway served in process, configured
    on a real instance (or, for the operator, the environment)."""

    extension_class = EXT_EMail

    @pytest.fixture
    def gateway(self, monkeypatch) -> Iterator[PMGTestServer]:
        with PMGTestServer(TOKEN, GATEWAY_DATA) as server:
            monkeypatch.setenv("EGRESS_ALLOWED_HOSTS", server.host)
            yield server

    def _instance(self, model_registry: Any, gateway: PMGTestServer, **options: Any):
        settings = {"api_url": gateway.api_url, "api_node": "mail01"}
        return email_instance(
            model_registry, PMG.name, settings, api_key=TOKEN, **options
        )

    def test_get_stats_hits_statistics_mail_with_token(self, model_registry, gateway):
        instance = self._instance(model_registry, gateway)
        result = _run(PMG.get_stats(instance, starttime=1, endtime=2))
        assert result == {"count_in": 10, "count_out": 5}
        (request,) = gateway.requests
        assert (request.method, request.path) == ("GET", "/api2/json/statistics/mail")
        assert request.headers["authorization"] == f"PMGAPIToken={TOKEN}"
        assert request.query == {"starttime": "1", "endtime": "2"}

    def test_list_quarantine_kind_path(self, model_registry, gateway):
        instance = self._instance(model_registry, gateway)
        rows = _run(PMG.list_quarantine("virus", provider_instance=instance))
        assert [r["id"] for r in rows] == ["C1", "C2"]
        assert gateway.requests[0].path == "/api2/json/quarantine/virus"

    def test_list_quarantine_rejects_unknown_kind(self):
        with pytest.raises(ValueError):
            _run(PMG.list_quarantine("nope"))

    def test_release_and_delete_post_content_action(self, model_registry, gateway):
        instance = self._instance(model_registry, gateway)
        _run(PMG.release_quarantine("C1", provider_instance=instance))
        _run(PMG.delete_quarantine("C2", provider_instance=instance))
        released, deleted = gateway.requests
        assert (released.method, released.path) == (
            "POST",
            "/api2/json/quarantine/content",
        )
        assert released.form() == {"id": "C1", "action": "deliver"}
        assert deleted.form() == {"id": "C2", "action": "delete"}

    def test_list_messages_hits_node_tracker(self, model_registry, gateway):
        instance = self._instance(model_registry, gateway)
        rows = _run(PMG.list_messages(instance))
        assert rows == [{"id": "T1"}]
        assert gateway.requests[0].path == "/api2/json/nodes/mail01/tracker"

    def test_the_operator_defaults_to_the_environment(self, gateway, set_env):
        set_env("PMG_API_URL", gateway.api_url)
        set_env("PMG_API_TOKEN", TOKEN)
        set_env("PMG_API_NODE", "mail01")
        assert _run(PMG.list_messages()) == [{"id": "T1"}]
        assert gateway.requests[0].headers["authorization"] == f"PMGAPIToken={TOKEN}"

    def test_a_users_instance_never_presents_the_operators_token(
        self, model_registry, gateway, set_env, admin_a
    ):
        """A user's instance without a token of its own used to present the
        operator's PMG_API_TOKEN to whatever api_url it named."""
        set_env("PMG_API_TOKEN", TOKEN)
        instance = email_instance(
            model_registry,
            PMG.name,
            {"api_url": gateway.api_url},
            requester_id=admin_a.id,
            scope="user",
        )
        with pytest.raises(httpx.HTTPStatusError) as refused:
            _run(PMG.get_stats(instance))
        assert refused.value.response.status_code == 401
        (request,) = gateway.requests
        assert "authorization" not in request.headers


class TestWebhookDispatch:
    def test_dispatch_normalises_pmg_event(self):
        captured = []

        async def cb(evt):
            captured.append(evt)

        subscribe_email_delivery(cb)
        try:
            payload = {
                "events": [{"event": "quarantine", "receiver": "x@y.z", "id": "M1"}]
            }
            _run(_dispatch_pmg_events(payload, "quarantine"))
            assert len(captured) == 1
            e = captured[0]
            assert e.provider == "proxmox_mail_gateway"
            assert e.event_type == "quarantine"
            assert e.recipient == "x@y.z"
            assert e.message_id == "M1"
        finally:
            unsubscribe_email_delivery(cb)


class TestSendValidation:
    """send_email must run the _validate_send_inputs SSOT first, like every
    sibling provider (regression for the header-injection gap in #228)."""

    def test_send_email_rejects_crlf_recipient(self):
        # Validation runs before any bonding/network, so provider_instance=None
        # is never reached — the CRLF recipient is denied up front.
        err = _run(
            PMG.send_email(
                None,
                "victim@example.com\r\nBcc: attacker@evil.test",
                "Subject",
                "Body",
            )
        )
        assert err == "Failed to send email: rejected CRLF in recipient"

    def test_send_email_rejects_nul_in_subject(self):
        err = _run(PMG.send_email(None, "victim@example.com", "Sub\x00ject", "Body"))
        assert "NUL byte" in err
