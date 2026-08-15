# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the secret vault extension and OpenBao provider.

No mocks. Tests exercise real code paths that don't require a live
vault: metadata, config validation, env var parsing, error paths,
client construction, and the extension manifest. CRUD tests that
need a live vault are gated on OPENBAO_ADDR.
"""

from __future__ import annotations

import os

import pytest

from zephyrex.extensions.AbstractExtensionProvider import HealthStatus
from zephyrex.extensions.secret_vault.EXT_Secret_Vault import (
    EXT_Secret_Vault,
    Capability,
)
from zephyrex.extensions.secret_vault.PRV_OpenBao import (
    OpenBaoProvider,
    _build_client,
    _get_addr,
    _get_mount,
    _get_namespace,
    _get_token,
)


def _vault_available() -> bool:
    return bool(os.environ.get("OPENBAO_ADDR") or os.environ.get("VAULT_ADDR"))


# ---------------------------------------------------------------------------
# Extension manifest
# ---------------------------------------------------------------------------


class TestExtSecretVault:
    def test_name(self):
        assert EXT_Secret_Vault.name == "secret_vault"

    def test_version_semver(self):
        parts = EXT_Secret_Vault.version.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_abilities_include_all_crud(self):
        expected = {"secret_read", "secret_write", "secret_delete", "secret_list", "secret_metadata"}
        assert expected.issubset(EXT_Secret_Vault._abilities)

    def test_env_vars_declared(self):
        assert "OPENBAO_ADDR" in EXT_Secret_Vault._env
        assert "OPENBAO_TOKEN" in EXT_Secret_Vault._env
        assert "VAULT_ADDR" in EXT_Secret_Vault._env
        assert "VAULT_TOKEN" in EXT_Secret_Vault._env
        assert "OPENBAO_MOUNT_POINT" in EXT_Secret_Vault._env
        assert "OPENBAO_NAMESPACE" in EXT_Secret_Vault._env
        assert "OPENBAO_ROLE_ID" in EXT_Secret_Vault._env
        assert "OPENBAO_SECRET_ID" in EXT_Secret_Vault._env

    def test_env_defaults_empty(self):
        assert EXT_Secret_Vault._env["OPENBAO_ADDR"] == ""
        assert EXT_Secret_Vault._env["OPENBAO_TOKEN"] == ""

    def test_default_mount_point(self):
        assert EXT_Secret_Vault._env["OPENBAO_MOUNT_POINT"] == "secret"

    def test_dependencies_include_hvac(self):
        dep_names = [d.name for d in EXT_Secret_Vault.dependencies.pip]
        assert "hvac" in dep_names

    def test_hvac_is_optional(self):
        hvac_dep = next(d for d in EXT_Secret_Vault.dependencies.pip if d.name == "hvac")
        assert hvac_dep.optional is True

    def test_on_initialize(self):
        assert EXT_Secret_Vault.on_initialize() is True

    def test_no_extension_dependencies(self):
        assert EXT_Secret_Vault.extension_dependencies == []

    def test_description_nonempty(self):
        assert len(EXT_Secret_Vault.description) > 10


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


class TestOpenBaoProviderMetadata:
    def test_name(self):
        assert OpenBaoProvider.name == "openbao"

    def test_version_semver(self):
        parts = OpenBaoProvider.version.split(".")
        assert len(parts) == 3

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
        assert expected.issubset(OpenBaoProvider._abilities)

    def test_env_dict(self):
        assert "OPENBAO_ADDR" in OpenBaoProvider._env
        assert "OPENBAO_TOKEN" in OpenBaoProvider._env


# ---------------------------------------------------------------------------
# Env var helpers
# ---------------------------------------------------------------------------


class TestEnvHelpers:
    def test_get_addr_openbao(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://bao:8200")
        monkeypatch.delenv("VAULT_ADDR", raising=False)
        assert _get_addr() == "http://bao:8200"

    def test_get_addr_vault_fallback(self, monkeypatch):
        monkeypatch.delenv("OPENBAO_ADDR", raising=False)
        monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
        assert _get_addr() == "http://vault:8200"

    def test_get_addr_empty(self, monkeypatch):
        monkeypatch.delenv("OPENBAO_ADDR", raising=False)
        monkeypatch.delenv("VAULT_ADDR", raising=False)
        assert _get_addr() == ""

    def test_get_token_openbao(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_TOKEN", "s.bao")
        monkeypatch.delenv("VAULT_TOKEN", raising=False)
        assert _get_token() == "s.bao"

    def test_get_token_vault_fallback(self, monkeypatch):
        monkeypatch.delenv("OPENBAO_TOKEN", raising=False)
        monkeypatch.setenv("VAULT_TOKEN", "s.vault")
        assert _get_token() == "s.vault"

    def test_get_mount_default(self, monkeypatch):
        monkeypatch.delenv("OPENBAO_MOUNT_POINT", raising=False)
        assert _get_mount() == "secret"

    def test_get_mount_custom(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_MOUNT_POINT", "kv")
        assert _get_mount() == "kv"

    def test_get_namespace_empty(self, monkeypatch):
        monkeypatch.delenv("OPENBAO_NAMESPACE", raising=False)
        assert _get_namespace() is None

    def test_get_namespace_set(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_NAMESPACE", "admin")
        assert _get_namespace() == "admin"


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestOpenBaoConfigValidation:
    def test_no_addr_fails(self, monkeypatch):
        monkeypatch.delenv("OPENBAO_ADDR", raising=False)
        monkeypatch.delenv("VAULT_ADDR", raising=False)
        assert OpenBaoProvider.validate_config() is False

    def test_no_auth_fails(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://vault:8200")
        monkeypatch.delenv("OPENBAO_TOKEN", raising=False)
        monkeypatch.delenv("VAULT_TOKEN", raising=False)
        monkeypatch.delenv("OPENBAO_ROLE_ID", raising=False)
        assert OpenBaoProvider.validate_config() is False

    def test_token_auth_passes(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://vault:8200")
        monkeypatch.setenv("OPENBAO_TOKEN", "s.test-token")
        assert OpenBaoProvider.validate_config() is True

    def test_approle_auth_passes(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://vault:8200")
        monkeypatch.delenv("OPENBAO_TOKEN", raising=False)
        monkeypatch.delenv("VAULT_TOKEN", raising=False)
        monkeypatch.setenv("OPENBAO_ROLE_ID", "role-123")
        assert OpenBaoProvider.validate_config() is True

    def test_vault_addr_fallback(self, monkeypatch):
        monkeypatch.delenv("OPENBAO_ADDR", raising=False)
        monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
        monkeypatch.setenv("VAULT_TOKEN", "s.legacy")
        assert OpenBaoProvider.validate_config() is True


# ---------------------------------------------------------------------------
# Health check (no-addr path — no network call)
# ---------------------------------------------------------------------------


class TestOpenBaoHealthCheck:
    def test_health_no_addr(self, monkeypatch):
        monkeypatch.delenv("OPENBAO_ADDR", raising=False)
        monkeypatch.delenv("VAULT_ADDR", raising=False)
        report = OpenBaoProvider.health_check()
        assert report.status == HealthStatus.DOWN
        assert "not set" in report.detail

    def test_health_unreachable(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://127.0.0.1:1")
        monkeypatch.setenv("OPENBAO_TOKEN", "s.x")
        report = OpenBaoProvider.health_check()
        assert report.status == HealthStatus.DOWN
        assert "connection error" in report.detail.lower() or "error" in report.detail.lower()


# ---------------------------------------------------------------------------
# Bond instance (no-addr path — no network call)
# ---------------------------------------------------------------------------


class TestOpenBaoBondInstance:
    def test_bond_no_addr(self, monkeypatch):
        monkeypatch.delenv("OPENBAO_ADDR", raising=False)
        monkeypatch.delenv("VAULT_ADDR", raising=False)

        class _Inst:
            id = "x"
            api_key = None

        bonded = OpenBaoProvider.bond_instance(_Inst())
        assert bonded is None


# ---------------------------------------------------------------------------
# Client construction (real hvac.Client, no server needed)
# ---------------------------------------------------------------------------


class TestBuildClient:
    def test_constructs_client_with_token(self):
        client = _build_client("http://127.0.0.1:1", token="s.test")
        assert client is not None
        url = client.url if isinstance(client.url, str) else client.url.geturl()
        assert url.startswith("http://127.0.0.1")

    def test_constructs_client_without_token(self):
        client = _build_client("http://127.0.0.1:1")
        assert client is not None


# ---------------------------------------------------------------------------
# Error paths (not connected — real code, no network)
# ---------------------------------------------------------------------------


class TestOpenBaoNotConnected:
    """All CRUD abilities raise RuntimeError when vault is unreachable."""

    def _make_instance(self):
        class _Inst:
            id = "x"
            api_key = None

        return _Inst()

    def test_read_not_connected(self, monkeypatch):
        monkeypatch.delenv("OPENBAO_ADDR", raising=False)
        monkeypatch.delenv("VAULT_ADDR", raising=False)
        with pytest.raises(RuntimeError, match="not connected"):
            OpenBaoProvider.read_secret(self._make_instance(), "any")

    def test_write_not_connected(self, monkeypatch):
        monkeypatch.delenv("OPENBAO_ADDR", raising=False)
        monkeypatch.delenv("VAULT_ADDR", raising=False)
        with pytest.raises(RuntimeError, match="not connected"):
            OpenBaoProvider.write_secret(self._make_instance(), "any", {"k": "v"})

    def test_delete_not_connected(self, monkeypatch):
        monkeypatch.delenv("OPENBAO_ADDR", raising=False)
        monkeypatch.delenv("VAULT_ADDR", raising=False)
        with pytest.raises(RuntimeError, match="not connected"):
            OpenBaoProvider.delete_secret(self._make_instance(), "any")

    def test_list_not_connected(self, monkeypatch):
        monkeypatch.delenv("OPENBAO_ADDR", raising=False)
        monkeypatch.delenv("VAULT_ADDR", raising=False)
        with pytest.raises(RuntimeError, match="not connected"):
            OpenBaoProvider.list_secrets(self._make_instance())

    def test_metadata_not_connected(self, monkeypatch):
        monkeypatch.delenv("OPENBAO_ADDR", raising=False)
        monkeypatch.delenv("VAULT_ADDR", raising=False)
        with pytest.raises(RuntimeError, match="not connected"):
            OpenBaoProvider.read_metadata(self._make_instance(), "any")


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class TestOpenBaoSecurity:
    def test_token_not_in_health_report(self, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "http://127.0.0.1:1")
        monkeypatch.setenv("OPENBAO_TOKEN", "s.super-secret-token-12345")
        report = OpenBaoProvider.health_check()
        assert "s.super-secret-token-12345" not in report.detail

    def test_env_defaults_empty_credentials(self):
        assert OpenBaoProvider._env["OPENBAO_ADDR"] == ""
        assert OpenBaoProvider._env["OPENBAO_TOKEN"] == ""
        assert OpenBaoProvider._env["OPENBAO_ROLE_ID"] == ""
        assert OpenBaoProvider._env["OPENBAO_SECRET_ID"] == ""


# ---------------------------------------------------------------------------
# Live CRUD — only runs when a real vault is available
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _vault_available(), reason="No OpenBao/Vault instance available")
class TestOpenBaoLiveCRUD:
    """Runs against a real vault. Set OPENBAO_ADDR + OPENBAO_TOKEN to enable."""

    def _inst(self):
        class _Inst:
            id = "live-test"
            api_key = None

        return _Inst()

    def test_write_and_read(self):
        path = "zephyrex-test/live-roundtrip"
        OpenBaoProvider.write_secret(self._inst(), path, {"key": "value"})
        result = OpenBaoProvider.read_secret(self._inst(), path)
        assert result["key"] == "value"
        OpenBaoProvider.delete_secret(self._inst(), path)

    def test_list_after_write(self):
        path = "zephyrex-test/list-check"
        OpenBaoProvider.write_secret(self._inst(), path, {"x": "1"})
        keys = OpenBaoProvider.list_secrets(self._inst(), "zephyrex-test")
        assert "list-check" in keys
        OpenBaoProvider.delete_secret(self._inst(), path)

    def test_read_metadata(self):
        path = "zephyrex-test/meta-check"
        OpenBaoProvider.write_secret(self._inst(), path, {"m": "1"})
        meta = OpenBaoProvider.read_metadata(self._inst(), path)
        assert "versions" in meta
        OpenBaoProvider.delete_secret(self._inst(), path)

    def test_health_ok(self):
        report = OpenBaoProvider.health_check()
        assert report.status == HealthStatus.OK

    def test_bond_instance(self):
        bonded = OpenBaoProvider.bond_instance(self._inst())
        assert bonded is not None
        assert bonded.sdk["client"].is_authenticated()
