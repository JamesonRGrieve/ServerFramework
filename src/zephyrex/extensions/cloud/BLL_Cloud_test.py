# SPDX-License-Identifier: AGPL-3.0-or-later
"""The /v1/cloud routes and the delegated Google Drive provider: Drive's
query and file shapes, the refusals a user can act on (not signed in with
Google, a sign-in without Drive access, Google refusing the token), that the
deployment's own object stores are never served, and a real call to Google
with a refused token when the network allows."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.cloud.EXT_Cloud import EXT_Cloud
from zephyrex.extensions.cloud.PRV_GoogleDrive import (
    DRIVE_READONLY_SCOPE,
    PRV_GoogleDrive,
    cloud_file,
    file_query,
)
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.SecretEncryption import encrypt_secret

# A Drive files.list answer, as Google sends it (size is a string; Docs have none).
DRIVE_PAGE: dict[str, Any] = {
    "files": [
        {
            "id": "1AbC-dEf_2",
            "name": "Keynote script",
            "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2026-10-30T18:04:11.512Z",
            "starred": True,
        },
        {
            "id": "9zz",
            "name": "notes.md",
            "mimeType": "text/markdown",
            "modifiedTime": "2026-10-01T09:00:00.000Z",
            "size": "2048",
        },
        {
            "id": "f0",
            "name": "Talks",
            "mimeType": "application/vnd.google-apps.folder",
            "starred": False,
        },
    ]
}


def _online(url: str) -> bool:
    try:
        httpx.head(url, timeout=5)
        return True
    except httpx.HTTPError:
        return False


class TestDriveShapes:
    def test_files_parse_with_sizes_stars_and_folders(self):
        parsed = [cloud_file(item).model_dump() for item in DRIVE_PAGE["files"]]
        assert parsed == [
            {
                "path": "1AbC-dEf_2",
                "name": "Keynote script",
                "mime_type": "application/vnd.google-apps.document",
                "modified_at": "2026-10-30T18:04:11.512Z",
                "size": None,
                "starred": True,
                "is_folder": False,
            },
            {
                "path": "9zz",
                "name": "notes.md",
                "mime_type": "text/markdown",
                "modified_at": "2026-10-01T09:00:00.000Z",
                "size": 2048,
                "starred": False,
                "is_folder": False,
            },
            {
                "path": "f0",
                "name": "Talks",
                "mime_type": "application/vnd.google-apps.folder",
                "modified_at": None,
                "size": None,
                "starred": False,
                "is_folder": True,
            },
        ]

    def test_query_escapes_a_search_and_scopes_a_folder(self):
        query = file_query("f0", "Bob's \\ talk")
        assert "name contains 'Bob\\'s \\\\ talk'" in query
        assert "'f0' in parents" in query
        assert "trashed = false" in query
        assert "mimeType = 'application/vnd.google-apps.document'" in query

    def test_a_folder_that_is_not_a_drive_id_is_refused(self):
        with pytest.raises(InvalidInputExternalError):
            file_query("x' or name contains '", None)

    async def test_google_refuses_a_bogus_token(self):
        if not _online("https://www.googleapis.com"):
            pytest.skip("Google is unreachable from this host")
        with pytest.raises(AuthExternalError):
            await PRV_GoogleDrive.list("not-a-real-token", "", None)


class TestCloudRoutes(ExtensionServerMixin):
    extension_class = EXT_Cloud

    @staticmethod
    def _headers(user: Any) -> dict[str, str]:
        return {"Authorization": f"Bearer {user.jwt}"}

    @staticmethod
    def _google_identity(server: Any, user: Any, scopes: str) -> None:
        """``user`` signed in with Google once (a configured Google client and
        an identity holding a current token with ``scopes``)."""
        from zephyrex.extensions.oauth_consumer.BLL_OAuthConsumer import (
            OAuthIdentityModel,
        )
        from zephyrex.logic.BLL_Providers import (
            ProviderInstanceManager,
            ProviderInstanceSettingManager,
            ProviderManager,
        )

        registry = server.app.state.model_registry
        root_id = env("ROOT_ID")
        record = ProviderManager(model_registry=registry, requester_id=root_id).get(
            name="oauth_google"
        )
        instance = ProviderInstanceManager(
            model_registry=registry, requester_id=root_id
        ).create(
            name=f"test-google-{secrets.token_hex(4)}",
            provider_id=record.id,
            scope="root",
        )
        ProviderInstanceSettingManager(
            model_registry=registry, requester_id=root_id
        ).create(provider_instance_id=instance.id, key="client_id", value="test-client")
        IdentityDB = OAuthIdentityModel.DB(registry.DB.manager.Base)
        IdentityDB.create(
            requester_id=root_id,
            model_registry=registry,
            return_type="dto",
            override_dto=OAuthIdentityModel,
            user_id=user.id,
            provider_instance_id=str(instance.id),
            provider="google",
            issuer="https://accounts.google.com",
            subject=f"google-{secrets.token_hex(6)}",
            access_token=encrypt_secret("not-a-real-token"),
            token_expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=scopes,
        )

    def test_signed_out_is_401_and_unknown_or_system_stores_are_404(
        self, server, admin_a
    ):
        assert server.get("/v1/cloud/google_drive/files").status_code == 401
        for store in ("nextcloud", "s3", "nothing"):
            response = server.get(
                f"/v1/cloud/{store}/files", headers=self._headers(admin_a)
            )
            assert response.status_code == 404, store

    def test_never_signed_in_with_google_is_409(self, server, admin_a):
        response = server.get(
            "/v1/cloud/google_drive/files", headers=self._headers(admin_a)
        )
        assert response.status_code == 409, response.text
        assert "Sign in with google" in response.json()["detail"]["message"]

    def test_a_sign_in_without_drive_access_is_403_then_google_decides(
        self, server, admin_b
    ):
        self._google_identity(server, admin_b, "openid email profile")
        response = server.get(
            "/v1/cloud/google_drive/files", headers=self._headers(admin_b)
        )
        assert response.status_code == 403, response.text
        assert "sign in again to allow it" in response.json()["detail"]["message"]

        if not _online("https://www.googleapis.com"):
            pytest.skip("Google is unreachable from this host")
        self._google_identity(server, admin_b, f"openid email {DRIVE_READONLY_SCOPE}")
        refused = server.get(
            "/v1/cloud/google_drive/file",
            params={"path": "1AbC-dEf_2"},
            headers=self._headers(admin_b),
        )
        assert refused.status_code == 403, refused.text
        assert "refused your sign-in" in refused.json()["detail"]["message"]
