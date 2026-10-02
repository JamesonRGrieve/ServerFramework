# SPDX-License-Identifier: AGPL-3.0-or-later
"""X (formerly Twitter), through the X API v2.

The instance's API key is an OAuth 2.0 user access token with the
``tweet.read``, ``tweet.write`` and ``users.read`` scopes (else
``X_ACCESS_TOKEN``). Posts are text only here: X takes media as uploaded
bytes, not as a URL it fetches. Reading posts needs an X API tier that
includes it; the free tier can post and read the profile.
"""

import json
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.extensions.social.EXT_Social import AbstractSocialProvider, count
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

API_URL = "https://api.x.com/2"
WEB_URL = "https://x.com"
# X's timeline endpoint takes 5 to 100 results.
MIN_TIMELINE_RESULTS = 5
UNSUPPORTED_AUTH = "Unsupported Authentication"


def problem(payload: Any) -> str:
    """The title and detail of an X API problem body."""
    try:
        body = json.loads(str(payload))
    except (ValueError, TypeError):
        return ""
    return f"{body.get('title', '')}: {body.get('detail', '')}".strip(": ")


class PRV_X_Social(AbstractSocialProvider):
    name: ClassVar[str] = "x"
    friendly_name: ClassVar[str] = "X"
    description: ClassVar[str] = "X (formerly Twitter)"
    _env: ClassVar[Dict[str, Any]] = {"X_ACCESS_TOKEN": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "OAuth 2.0 user access token (tweet.read, tweet.write, users.read)",
            env="X_ACCESS_TOKEN",
            secret=True,
            field="api_key",
        ),
    )

    @classmethod
    async def _call(
        cls,
        instance: ProviderInstanceModel,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        try:
            return await cls.http().request(
                method,
                f"{API_URL}{path}",
                params=params,
                json=json_body,
                headers={"Authorization": f"Bearer {cls.token(instance)}"},
            )
        except AuthExternalError as exc:
            # A 403 from X is a verdict on the request (a duplicate post, an
            # API tier without this endpoint), not on the token.
            detail = problem(exc.upstream_payload)
            # A token that is not a user token: the credential is wrong.
            if exc.upstream_status != 403 or detail.startswith(UNSUPPORTED_AUTH):
                raise
            if "duplicate" in detail.lower():
                raise InvalidInputExternalError(
                    f"X: {detail}", provider=cls.name, upstream_status=403
                ) from exc
            raise PermanentExternalError(
                f"X refused the request: {detail or 'forbidden'}",
                provider=cls.name,
                upstream_status=403,
            ) from exc

    @classmethod
    async def _me(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        found = await cls._call(
            instance,
            "GET",
            "/users/me",
            params={"user.fields": "description,public_metrics,url"},
        )
        me: Dict[str, Any] = found["data"]
        return me

    @classmethod
    async def publish(
        cls, instance: ProviderInstanceModel, content: str, media_urls: List[str]
    ) -> Dict[str, Any]:
        if media_urls:
            raise cls.refuse(
                "media are uploaded as bytes, which this provider does not do"
            )
        found = await cls._call(
            instance, "POST", "/tweets", json_body={"text": content}
        )
        post_id = found["data"]["id"]
        return cls.published(instance, post_id, f"{WEB_URL}/i/web/status/{post_id}")

    @classmethod
    async def feed(
        cls, instance: ProviderInstanceModel, limit: int
    ) -> List[Dict[str, Any]]:
        me = await cls._me(instance)
        found = await cls._call(
            instance,
            "GET",
            f"/users/{me['id']}/tweets",
            params={
                "max_results": max(limit, MIN_TIMELINE_RESULTS),
                "tweet.fields": "created_at",
            },
        )
        return [
            {
                "id": post["id"],
                "text": post.get("text", ""),
                "url": f"{WEB_URL}/{me['username']}/status/{post['id']}",
                "created_at": post.get("created_at"),
            }
            for post in found.get("data", [])
        ][:limit]

    @classmethod
    async def profile(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        me = await cls._me(instance)
        metrics = me.get("public_metrics") or {}
        return {
            "username": me.get("username", ""),
            "name": me.get("name", ""),
            "url": f"{WEB_URL}/{me.get('username', '')}",
            "followers": count(metrics.get("followers_count")),
            "following": count(metrics.get("following_count")),
            "posts": count(metrics.get("tweet_count")),
            "provider": cls.name,
        }
