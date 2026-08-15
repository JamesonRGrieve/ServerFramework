# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the SMTP2Go email provider.

No live credentials required — all API calls are intercepted by a
monkeypatched httpx response. Tests verify payload construction,
error handling, config validation, and the typed send path.
"""

from __future__ import annotations

import asyncio
import base64
import os
from decimal import Decimal
from typing import Any, Dict
from unittest.mock import AsyncMock

import pytest

from zephyrex.extensions.AbstractExtensionProvider import HealthStatus
from zephyrex.extensions.email.PRV_SMTP2Go_EMail import Smtp2goProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeInstance:
    """Minimal stand-in for ProviderInstanceModel."""

    def __init__(self, api_key=None, from_email=None):
        self.id = "test-instance"
        self.api_key = api_key
        self.settings = {}
        self._from_email = from_email

    def get_setting(self, key):
        if key == "from_email":
            return self._from_email
        return self.settings.get(key)


class _FakeResponse:
    def __init__(self, status_code=200, text="ok", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data or {}

    def json(self):
        return self._json


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


class TestSmtp2goProviderMetadata:
    def test_name(self):
        assert Smtp2goProvider.name == "smtp2go"

    def test_version_semver(self):
        parts = Smtp2goProvider.version.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_platform_name(self):
        assert Smtp2goProvider.get_platform_name() == "SMTP2go"

    def test_services(self):
        svc = Smtp2goProvider.services()
        assert "email" in svc

    def test_abilities_include_send(self):
        assert "email_send" in Smtp2goProvider._abilities

    def test_capabilities_include_send(self):
        from zephyrex.extensions.email.EXT_EMail import Capability

        assert Capability.SEND in Smtp2goProvider.capabilities

    def test_capabilities_include_attachments(self):
        from zephyrex.extensions.email.EXT_EMail import Capability

        assert Capability.ATTACHMENTS in Smtp2goProvider.capabilities

    def test_cost_model(self):
        assert Smtp2goProvider.cost_model.per_call_usd == Decimal("0.0001")

    def test_rate_limit(self):
        assert Smtp2goProvider.rate_limit.rps == 100
        assert Smtp2goProvider.rate_limit.burst == 200

    def test_bulk_max_batch(self):
        assert Smtp2goProvider.SEND_BULK_MAX_BATCH == 1000

    def test_auth_strategy(self):
        assert Smtp2goProvider.default_auth_strategy == "api_key"


# ---------------------------------------------------------------------------
# Settings model
# ---------------------------------------------------------------------------


class TestSmtp2goSettings:
    def test_env_field_map_keys(self):
        expected = {"from_email", "api_key", "api_url"}
        assert set(Smtp2goProvider.Settings._env_field_map.keys()) == expected

    def test_default_api_url(self):
        assert "smtp2go.com" in str(Smtp2goProvider.Settings.model_fields["api_url"].default)

    def test_env_dict_keys(self):
        assert "SMTP2GO_API_KEY" in Smtp2goProvider._env
        assert "SMTP2GO_FROM_EMAIL" in Smtp2goProvider._env
        assert "SMTP2GO_API_URL" in Smtp2goProvider._env


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestSmtp2goConfigValidation:
    def test_validate_config_no_key(self, monkeypatch):
        monkeypatch.setenv("SMTP2GO_API_KEY", "")
        from zephyrex.lib.Environment import refresh_settings

        refresh_settings()
        assert Smtp2goProvider.validate_config() is False

    def test_validate_config_with_key(self, monkeypatch):
        monkeypatch.setenv("SMTP2GO_API_KEY", "test-key")
        from zephyrex.lib.Environment import refresh_settings

        refresh_settings()
        assert Smtp2goProvider.validate_config() is True

    def test_validate_config_from_instance(self, monkeypatch):
        monkeypatch.setenv("SMTP2GO_API_KEY", "")
        from zephyrex.lib.Environment import refresh_settings

        refresh_settings()
        inst = _FakeInstance(api_key="instance-key")
        assert Smtp2goProvider.validate_config(instance=inst) is True


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestSmtp2goHealthCheck:
    def test_health_no_api_key(self, monkeypatch):
        monkeypatch.setenv("SMTP2GO_API_KEY", "")
        from zephyrex.lib.Environment import refresh_settings

        refresh_settings()
        report = Smtp2goProvider.health_check()
        assert report.status == HealthStatus.DOWN
        assert "not configured" in report.detail

    def test_health_network_error(self, monkeypatch):
        monkeypatch.setenv("SMTP2GO_API_KEY", "test-key")
        monkeypatch.setenv("SMTP2GO_API_URL", "http://localhost:1/nonexistent")
        from zephyrex.lib.Environment import refresh_settings

        refresh_settings()
        report = Smtp2goProvider.health_check()
        assert report.status == HealthStatus.DOWN


# ---------------------------------------------------------------------------
# Bond instance
# ---------------------------------------------------------------------------


class TestSmtp2goBondInstance:
    def test_bond_returns_sdk(self, monkeypatch):
        monkeypatch.setenv("SMTP2GO_API_KEY", "bond-test-key")
        monkeypatch.setenv("SMTP2GO_FROM_EMAIL", "test@example.com")
        from zephyrex.lib.Environment import refresh_settings

        refresh_settings()
        inst = _FakeInstance(api_key="bond-test-key")
        bonded = Smtp2goProvider.bond_instance(inst)
        assert bonded is not None
        assert bonded.sdk["api_key"] == "bond-test-key"

    def test_bond_no_key_returns_none(self, monkeypatch):
        monkeypatch.setenv("SMTP2GO_API_KEY", "")
        from zephyrex.lib.Environment import refresh_settings

        refresh_settings()
        inst = _FakeInstance(api_key=None)
        bonded = Smtp2goProvider.bond_instance(inst)
        assert bonded is None


# ---------------------------------------------------------------------------
# send_email
# ---------------------------------------------------------------------------


class TestSmtp2goSendEmail:
    @pytest.mark.asyncio
    async def test_send_plain_text(self, monkeypatch):
        monkeypatch.setenv("SMTP2GO_API_KEY", "test-key")
        monkeypatch.setenv("SMTP2GO_FROM_EMAIL", "sender@example.com")
        monkeypatch.setenv("SMTP2GO_API_URL", "https://api.smtp2go.com/v3")
        from zephyrex.lib.Environment import refresh_settings

        refresh_settings()

        captured: Dict[str, Any] = {}

        async def fake_post(url, json=None, **kw):
            captured["url"] = str(url)
            captured["payload"] = json
            return _FakeResponse(200)

        from zephyrex.lib import ProviderHTTPClient

        class _FakeClient:
            async def post(self, url, **kwargs):
                return await fake_post(url, **kwargs)

        monkeypatch.setattr(ProviderHTTPClient, "get_async_client", lambda *a, **kw: _FakeClient())

        inst = _FakeInstance(api_key="test-key", from_email="sender@example.com")
        result = await Smtp2goProvider.send_email(
            inst, "to@example.com", "Subject", "Plain body"
        )
        assert "successfully" in result.lower()
        assert captured["payload"]["text_body"] == "Plain body"
        assert "html_body" not in captured["payload"]
        assert captured["payload"]["to"] == ["to@example.com"]
        assert captured["payload"]["subject"] == "Subject"

    @pytest.mark.asyncio
    async def test_send_html(self, monkeypatch):
        monkeypatch.setenv("SMTP2GO_API_KEY", "test-key")
        monkeypatch.setenv("SMTP2GO_FROM_EMAIL", "sender@example.com")
        from zephyrex.lib.Environment import refresh_settings

        refresh_settings()

        captured: Dict[str, Any] = {}

        async def fake_post(url, json=None, **kw):
            captured["payload"] = json
            return _FakeResponse(200)

        from zephyrex.lib import ProviderHTTPClient

        class _FakeClient:
            async def post(self, url, **kwargs):
                return await fake_post(url, **kwargs)

        monkeypatch.setattr(ProviderHTTPClient, "get_async_client", lambda *a, **kw: _FakeClient())

        inst = _FakeInstance(api_key="test-key")
        result = await Smtp2goProvider.send_email(
            inst, "to@example.com", "Subject", "<html><body>Hi</body></html>"
        )
        assert "successfully" in result.lower()
        assert "html_body" in captured["payload"]
        assert "text_body" not in captured["payload"]

    @pytest.mark.asyncio
    async def test_send_with_attachment(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SMTP2GO_API_KEY", "test-key")
        monkeypatch.setenv("SMTP2GO_FROM_EMAIL", "sender@example.com")
        from zephyrex.lib.Environment import refresh_settings

        refresh_settings()

        attachment = tmp_path / "doc.txt"
        attachment.write_text("hello")

        captured: Dict[str, Any] = {}

        async def fake_post(url, json=None, **kw):
            captured["payload"] = json
            return _FakeResponse(200)

        from zephyrex.lib import ProviderHTTPClient

        class _FakeClient:
            async def post(self, url, **kwargs):
                return await fake_post(url, **kwargs)

        monkeypatch.setattr(ProviderHTTPClient, "get_async_client", lambda *a, **kw: _FakeClient())

        inst = _FakeInstance(api_key="test-key")
        result = await Smtp2goProvider.send_email(
            inst, "to@example.com", "Subject", "Body", attachments=[str(attachment)]
        )
        assert "successfully" in result.lower()
        atts = captured["payload"]["attachments"]
        assert len(atts) == 1
        assert atts[0]["filename"] == "doc.txt"
        decoded = base64.b64decode(atts[0]["fileblob"])
        assert decoded == b"hello"

    @pytest.mark.asyncio
    async def test_send_api_error(self, monkeypatch):
        monkeypatch.setenv("SMTP2GO_API_KEY", "test-key")
        monkeypatch.setenv("SMTP2GO_FROM_EMAIL", "sender@example.com")
        from zephyrex.lib.Environment import refresh_settings

        refresh_settings()

        async def fake_post(url, json=None, **kw):
            return _FakeResponse(403, "Forbidden")

        from zephyrex.lib import ProviderHTTPClient

        class _FakeClient:
            async def post(self, url, **kwargs):
                return await fake_post(url, **kwargs)

        monkeypatch.setattr(ProviderHTTPClient, "get_async_client", lambda *a, **kw: _FakeClient())

        inst = _FakeInstance(api_key="test-key")
        result = await Smtp2goProvider.send_email(
            inst, "to@example.com", "Subject", "Body"
        )
        assert "failed" in result.lower()
        assert "403" in result

    @pytest.mark.asyncio
    async def test_send_invalid_recipient(self):
        inst = _FakeInstance(api_key="test-key")
        result = await Smtp2goProvider.send_email(inst, "", "Subject", "Body")
        assert result is not None

    @pytest.mark.asyncio
    async def test_send_no_from_email(self, monkeypatch):
        monkeypatch.setenv("SMTP2GO_API_KEY", "test-key")
        monkeypatch.setenv("SMTP2GO_FROM_EMAIL", "")
        from zephyrex.lib.Environment import refresh_settings

        refresh_settings()
        inst = _FakeInstance(api_key="test-key", from_email=None)
        result = await Smtp2goProvider.send_email(
            inst, "to@example.com", "Subject", "Body"
        )
        assert "from_email" in result.lower()

    @pytest.mark.asyncio
    async def test_send_missing_attachment_skipped(self, monkeypatch):
        monkeypatch.setenv("SMTP2GO_API_KEY", "test-key")
        monkeypatch.setenv("SMTP2GO_FROM_EMAIL", "sender@example.com")
        from zephyrex.lib.Environment import refresh_settings

        refresh_settings()

        captured: Dict[str, Any] = {}

        async def fake_post(url, json=None, **kw):
            captured["payload"] = json
            return _FakeResponse(200)

        from zephyrex.lib import ProviderHTTPClient

        class _FakeClient:
            async def post(self, url, **kwargs):
                return await fake_post(url, **kwargs)

        monkeypatch.setattr(ProviderHTTPClient, "get_async_client", lambda *a, **kw: _FakeClient())

        inst = _FakeInstance(api_key="test-key")
        result = await Smtp2goProvider.send_email(
            inst, "to@example.com", "Subject", "Body",
            attachments=["/nonexistent/file.txt"],
        )
        assert "successfully" in result.lower()
        assert "attachments" not in captured["payload"]


# ---------------------------------------------------------------------------
# Unsupported abilities
# ---------------------------------------------------------------------------


class TestSmtp2goUnsupportedAbilities:
    @pytest.mark.asyncio
    async def test_get_emails_returns_empty(self):
        result = await Smtp2goProvider.get_emails(None)
        assert result == []

    @pytest.mark.asyncio
    async def test_create_draft_returns_message(self):
        result = await Smtp2goProvider.create_draft_email(None, "r", "s", "b")
        assert "not supported" in result.lower()

    @pytest.mark.asyncio
    async def test_search_returns_empty(self):
        result = await Smtp2goProvider.search_emails(None, "query")
        assert result == []

    @pytest.mark.asyncio
    async def test_reply_returns_message(self):
        result = await Smtp2goProvider.reply_to_email(None, "id", "body")
        assert "not supported" in result.lower()

    @pytest.mark.asyncio
    async def test_delete_returns_message(self):
        result = await Smtp2goProvider.delete_email(None, "id")
        assert "not supported" in result.lower()

    @pytest.mark.asyncio
    async def test_process_attachments_returns_empty(self):
        result = await Smtp2goProvider.process_attachments(None, "id")
        assert result == []


# ---------------------------------------------------------------------------
# API key not in payload when missing
# ---------------------------------------------------------------------------


class TestSmtp2goSecurity:
    def test_api_key_not_leaked_in_health_report(self, monkeypatch):
        monkeypatch.setenv("SMTP2GO_API_KEY", "secret-key-12345")
        from zephyrex.lib.Environment import refresh_settings

        refresh_settings()
        report = Smtp2goProvider.health_check()
        assert "secret-key-12345" not in report.detail

    def test_env_dict_defaults_empty_key(self):
        assert Smtp2goProvider._env["SMTP2GO_API_KEY"] == ""
