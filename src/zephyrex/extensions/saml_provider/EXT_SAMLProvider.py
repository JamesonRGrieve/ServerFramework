# SPDX-License-Identifier: AGPL-3.0-or-later
"""This server as a SAML 2.0 Identity Provider for external Service
Providers: apps that let users sign in with their account here.

Setting it up:

1. Give the IdP a signing key: a ``saml_idp_signing_key`` provider instance
   with ``signing_certificate`` and ``signing_key`` (the key is a secret
   setting, stored encrypted and never returned), or the
   ``SAML_PROVIDER_SIGNING_CERTIFICATE`` / ``SAML_PROVIDER_SIGNING_KEY``
   environment values.
2. Set ``SAML_PROVIDER_BASE_URL`` to the origin SPs reach the server at
   (``SERVER_URI`` otherwise); the entity id is
   ``<base>/v1/saml_provider/metadata`` unless ``SAML_PROVIDER_ENTITY_ID``
   names another. ``SAML_PROVIDER_LOGIN_URL`` is where a browser without a
   session is sent to sign in (``/user`` by default).
3. As root, register each SP at ``/v1/saml_provider/service_provider``:
   entity id, ACS URLs (matched exactly), its certificate (to verify its
   signed AuthnRequests, and to encrypt assertions to it), whether its
   requests must be signed, its NameID format and the attributes it gets.

IdP-initiated sign-in (``GET /v1/saml_provider/initiate?sp=<entity id>``)
is off for every SP; set ``allow_idp_initiated`` on an SP's registration to
allow it for that SP. The protocol is described in ``BLL_SAMLProvider``.
"""

from typing import Any, ClassVar, Dict, Set

from fastapi import HTTPException

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.extensions.saml_provider.BLL_SAMLProvider import (
    metadata_document,
    off_loop,
)
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency, SYS_Dependency
from zephyrex.pydantic2.registry import ModelRegistry


class EXT_SAMLProvider(AbstractStaticExtension):
    name: ClassVar[str] = "saml_provider"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = "Run this server as a SAML 2.0 Identity Provider."

    _env: ClassVar[Dict[str, Any]] = {
        "SAML_PROVIDER_BASE_URL": "",
        "SAML_PROVIDER_ENTITY_ID": "",
        "SAML_PROVIDER_LOGIN_URL": "/user",
        "SAML_PROVIDER_ASSERTION_LIFETIME_SECONDS": "300",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="pysaml2",
                friendly_name="pysaml2",
                semver=">=7.5.5",
                reason="SAML 2.0 messages, metadata, signatures and encryption",
            ),
            SYS_Dependency.for_apt(
                "xmlsec1",
                "xmlsec1",
                friendly_name="xmlsec1",
                reason="Signs and encrypts the IdP's XML (pysaml2 runs it)",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {"saml_idp_metadata"}

    @classmethod
    @ability("saml_idp_metadata")
    async def saml_idp_metadata(cls) -> Dict[str, str]:
        """The IdP's entity id and SAML metadata, to give an SP."""
        registry = ModelRegistry.attached()
        if registry is None:
            raise HTTPException(status_code=503, detail=f"{cls.name}: no running app")
        entity_id, metadata = await off_loop(lambda: metadata_document(registry))
        return {"entity_id": entity_id, "metadata": metadata}
