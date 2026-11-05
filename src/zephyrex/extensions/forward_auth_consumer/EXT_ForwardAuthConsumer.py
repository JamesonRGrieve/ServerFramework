# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign in through a forward-auth service: the browser's session at
Authelia, oauth2-proxy, Authentik or the like vouches for it here.

Each ``forward_auth_verifier`` provider instance is one verifier endpoint
and the cookies or headers it reads; ``GET /v1/auth/forward-auth/login``
asks it who the browser is (2xx allows and names the user in its response
headers, 401/403 or a redirect denies, anything else fails closed), maps
the identity to a local user and issues the same session as password
login. See ``BLL_ForwardAuthConsumer`` for the trust rules. The verifier
may instead come from the environment (``FORWARD_AUTH_CONSUMER_VERIFY_URL``
and friends), read by the provider's seeded root instance.

The complementary ``forward_auth_provider`` extension is the other side:
this server acting as the forward-auth endpoint for a reverse proxy.
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency


class EXT_ForwardAuthConsumer(AbstractStaticExtension):
    name: ClassVar[str] = "forward_auth_consumer"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Sign in through a forward-auth service (Authelia, oauth2-proxy, "
        "Authentik), verified by a subrequest to an operator-declared endpoint."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "FORWARD_AUTH_CONSUMER_VERIFY_URL": "",
        "FORWARD_AUTH_CONSUMER_FORWARD_COOKIES": "",
        "FORWARD_AUTH_CONSUMER_FORWARD_HEADERS": "",
        "FORWARD_AUTH_CONSUMER_USER_HEADER": "Remote-User",
        "FORWARD_AUTH_CONSUMER_EMAIL_HEADER": "Remote-Email",
        "FORWARD_AUTH_CONSUMER_NAME_HEADER": "Remote-Name",
        "FORWARD_AUTH_CONSUMER_TRUSTED_FOR_EMAIL": "false",
        "FORWARD_AUTH_CONSUMER_TIMEOUT_SECONDS": "5",
        "FORWARD_AUTH_CONSUMER_ORIGINAL_URL": "",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
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

    _abilities: ClassVar[Set[str]] = {"forward_auth_linked_identities"}

    @classmethod
    @ability("forward_auth_linked_identities")
    async def forward_auth_linked_identities(
        cls, requester_id: str
    ) -> List[Dict[str, Any]]:
        """The forward-auth identities that sign in as ``requester_id``."""
        from zephyrex.extensions.forward_auth_consumer.BLL_ForwardAuthConsumer import (
            ForwardAuthIdentityManager,
        )

        manager = cls.as_requester(ForwardAuthIdentityManager, requester_id)
        return [
            {
                "id": str(link.id),
                "provider_instance_id": link.provider_instance_id,
                "identity": link.identity,
                "last_login_at": link.last_login_at,
            }
            for link in manager.list(user_id=requester_id) or []
        ]
