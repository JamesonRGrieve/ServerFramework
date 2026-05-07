import logging
from typing import Any, Dict, List, Optional

import requests
from fastapi import HTTPException

from extensions.oauth.PRV_OAuth import AbstractOAuthProvider
from lib.Environment import env

# Microsoft OAuth scopes
SCOPES = (
    "offline_access "
    "https://graph.microsoft.com/User.Read "
    "https://graph.microsoft.com/Mail.Send "
    "https://graph.microsoft.com/Calendars.ReadWrite.Shared "
)


class MicrosoftOAuthProvider(AbstractOAuthProvider):
    def __init__(
        self,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        **kwargs,
    ):
        client_id = env("MICROSOFT_CLIENT_ID")
        client_secret = env("MICROSOFT_CLIENT_SECRET")

        super().__init__(
            access_token=access_token,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
            **kwargs,
        )
        self.name = "MicrosoftOAuth"

    @staticmethod
    def services():
        return ["auth", "user_info", "mail", "calendar", "microsoft_graph"]

    def get_new_token(self) -> str:
        try:
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
            uri = "https://graph.microsoft.com/v1.0/me"
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
                "email": data.get("mail", data.get("userPrincipalName", "")),
                "first_name": data.get("givenName", ""),
                "last_name": data.get("surname", ""),
                "display_name": data.get("displayName", ""),
                "id": data.get("id", ""),
            }
        except Exception as e:
            self.handle_auth_error(e, "user info retrieval")

    def send_email(
        self,
        subject: str,
        body: str,
        to_recipients: List[str],
        cc_recipients=None,
        is_html=False,
    ) -> Dict[str, Any]:
        if not self.access_token:
            raise HTTPException(status_code=401, detail="Authentication required")

        try:
            uri = "https://graph.microsoft.com/v1.0/me/sendMail"

            # Format recipients
            to_list = [{"emailAddress": {"address": email}} for email in to_recipients]
            cc_list = []
            if cc_recipients:
                cc_list = [
                    {"emailAddress": {"address": email}} for email in cc_recipients
                ]

            # Prepare email data
            email_data = {
                "message": {
                    "subject": subject,
                    "body": {
                        "contentType": "HTML" if is_html else "Text",
                        "content": body,
                    },
                    "toRecipients": to_list,
                }
            }

            if cc_recipients:
                email_data["message"]["ccRecipients"] = cc_list

            response = requests.post(
                uri,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                },
                json=email_data,
            )

            if response.status_code == 401 and self.refresh_token:
                self.access_token = self.get_new_token()
                response = requests.post(
                    uri,
                    headers={
                        "Authorization": f"Bearer {self.access_token}",
                        "Content-Type": "application/json",
                    },
                    json=email_data,
                )

            if response.status_code != 202:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Failed to send email: {response.text}",
                )

            return {"status": "sent"}
        except Exception as e:
            logging.error(f"Error sending email: {str(e)}")
            raise HTTPException(
                status_code=400, detail=f"Error sending email: {str(e)}"
            )

    def get_calendar_events(self, start_datetime, end_datetime) -> List[Dict[str, Any]]:
        if not self.access_token:
            raise HTTPException(status_code=401, detail="Authentication required")

        try:
            uri = (
                f"https://graph.microsoft.com/v1.0/me/calendarView?"
                f"startDateTime={start_datetime}&endDateTime={end_datetime}"
            )

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
                    detail=f"Failed to get calendar events: {response.text}",
                )

            return response.json().get("value", [])
        except Exception as e:
            logging.error(f"Error getting calendar events: {str(e)}")
            raise HTTPException(
                status_code=400, detail=f"Error getting calendar events: {str(e)}"
            )

    @classmethod
    def sso_handler(cls, code, redirect_uri=None):
        if not redirect_uri:
            redirect_uri = env("MAGIC_LINK_URL")

        code = cls.sanitize_code(code)

        try:
            response = requests.post(
                "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                data={
                    "client_id": env("MICROSOFT_CLIENT_ID"),
                    "client_secret": env("MICROSOFT_CLIENT_SECRET"),
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )

            if response.status_code != 200:
                logging.error(f"Error getting Microsoft access token: {response.text}")
                return None

            data = response.json()
            access_token = data["access_token"]
            refresh_token = data.get("refresh_token", "Not provided")

            return cls(access_token=access_token, refresh_token=refresh_token)
        except Exception as e:
            logging.error(f"Error in Microsoft SSO: {str(e)}")
            return None
