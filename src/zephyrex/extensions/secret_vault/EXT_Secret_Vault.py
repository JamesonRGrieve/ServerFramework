# SPDX-License-Identifier: AGPL-3.0-or-later
"""Secret vault extension — OpenBao/Vault KV v2 secret management.

Wraps the KV v2 secrets engine into the provider/ability pattern so
consumer apps can read/write/list/delete secrets through the standard
extension surface. Auth supports token and AppRole.
"""

from __future__ import annotations

from abc import abstractmethod
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional, Set, Type

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance_SDK,
    AbstractStaticProvider,
    HealthReport,
    HealthStatus,
    ability,
)
from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
)
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Logging import logger


class Capability(str, Enum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    LIST = "list"
    METADATA = "metadata"
    TRANSIT = "transit"


class AbstractSecretVaultProvider(AbstractStaticProvider):
    """Base class for secret vault providers."""

    extension: ClassVar[Optional[Type[AbstractStaticExtension]]] = None

    @classmethod
    @abstractmethod
    @ability(name="secret_read")
    def read_secret(cls, provider_instance, path: str, version: Optional[int] = None) -> Dict[str, Any]:
        ...

    @classmethod
    @abstractmethod
    @ability(name="secret_write")
    def write_secret(cls, provider_instance, path: str, data: Dict[str, Any]) -> Dict[str, Any]:
        ...

    @classmethod
    @abstractmethod
    @ability(name="secret_delete")
    def delete_secret(cls, provider_instance, path: str) -> bool:
        ...

    @classmethod
    @abstractmethod
    @ability(name="secret_list")
    def list_secrets(cls, provider_instance, path: str = "") -> List[str]:
        ...

    @classmethod
    @abstractmethod
    @ability(name="secret_metadata")
    def read_metadata(cls, provider_instance, path: str) -> Dict[str, Any]:
        ...


class EXT_Secret_Vault(AbstractStaticExtension):
    name: ClassVar[str] = "secret_vault"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Secret vault management via OpenBao/Vault KV v2."

    _env: ClassVar[Dict[str, Any]] = {
        "OPENBAO_ADDR": "",
        "OPENBAO_TOKEN": "",
        "OPENBAO_ROLE_ID": "",
        "OPENBAO_SECRET_ID": "",
        "OPENBAO_MOUNT_POINT": "secret",
        "OPENBAO_NAMESPACE": "",
        "VAULT_ADDR": "",
        "VAULT_TOKEN": "",
    }
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="hvac",
                friendly_name="HashiCorp Vault Client",
                semver=">=2.1.0",
                reason="OpenBao/Vault API client for KV v2 secret management",
                optional=True,
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "secret_read",
        "secret_write",
        "secret_delete",
        "secret_list",
        "secret_metadata",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def on_initialize(cls) -> bool:
        logger.debug("secret_vault initialized")
        return True
