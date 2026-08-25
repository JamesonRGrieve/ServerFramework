# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Proxmox Mail Gateway provider (send relay + REST API surfaces)."""

import hashlib
import hmac

import pytest

from zephyrex.extensions.email.EXT_EMail import (
    Importance,
    subscribe_email_delivery,
    unsubscribe_email_delivery,
)
from zephyrex.extensions.email.PRV_ProxmoxMailGateway_EMail import (
    ProxmoxMailGatewayProvider as PMG,
)
from zephyrex.extensions.email.PRV_ProxmoxMailGateway_EMail import (
    _dispatch_pmg_events,
)
from zephyrex.extensions.FieldMappings import apply_from_external, apply_to_external


class _FakeResp:
    def __init__(self, data):
        self._data = {"data": data}
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _FakeClient:
    """Records requests; returns whatever ``data`` the test seeds."""

    def __init__(self, calls, data):
        self.calls = calls
        self.data = data

    async def get(self, url, headers=None, params=None):
        self.calls.append(("GET", url, headers, params))
        return _FakeResp(self.data)

    async def post(self, url, headers=None, data=None):
        self.calls.append(("POST", url, headers, data))
        return _FakeResp(self.data)


def _wire(monkeypatch, calls, data):
    monkeypatch.setenv("PMG_API_URL", "https://pmg.example:8006/api2/json")
    monkeypatch.setenv("PMG_API_TOKEN", "root@pam!tok=secret")
    monkeypatch.setattr(
        PMG, "_api_client", classmethod(lambda cls: _FakeClient(calls, data))
    )


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


class TestRestApi:
    def test_get_stats_hits_statistics_mail_with_token(self, monkeypatch):
        calls: list = []
        _wire(monkeypatch, calls, {"count_in": 10, "count_out": 5})
        result = _run(PMG.get_stats(starttime=1, endtime=2))
        assert result == {"count_in": 10, "count_out": 5}
        method, url, headers, params = calls[0]
        assert method == "GET" and url.endswith("/statistics/mail")
        assert headers["Authorization"] == "PMGAPIToken=root@pam!tok=secret"
        assert params == {"starttime": 1, "endtime": 2}

    def test_list_quarantine_kind_path(self, monkeypatch):
        calls: list = []
        _wire(monkeypatch, calls, [{"id": "C1"}, {"id": "C2"}])
        rows = _run(PMG.list_quarantine("virus"))
        assert [r["id"] for r in rows] == ["C1", "C2"]
        assert calls[0][1].endswith("/quarantine/virus")

    def test_list_quarantine_rejects_unknown_kind(self):
        with pytest.raises(ValueError):
            _run(PMG.list_quarantine("nope"))

    def test_release_and_delete_post_content_action(self, monkeypatch):
        calls: list = []
        _wire(monkeypatch, calls, "ok")
        _run(PMG.release_quarantine("C1"))
        _run(PMG.delete_quarantine("C2"))
        m1, u1, _, d1 = calls[0]
        m2, u2, _, d2 = calls[1]
        assert m1 == "POST" and u1.endswith("/quarantine/content")
        assert d1 == {"id": "C1", "action": "deliver"}
        assert d2 == {"id": "C2", "action": "delete"}

    def test_list_messages_hits_node_tracker(self, monkeypatch):
        calls: list = []
        monkeypatch.setenv("PMG_API_NODE", "mail01")
        _wire(monkeypatch, calls, [{"id": "T1"}])
        rows = _run(PMG.list_messages())
        assert rows == [{"id": "T1"}]
        assert calls[0][1].endswith("/nodes/mail01/tracker")


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
