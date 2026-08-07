import logging
from typing import Any, Dict, Optional

import requests
from fastapi import HTTPException

from zephyrex.extensions.auth_oauth.PRV_OAuth import AbstractOAuthProvider
from zephyrex.lib.Environment import env

# Google OAuth scopes
OAUTH_SCOPES = (
    "https://www.googleapis.com/auth/userinfo.profile "
    "https://www.googleapis.com/auth/calendar.events.owned "
    "https://www.googleapis.com/auth/contacts.readonly "
    "https://www.googleapis.com/auth/calendar.readonly"
)


class GoogleOAuthProvider(AbstractOAuthProvider):
    def __init__(
        self,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        **kwargs,
    ):
        client_id = env("GOOGLE_CLIENT_ID")
        client_secret = env("GOOGLE_CLIENT_SECRET")

        super().__init__(
            access_token=access_token,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            scopes=OAUTH_SCOPES,
            **kwargs,
        )
        self.name = "GoogleOAuth"
        self.email_address = self.get_email()

    @staticmethod
    def services():
        return ["auth", "user_info", "calendar"]

    def get_new_token(self):
        try:
            response = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                    "scope": self.scopes,
                },
            )

            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Token refresh failed: {response.text}",
                )

            self.access_token = response.json()["access_token"]
            return self.access_token
        except Exception as e:
            self.handle_auth_error(e, "token refresh")

    def get_user_info(self) -> Dict[str, Any]:  # type: ignore[return]
        if not self.access_token:
            return {}

        try:
            uri = "https://people.googleapis.com/v1/people/me?personFields=names,emailAddresses"
            response = requests.get(
                uri,
                headers={"Authorization": f"Bearer {self.access_token}"},
            )

            if response.status_code == 401 and self.refresh_token:
                self.access_token = self.get_new_token()
                response = requests.get(
                    uri,
                    headers={"Authorization": f"Bearer {self.access_token}"},
                )

            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Failed to get user info: {response.text}",
                )

            data = response.json()
            first_name = data["names"][0]["givenName"]
            last_name = data["names"][0]["familyName"]
            email = data["emailAddresses"][0]["value"]

            return {
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "display_name": f"{first_name} {last_name}",
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
                "https://accounts.google.com/o/oauth2/token",
                params={
                    "code": code,
                    "client_id": env("GOOGLE_CLIENT_ID"),
                    "client_secret": env("GOOGLE_CLIENT_SECRET"),
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                    "scope": OAUTH_SCOPES,
                    "access_type": "offline",
                },
            )

            if response.status_code != 200:
                logging.error(f"Error getting Google access token: {response.text}")
                return None

            data = response.json()
            access_token = data["access_token"]
            refresh_token = data.get("refresh_token", "")

            return cls(access_token=access_token, refresh_token=refresh_token)
        except Exception as e:
            logging.error(f"Error in Google SSO: {str(e)}")
            return None
