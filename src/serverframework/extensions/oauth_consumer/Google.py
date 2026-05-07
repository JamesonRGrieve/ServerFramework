"""Google IdP for the oauth_consumer extension."""

from typing import Any, Dict, Optional

import requests
from fastapi import HTTPException

from serverframework.extensions.oauth_consumer.IdPRegistry import register_idp
from serverframework.extensions.oauth_consumer.PRV_AbstractIdP import AbstractIdPProvider
from serverframework.lib.Environment import env
from serverframework.lib.Logging import logger


GOOGLE_SCOPES = (
    "openid email profile "
    "https://www.googleapis.com/auth/userinfo.profile"
)


class GoogleIdP(AbstractIdPProvider):
    name = "google"

    def __init__(
        self,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            client_id=env("GOOGLE_CLIENT_ID"),
            client_secret=env("GOOGLE_CLIENT_SECRET"),
            access_token=access_token,
            refresh_token=refresh_token,
            scopes=GOOGLE_SCOPES,
            **kwargs,
        )

    async def get_new_token(self) -> Dict[str, Any]:
        response = requests.post(
            "https://oauth2.googleapis.com/token",
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
                detail=f"Google token refresh failed: {response.text}",
            )
        data = response.json()
        self.access_token = data["access_token"]
        return data

    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        uri = "https://people.googleapis.com/v1/people/me?personFields=names,emailAddresses"
        response = requests.get(uri, headers={"Authorization": f"Bearer {access_token}"})
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Google user info failed: {response.text}",
            )
        data = response.json()
        first_name = data["names"][0]["givenName"] if data.get("names") else ""
        last_name = data["names"][0]["familyName"] if data.get("names") else ""
        email = data["emailAddresses"][0]["value"] if data.get("emailAddresses") else ""
        return {
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "display_name": f"{first_name} {last_name}".strip(),
            "provider_user_id": data.get("resourceName", "").split("/")[-1],
        }

    @classmethod
    async def sso_handler(cls, code: str, redirect_uri: str) -> Optional["GoogleIdP"]:
        code = cls.sanitize_code(code)
        response = requests.post(
            "https://accounts.google.com/o/oauth2/token",
            params={
                "code": code,
                "client_id": env("GOOGLE_CLIENT_ID"),
                "client_secret": env("GOOGLE_CLIENT_SECRET"),
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "scope": GOOGLE_SCOPES,
                "access_type": "offline",
            },
        )
        if response.status_code != 200:
            logger.error(f"Google SSO token exchange failed: {response.text}")
            return None
        data = response.json()
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
        )


register_idp("google", GoogleIdP)
