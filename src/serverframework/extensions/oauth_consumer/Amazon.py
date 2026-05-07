"""Amazon Cognito IdP for the oauth_consumer extension."""

from typing import Any, Dict, Optional

import requests
from fastapi import HTTPException

from serverframework.extensions.oauth_consumer.IdPRegistry import register_idp
from serverframework.extensions.oauth_consumer.PRV_AbstractIdP import AbstractIdPProvider
from serverframework.lib.Environment import env
from serverframework.lib.Logging import logger


AMAZON_SCOPES = "openid email profile"


def _cognito_base() -> str:
    user_pool = env("AWS_USER_POOL_ID")
    region = env("AWS_REGION")
    return f"https://{user_pool}.auth.{region}.amazoncognito.com"


class AmazonIdP(AbstractIdPProvider):
    name = "amazon"

    @property
    def AUTHORIZE_URL(self) -> str:  # noqa: N802 — matches per-IdP convention
        return f"{_cognito_base()}/oauth2/authorize"

    def __init__(
        self,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            client_id=env("AWS_CLIENT_ID"),
            client_secret=env("AWS_CLIENT_SECRET"),
            access_token=access_token,
            refresh_token=refresh_token,
            scopes=AMAZON_SCOPES,
            **kwargs,
        )

    async def get_new_token(self) -> Dict[str, Any]:
        response = requests.post(
            f"{_cognito_base()}/oauth2/token",
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Amazon token refresh failed: {response.text}",
            )
        data = response.json()
        self.access_token = data["access_token"]
        return data

    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        response = requests.get(
            f"{_cognito_base()}/oauth2/userInfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Amazon user info failed: {response.text}",
            )
        data = response.json()
        return {
            "email": data.get("email", ""),
            "first_name": data.get("given_name", ""),
            "last_name": data.get("family_name", ""),
            "display_name": data.get("name", ""),
            "provider_user_id": data.get("sub") or data.get("username", ""),
        }

    @classmethod
    async def sso_handler(cls, code: str, redirect_uri: str) -> Optional["AmazonIdP"]:
        code = cls.sanitize_code(code)
        response = requests.post(
            f"{_cognito_base()}/oauth2/token",
            data={
                "client_id": env("AWS_CLIENT_ID"),
                "client_secret": env("AWS_CLIENT_SECRET"),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        if response.status_code != 200:
            logger.error(f"Amazon SSO token exchange failed: {response.text}")
            return None
        data = response.json()
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
        )


register_idp("amazon", AmazonIdP)
