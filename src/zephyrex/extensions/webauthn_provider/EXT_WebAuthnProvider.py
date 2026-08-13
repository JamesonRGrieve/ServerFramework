"""WebAuthn provider extension manifest.

This server acts as a WebAuthn relying party, exposing FIDO2 registration
and assertion endpoints so third-party applications can delegate passkey
authentication here. Manages relying-party configuration and credential
storage on behalf of external consumers.

The complementary ``webauthn_consumer`` extension implements the *client*
side (authenticate local users via their own authenticators).
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_WebAuthnProvider(AbstractStaticExtension):
    name: ClassVar[str] = "webauthn_provider"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Run this server as a WebAuthn relying party for third-party consumers."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "WEBAUTHN_PROVIDER_RP_ID": "",
        "WEBAUTHN_PROVIDER_RP_NAME": "",
        "WEBAUTHN_PROVIDER_ORIGIN": "",
        "WEBAUTHN_PROVIDER_ATTESTATION": "none",
        "WEBAUTHN_PROVIDER_USER_VERIFICATION": "preferred",
        "WEBAUTHN_PROVIDER_TIMEOUT_MS": "60000",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="fido2",
                friendly_name="FIDO2/WebAuthn library",
                semver=">=1.1.0",
                reason="WebAuthn relying-party ceremony implementation",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "webauthn_provider_register",
        "webauthn_provider_authenticate",
        "webauthn_provider_manage_rp",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.webauthn_provider import (  # noqa: F401
            BLL_WebAuthnProvider,
        )

        logger.debug("webauthn_provider initialized")
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        from zephyrex.lib.Environment import env as _env

        issues: List[str] = []
        if not _env("WEBAUTHN_PROVIDER_RP_ID"):
            issues.append("WEBAUTHN_PROVIDER_RP_ID is unset; relying party ID required")
        if not _env("WEBAUTHN_PROVIDER_ORIGIN"):
            issues.append(
                "WEBAUTHN_PROVIDER_ORIGIN is unset; origin validation will fail"
            )
        return issues
