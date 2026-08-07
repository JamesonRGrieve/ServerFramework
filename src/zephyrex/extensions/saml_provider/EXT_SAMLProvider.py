"""SAML 2.0 Identity Provider extension manifest.

This server acts as a SAML 2.0 Identity Provider (IdP), issuing signed
assertions to registered Service Providers. Supports HTTP-POST and
HTTP-Redirect bindings, configurable attribute statements, and single
logout.

The complementary ``saml_consumer`` extension implements the *client* side
(Service Provider authenticating against an external IdP).
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_SAMLProvider(AbstractStaticExtension):
    name: ClassVar[str] = "saml_provider"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Run this server as a SAML 2.0 Identity Provider."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "SAML_PROVIDER_ENTITY_ID": "",
        "SAML_PROVIDER_SSO_URL": "",
        "SAML_PROVIDER_SLO_URL": "",
        "SAML_PROVIDER_CERT_PATH": "",
        "SAML_PROVIDER_KEY_PATH": "",
        "SAML_PROVIDER_ASSERTION_TTL_MINUTES": "5",
        "SAML_PROVIDER_SIGN_ASSERTIONS": "true",
        "SAML_PROVIDER_SIGN_RESPONSES": "true",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="pysaml2",
                friendly_name="SAML 2.0 library",
                semver=">=7.0.0",
                reason="SAML assertion generation, signing, and IdP metadata",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "saml_provider_sso",
        "saml_provider_slo",
        "saml_provider_metadata",
        "saml_provider_manage_sp",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.saml_provider import (  # noqa: F401
            BLL_SAMLProvider,
        )

        logger.debug("saml_provider initialized")
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
        if not _env("SAML_PROVIDER_ENTITY_ID"):
            issues.append("SAML_PROVIDER_ENTITY_ID is unset; IdP metadata incomplete")
        if not _env("SAML_PROVIDER_CERT_PATH"):
            issues.append("SAML_PROVIDER_CERT_PATH is unset; assertion signing will fail")
        return issues

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
