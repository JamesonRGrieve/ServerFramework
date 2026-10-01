"""SAML 2.0 consumer (Service Provider) extension manifest.

Authenticate local users against an external SAML 2.0 Identity Provider.
Supports HTTP-POST and HTTP-Redirect bindings, signed assertions, and
attribute mapping from SAML assertions to local user attributes.

The complementary ``saml_provider`` extension implements the *server* side
(this server acts as a SAML Identity Provider).
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_SAMLConsumer(AbstractStaticExtension):
    name: ClassVar[str] = "saml_consumer"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Authenticate users via SAML 2.0 against an external Identity Provider."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "SAML_CONSUMER_ENTITY_ID": "",
        "SAML_CONSUMER_ACS_URL": "",
        "SAML_CONSUMER_SLS_URL": "",
        "SAML_CONSUMER_IDP_METADATA_URL": "",
        "SAML_CONSUMER_IDP_SSO_URL": "",
        "SAML_CONSUMER_IDP_SLO_URL": "",
        "SAML_CONSUMER_IDP_CERT_PATH": "",
        "SAML_CONSUMER_SP_CERT_PATH": "",
        "SAML_CONSUMER_SP_KEY_PATH": "",
        "SAML_CONSUMER_WANT_ASSERTIONS_SIGNED": "true",
        "SAML_CONSUMER_WANT_RESPONSE_SIGNED": "true",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="pysaml2",
                friendly_name="SAML 2.0 library",
                semver=">=7.0.0",
                reason="SAML assertion parsing, validation, and SP metadata generation",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "saml_consumer_login",
        "saml_consumer_acs",
        "saml_consumer_sls",
        "saml_consumer_metadata",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.saml_consumer import (  # noqa: F401
            BLL_SAMLConsumer,
        )

        logger.debug("saml_consumer initialized")
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        from zephyrex.lib.Environment import env as _env

        issues: List[str] = []
        if not _env("SAML_CONSUMER_ENTITY_ID"):
            issues.append("SAML_CONSUMER_ENTITY_ID is unset; SP metadata incomplete")
        if not _env("SAML_CONSUMER_ACS_URL"):
            issues.append(
                "SAML_CONSUMER_ACS_URL is unset; assertion consumer service undefined"
            )
        return issues
