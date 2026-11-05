# SPDX-License-Identifier: AGPL-3.0-or-later
"""Parked: this server as a Kerberos KDC. Not built; it provides nothing yet.

When built, it would run this server as a Kerberos Key Distribution Centre
for its own realm: it would hold principals for local users, services and
hosts, issue and renew tickets, and let third-party services authenticate
this server's users over GSSAPI/SPNEGO.

Until then the extension declares no models, abilities, routes, settings or
dependencies, so enabling it changes nothing. Signing in to this server with
an existing realm's tickets is the separate, finished ``kerberos_consumer``
extension.
"""

from typing import ClassVar

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension


class EXT_KerberosProvider(AbstractStaticExtension):
    name: ClassVar[str] = "kerberos_provider"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Parked, not built: would run this server as a Kerberos KDC that issues tickets for its "
        "users and services. Provides nothing yet; sign-in via keytab is kerberos_consumer."
    )
