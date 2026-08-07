"""OpenID Connect consumer extension manifest.

Authenticate local users against an external OpenID Connect provider
(Keycloak, Auth0, Okta, Azure AD, etc.) using the authorization code
flow with PKCE. Validates ID tokens, discovers provider metadata via
.well-known/openid-configuration, and maps claims to local user attributes.

The complementary ``oidc_provider`` extension implements the *server* side
(this server acts as an OpenID Connect identity provider).
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_OIDCConsumer(AbstractStaticExtension):
    name: ClassVar[str] = "oidc_consumer"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Authenticate users via OpenID Connect against an external identity provider."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "OIDC_CONSUMER_ISSUER_URL": "",
        "OIDC_CONSUMER_CLIENT_ID": "",
        "OIDC_CONSUMER_CLIENT_SECRET": "",
        "OIDC_CONSUMER_REDIRECT_URI": "",
        "OIDC_CONSUMER_SCOPES": "openid profile email",
        "OIDC_CONSUMER_RESPONSE_TYPE": "code",
        "OIDC_CONSUMER_USE_PKCE": "true",
        "OIDC_CONSUMER_CLAIM_MAPPING_EMAIL": "email",
        "OIDC_CONSUMER_CLAIM_MAPPING_NAME": "name",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="requests",
                friendly_name="HTTP requests library",
                semver=">=2.31.0",
                reason="OIDC discovery, token exchange, and userinfo requests",
            ),
            PIP_Dependency(
                name="PyJWT",
                friendly_name="JWT library",
                semver=">=2.8.0",
                reason="ID token validation and decoding",
            ),
            PIP_Dependency(
                name="cryptography",
                friendly_name="Cryptographic library",
                semver=">=41.0.0",
                reason="JWKS key import for ID token signature verification",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "oidc_consumer_authorize",
        "oidc_consumer_callback",
        "oidc_consumer_userinfo",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.oidc_consumer import (  # noqa: F401
            BLL_OIDCConsumer,
        )

        logger.debug("oidc_consumer initialized")
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
        if not _env("OIDC_CONSUMER_ISSUER_URL"):
            issues.append("OIDC_CONSUMER_ISSUER_URL is unset; discovery will fail")
        if not _env("OIDC_CONSUMER_CLIENT_ID"):
            issues.append("OIDC_CONSUMER_CLIENT_ID is unset; auth flow will fail")
        return issues

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
