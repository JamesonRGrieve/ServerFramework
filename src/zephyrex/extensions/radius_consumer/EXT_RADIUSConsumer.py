# SPDX-License-Identifier: AGPL-3.0-or-later
"""Users sign in to this server by having a RADIUS server (FreeRADIUS, NPS,
an OTP or 2FA gateway) check their credentials.

PAP over RADIUS over TLS (RadSec, RFC 6614) by default, or plain UDP once
``RADIUS_CONSUMER_ALLOW_UDP=true`` allows it; Message-Authenticator is sent
on every request and required on every reply (the Blast-RADIUS
mitigation). Access-Challenge flows (one-time codes) are a second step of
the sign-in. A sign-in issues the same session as password sign-in. See
``BLL_RADIUSConsumer`` for the routes and ``RADIUSClient`` for the wire.

Not supported: CHAP, MS-CHAP and EAP (a PAP password is all the sign-in
form has), accounting, and Dynamic Authorization (CoA/Disconnect). The
complementary ``radius_provider`` extension is the server side.
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency


class EXT_RADIUSConsumer(AbstractStaticExtension):
    name: ClassVar[str] = "radius_consumer"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Sign users in through RADIUS servers: PAP over RadSec or UDP, "
        "with Access-Challenge for one-time codes"
    )

    _env: ClassVar[Dict[str, Any]] = {"RADIUS_CONSUMER_ALLOW_UDP": "false"}

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="pyrad",
                friendly_name="pyrad",
                semver=">=2.5.4",
                reason="Encodes and decodes RADIUS attributes and hides passwords",
            ),
            EXT_Dependency(
                name="auth_session",
                friendly_name="Sessions",
                optional=True,
                reason="Persisted, revocable sessions, as password sign-in issues",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = set()

    @classmethod
    def validate_config(cls) -> List[str]:
        from zephyrex.extensions.radius_consumer.BLL_RADIUSConsumer import (
            ALLOW_UDP_ENV,
            udp_allowed,
        )

        if udp_allowed():
            return [
                f"{ALLOW_UDP_ENV} is on: plain RADIUS over UDP rests on the shared "
                "secret alone; prefer RadSec"
            ]
        return []
