# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Kerberos service this server accepts sign-ins for, by its keytab.

Each provider instance is one service principal (``HTTP/sso.example.com@
EXAMPLE.COM``, registered in an Active Directory or MIT realm) and the
keytab holding its keys. Accepting a client's GSSAPI token needs no KDC:
the ticket inside it is decrypted with the service key.

The keytab is a write-only secret, stored base64-encoded. It reaches the
disk only while one token is being accepted, as a file readable by this
process alone in a private temporary directory, removed straight after;
the GSSAPI credential store reads it from there.

Replay protection, ticket expiry and clock skew are GSSAPI's own (the
acceptor's replay cache stays on). A token is accepted only when the
context completes in one round, as HTTP Negotiate needs.
"""

import base64
import binascii
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Dict, FrozenSet, Optional, Set, Tuple

import gssapi
from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding
from gssapi.raw import ChannelBindings

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticProvider,
    InstanceSetting,
)
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

KEYTAB_FILE_MODE = 0o600
KEYTAB_FILE_NAME = "service.keytab"
# RFC 5929 section 4.1: the certificate's own signature hash, except that
# MD5 and SHA-1 are replaced by SHA-256 (as is a signature with no hash).
_WEAK_CERTIFICATE_HASHES = frozenset({"md5", "sha1"})
TLS_SERVER_END_POINT_PREFIX = b"tls-server-end-point:"
ANONYMOUS_REALM = "WELLKNOWN:ANONYMOUS"


@dataclass(frozen=True)
class AcceptedPrincipal:
    """A client the service accepted: its realm-qualified principal, and
    the token answering it (mutual authentication), if the client asked."""

    principal: str
    realm: str
    service: str
    reply_token: Optional[bytes]


def principal_realm(principal: str) -> str:
    """The realm of a realm-qualified principal (``alice@EXAMPLE.COM``).
    A component may hold an escaped ``\\@``; the realm follows the last
    unescaped one."""
    index = len(principal)
    while True:
        index = principal.rfind("@", 0, index)
        if index <= 0:
            raise InvalidInputExternalError(
                f"Kerberos principal {principal!r} names no realm"
            )
        backslashes = len(principal[:index]) - len(principal[:index].rstrip("\\"))
        if backslashes % 2 == 0:
            realm = principal[index + 1 :]
            if not realm:
                raise InvalidInputExternalError(
                    f"Kerberos principal {principal!r} names no realm"
                )
            return realm


def realm_list(value: Optional[str]) -> FrozenSet[str]:
    """Realms from a comma- or whitespace-separated list. Kerberos realm
    names are case-sensitive, so they are kept as written."""
    return frozenset((value or "").replace(",", " ").split())


def decode_keytab(value: str) -> bytes:
    """The keytab's bytes from its base64 setting (line breaks allowed)."""
    try:
        keytab = base64.b64decode("".join(value.split()), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PermanentExternalError(
            "The service keytab setting is not base64"
        ) from exc
    if not keytab:
        raise PermanentExternalError("The service keytab setting is empty")
    return keytab


def tls_server_end_point(certificate_pem: str) -> bytes:
    """The ``tls-server-end-point`` channel binding (RFC 5929) of the TLS
    certificate clients see when they reach this server."""
    try:
        certificate = x509.load_pem_x509_certificate(certificate_pem.encode())
    except ValueError as exc:
        raise PermanentExternalError(
            "The channel binding certificate is not a PEM certificate"
        ) from exc
    try:
        signature_hash = certificate.signature_hash_algorithm
    except UnsupportedAlgorithm:
        signature_hash = None
    algorithm: hashes.HashAlgorithm = (
        signature_hash
        if signature_hash is not None
        and signature_hash.name not in _WEAK_CERTIFICATE_HASHES
        else hashes.SHA256()
    )
    digest = hashes.Hash(algorithm)
    digest.update(certificate.public_bytes(Encoding.DER))
    return TLS_SERVER_END_POINT_PREFIX + digest.finalize()


class PRV_KerberosKeytab(AbstractStaticProvider):
    name: ClassVar[str] = "kerberos_keytab"
    friendly_name: ClassVar[str] = "Kerberos service keytab"
    description: ClassVar[str] = (
        "A Kerberos service principal and its keytab, accepting HTTP Negotiate"
    )
    _abilities: ClassVar[Set[str]] = set()
    _env: ClassVar[Dict[str, str]] = {}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "keytab",
            "The service principal's keytab, base64-encoded (base64 -w0 http.keytab)",
            env="KERBEROS_CONSUMER_KEYTAB",
            secret=True,
            multiline=True,
        ),
        InstanceSetting(
            "service_principal",
            "The service principal clients ask tickets for "
            "(HTTP/sso.example.com@EXAMPLE.COM); empty accepts any in the keytab",
            env="KERBEROS_CONSUMER_SERVICE_PRINCIPAL",
        ),
        InstanceSetting(
            "allowed_realms",
            "Realms whose users may sign in, comma-separated (EXAMPLE.COM, "
            "CORP.EXAMPLE.COM); empty means only the service principal's realm",
            env="KERBEROS_CONSUMER_ALLOWED_REALMS",
        ),
        InstanceSetting(
            "channel_binding_certificate",
            "The TLS certificate (PEM) clients see; when set, a client's "
            "channel bindings must match it (RFC 5929 tls-server-end-point)",
            env="KERBEROS_CONSUMER_CHANNEL_BINDING_CERTIFICATE",
            multiline=True,
        ),
    )

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def is_configured_instance(cls, instance: ProviderInstanceModel) -> bool:
        return bool(cls.setting(instance, "keytab"))

    @classmethod
    def service_name(cls, instance: ProviderInstanceModel) -> Optional[str]:
        principal = (cls.setting(instance, "service_principal") or "").strip()
        if not principal:
            return None
        if "@" not in principal:
            raise PermanentExternalError(
                "The service principal must name its realm "
                "(HTTP/sso.example.com@EXAMPLE.COM)"
            )
        return principal

    @classmethod
    def allowed_realms(cls, instance: ProviderInstanceModel) -> FrozenSet[str]:
        listed = realm_list(cls.setting(instance, "allowed_realms"))
        if listed:
            return listed
        service = cls.service_name(instance)
        return frozenset({principal_realm(service)}) if service else frozenset()

    @classmethod
    def channel_bindings(
        cls, instance: ProviderInstanceModel
    ) -> Optional[ChannelBindings]:
        certificate = cls.setting(instance, "channel_binding_certificate")
        if not certificate or not certificate.strip():
            return None
        return ChannelBindings(application_data=tls_server_end_point(certificate))

    @classmethod
    def acceptor(cls, instance: ProviderInstanceModel) -> "Acceptor":
        """Everything accepting a token for ``instance`` needs, read from its
        settings (the database) before the GSSAPI work leaves the event
        loop."""
        keytab = cls.setting(instance, "keytab")
        if not keytab:
            raise PermanentExternalError("No service keytab is configured")
        return Acceptor(
            keytab=decode_keytab(keytab),
            service=cls.service_name(instance),
            allowed_realms=cls.allowed_realms(instance),
            bindings=cls.channel_bindings(instance),
        )


@dataclass(frozen=True)
class Acceptor:
    """Accepts one GSSAPI (Kerberos or SPNEGO) initiator token."""

    # Never in a repr: a traceback or log line must not carry the keys.
    keytab: bytes = field(repr=False)
    service: Optional[str]
    allowed_realms: FrozenSet[str]
    bindings: Optional[ChannelBindings]

    def accept(self, token: bytes) -> AcceptedPrincipal:
        """The client ``token`` authenticates, or InvalidInputExternalError
        when GSSAPI refuses it (another service's ticket, expired, replayed,
        mismatched channel bindings, a multi-round exchange)."""
        with tempfile.TemporaryDirectory(prefix="zx-keytab-") as directory:
            path = Path(directory) / KEYTAB_FILE_NAME
            descriptor = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, KEYTAB_FILE_MODE
            )
            with os.fdopen(descriptor, "wb") as keytab_file:
                keytab_file.write(self.keytab)
            return self._accept(token, f"FILE:{path}")

    def _accept(self, token: bytes, keytab: str) -> AcceptedPrincipal:
        try:
            service_name = (
                gssapi.Name(self.service, gssapi.NameType.kerberos_principal)
                if self.service
                else None
            )
            credentials = gssapi.Credentials(
                name=service_name, usage="accept", store={"keytab": keytab}
            )
        except gssapi.exceptions.GSSError as exc:
            raise PermanentExternalError(
                f"The keytab holds no usable key for the service: {exc}"
            ) from exc
        context = gssapi.SecurityContext(
            creds=credentials, usage="accept", channel_bindings=self.bindings
        )
        # A refusal under SPNEGO comes back as a reject token: step() returns
        # it and the error surfaces on the context's next read.
        try:
            reply = context.step(token)
            complete = context.complete
            if complete:
                flags = context.actual_flags
                principal = str(context.initiator_name)
                target = str(context.target_name)
        except gssapi.exceptions.GSSError as exc:
            raise InvalidInputExternalError(
                f"Kerberos refused the token: {exc}"
            ) from exc
        if not complete:
            raise InvalidInputExternalError(
                "The Negotiate exchange needs more than one round"
            )
        if gssapi.RequirementFlag.anonymity in flags:
            raise InvalidInputExternalError("Anonymous Kerberos sign-in is refused")
        if self.service is not None and target != self.service:
            raise InvalidInputExternalError(
                f"The ticket is for {target!r}, not this service"
            )
        realm = principal_realm(principal)
        if realm == ANONYMOUS_REALM:
            raise InvalidInputExternalError("Anonymous Kerberos sign-in is refused")
        return AcceptedPrincipal(
            principal=principal,
            realm=realm,
            service=target,
            reply_token=reply or None,
        )
