# SPDX-License-Identifier: AGPL-3.0-or-later
"""A downstream service the proxy forwards to: one provider instance each.

Only an instance ROOT created is an upstream (anyone may create provider
instances, and a user's own must never point the proxy somewhere). Its
name is the first path segment under ``/v1/proxy``; its ``url`` is the
service's base address, whose host, when it is on a private network, must
be listed in ``EGRESS_ALLOWED_HOSTS`` like any other outbound address."""

import math
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, FrozenSet, Optional, Set, Tuple
from urllib.parse import urlsplit

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticProvider,
    InstanceSetting,
)
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 600
DEFAULT_MAX_REQUEST_BYTES = 10 * 1024 * 1024
# A GET answer is held whole by the app's ETag middleware before it is sent,
# so the default keeps that in bounds.
DEFAULT_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MIN_SIGNING_SECRET_BYTES = 32


class UpstreamMisconfigured(ValueError):
    """An upstream's settings cannot be used; the message names the setting."""


@dataclass(frozen=True)
class Upstream:
    """One upstream's settings, checked."""

    name: str
    base_url: str
    signing_secret: Optional[bytes]
    allowed_teams: FrozenSet[str]
    timeout_seconds: float
    max_request_bytes: int
    max_response_bytes: int

    def admits(self, teams: FrozenSet[str]) -> bool:
        """Whether a member of ``teams`` may use it: anyone signed in when
        it names no teams."""
        return not self.allowed_teams or bool(self.allowed_teams & teams)


def base_url(raw: Optional[str]) -> str:
    url = (raw or "").strip()
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise UpstreamMisconfigured("url is an http(s) address with a host")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise UpstreamMisconfigured("url has no credentials, query or fragment")
    return url


def positive_number(raw: Optional[str], what: str, ceiling: float) -> float:
    try:
        value = float(raw or "")
    except ValueError:
        raise UpstreamMisconfigured(f"{what} is a number") from None
    if not math.isfinite(value) or not 0 < value <= ceiling:
        raise UpstreamMisconfigured(f"{what} is above 0 and at most {ceiling:g}")
    return value


def byte_count(raw: Optional[str], what: str) -> int:
    text = (raw or "").strip()
    if not text.isdigit() or int(text) <= 0:
        raise UpstreamMisconfigured(f"{what} is a whole number of bytes above 0")
    return int(text)


def team_ids(raw: Optional[str]) -> FrozenSet[str]:
    return frozenset(item for item in (raw or "").replace(",", " ").split() if item)


class PRV_Upstream_ProxyAuthProvider(AbstractStaticProvider):
    name: ClassVar[str] = "proxy_auth_upstream"
    friendly_name: ClassVar[str] = "Proxied upstream"
    description: ClassVar[str] = (
        "A downstream HTTP service reached through this server's "
        "authenticating proxy"
    )
    _abilities: ClassVar[Set[str]] = set()
    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "url", "The service's base address (http://grafana.internal:3000/)"
        ),
        InstanceSetting(
            "signing_secret",
            "Shared secret (32+ bytes) the identity headers are signed with, "
            "so the service can check this server sent them",
            secret=True,
        ),
        InstanceSetting(
            "allowed_teams",
            "Ids of the teams whose members may use it, comma-separated; "
            "empty lets every signed-in user through",
        ),
        InstanceSetting(
            "timeout_seconds",
            "Seconds to wait to connect, and between bytes",
            default=str(DEFAULT_TIMEOUT_SECONDS),
        ),
        InstanceSetting(
            "max_request_bytes",
            "Largest request body sent up",
            default=str(DEFAULT_MAX_REQUEST_BYTES),
        ),
        InstanceSetting(
            "max_response_bytes",
            "Largest response body passed back",
            default=str(DEFAULT_MAX_RESPONSE_BYTES),
        ),
    )

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def upstream(cls, instance: ProviderInstanceModel) -> Upstream:
        """``instance``'s settings, checked. Raises UpstreamMisconfigured."""
        secret = cls.setting(instance, "signing_secret")
        if secret and len(secret.encode("utf-8")) < MIN_SIGNING_SECRET_BYTES:
            raise UpstreamMisconfigured(
                f"signing_secret is at least {MIN_SIGNING_SECRET_BYTES} bytes"
            )
        return Upstream(
            name=instance.name,
            base_url=base_url(cls.setting(instance, "url")),
            signing_secret=secret.encode("utf-8") if secret else None,
            allowed_teams=team_ids(cls.setting(instance, "allowed_teams")),
            timeout_seconds=positive_number(
                cls.setting(instance, "timeout_seconds"),
                "timeout_seconds",
                MAX_TIMEOUT_SECONDS,
            ),
            max_request_bytes=byte_count(
                cls.setting(instance, "max_request_bytes"), "max_request_bytes"
            ),
            max_response_bytes=byte_count(
                cls.setting(instance, "max_response_bytes"), "max_response_bytes"
            ),
        )
