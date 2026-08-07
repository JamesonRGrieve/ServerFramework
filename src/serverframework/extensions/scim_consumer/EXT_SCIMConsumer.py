"""SCIM 2.0 consumer extension manifest.

Provision and deprovision local users and groups from an external SCIM 2.0
identity provider (Azure AD, Okta, etc.). The external IdP pushes user
lifecycle events (create, update, disable, delete) to this server's SCIM
endpoints.

The complementary ``scim_provider`` extension implements the *server* side
(this server pushes SCIM events to downstream service providers).
"""

from typing import Any, ClassVar, Dict, List, Set

from serverframework.extensions.AbstractExtensionProvider import AbstractStaticExtension
from serverframework.lib.Dependencies import Dependencies, PIP_Dependency
from serverframework.lib.Logging import logger


class EXT_SCIMConsumer(AbstractStaticExtension):
    name: ClassVar[str] = "scim_consumer"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Provision users and groups from an external SCIM 2.0 identity provider."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "SCIM_CONSUMER_BEARER_TOKEN": "",
        "SCIM_CONSUMER_BASE_PATH": "/scim/v2",
        "SCIM_CONSUMER_AUTO_CREATE_USERS": "true",
        "SCIM_CONSUMER_AUTO_DEACTIVATE_USERS": "true",
        "SCIM_CONSUMER_DEFAULT_ROLE": "",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="requests",
                friendly_name="HTTP requests library",
                semver=">=2.31.0",
                reason="SCIM schema discovery and outbound requests",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "scim_consumer_users",
        "scim_consumer_groups",
        "scim_consumer_schemas",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from serverframework.extensions.scim_consumer import (  # noqa: F401
            BLL_SCIMConsumer,
        )

        logger.debug("scim_consumer initialized")
        return True

    @classmethod
    def on_start(cls) -> bool:
        return True

    @classmethod
    def on_stop(cls) -> bool:
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        from serverframework.lib.Environment import env as _env

        issues: List[str] = []
        if not _env("SCIM_CONSUMER_BEARER_TOKEN"):
            issues.append(
                "SCIM_CONSUMER_BEARER_TOKEN is unset; SCIM endpoints will be unauthenticated"
            )
        return issues

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
