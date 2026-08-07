"""Amazon Cognito IdP for the oauth_consumer extension."""

from typing import Any, Dict, Optional

from fastapi import HTTPException

from zephyrex.extensions.oauth_consumer.IdPRegistry import register_idp
from zephyrex.extensions.oauth_consumer.PRV_AbstractIdP import (
    AbstractIdPProvider,
    get_http_client,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger


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
        client = get_http_client()
        response = await client.post(
            f"{_cognito_base()}/oauth2/token",
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if response.status_code != 200:
            logger.warning(
                "Amazon token refresh failed (status=%s)", response.status_code
            )
            raise HTTPException(status_code=502, detail="Amazon token refresh failed")
        data = response.json()
        self.access_token = data["access_token"]
        return data  # type: ignore[no-any-return]

    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        client = get_http_client()
        response = await client.get(
            f"{_cognito_base()}/oauth2/userInfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code != 200:
            logger.warning(
                "Amazon userInfo failed (status=%s)", response.status_code
            )
            raise HTTPException(status_code=502, detail="Amazon user info failed")
        data = response.json()
        # Cognito's ``email_verified`` is a string ("true"/"false") in
        # most pools. Normalize.
        raw_verified = data.get("email_verified")
        if isinstance(raw_verified, bool):
            email_verified = raw_verified
        else:
            email_verified = str(raw_verified or "").lower() == "true"
        return {
            "email": data.get("email", ""),
            "email_verified": email_verified,
            "first_name": data.get("given_name", "") or "",
            "last_name": data.get("family_name", "") or "",
            "display_name": data.get("name", "") or "",
            "provider_user_id": str(data.get("sub") or data.get("username", "")),
        }

    @classmethod
    async def sso_handler(cls, code: str, redirect_uri: str) -> Optional["AmazonIdP"]:
        code = cls.sanitize_code(code)
        client = get_http_client()
        response = await client.post(
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
            logger.error(
                "Amazon SSO token exchange failed (status=%s)", response.status_code
            )
            return None
        data = response.json()
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
        )


register_idp("amazon", AmazonIdP)
