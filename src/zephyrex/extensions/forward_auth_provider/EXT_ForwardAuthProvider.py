# SPDX-License-Identifier: AGPL-3.0-or-later
"""This server as the forward-auth endpoint of a reverse proxy (Traefik
ForwardAuth, nginx auth_request, Caddy forward_auth): see
BLL_ForwardAuthProvider for the endpoint, its answers and its rules.

Settings:

- ``FORWARD_AUTH_PROVIDER_TRUSTED_PROXIES``: comma-separated addresses or
  CIDRs of the proxies that may ask (as the app sees the peer). Empty:
  every request is refused.
- ``FORWARD_AUTH_PROVIDER_IDENTITY_HEADERS``: ``Header=claim`` pairs the
  200 answer carries, e.g. ``X-Forwarded-User=id,X-Forwarded-Email=email,
  X-Forwarded-Groups=teams``; claims are id, email, username, name and
  teams (team names, comma separated). Only these are sent.
- ``FORWARD_AUTH_PROVIDER_LOGIN_URL``: where a browser without a session
  is sent; empty: it gets a plain 401.
- ``FORWARD_AUTH_PROVIDER_RETURN_PARAMETER``: the login URL's parameter
  for the page to return to (default ``rd``).
- ``FORWARD_AUTH_PROVIDER_RETURN_HOSTS``: hosts a login may return to
  (``app.example.com``, or ``.example.com`` for any below it). Empty: the
  return page is never passed on.
- ``FORWARD_AUTH_PROVIDER_REQUIRE_RULE``: ``true`` refuses (403) a page no
  rule covers; otherwise anyone signed in passes it.

The abilities act as the server: rules are its configuration.

The complementary ``forward_auth_consumer`` extension is the other side:
this server asking an external forward-auth service.
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.extensions.forward_auth_provider.BLL_ForwardAuthProvider import (
    DEFAULT_RETURN_PARAMETER,
    IDENTITY_HEADERS_SETTING,
    LOGIN_URL_SETTING,
    REQUIRE_RULE_SETTING,
    RETURN_HOSTS_SETTING,
    RETURN_PARAMETER_SETTING,
    TRUSTED_PROXIES_SETTING,
    ForwardAuthConfig,
    ForwardAuthRuleManager,
    decide,
    enabled_rules,
    live_teams,
    live_user,
    normalized_host,
    normalized_path,
)
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency
from zephyrex.lib.Environment import env


class EXT_ForwardAuthProvider(AbstractStaticExtension):
    name: ClassVar[str] = "forward_auth_provider"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Serve as the forward-auth endpoint for Traefik, nginx and Caddy"
    )

    _env: ClassVar[Dict[str, Any]] = {
        TRUSTED_PROXIES_SETTING: "",
        IDENTITY_HEADERS_SETTING: "",
        LOGIN_URL_SETTING: "",
        RETURN_PARAMETER_SETTING: DEFAULT_RETURN_PARAMETER,
        RETURN_HOSTS_SETTING: "",
        REQUIRE_RULE_SETTING: "false",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="auth_session",
                friendly_name="Sessions",
                reason="A revoked session stops passing forward auth",
            )
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "list_forward_auth_rules",
        "check_forward_auth_access",
    }

    @classmethod
    def validate_config(cls) -> List[str]:
        return ForwardAuthConfig.problems()

    @classmethod
    def _registry(cls) -> Any:
        return cls.as_requester(ForwardAuthRuleManager, env("ROOT_ID")).model_registry

    @classmethod
    @ability("list_forward_auth_rules")
    async def list_forward_auth_rules(cls) -> List[Dict[str, Any]]:
        """The rules in force (enabled, not deleted)."""
        return [rule.model_dump(mode="json") for rule in enabled_rules(cls._registry())]

    @classmethod
    @ability("check_forward_auth_access")
    async def check_forward_auth_access(
        cls, user_id: str, host: str, path: str
    ) -> Dict[str, Any]:
        """Whether the user, signed in, would pass forward auth for
        ``host`` + ``path``, and the rule that decides it."""
        try:
            checked_host, checked_path = normalized_host(host), normalized_path(path)
        except ValueError as exc:
            raise InvalidInputExternalError(str(exc)) from None
        registry = cls._registry()
        user = live_user(registry, user_id)
        if user is None:
            raise InvalidInputExternalError("No such user")
        if user.active is False:
            return {
                "allowed": False,
                "reason": "This account is disabled",
                "rule_id": None,
            }
        decision = decide(
            enabled_rules(registry),
            checked_host,
            checked_path,
            list(live_teams(registry, user_id)),
            ForwardAuthConfig.load().require_rule,
        )
        return {
            "allowed": decision.allowed,
            "reason": decision.reason,
            "rule_id": decision.rule_id,
        }
