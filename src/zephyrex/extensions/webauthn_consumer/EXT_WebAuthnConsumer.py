"""WebAuthn consumer extension manifest.

Authenticate local users via WebAuthn/FIDO2/Passkeys. Users register
hardware security keys or platform authenticators (Touch ID, Windows Hello)
and authenticate with cryptographic challenge-response instead of passwords.

The complementary ``webauthn_provider`` extension implements the *server*
side (this server acts as a WebAuthn relying party for third-party consumers).
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_WebAuthnConsumer(AbstractStaticExtension):
    name: ClassVar[str] = "webauthn_consumer"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Authenticate users via WebAuthn/FIDO2/Passkeys."

    _env: ClassVar[Dict[str, Any]] = {
        "WEBAUTHN_CONSUMER_RP_ID": "",
        "WEBAUTHN_CONSUMER_RP_NAME": "",
        "WEBAUTHN_CONSUMER_ORIGIN": "",
        "WEBAUTHN_CONSUMER_ATTESTATION": "none",
        "WEBAUTHN_CONSUMER_USER_VERIFICATION": "preferred",
        "WEBAUTHN_CONSUMER_TIMEOUT_MS": "60000",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="fido2",
                friendly_name="FIDO2/WebAuthn library",
                semver=">=1.1.0",
                reason="WebAuthn credential registration and authentication",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "webauthn_consumer_register",
        "webauthn_consumer_authenticate",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.webauthn_consumer import (  # noqa: F401
            BLL_WebAuthnConsumer,
        )

        logger.debug("webauthn_consumer initialized")
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        from zephyrex.lib.Environment import env as _env

        issues: List[str] = []
        if not _env("WEBAUTHN_CONSUMER_RP_ID"):
            issues.append("WEBAUTHN_CONSUMER_RP_ID is unset; relying party ID required")
        if not _env("WEBAUTHN_CONSUMER_ORIGIN"):
            issues.append(
                "WEBAUTHN_CONSUMER_ORIGIN is unset; origin validation will fail"
            )
        return issues
