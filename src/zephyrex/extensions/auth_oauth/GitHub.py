import logging
from typing import Any, Dict, Optional

import requests
from fastapi import HTTPException

from zephyrex.extensions.auth_oauth.PRV_OAuth import AbstractOAuthProvider
from zephyrex.lib.Environment import env


class GitHubOAuthProvider(AbstractOAuthProvider):
    def __init__(
        self,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        **kwargs,
    ):
        # Set client info from environment
        client_id = env("GITHUB_CLIENT_ID")
        client_secret = env("GITHUB_CLIENT_SECRET")

        # Initialize the parent class
        super().__init__(
            access_token=access_token,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            scopes="user:email read:user",
            **kwargs,
        )
        self.name = "GitHubOAuth"

    @staticmethod
    def services():
        return ["auth", "user_info", "repo_management"]

    def get_new_token(self) -> str:  # type: ignore[return]
        try:
            # GitHub tokens do not support refresh tokens directly, we need to re-authorize.
            response = requests.post(
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
            uri = "https://api.github.com/user"
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

            # Get primary email
            email_response = requests.get(
                "https://api.github.com/user/emails",
                headers={"Authorization": f"Bearer {self.access_token}"},
            )

            if email_response.status_code != 200:
                raise HTTPException(
                    status_code=email_response.status_code,
                    detail=f"Failed to get email: {email_response.text}",
                )

            email_data = email_response.json()
            primary_email = next(
                (email["email"] for email in email_data if email["primary"]), None
            )

            return {
                "email": primary_email,
                "first_name": (
                    data.get("name", "").split()[0] if data.get("name") else ""
                ),
                "last_name": (
                    data.get("name", "").split()[-1] if data.get("name") else ""
                ),
                "display_name": data.get("name", ""),
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
                f"https://github.com/login/oauth/access_token",
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
                logging.error(f"Error getting GitHub access token: {response.text}")
                return None

            data = response.json()
            access_token = data["access_token"]
            refresh_token = data.get("refresh_token", "Not provided")

            return cls(access_token=access_token, refresh_token=refresh_token)
        except Exception as e:
            logging.error(f"Error in GitHub SSO: {str(e)}")
            return None
