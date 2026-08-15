# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the secret vault extension and OpenBao provider.

All hvac API calls are intercepted — no live OpenBao/Vault instance
required. Tests verify config validation, health checks, all CRUD
abilities, auth method selection, error handling, and metadata.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from zephyrex.extensions.AbstractExtensionProvider import HealthStatus
from zephyrex.extensions.secret_vault.EXT_Secret_Vault import (
    EXT_Secret_Vault,
    AbstractSecretVaultProvider,
    Capability,
)
from zephyrex.extensions.secret_vault.PRV_OpenBao import (
    OpenBaoProvider,
    _build_client,
    _get_addr,
    _get_mount,
    _get_token,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeInstance:
    id = "test-vault-instance"
    api_key = None


def _mock_hvac_client(
    authenticated: bool = True,
    health: Optional[Dict[str, Any]] = None,
    secrets: Optional[Dict[str, Dict[str, Any]]] = None,
):
    """Build a mock hvac.Client with configurable responses."""
    client = MagicMock()
    client.is_authenticated.return_value = authenticated

    if health is None:
        health = {"initialized": True, "sealed": False, "standby": False}
    client.sys.read_health_status.return_value = health

    store: Dict[str, Dict[str, Any]] = dict(secrets or {})

    def read_secret_version(path, mount_point="secret", version=None):
        if path not in store:
            raise Exception(f"secret not found: {path}")
        return {"data": {"data": store[path], "metadata": {"version": 1}}}

    def create_or_update_secret(path, secret, mount_point="secret"):
        store[path] = secret
        return {"data": {"version": len(store)}}

    def delete_latest_version(path, mount_point="secret"):
        store.pop(path, None)

    def list_secrets_fn(path="", mount_point="secret"):
        prefix = f"{path}/" if path else ""
        keys = [k[len(prefix):] for k in store if k.startswith(prefix) or not prefix]
        return {"data": {"keys": keys}}

    def read_metadata(path, mount_point="secret"):
        return {"data": {"versions": {"1": {"created_time": "2026-01-01T00:00:00Z"}}}}

    kv = client.secrets.kv.v2
    kv.read_secret_version.side_effect = read_secret_version
    kv.create_or_update_secret.side_effect = create_or_update_secret
    kv.delete_latest_version_of_secret.side_effect = delete_latest_version
    kv.list_secrets.side_effect = list_secrets_fn
    kv.read_secret_metadata.side_effect = read_metadata

    client.auth.approle.login.return_value = {"auth": {"client_token": "s.fake"}}

    return client


# ---------------------------------------------------------------------------
# Extension manifest
# ---------------------------------------------------------------------------


class TestExtSecretVault:
    def test_name(self):
        assert EXT_Secret_Vault.name == "secret_vault"

    def test_version_semver(self):
        parts = EXT_Secret_Vault.version.split(".")
        assert len(parts) == 3

    def test_abilities(self):
        expected = {"secret_read", "secret_write", "secret_delete", "secret_list", "secret_metadata"}
        assert expected.issubset(EXT_Secret_Vault._abilities)

    def test_env_vars(self):
        assert "OPENBAO_ADDR" in EXT_Secret_Vault._env
        assert "OPENBAO_TOKEN" in EXT_Secret_Vault._env
        assert "VAULT_ADDR" in EXT_Secret_Vault._env

    def test_dependencies_include_hvac(self):
        dep_names = [d.name for d in EXT_Secret_Vault.dependencies.pip]
        assert "hvac" in dep_names

    def test_on_initialize(self):
        assert EXT_Secret_Vault.on_initialize() is True

    def test_no_extension_dependencies(self):
        assert EXT_Secret_Vault.extension_dependencies == []


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


class TestOpenBaoProviderMetadata:
    def test_name(self):
        assert OpenBaoProvider.name == "openbao"

    def test_platform_name(self):
        assert OpenBaoProvider.get_platform_name() == "OpenBao"

    def test_services(self):
        svc = OpenBaoProvider.services()
        assert "secrets" in svc
        assert "vault" in svc

    def test_capabilities(self):
        assert Capability.READ in OpenBaoProvider.capabilities
        assert Capability.WRITE in OpenBaoProvider.capabilities
        assert Capability.DELETE in OpenBaoProvider.capabilities
        assert Capability.LIST in OpenBaoProvider.capabilities
        assert Capability.METADATA in OpenBaoProvider.capabilities

    def test_abilities(self):
        expected = {"secret_read", "secret_write", "secret_delete", "secret_list", "secret_metadata"}
        assert OpenBaoProvider._abilities == expected


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestOpenBaoConfigValidation:
    def test_no_addr_fails(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "")
        monkeypatch.setenv("VAULT_ADDR", "")
        assert OpenBaoProvider.validate_config() is False

    def test_no_auth_fails(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://vault:8200")
        monkeypatch.setenv("OPENBAO_TOKEN", "")
        monkeypatch.setenv("OPENBAO_ROLE_ID", "")
        monkeypatch.setenv("VAULT_TOKEN", "")
        assert OpenBaoProvider.validate_config() is False

    def test_token_auth_passes(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://vault:8200")
        monkeypatch.setenv("OPENBAO_TOKEN", "s.test-token")
        assert OpenBaoProvider.validate_config() is True

    def test_approle_auth_passes(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://vault:8200")
        monkeypatch.setenv("OPENBAO_TOKEN", "")
        monkeypatch.setenv("OPENBAO_ROLE_ID", "role-123")
        assert OpenBaoProvider.validate_config() is True

    def test_vault_addr_fallback(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "")
        monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
        monkeypatch.setenv("VAULT_TOKEN", "s.legacy")
        assert OpenBaoProvider.validate_config() is True


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestOpenBaoHealthCheck:
    def test_health_no_addr(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "")
        monkeypatch.setenv("VAULT_ADDR", "")
        report = OpenBaoProvider.health_check()
        assert report.status == HealthStatus.DOWN

    @patch("zephyrex.extensions.secret_vault.PRV_OpenBao._build_client")
    def test_health_ok(self, mock_build, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://vault:8200")
        monkeypatch.setenv("OPENBAO_TOKEN", "s.token")
        mock_build.return_value = _mock_hvac_client()
        report = OpenBaoProvider.health_check()
        assert report.status == HealthStatus.OK

    @patch("zephyrex.extensions.secret_vault.PRV_OpenBao._build_client")
    def test_health_sealed(self, mock_build, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://vault:8200")
        monkeypatch.setenv("OPENBAO_TOKEN", "s.token")
        mock_build.return_value = _mock_hvac_client(
            health={"initialized": True, "sealed": True}
        )
        report = OpenBaoProvider.health_check()
        assert report.status == HealthStatus.DEGRADED

    @patch("zephyrex.extensions.secret_vault.PRV_OpenBao._build_client")
    def test_health_connection_error(self, mock_build, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://vault:8200")
        monkeypatch.setenv("OPENBAO_TOKEN", "s.token")
        mock_build.side_effect = ConnectionError("refused")
        report = OpenBaoProvider.health_check()
        assert report.status == HealthStatus.DOWN


# ---------------------------------------------------------------------------
# Bond instance
# ---------------------------------------------------------------------------


class TestOpenBaoBondInstance:
    @patch("zephyrex.extensions.secret_vault.PRV_OpenBao._build_client")
    def test_bond_success(self, mock_build, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://vault:8200")
        monkeypatch.setenv("OPENBAO_TOKEN", "s.token")
        monkeypatch.setenv("OPENBAO_MOUNT_POINT", "kv")
        mock_build.return_value = _mock_hvac_client()
        bonded = OpenBaoProvider.bond_instance(_FakeInstance())
        assert bonded is not None
        assert bonded.sdk["mount_point"] == "kv"

    @patch("zephyrex.extensions.secret_vault.PRV_OpenBao._build_client")
    def test_bond_auth_failure(self, mock_build, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://vault:8200")
        monkeypatch.setenv("OPENBAO_TOKEN", "s.bad")
        mock_build.return_value = _mock_hvac_client(authenticated=False)
        bonded = OpenBaoProvider.bond_instance(_FakeInstance())
        assert bonded is None

    def test_bond_no_addr(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "")
        monkeypatch.setenv("VAULT_ADDR", "")
        bonded = OpenBaoProvider.bond_instance(_FakeInstance())
        assert bonded is None


# ---------------------------------------------------------------------------
# CRUD abilities
# ---------------------------------------------------------------------------


class TestOpenBaoSecretCRUD:
    @patch("zephyrex.extensions.secret_vault.PRV_OpenBao._build_client")
    def test_read_secret(self, mock_build, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://vault:8200")
        monkeypatch.setenv("OPENBAO_TOKEN", "s.token")
        mock_build.return_value = _mock_hvac_client(
            secrets={"app/db": {"username": "admin", "password": "s3cret"}}
        )
        result = OpenBaoProvider.read_secret(_FakeInstance(), "app/db")
        assert result["username"] == "admin"
        assert result["password"] == "s3cret"

    @patch("zephyrex.extensions.secret_vault.PRV_OpenBao._build_client")
    def test_read_secret_not_found(self, mock_build, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://vault:8200")
        monkeypatch.setenv("OPENBAO_TOKEN", "s.token")
        mock_build.return_value = _mock_hvac_client(secrets={})
        with pytest.raises(Exception, match="not found"):
            OpenBaoProvider.read_secret(_FakeInstance(), "nonexistent")

    @patch("zephyrex.extensions.secret_vault.PRV_OpenBao._build_client")
    def test_write_secret(self, mock_build, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://vault:8200")
        monkeypatch.setenv("OPENBAO_TOKEN", "s.token")
        client = _mock_hvac_client(secrets={})
        mock_build.return_value = client
        result = OpenBaoProvider.write_secret(
            _FakeInstance(), "app/new", {"key": "value"}
        )
        assert isinstance(result, dict)

    @patch("zephyrex.extensions.secret_vault.PRV_OpenBao._build_client")
    def test_write_then_read(self, mock_build, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://vault:8200")
        monkeypatch.setenv("OPENBAO_TOKEN", "s.token")
        store: Dict[str, Any] = {}
        client = _mock_hvac_client(secrets=store)
        mock_build.return_value = client
        OpenBaoProvider.write_secret(_FakeInstance(), "roundtrip", {"foo": "bar"})
        result = OpenBaoProvider.read_secret(_FakeInstance(), "roundtrip")
        assert result["foo"] == "bar"

    @patch("zephyrex.extensions.secret_vault.PRV_OpenBao._build_client")
    def test_delete_secret(self, mock_build, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://vault:8200")
        monkeypatch.setenv("OPENBAO_TOKEN", "s.token")
        mock_build.return_value = _mock_hvac_client(
            secrets={"to-delete": {"x": "y"}}
        )
        result = OpenBaoProvider.delete_secret(_FakeInstance(), "to-delete")
        assert result is True

    @patch("zephyrex.extensions.secret_vault.PRV_OpenBao._build_client")
    def test_list_secrets(self, mock_build, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://vault:8200")
        monkeypatch.setenv("OPENBAO_TOKEN", "s.token")
        mock_build.return_value = _mock_hvac_client(
            secrets={"a": {"v": 1}, "b": {"v": 2}}
        )
        keys = OpenBaoProvider.list_secrets(_FakeInstance())
        assert "a" in keys
        assert "b" in keys

    @patch("zephyrex.extensions.secret_vault.PRV_OpenBao._build_client")
    def test_list_empty(self, mock_build, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://vault:8200")
        monkeypatch.setenv("OPENBAO_TOKEN", "s.token")
        client = _mock_hvac_client(secrets={})
        client.secrets.kv.v2.list_secrets.side_effect = Exception("no keys")
        mock_build.return_value = client
        keys = OpenBaoProvider.list_secrets(_FakeInstance())
        assert keys == []

    @patch("zephyrex.extensions.secret_vault.PRV_OpenBao._build_client")
    def test_read_metadata(self, mock_build, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://vault:8200")
        monkeypatch.setenv("OPENBAO_TOKEN", "s.token")
        mock_build.return_value = _mock_hvac_client(secrets={"meta-test": {"a": 1}})
        meta = OpenBaoProvider.read_metadata(_FakeInstance(), "meta-test")
        assert "versions" in meta


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestOpenBaoErrorHandling:
    def test_read_not_connected(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "")
        monkeypatch.setenv("VAULT_ADDR", "")
        with pytest.raises(RuntimeError, match="not connected"):
            OpenBaoProvider.read_secret(_FakeInstance(), "any")

    def test_write_not_connected(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "")
        monkeypatch.setenv("VAULT_ADDR", "")
        with pytest.raises(RuntimeError, match="not connected"):
            OpenBaoProvider.write_secret(_FakeInstance(), "any", {"k": "v"})

    def test_delete_not_connected(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "")
        monkeypatch.setenv("VAULT_ADDR", "")
        with pytest.raises(RuntimeError, match="not connected"):
            OpenBaoProvider.delete_secret(_FakeInstance(), "any")

    def test_list_not_connected(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "")
        monkeypatch.setenv("VAULT_ADDR", "")
        with pytest.raises(RuntimeError, match="not connected"):
            OpenBaoProvider.list_secrets(_FakeInstance())

    def test_metadata_not_connected(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "")
        monkeypatch.setenv("VAULT_ADDR", "")
        with pytest.raises(RuntimeError, match="not connected"):
            OpenBaoProvider.read_metadata(_FakeInstance(), "any")


# ---------------------------------------------------------------------------
# Auth method selection
# ---------------------------------------------------------------------------


class TestOpenBaoAuth:
    @patch("zephyrex.extensions.secret_vault.PRV_OpenBao.hvac")
    def test_token_auth(self, mock_hvac, monkeypatch):
        monkeypatch.setenv("OPENBAO_TOKEN", "s.my-token")
        monkeypatch.setenv("OPENBAO_ROLE_ID", "")
        monkeypatch.setenv("OPENBAO_SECRET_ID", "")
        mock_client = MagicMock()
        mock_hvac.Client.return_value = mock_client
        _build_client("http://vault:8200", "s.my-token")
        mock_hvac.Client.assert_called_once_with(url="http://vault:8200", token="s.my-token")
        mock_client.auth.approle.login.assert_not_called()

    @patch("zephyrex.extensions.secret_vault.PRV_OpenBao.hvac")
    def test_approle_auth(self, mock_hvac, monkeypatch):
        monkeypatch.setenv("OPENBAO_TOKEN", "")
        monkeypatch.setenv("OPENBAO_ROLE_ID", "role-abc")
        monkeypatch.setenv("OPENBAO_SECRET_ID", "secret-xyz")
        mock_client = MagicMock()
        mock_hvac.Client.return_value = mock_client
        _build_client("http://vault:8200")
        mock_client.auth.approle.login.assert_called_once_with(
            role_id="role-abc", secret_id="secret-xyz"
        )

    @patch("zephyrex.extensions.secret_vault.PRV_OpenBao.hvac")
    def test_namespace_passed(self, mock_hvac, monkeypatch):
        monkeypatch.setenv("OPENBAO_TOKEN", "s.t")
        mock_client = MagicMock()
        mock_hvac.Client.return_value = mock_client
        _build_client("http://vault:8200", "s.t", namespace="admin")
        mock_hvac.Client.assert_called_once_with(
            url="http://vault:8200", token="s.t", namespace="admin"
        )


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class TestOpenBaoSecurity:
    def test_token_not_in_health_report(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://vault:8200")
        monkeypatch.setenv("OPENBAO_TOKEN", "s.super-secret-token")
        report = OpenBaoProvider.health_check()
        assert "s.super-secret-token" not in report.detail

    def test_env_defaults_empty(self):
        assert OpenBaoProvider._env["OPENBAO_ADDR"] == ""
        assert OpenBaoProvider._env["OPENBAO_TOKEN"] == ""

    def test_default_mount_point(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_MOUNT_POINT", "")
        assert _get_mount() == "secret"
