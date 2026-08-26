# SPDX-License-Identifier: AGPL-3.0-or-later
"""OpenBao (Vault-compatible) secret vault provider — KV v2 engine.

Authenticates via token or AppRole. Reads OPENBAO_ADDR/OPENBAO_TOKEN
(or VAULT_ADDR/VAULT_TOKEN for backward compat). Uses the ``hvac``
library for the Vault HTTP API.
"""

from __future__ import annotations

from typing import Any, Callable, ClassVar, Dict, List, Optional, Set, Tuple, cast

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance_SDK,
    HealthReport,
    HealthStatus,
    ability,
)
from zephyrex.extensions.secret_vault.EXT_Secret_Vault import (
    AbstractSecretVaultProvider,
    Capability,
)
import os

from zephyrex.lib.Logging import logger


def _env(key: str) -> str:
    return os.environ.get(key, "")


try:
    import hvac
    from hvac import exceptions as hvac_exceptions

    _hvac_available = True
except ImportError:
    hvac = None  # type: ignore[assignment]
    hvac_exceptions = None  # type: ignore[assignment]
    _hvac_available = False


def _get_addr() -> str:
    return _env("OPENBAO_ADDR") or _env("VAULT_ADDR") or ""


def _get_token() -> str:
    return _env("OPENBAO_TOKEN") or _env("VAULT_TOKEN") or ""


def _get_mount() -> str:
    return _env("OPENBAO_MOUNT_POINT") or "secret"


def _get_namespace() -> Optional[str]:
    ns = _env("OPENBAO_NAMESPACE")
    return ns if ns else None


def _build_client(
    addr: str,
    token: Optional[str] = None,
    namespace: Optional[str] = None,
) -> Any:
    """Build an hvac Client. Authenticate via token or AppRole."""
    if not _hvac_available:
        raise RuntimeError("hvac library not installed")

    kwargs: Dict[str, Any] = {"url": addr}
    if token:
        kwargs["token"] = token
    if namespace:
        kwargs["namespace"] = namespace

    client = hvac.Client(**kwargs)

    if not token:
        role_id = _env("OPENBAO_ROLE_ID")
        secret_id = _env("OPENBAO_SECRET_ID")
        if role_id and secret_id:
            client.auth.approle.login(role_id=role_id, secret_id=secret_id)

    return client


# Persistent per-connection cache of authenticated, bonded clients. Mirrors the
# email extension's persistent-transport pattern (``_SEND_HTTP_CLIENTS``): the
# ``hvac`` client -- and the token/lease it holds -- is built and authenticated
# once, then reused across every secret op, rather than paying a full auth
# round-trip (``is_authenticated`` and, for AppRole, ``approle.login``) on every
# single read/write. Keyed by the whole connection identity so a change of
# address, credentials, namespace, or mount rebinds instead of reusing a stale
# client. Re-auth happens only on expiry/revocation (see ``_run_with_client``).
_BONDED_CLIENTS: Dict[Tuple[str, str, str, str, str, str], Any] = {}


def _connection_key() -> Tuple[str, str, str, str, str, str]:
    """Identity of the current OpenBao connection, resolved from the env.

    Any change to a connection-determining value yields a distinct key, so a
    cached client is never reused across configurations (e.g. between tests
    that point at different addresses).
    """
    return (
        _get_addr(),
        _get_token(),
        _env("OPENBAO_ROLE_ID"),
        _env("OPENBAO_SECRET_ID"),
        _get_namespace() or "",
        _get_mount(),
    )


def _is_auth_error(exc: BaseException) -> bool:
    """True for a Vault auth failure (expired/revoked token, 401/403)."""
    if hvac_exceptions is None:
        return False
    return isinstance(exc, (hvac_exceptions.Forbidden, hvac_exceptions.Unauthorized))


class OpenBaoProvider(AbstractSecretVaultProvider):
    """OpenBao/Vault KV v2 secret vault provider."""

    name: ClassVar[str] = "openbao"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "OpenBao (Vault-compatible) KV v2 secret vault"

    _abilities: ClassVar[Set[str]] = {
        "secret_read",
        "secret_write",
        "secret_delete",
        "secret_list",
        "secret_metadata",
    }
    capabilities: ClassVar = frozenset(
        {
            Capability.READ,
            Capability.WRITE,
            Capability.DELETE,
            Capability.LIST,
            Capability.METADATA,
        }
    )

    _env: ClassVar[Dict[str, Any]] = {
        "OPENBAO_ADDR": "",
        "OPENBAO_TOKEN": "",
        "OPENBAO_ROLE_ID": "",
        "OPENBAO_SECRET_ID": "",
        "OPENBAO_MOUNT_POINT": "secret",
        "OPENBAO_NAMESPACE": "",
    }

    @classmethod
    def services(cls) -> List[str]:
        return ["secrets", "vault", "configuration"]

    @classmethod
    def get_platform_name(cls) -> str:
        return "OpenBao"

    @classmethod
    def validate_config(cls, instance=None) -> bool:
        if not _hvac_available:
            logger.error("hvac library not installed")
            return False
        addr = _get_addr()
        if not addr:
            logger.error("OPENBAO_ADDR (or VAULT_ADDR) not set")
            return False
        token = _get_token()
        role_id = _env("OPENBAO_ROLE_ID")
        if not token and not role_id:
            logger.error(
                "No auth method configured (set OPENBAO_TOKEN or OPENBAO_ROLE_ID)"
            )
            return False
        return True

    @classmethod
    def health_check(cls) -> HealthReport:
        addr = _get_addr()
        if not addr:
            return HealthReport(HealthStatus.DOWN, detail="OPENBAO_ADDR not set")
        if not _hvac_available:
            return HealthReport(HealthStatus.DOWN, detail="hvac library not installed")
        try:
            client = _build_client(addr, _get_token(), _get_namespace())
            health = client.sys.read_health_status(method="GET")
            if isinstance(health, dict):
                if health.get("initialized") and not health.get("sealed"):
                    return HealthReport(
                        HealthStatus.OK, detail="vault unsealed and active"
                    )
                return HealthReport(
                    HealthStatus.DEGRADED,
                    detail=f"initialized={health.get('initialized')} sealed={health.get('sealed')}",
                )
            status = getattr(health, "status_code", 500)
            if 200 <= status < 300:
                return HealthReport(HealthStatus.OK, detail=f"status {status}")
            return HealthReport(HealthStatus.DEGRADED, detail=f"status {status}")
        except Exception as exc:
            return HealthReport(HealthStatus.DOWN, detail=f"connection error: {exc}")

    @classmethod
    def bond_instance(cls, instance) -> Optional[AbstractProviderInstance_SDK]:
        if not _hvac_available:
            logger.error("hvac library not installed")
            return None
        addr = _get_addr()
        token = _get_token()
        if not addr:
            logger.error("OPENBAO_ADDR not set")
            return None

        key = _connection_key()
        cached = _BONDED_CLIENTS.get(key)
        if cached is not None:
            return cast(AbstractProviderInstance_SDK, cached)

        try:
            client = _build_client(addr, token, _get_namespace())
            if not client.is_authenticated():
                logger.error("OpenBao authentication failed")
                return None
            sdk = {
                "client": client,
                "mount_point": _get_mount(),
                "addr": addr,
            }
            bonded = AbstractProviderInstance_SDK(sdk)
            _BONDED_CLIENTS[key] = bonded
            return bonded
        except Exception as e:
            logger.error(f"Failed to bond OpenBao instance: {e}")
            return None

    @classmethod
    def _invalidate_cache(cls) -> None:
        """Drop the cached client for the current connection."""
        _BONDED_CLIENTS.pop(_connection_key(), None)

    @classmethod
    def _run_with_client(
        cls, provider_instance: Any, op: Callable[[Any, str], Any]
    ) -> Any:
        """Run ``op(client, mount_point)`` against the cached bonded client.

        On a Vault auth failure (expired/revoked token) the cached client is
        dropped and re-authenticated exactly once before retrying; any other
        error propagates unchanged. This is what lets every op reuse a single
        authenticated client while still recovering from an expired lease.
        """
        bonded = cls.bond_instance(provider_instance)
        if not bonded or not bonded.sdk:
            raise RuntimeError("OpenBao not connected")
        try:
            return op(bonded.sdk["client"], bonded.sdk["mount_point"])
        except Exception as exc:
            if not _is_auth_error(exc):
                raise
            cls._invalidate_cache()

        rebonded = cls.bond_instance(provider_instance)
        if not rebonded or not rebonded.sdk:
            raise RuntimeError("OpenBao not connected")
        return op(rebonded.sdk["client"], rebonded.sdk["mount_point"])

    @classmethod
    @ability(name="secret_read")
    def read_secret(
        cls, provider_instance, path: str, version: Optional[int] = None
    ) -> Dict[str, Any]:
        """Read a secret from KV v2."""

        def _op(client: Any, mount: str) -> Dict[str, Any]:
            kwargs: Dict[str, Any] = {"path": path, "mount_point": mount}
            if version is not None:
                kwargs["version"] = version
            response = client.secrets.kv.v2.read_secret_version(**kwargs)
            return cast(Dict[str, Any], response["data"]["data"])

        return cast(Dict[str, Any], cls._run_with_client(provider_instance, _op))

    @classmethod
    @ability(name="secret_write")
    def write_secret(
        cls, provider_instance, path: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Write/update a secret in KV v2."""

        def _op(client: Any, mount: str) -> Dict[str, Any]:
            response = client.secrets.kv.v2.create_or_update_secret(
                path=path, secret=data, mount_point=mount
            )
            return response.get("data", {}) if isinstance(response, dict) else {}

        return cast(Dict[str, Any], cls._run_with_client(provider_instance, _op))

    @classmethod
    @ability(name="secret_delete")
    def delete_secret(cls, provider_instance, path: str) -> bool:
        """Soft-delete latest version of a secret."""

        def _op(client: Any, mount: str) -> bool:
            client.secrets.kv.v2.delete_latest_version_of_secret(
                path=path, mount_point=mount
            )
            return True

        return cast(bool, cls._run_with_client(provider_instance, _op))

    @classmethod
    @ability(name="secret_list")
    def list_secrets(cls, provider_instance, path: str = "") -> List[str]:
        """List secret keys under a path."""

        def _op(client: Any, mount: str) -> List[str]:
            response = client.secrets.kv.v2.list_secrets(path=path, mount_point=mount)
            return cast(List[str], response["data"]["keys"])

        try:
            return cast(List[str], cls._run_with_client(provider_instance, _op))
        except RuntimeError:
            raise
        except Exception:
            return []

    @classmethod
    @ability(name="secret_metadata")
    def read_metadata(cls, provider_instance, path: str) -> Dict[str, Any]:
        """Read secret metadata (versions, timestamps)."""

        def _op(client: Any, mount: str) -> Dict[str, Any]:
            response = client.secrets.kv.v2.read_secret_metadata(
                path=path, mount_point=mount
            )
            return response.get("data", {}) if isinstance(response, dict) else {}

        return cast(Dict[str, Any], cls._run_with_client(provider_instance, _op))
