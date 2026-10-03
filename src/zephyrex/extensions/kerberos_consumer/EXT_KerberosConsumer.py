# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign in with Kerberos: HTTP Negotiate (RFC 4559) single sign-on for
desktops joined to an Active Directory or MIT Kerberos realm.

Each ``kerberos_keytab`` provider instance is one service principal and
its keytab; ``GET /v1/auth/kerberos/login/negotiate`` accepts the
browser's SPNEGO token and issues the same session as password login.
The service keytab may instead come from the environment
(``KERBEROS_CONSUMER_KEYTAB`` and friends), read by the provider's seeded
root instance.

The sibling ``kerberos_provider`` extension is the other side: this
server acting as a Kerberos KDC. The two are independent.
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.lib.Dependencies import (
    Dependencies,
    EXT_Dependency,
    PIP_Dependency,
    SYS_Dependency,
)

GSSAPI_REQUIREMENT = ">=1.12.0"


class EXT_KerberosConsumer(AbstractStaticExtension):
    name: ClassVar[str] = "kerberos_consumer"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Sign in with Kerberos: HTTP Negotiate (SPNEGO) single sign-on "
        "against Active Directory or MIT realms."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "KERBEROS_CONSUMER_KEYTAB": "",
        "KERBEROS_CONSUMER_SERVICE_PRINCIPAL": "",
        "KERBEROS_CONSUMER_ALLOWED_REALMS": "",
        "KERBEROS_CONSUMER_CHANNEL_BINDING_CERTIFICATE": "",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="gssapi",
                friendly_name="python-gssapi",
                semver=GSSAPI_REQUIREMENT,
                reason="Accepting Kerberos and SPNEGO tokens (GSSAPI)",
            ),
            SYS_Dependency.for_all_platforms(
                "libkrb5-dev",
                apt_pkg="libkrb5-dev",
                brew_pkg="krb5",
                friendly_name="MIT Kerberos GSSAPI library",
                reason="python-gssapi builds against it and calls it",
            ),
            EXT_Dependency(
                name="auth_session",
                friendly_name="Sessions",
                optional=True,
                reason="Persists the sessions sign-in issues, so they can be revoked",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {"kerberos_linked_principals"}

    @classmethod
    @ability("kerberos_linked_principals")
    async def kerberos_linked_principals(
        cls, requester_id: str
    ) -> List[Dict[str, Any]]:
        """The Kerberos principals that sign in as ``requester_id``."""
        from zephyrex.extensions.kerberos_consumer.BLL_KerberosConsumer import (
            KerberosPrincipalManager,
        )

        manager = cls.as_requester(KerberosPrincipalManager, requester_id)
        return [
            {
                "id": str(link.id),
                "principal": link.principal,
                "realm": link.realm,
                "last_login_at": link.last_login_at,
            }
            for link in manager.list(user_id=requester_id) or []
        ]
