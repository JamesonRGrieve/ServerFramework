"""SCIM 2.0 provider extension manifest.

This server pushes user and group provisioning events to downstream
SCIM 2.0 service providers. When local users are created, updated,
or deactivated, the change is propagated to registered SCIM consumers.

The complementary ``scim_consumer`` extension implements the *receiver* side
(external IdP pushes events to this server).
"""

from typing import Any, ClassVar, Dict, List, Set

from serverframework.extensions.AbstractExtensionProvider import AbstractStaticExtension
from serverframework.lib.Dependencies import Dependencies, PIP_Dependency
from serverframework.lib.Logging import logger


class EXT_SCIMProvider(AbstractStaticExtension):
    name: ClassVar[str] = "scim_provider"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Push user and group provisioning to downstream SCIM 2.0 service providers."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "SCIM_PROVIDER_DEFAULT_TIMEOUT_SECONDS": "10",
        "SCIM_PROVIDER_RETRY_COUNT": "3",
        "SCIM_PROVIDER_BATCH_SIZE": "100",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="requests",
                friendly_name="HTTP requests library",
                semver=">=2.31.0",
                reason="Outbound SCIM provisioning requests to service providers",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "scim_provider_push_users",
        "scim_provider_push_groups",
        "scim_provider_manage_targets",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from serverframework.extensions.scim_provider import (  # noqa: F401
            BLL_SCIMProvider,
        )

        logger.debug("scim_provider initialized")
        return True

    @classmethod
    def on_start(cls) -> bool:
        return True

    @classmethod
    def on_stop(cls) -> bool:
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        return []

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
