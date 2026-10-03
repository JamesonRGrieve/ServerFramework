# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign in with an X.509 client certificate (mutual TLS).

``POST /v1/auth/x509/login`` verifies the certificate the client presented
(forwarded by a trusted TLS terminator, or handed over by an ASGI server
that terminates TLS itself) against the trust anchors ROOT manages at
``/v1/auth/x509/trust-anchor``, checks its revocation, maps it to a local
user and issues the same session as password login. See
``BLL_X509Consumer`` for the trust rules.

- ``X509_CONSUMER_TRUSTED_PROXIES``: comma-separated addresses or CIDRs of
  the TLS terminators whose forwarded certificate header is believed (as
  the app sees the peer). Empty: no header is ever believed.
- ``X509_CONSUMER_CERT_HEADER``: the header they forward the certificate
  in (URL-encoded PEM, as nginx's ``$ssl_client_escaped_cert``, or base64
  DER). Default ``X-SSL-Client-Cert``.

The complementary ``x509_provider`` extension is the other side: this
server as the CA that issues client certificates.
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency

# x509.verification's client verifier and extension policies (45.0).
CRYPTOGRAPHY_REQUIREMENT = ">=45.0.0"


class EXT_X509Consumer(AbstractStaticExtension):
    name: ClassVar[str] = "x509_consumer"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Sign in with an X.509 client certificate (mutual TLS), verified "
        "against admin-managed trust anchors with CRL and OCSP revocation."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "X509_CONSUMER_TRUSTED_PROXIES": "",
        "X509_CONSUMER_CERT_HEADER": "X-SSL-Client-Cert",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="cryptography",
                friendly_name="Cryptographic library",
                semver=CRYPTOGRAPHY_REQUIREMENT,
                reason="X.509 path validation, CRL and OCSP",
            ),
            EXT_Dependency(
                name="auth_session",
                friendly_name="Sessions",
                optional=True,
                reason="Persists the sessions sign-in issues, so they can be revoked",
            ),
            EXT_Dependency(
                name="auth_invitations",
                friendly_name="Invitations",
                optional=True,
                reason="REGISTRATION_MODE=invite admits a new user by invitation",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {"x509_linked_identities"}

    @classmethod
    @ability("x509_linked_identities")
    async def x509_linked_identities(cls, requester_id: str) -> List[Dict[str, Any]]:
        """The certificate identities that sign in as ``requester_id``."""
        from zephyrex.extensions.x509_consumer.BLL_X509Consumer import (
            UserX509LinkManager,
        )

        manager = cls.as_requester(UserX509LinkManager, requester_id)
        return [
            {
                "id": str(link.id),
                "trust_anchor_id": link.trust_anchor_id,
                "identity": link.identity,
                "subject_dn": link.subject_dn,
                "not_after": link.not_after,
                "last_login_at": link.last_login_at,
            }
            for link in manager.list(user_id=requester_id) or []
        ]
