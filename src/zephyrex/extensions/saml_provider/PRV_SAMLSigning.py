# SPDX-License-Identifier: AGPL-3.0-or-later
"""The key the SAML Identity Provider signs with.

An instance of this provider holds one signing identity: an RSA private key
(a secret setting, stored encrypted and never returned once written) and
the X.509 certificate the IdP publishes for it in its metadata. Without an
instance, the identity comes from ``SAML_PROVIDER_SIGNING_KEY`` and
``SAML_PROVIDER_SIGNING_CERTIFICATE`` (as an operator's secret store
injects them). The newest enabled instance is the one used.
"""

from typing import Any, ClassVar, Dict, Optional, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticProvider,
    InstanceSetting,
)
from zephyrex.extensions.saml_provider.IdP import SigningIdentity
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

CERTIFICATE_SETTING = "signing_certificate"
KEY_SETTING = "signing_key"


class SigningKeyMissing(LookupError):
    """No signing identity is configured, or the one configured is unusable.
    The text names what to fix, never a key's value."""


class PRV_SAMLSigningKey(AbstractStaticProvider):
    name: ClassVar[str] = "saml_idp_signing_key"
    friendly_name: ClassVar[str] = "SAML IdP signing key"
    description: ClassVar[str] = (
        "The key and certificate this server's SAML Identity Provider signs with"
    )
    _abilities: ClassVar[Set[str]] = set()
    _env: ClassVar[Dict[str, Any]] = {}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            CERTIFICATE_SETTING,
            "The IdP's X.509 signing certificate (PEM), published in its metadata",
            env="SAML_PROVIDER_SIGNING_CERTIFICATE",
            multiline=True,
        ),
        InstanceSetting(
            KEY_SETTING,
            "The certificate's RSA private key (PEM, unencrypted)",
            env="SAML_PROVIDER_SIGNING_KEY",
            secret=True,
            multiline=True,
        ),
    )

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def identity(cls, instance: Optional[ProviderInstanceModel]) -> SigningIdentity:
        """The signing identity ``instance`` holds (the environment's, for
        None), checked to be an RSA key and its certificate."""
        certificate = cls.setting(instance, CERTIFICATE_SETTING)
        key = cls.setting(instance, KEY_SETTING)
        if not certificate or not key:
            raise SigningKeyMissing(
                "the SAML IdP has no signing key: create a "
                f"{cls.name} provider instance with {CERTIFICATE_SETTING} and "
                f"{KEY_SETTING}, or set SAML_PROVIDER_SIGNING_CERTIFICATE and "
                "SAML_PROVIDER_SIGNING_KEY"
            )
        try:
            return SigningIdentity.checked(certificate, key)
        except ValueError as exc:
            raise SigningKeyMissing(
                f"the SAML IdP's signing identity is unusable: {exc}"
            ) from exc
