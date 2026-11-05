# SPDX-License-Identifier: AGPL-3.0-or-later
"""The certificate authority the x509 provider signs with.

An instance of this provider holds one CA: its certificate (published) and
its private key (a secret setting: stored encrypted at rest with the
framework's key, never returned once written). Root generates or imports a
CA through ``/v1/x509_provider/ca/generate`` and ``/ca/import``, which store
it as an instance. Without an instance, the CA comes from
``X509_PROVIDER_CA_CERTIFICATE`` and ``X509_PROVIDER_CA_KEY``, as an
operator's secret store (OpenBao) injects them. The newest enabled instance
issues; every instance, enabled or not, still signs its own CRL.
"""

from typing import Any, ClassVar, Dict, Optional, Set, Tuple

from cryptography import x509

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticProvider,
    InstanceSetting,
)
from zephyrex.extensions.x509_provider.CertificateAuthority import (
    Authority,
    CertificateRefused,
    load_certificate,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

CERTIFICATE_SETTING = "ca_certificate"
KEY_SETTING = "ca_key"


class AuthorityMissing(LookupError):
    """No CA is configured, or the one configured is unusable. The text
    names what to fix, never a key's value."""


class PRV_X509CertificateAuthority(AbstractStaticProvider):
    name: ClassVar[str] = "x509_certificate_authority"
    friendly_name: ClassVar[str] = "X.509 certificate authority"
    description: ClassVar[str] = (
        "The CA certificate and key this server issues client certificates with"
    )
    _abilities: ClassVar[Set[str]] = set()
    _env: ClassVar[Dict[str, Any]] = {}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            CERTIFICATE_SETTING,
            "The CA's X.509 certificate (PEM)",
            env="X509_PROVIDER_CA_CERTIFICATE",
            multiline=True,
        ),
        InstanceSetting(
            KEY_SETTING,
            "The CA certificate's private key (PEM, unencrypted PKCS#8)",
            env="X509_PROVIDER_CA_KEY",
            secret=True,
            multiline=True,
        ),
    )

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def certificate(
        cls, instance: Optional[ProviderInstanceModel]
    ) -> Optional[x509.Certificate]:
        """The CA certificate ``instance`` holds (the environment's, for
        None), without touching its key; None when it holds none."""
        pem = cls.setting(instance, CERTIFICATE_SETTING)
        if not pem:
            return None
        try:
            return load_certificate(pem)
        except CertificateRefused:
            return None

    @classmethod
    def authority(cls, instance: Optional[ProviderInstanceModel]) -> Authority:
        """The CA ``instance`` holds (the environment's, for None), its key
        checked to be the certificate's and the certificate a CA's."""
        certificate = cls.setting(instance, CERTIFICATE_SETTING)
        key = cls.setting(instance, KEY_SETTING)
        if not certificate or not key:
            raise AuthorityMissing(
                "the x509 provider has no certificate authority: generate or "
                "import one, or set X509_PROVIDER_CA_CERTIFICATE and "
                "X509_PROVIDER_CA_KEY"
            )
        try:
            return Authority.loaded(certificate, key)
        except CertificateRefused as exc:
            raise AuthorityMissing(
                f"the x509 provider's certificate authority is unusable: {exc}"
            ) from exc
