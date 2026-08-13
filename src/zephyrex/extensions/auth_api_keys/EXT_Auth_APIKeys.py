"""Extension manifest for auth_api_keys.

Owns ``APIKeyModel`` and the ``APIKeyManager`` issue/validate/revoke/rotate
flow. Keys are stored hashed; the raw value is returned exactly once at
issuance.
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Logging import logger


class EXT_Auth_APIKeys(AbstractStaticExtension):
    name: ClassVar[str] = "auth_api_keys"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "API-key authentication: issue, validate, revoke, and rotate hashed-at-rest keys."
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])

    _abilities: ClassVar[Set[str]] = {
        "api_key_issue",
        "api_key_validate",
        "api_key_revoke",
        "api_key_rotate",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.auth_api_keys import (  # noqa: F401
            BLL_Auth_APIKeys,
        )
        from zephyrex.extensions.auth_api_keys.BLL_Auth_APIKeys import (
            register_merge_participation,
        )

        register_merge_participation()
        logger.debug("auth_api_keys initialized")
        return True
