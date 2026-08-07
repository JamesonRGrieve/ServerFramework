"""OpenID Connect provider extension manifest.

This server acts as an OpenID Connect identity provider, layering OIDC
identity semantics (ID tokens, userinfo endpoint, discovery document,
JWKS) on top of the OAuth2 provider. Third-party relying parties
authenticate users via the standard OIDC authorization code flow.

The complementary ``oidc_consumer`` extension implements the *client* side
(authenticate against an external OIDC provider).
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_OIDCProvider(AbstractStaticExtension):
    name: ClassVar[str] = "oidc_provider"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Run this server as an OpenID Connect identity provider."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "OIDC_PROVIDER_ISSUER_URL": "",
        "OIDC_PROVIDER_SIGNING_KEY_PATH": "",
        "OIDC_PROVIDER_SIGNING_ALGORITHM": "RS256",
        "OIDC_PROVIDER_ID_TOKEN_TTL_MINUTES": "60",
        "OIDC_PROVIDER_SUPPORTED_SCOPES": "openid profile email",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="PyJWT",
                friendly_name="JWT library",
                semver=">=2.8.0",
                reason="ID token signing and encoding",
            ),
            PIP_Dependency(
                name="cryptography",
                friendly_name="Cryptographic library",
                semver=">=41.0.0",
                reason="RSA/EC key management for JWKS and ID token signing",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "oidc_provider_discovery",
        "oidc_provider_jwks",
        "oidc_provider_id_token",
        "oidc_provider_userinfo",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session", "oauth_provider"]

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.oidc_provider import (  # noqa: F401
            BLL_OIDCProvider,
        )

        logger.debug("oidc_provider initialized")
        return True

    @classmethod
    def on_start(cls) -> bool:
        return True

    @classmethod
    def on_stop(cls) -> bool:
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        from zephyrex.lib.Environment import env as _env

        issues: List[str] = []
        if not _env("OIDC_PROVIDER_ISSUER_URL"):
            issues.append("OIDC_PROVIDER_ISSUER_URL is unset; discovery document incomplete")
        if not _env("OIDC_PROVIDER_SIGNING_KEY_PATH"):
            issues.append("OIDC_PROVIDER_SIGNING_KEY_PATH is unset; ID token signing will fail")
        return issues

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
