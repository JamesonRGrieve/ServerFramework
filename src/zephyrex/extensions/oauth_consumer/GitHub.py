"""GitHub IdP for the oauth_consumer extension."""

from typing import Any, Dict, Optional

from fastapi import HTTPException

from zephyrex.extensions.oauth_consumer.IdPRegistry import register_idp
from zephyrex.extensions.oauth_consumer.PRV_AbstractIdP import (
    AbstractIdPProvider,
    get_http_client,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger


GITHUB_SCOPES = "user:email read:user"


class GitHubIdP(AbstractIdPProvider):
    name = "github"
    AUTHORIZE_URL = "https://github.com/login/oauth/authorize"

    def __init__(
        self,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            client_id=env("GITHUB_CLIENT_ID"),
            client_secret=env("GITHUB_CLIENT_SECRET"),
            access_token=access_token,
            refresh_token=refresh_token,
            scopes=GITHUB_SCOPES,
            **kwargs,
        )

    async def get_new_token(self) -> Dict[str, Any]:
        # GitHub classic OAuth tokens do not refresh; re-authorization is required.
        # Fine-grained PATs / GitHub Apps support refresh.
        client = get_http_client()
        response = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if response.status_code != 200:
            logger.warning(
                "GitHub token refresh failed (status=%s)", response.status_code
            )
            raise HTTPException(status_code=502, detail="GitHub token refresh failed")
        data = response.json()
        if "access_token" not in data:
            raise HTTPException(
                status_code=400,
                detail=(
                    "GitHub did not return an access token "
                    "(token may not support refresh)"
                ),
            )
        self.access_token = data["access_token"]
        return data  # type: ignore[no-any-return]

    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        client = get_http_client()
        user_response = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if user_response.status_code != 200:
            logger.warning(
                "GitHub /user failed (status=%s)", user_response.status_code
            )
            raise HTTPException(status_code=502, detail="GitHub user info failed")
        user_data = user_response.json()

        email_response = await client.get(
            "https://api.github.com/user/emails",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        primary_email = None
        primary_verified = False
        if email_response.status_code == 200:
            for entry in email_response.json():
                if entry.get("primary"):
                    primary_email = entry.get("email")
                    primary_verified = bool(entry.get("verified", False))
                    break

        full_name = user_data.get("name") or ""
        first, _, last = full_name.partition(" ")
        return {
            "email": primary_email or user_data.get("email", ""),
            "email_verified": primary_verified,
            "first_name": first,
            "last_name": last,
            "display_name": full_name or user_data.get("login", ""),
            "username": user_data.get("login"),
            "provider_user_id": str(user_data.get("id", "")),
        }

    @classmethod
    async def sso_handler(cls, code: str, redirect_uri: str) -> Optional["GitHubIdP"]:
        code = cls.sanitize_code(code)
        client = get_http_client()
        response = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": env("GITHUB_CLIENT_ID"),
                "client_secret": env("GITHUB_CLIENT_SECRET"),
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if response.status_code != 200:
            logger.error(
                "GitHub SSO token exchange failed (status=%s)",
                response.status_code,
            )
            return None
        data = response.json()
        access_token = data.get("access_token")
        if not access_token:
            logger.error("GitHub SSO returned no access_token")
            return None
        return cls(access_token=access_token, refresh_token=data.get("refresh_token", ""))


register_idp("github", GitHubIdP)
