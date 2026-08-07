"""Microsoft IdP for the oauth_consumer extension."""

from typing import Any, Dict, Optional

from fastapi import HTTPException

from serverframework.extensions.oauth_consumer.IdPRegistry import register_idp
from serverframework.extensions.oauth_consumer.PRV_AbstractIdP import (
    AbstractIdPProvider,
    get_http_client,
)
from serverframework.lib.Environment import env
from serverframework.lib.Logging import logger


# ``email`` claim from /me is verified for Entra ID work/school accounts;
# Microsoft does not expose a dedicated ``email_verified`` flag in Graph
# /me, but a personal MSA account requires email verification at sign-up
# and the work/school flow only returns mailbox-bound emails. We trust
# Graph /me's email per Microsoft's identity-platform documentation.
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
        client = get_http_client()
        response = await client.post(
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if response.status_code != 200:
            logger.warning(
                "Microsoft token refresh failed (status=%s)", response.status_code
            )
            raise HTTPException(
                status_code=502, detail="Microsoft token refresh failed"
            )
        data = response.json()
        self.access_token = data["access_token"]
        return data

    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        client = get_http_client()
        response = await client.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code != 200:
            logger.warning(
                "Microsoft /me failed (status=%s)", response.status_code
            )
            raise HTTPException(status_code=502, detail="Microsoft user info failed")
        data = response.json()
        # ``mail`` is the verified mailbox; ``userPrincipalName`` is the
        # sign-in identifier. We accept either, but only treat ``mail`` as
        # verified — UPN can be reassigned by an admin and is not
        # equivalent to a verified inbox.
        verified_email = data.get("mail")
        return {
            "email": verified_email or data.get("userPrincipalName", ""),
            "email_verified": bool(verified_email),
            "first_name": data.get("givenName", "") or "",
            "last_name": data.get("surname", "") or "",
            "display_name": data.get("displayName", "") or "",
            "provider_user_id": str(data.get("id", "")),
        }

    @classmethod
    async def sso_handler(
        cls, code: str, redirect_uri: str
    ) -> Optional["MicrosoftIdP"]:
        code = cls.sanitize_code(code)
        client = get_http_client()
        response = await client.post(
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
            logger.error(
                "Microsoft SSO token exchange failed (status=%s)",
                response.status_code,
            )
            return None
        data = response.json()
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
        )


register_idp("microsoft", MicrosoftIdP)
