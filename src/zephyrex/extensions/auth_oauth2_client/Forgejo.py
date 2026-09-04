import logging
from typing import Any, Dict, Optional

import requests
from fastapi import HTTPException

from zephyrex.extensions.auth_oauth2_client.PRV_OAuth import AbstractOAuthProvider
from zephyrex.lib.Environment import env

REQUEST_TIMEOUT_SECONDS = 10


def _forgejo_base() -> str:
    """Base URL of the self-hosted Forgejo instance (no public default host).

    Prefers ``FORGEJO_OAUTH_BASE_URL`` so the OAuth endpoint can differ from
    the API base if needed, falling back to ``FORGEJO_BASE_URL``.
    """
    base = env("FORGEJO_OAUTH_BASE_URL") or env("FORGEJO_BASE_URL")
    return (base or "").rstrip("/")


class ForgejoOAuthProvider(AbstractOAuthProvider):
    """Self-hosted Forgejo (Gitea-compatible) OAuth2 identity provider.

    Unlike GitHub/Google there is no public host, so every endpoint derives
    from ``FORGEJO_BASE_URL`` (or ``FORGEJO_OAUTH_BASE_URL``):

    - authorize: ``{base}/login/oauth/authorize``
    - token:     ``{base}/login/oauth/access_token``
    - userinfo:  ``{base}/api/v1/user``

    Client credentials come from ``FORGEJO_CLIENT_ID`` / ``FORGEJO_CLIENT_SECRET``.
    ``get_user_info`` reports ``username`` (Forgejo ``login``) so consumers such
    as forgejo-classroom can auto-fill the student's Forgejo account.
    """

    def __init__(
        self,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(
            access_token=access_token,
            refresh_token=refresh_token,
            client_id=env("FORGEJO_CLIENT_ID"),
            client_secret=env("FORGEJO_CLIENT_SECRET"),
            scopes="read:user",
            **kwargs,
        )
        self.name = "ForgejoOAuth"

    @staticmethod
    def services():
        return ["auth", "user_info"]

    def get_new_token(self) -> str:  # type: ignore[return]
        try:
            response = requests.post(
                f"{_forgejo_base()}/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Token refresh failed: {response.text}",
                )

            self.access_token = response.json()["access_token"]
            return self.access_token  # type: ignore[no-any-return]
        except Exception as e:
            self.handle_auth_error(e, "token refresh")

    def get_user_info(self) -> Dict[str, Any]:  # type: ignore[return]
        if not self.access_token:
            return {}

        try:
            uri = f"{_forgejo_base()}/api/v1/user"
            response = requests.get(
                uri,
                headers={"Authorization": f"token {self.access_token}"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            if response.status_code == 401 and self.refresh_token:
                self.access_token = self.get_new_token()
                response = requests.get(
                    uri,
                    headers={"Authorization": f"token {self.access_token}"},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )

            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Failed to get user info: {response.text}",
                )

            data = response.json()
            full_name = data.get("full_name") or ""

            return {
                "id": data.get("id"),
                "email": data.get("email"),
                "first_name": full_name.split()[0] if full_name else "",
                "last_name": full_name.split()[-1] if full_name else "",
                "display_name": full_name or data.get("login", ""),
                "username": data.get("login", ""),
                "avatar_url": data.get("avatar_url", ""),
            }
        except Exception as e:
            self.handle_auth_error(e, "user info retrieval")

    @classmethod
    def sso_handler(cls, code, redirect_uri=None):
        if not redirect_uri:
            redirect_uri = env("MAGIC_LINK_URL")

        code = cls.sanitize_code(code)

        try:
            response = requests.post(
                f"{_forgejo_base()}/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": env("FORGEJO_CLIENT_ID"),
                    "client_secret": env("FORGEJO_CLIENT_SECRET"),
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            if response.status_code != 200:
                logging.error(f"Error getting Forgejo access token: {response.text}")
                return None

            data = response.json()
            access_token = data["access_token"]
            refresh_token = data.get("refresh_token", "Not provided")

            return cls(access_token=access_token, refresh_token=refresh_token)
        except Exception as e:
            logging.error(f"Error in Forgejo SSO: {str(e)}")
            return None
