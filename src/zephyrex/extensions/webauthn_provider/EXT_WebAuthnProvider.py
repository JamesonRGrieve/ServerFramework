# SPDX-License-Identifier: AGPL-3.0-or-later
"""Parked: this server as a hosted passkey service. Not built; it provides
nothing yet.

When built, it would let third-party applications delegate passkey
authentication to this server: each application would register as a relying
party here, and this server would run the WebAuthn registration and assertion
ceremonies for that application's users and keep their credentials.

Until then the extension declares no models, abilities, routes, settings or
dependencies, so enabling it changes nothing. Passkey sign-in for this
server's own users is the separate, finished ``webauthn_consumer`` extension.
"""

from typing import ClassVar

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension


class EXT_WebAuthnProvider(AbstractStaticExtension):
    name: ClassVar[str] = "webauthn_provider"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Parked, not built: would run WebAuthn passkey ceremonies for third-party applications' "
        "users. Provides nothing yet; passkeys for this server's users are webauthn_consumer."
    )
