# SPDX-License-Identifier: AGPL-3.0-or-later
"""Users sign in through external SAML 2.0 identity providers: this server
is the Service Provider (SP-initiated HTTP-Redirect AuthnRequests, the
HTTP-POST Assertion Consumer Service, SP metadata, IdP metadata import).
See BLL_SAMLConsumer for the routes and SAMLProtocol for what a response
must pass.

The complementary ``saml_provider`` extension makes this server an IdP.
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.extensions.saml_consumer.BLL_SAMLConsumer import (
    SamlIdentityManager,
    SamlIdentityProviderManager,
)
from zephyrex.lib.Dependencies import (
    Dependencies,
    EXT_Dependency,
    PIP_Dependency,
    SYS_Dependency,
)
from zephyrex.lib.SecretEncryption import (
    MissingFernetKeyError,
    assert_encryption_available,
)


class EXT_SAMLConsumer(AbstractStaticExtension):
    name: ClassVar[str] = "saml_consumer"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = "Sign in through external SAML 2.0 identity providers"

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="auth_session",
                friendly_name="Sessions",
                reason="A SAML sign-in issues a persisted session",
            ),
            PIP_Dependency(
                name="pysaml2",
                friendly_name="pysaml2",
                semver=">=7.5.5,<8",
                reason="SAML 2.0 messages, signatures (via xmlsec1) and metadata",
            ),
            PIP_Dependency(
                name="defusedxml",
                friendly_name="defusedxml",
                semver=">=0.7.1",
                reason="Parsing untrusted SAML XML without entity expansion",
            ),
            SYS_Dependency.for_apt(
                "xmlsec1",
                "xmlsec1",
                friendly_name="xmlsec1",
                reason="Checking and making XML signatures, decrypting assertions",
            ),
        ]
    )
    _abilities: ClassVar[Set[str]] = {
        "list_saml_identity_providers",
        "list_saml_identities",
    }

    @classmethod
    def validate_config(cls) -> List[str]:
        try:
            assert_encryption_available()
        except MissingFernetKeyError:
            return ["FRAMEWORK_FERNET_KEY is unset: SP private keys cannot be stored"]
        return []

    @classmethod
    @ability("list_saml_identity_providers")
    async def list_saml_identity_providers(
        cls, requester_id: str
    ) -> List[Dict[str, Any]]:
        """The IdPs users can sign in through: id, name and login URL."""
        manager: SamlIdentityProviderManager = cls.as_requester(
            SamlIdentityProviderManager, requester_id
        )
        listed = manager.list_enabled_route()
        return [entry.model_dump() for entry in listed.identity_providers]

    @classmethod
    @ability("list_saml_identities")
    async def list_saml_identities(cls, requester_id: str) -> List[Dict[str, Any]]:
        """The user's identities at SAML IdPs: which IdP, and the subject."""
        manager: SamlIdentityManager = cls.as_requester(
            SamlIdentityManager, requester_id
        )
        return [identity.model_dump(mode="json") for identity in manager.list()]
