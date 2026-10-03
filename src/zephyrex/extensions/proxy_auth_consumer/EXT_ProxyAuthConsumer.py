# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign in as the user a trusted, authenticating reverse proxy asserts.

``POST /v1/auth/proxy/login`` reads the identity an upstream proxy that has
already authenticated the user puts in a request header, maps it to a local
user through a link made on first sign-in, and issues the same session as
password login. Users list and remove their links at ``/v1/auth/proxy``.
See ``BLL_ProxyAuthConsumer`` for the trust and mapping rules.

- ``PROXY_AUTH_CONSUMER_TRUSTED_PROXIES``: comma-separated addresses or
  CIDRs of the proxies whose headers are believed (as the app sees the
  peer). Empty: no header is ever believed.
- ``PROXY_AUTH_CONSUMER_USER_HEADER``: the header naming the user. Default
  ``X-Forwarded-User``.
- ``PROXY_AUTH_CONSUMER_NAME_HEADER``: a new account's display name.
  Default ``X-Forwarded-Name``.
- ``PROXY_AUTH_CONSUMER_EMAIL_HEADER``: the user's email. Default
  ``X-Forwarded-Email``. Read only when
- ``PROXY_AUTH_CONSUMER_TRUST_EMAIL`` is true: the proxy has verified the
  emails it asserts, so one may sign in to the account that has it.

The complementary ``proxy_auth_provider`` extension implements the other
side (this server setting proxy headers for downstream services).
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency
from zephyrex.lib.InboundSecurity import _parse_trusted_proxies
from zephyrex.lib.Environment import env


class EXT_ProxyAuthConsumer(AbstractStaticExtension):
    name: ClassVar[str] = "proxy_auth_consumer"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Sign in as the user a trusted, authenticating reverse proxy asserts "
        "in a request header (X-Forwarded-User, Remote-User)."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "PROXY_AUTH_CONSUMER_TRUSTED_PROXIES": "",
        "PROXY_AUTH_CONSUMER_USER_HEADER": "X-Forwarded-User",
        "PROXY_AUTH_CONSUMER_NAME_HEADER": "X-Forwarded-Name",
        "PROXY_AUTH_CONSUMER_EMAIL_HEADER": "X-Forwarded-Email",
        "PROXY_AUTH_CONSUMER_TRUST_EMAIL": "false",
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

    _abilities: ClassVar[Set[str]] = {"proxy_auth_linked_identities"}

    @classmethod
    def validate_config(cls) -> List[str]:
        raw = (env("PROXY_AUTH_CONSUMER_TRUSTED_PROXIES") or "").strip()
        if not raw:
            return [
                "PROXY_AUTH_CONSUMER_TRUSTED_PROXIES is unset; "
                "proxy sign-in is refused from every address"
            ]
        try:
            _parse_trusted_proxies(raw)
        except ValueError as exc:
            return [f"PROXY_AUTH_CONSUMER_TRUSTED_PROXIES: {exc}"]
        return []

    @classmethod
    @ability("proxy_auth_linked_identities")
    async def proxy_auth_linked_identities(
        cls, requester_id: str
    ) -> List[Dict[str, Any]]:
        """The proxy identities that sign in as ``requester_id``."""
        from zephyrex.extensions.proxy_auth_consumer.BLL_ProxyAuthConsumer import (
            UserProxyAuthLinkManager,
        )

        manager = cls.as_requester(UserProxyAuthLinkManager, requester_id)
        return [
            {
                "id": str(link.id),
                "identity": link.identity,
                "last_login_at": link.last_login_at,
            }
            for link in manager.list(user_id=requester_id) or []
        ]
