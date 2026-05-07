"""Microsoft IdP for the oauth_consumer extension."""

from typing import Any, Dict, Optional

import requests
from fastapi import HTTPException

from serverframework.extensions.oauth_consumer.IdPRegistry import register_idp
from serverframework.extensions.oauth_consumer.PRV_AbstractIdP import AbstractIdPProvider
from serverframework.lib.Environment import env
from serverframework.lib.Logging import logger


MICROSOFT_SCOPES = "openid email profile offline_access User.Read"


class MicrosoftIdP(AbstractIdPProvider):
    name = "microsoft"
    AUTHORIZE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"

    def __init__(
        self,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            client_id=env("MICROSOFT_CLIENT_ID"),
            client_secret=env("MICROSOFT_CLIENT_SECRET"),
            access_token=access_token,
            refresh_token=refresh_token,
            scopes=MICROSOFT_SCOPES,
            **kwargs,
        )

    async def get_new_token(self) -> Dict[str, Any]:
        response = requests.post(
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
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
                detail=f"Microsoft token refresh failed: {response.text}",
            )
        data = response.json()
        self.access_token = data["access_token"]
        return data

    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        response = requests.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Microsoft user info failed: {response.text}",
            )
        data = response.json()
        return {
            "email": data.get("mail") or data.get("userPrincipalName", ""),
            "first_name": data.get("givenName", ""),
            "last_name": data.get("surname", ""),
            "display_name": data.get("displayName", ""),
            "provider_user_id": data.get("id", ""),
        }

    @classmethod
    async def sso_handler(cls, code: str, redirect_uri: str) -> Optional["MicrosoftIdP"]:
        code = cls.sanitize_code(code)
        response = requests.post(
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            data={
                "client_id": env("MICROSOFT_CLIENT_ID"),
                "client_secret": env("MICROSOFT_CLIENT_SECRET"),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "scope": MICROSOFT_SCOPES,
            },
        )
        if response.status_code != 200:
            logger.error(f"Microsoft SSO token exchange failed: {response.text}")
            return None
        data = response.json()
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
        )


register_idp("microsoft", MicrosoftIdP)
