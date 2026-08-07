"""Google IdP for the oauth_consumer extension."""

from typing import Any, Dict, Optional

from fastapi import HTTPException

from serverframework.extensions.oauth_consumer.IdPRegistry import register_idp
from serverframework.extensions.oauth_consumer.PRV_AbstractIdP import (
    AbstractIdPProvider,
    get_http_client,
)
from serverframework.lib.Environment import env
from serverframework.lib.Logging import logger


GOOGLE_SCOPES = (
    "openid email profile "
    "https://www.googleapis.com/auth/userinfo.profile"
)


class GoogleIdP(AbstractIdPProvider):
    name = "google"
    AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"

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
        client = get_http_client()
        response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if response.status_code != 200:
            logger.warning(
                "Google token refresh failed (status=%s)", response.status_code
            )
            raise HTTPException(
                status_code=502, detail="Google token refresh failed"
            )
        data = response.json()
        self.access_token = data["access_token"]
        return data

    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        # Use the OIDC userinfo endpoint so we get the standard
        # ``email_verified`` claim. The People API does not expose a
        # uniform verification flag.
        client = get_http_client()
        response = await client.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code != 200:
            logger.warning(
                "Google userinfo failed (status=%s)", response.status_code
            )
            raise HTTPException(status_code=502, detail="Google user info failed")
        data = response.json()
        first_name = data.get("given_name", "") or ""
        last_name = data.get("family_name", "") or ""
        return {
            "email": data.get("email", ""),
            "email_verified": bool(data.get("email_verified", False)),
            "first_name": first_name,
            "last_name": last_name,
            "display_name": data.get("name") or f"{first_name} {last_name}".strip(),
            "provider_user_id": str(data.get("sub", "")),
        }

    @classmethod
    async def sso_handler(cls, code: str, redirect_uri: str) -> Optional["GoogleIdP"]:
        code = cls.sanitize_code(code)
        client = get_http_client()
        response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
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
            logger.error(
                "Google SSO token exchange failed (status=%s)", response.status_code
            )
            return None
        data = response.json()
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
        )


register_idp("google", GoogleIdP)
