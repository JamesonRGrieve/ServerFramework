import logging
from typing import Any, Dict, Optional

import requests
from fastapi import HTTPException

from serverframework.extensions.auth_oauth.PRV_OAuth import AbstractOAuthProvider
from serverframework.lib.Environment import env


class AmazonOAuthProvider(AbstractOAuthProvider):
    def __init__(
        self,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        **kwargs,
    ):
        # Set client info from environment
        client_id = env("AWS_CLIENT_ID")
        client_secret = env("AWS_CLIENT_SECRET")

        # Setup AWS-specific configuration
        self.user_pool_id = env("AWS_USER_POOL_ID")
        self.region = env("AWS_REGION")

        # Initialize the parent class
        super().__init__(
            access_token=access_token,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            scopes="openid email profile",
            **kwargs,
        )
        self.name = "AmazonOAuth"

    @staticmethod
    def services():
        return ["auth", "user_info", "aws_services"]

    def get_new_token(self) -> str:
        try:
            response = requests.post(
                f"https://{self.user_pool_id}.auth.{self.region}.amazoncognito.com/oauth2/token",
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
            return self.access_token
        except Exception as e:
            self.handle_auth_error(e, "token refresh")

    def get_user_info(self) -> Dict[str, Any]:
        if not self.access_token:
            return {}

        try:
            uri = f"https://{self.user_pool_id}.auth.{self.region}.amazoncognito.com/oauth2/userInfo"
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
            return {
                "email": data["email"],
                "first_name": data.get("given_name", ""),
                "last_name": data.get("family_name", ""),
                "display_name": data.get("name", ""),
            }
        except Exception as e:
            self.handle_auth_error(e, "user info retrieval")

    @classmethod
    def sso_handler(cls, code, redirect_uri=None):
        if not redirect_uri:
            redirect_uri = env("MAGIC_LINK_URL")

        code = cls.sanitize_code(code)

        try:
            user_pool_id = env("AWS_USER_POOL_ID")
            region = env("AWS_REGION")

            response = requests.post(
                f"https://{user_pool_id}.auth.{region}.amazoncognito.com/oauth2/token",
                data={
                    "client_id": env("AWS_CLIENT_ID"),
                    "client_secret": env("AWS_CLIENT_SECRET"),
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )

            if response.status_code != 200:
                logging.error(f"Error getting Amazon access token: {response.text}")
                return None

            data = response.json()
            access_token = data["access_token"]
            refresh_token = data.get("refresh_token", "")

            return cls(access_token=access_token, refresh_token=refresh_token)
        except Exception as e:
            logging.error(f"Error in Amazon SSO: {str(e)}")
            return None
