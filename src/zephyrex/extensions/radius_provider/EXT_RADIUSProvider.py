# SPDX-License-Identifier: AGPL-3.0-or-later
"""This server as a RADIUS authenticator, so network gear (WPA-Enterprise
Wi-Fi through its controller, VPN concentrators, switch logins) signs users
in with their framework accounts.

It answers PAP Access-Requests from registered NAS clients
(BLL_RADIUSProvider) on a UDP listener run as a background service
(SVC_RADIUSProvider, started when ``RUN_BACKGROUND_SERVICES=true``).
Accepted members of a team get the team's reply policy (VLAN, Filter-Id,
Session-Timeout).

EAP is not spoken here. WPA-Enterprise clients speak EAP (PEAP, TTLS) to the
NAS, so a deployment puts FreeRADIUS in front to terminate the TLS tunnel and
proxy its inner EAP-TTLS/PAP request to this server. Inner methods that need
the cleartext-equivalent password (MSCHAPv2, CHAP) cannot be verified against
the framework's password hashes.
"""

from typing import Any, ClassVar, Dict, List

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency
from zephyrex.lib.Environment import env

DEFAULT_BIND_ADDRESS = "0.0.0.0"
DEFAULT_AUTH_PORT = 1812
MAX_PORT = 65535


class EXT_RADIUSProvider(AbstractStaticExtension):
    name: ClassVar[str] = "radius_provider"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Answer RADIUS PAP logins from network gear against framework accounts"
    )

    _env: ClassVar[Dict[str, Any]] = {
        "RADIUS_PROVIDER_BIND_ADDRESS": DEFAULT_BIND_ADDRESS,
        "RADIUS_PROVIDER_AUTH_PORT": str(DEFAULT_AUTH_PORT),
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="pyrad",
                friendly_name="RADIUS packet library",
                semver=">=2.5.4",
                reason="Encodes and decodes RADIUS packets",
            ),
            EXT_Dependency(
                name="auth_lockout",
                friendly_name="Account lockout",
                optional=True,
                reason="RADIUS login failures count toward the per-user lockout",
            ),
        ]
    )

    @classmethod
    def auth_port(cls) -> int:
        raw = env("RADIUS_PROVIDER_AUTH_PORT") or str(DEFAULT_AUTH_PORT)
        port = int(raw)
        if not 0 <= port <= MAX_PORT:
            raise ValueError(f"RADIUS_PROVIDER_AUTH_PORT {raw} is not a UDP port")
        return port

    @classmethod
    def register_services(cls, model_registry: Any, requester_id: str) -> List[Any]:
        """The UDP listener, started by the framework's background services."""
        from zephyrex.extensions.radius_provider.SVC_RADIUSProvider import (
            RADIUSAuthService,
        )

        return [
            RADIUSAuthService(
                requester_id=requester_id,
                model_registry=model_registry,
                bind_address=env("RADIUS_PROVIDER_BIND_ADDRESS")
                or DEFAULT_BIND_ADDRESS,
                port=cls.auth_port(),
            )
        ]

    @classmethod
    def validate_config(cls) -> List[str]:
        issues: List[str] = []
        if (env("RUN_BACKGROUND_SERVICES") or "").strip().lower() != "true":
            issues.append(
                "RUN_BACKGROUND_SERVICES is not true, so the RADIUS listener "
                "does not run"
            )
        try:
            cls.auth_port()
        except ValueError as exc:
            issues.append(str(exc))
        return issues
