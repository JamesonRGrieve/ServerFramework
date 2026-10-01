# SPDX-License-Identifier: AGPL-3.0-or-later
"""The cloud extension: path containment, upload limits, Nextcloud's WebDAV
listing (and its refusal of an entity bomb), each provider's configuration,
real calls with refused credentials, and live round trips when a test
store is configured."""

import uuid

import httpx
import pytest
from defusedxml import EntitiesForbidden

from zephyrex.extensions.cloud.EXT_Cloud import (
    MAX_UPLOAD_BYTES,
    EXT_Cloud,
    object_path,
)
from zephyrex.extensions.cloud.PRV_Azure import PRV_Azure_Cloud
from zephyrex.extensions.cloud.PRV_Dropbox import PRV_Dropbox_Cloud
from zephyrex.extensions.cloud.PRV_GCS import PRV_GCS_Cloud
from zephyrex.extensions.cloud.PRV_Nextcloud import PRV_Nextcloud_Cloud
from zephyrex.extensions.cloud.PRV_S3 import PRV_S3_Cloud
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    TransientExternalError,
)

PROPFIND = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
 <d:response><d:href>/remote.php/dav/files/ada/docs/</d:href>
  <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
 </d:response>
 <d:response><d:href>/remote.php/dav/files/ada/docs/notes%20one.txt</d:href>
  <d:propstat><d:prop><d:resourcetype/><d:getcontentlength>12</d:getcontentlength>
   <d:getlastmodified>Wed, 01 Oct 2026 12:00:00 GMT</d:getlastmodified></d:prop></d:propstat>
 </d:response>
 <d:response><d:href>/remote.php/dav/files/ada/docs/archive/</d:href>
  <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
 </d:response>
</d:multistatus>"""

ENTITY_BOMB = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">]>
<d:multistatus xmlns:d="DAV:"><d:response><d:href>&lol2;</d:href></d:response></d:multistatus>"""


def _online(url: str) -> bool:
    try:
        httpx.head(url, timeout=5)
        return True
    except httpx.HTTPError:
        return False


class TestPaths:
    @pytest.mark.parametrize(
        "given, path",
        [
            ("a/b.txt", "a/b.txt"),
            ("/a//b.txt", "a/b.txt"),
            ("./a/./b", "a/b"),
            ("a\\b", "a/b"),
        ],
    )
    def test_normalised(self, given, path):
        assert object_path(given) == path

    @pytest.mark.parametrize("given", ["../etc/passwd", "a/../../b", "a/.."])
    def test_a_path_cannot_climb_out(self, given):
        with pytest.raises(InvalidInputExternalError, match="climbs out"):
            object_path(given)

    def test_a_file_needs_a_name_but_a_folder_may_be_the_root(self):
        with pytest.raises(InvalidInputExternalError):
            object_path("/")
        assert object_path("/", folder=True) == ""


class TestExtension:
    def test_providers(self):
        assert {p.name for p in EXT_Cloud.providers} == {
            "s3",
            "azure_blob",
            "gcs",
            "dropbox",
            "nextcloud",
        }

    async def test_an_upload_over_the_limit_is_refused(self):
        with pytest.raises(InvalidInputExternalError, match="at most"):
            await EXT_Cloud.upload_file("big.bin", b"\0" * (MAX_UPLOAD_BYTES + 1))


class TestNextcloudListing:
    def test_children_of_the_folder(self):
        entries = PRV_Nextcloud_Cloud.entries(PROPFIND, "docs")
        assert entries == [
            {
                "name": "notes one.txt",
                "path": "docs/notes one.txt",
                "size": 12,
                "modified": "Wed, 01 Oct 2026 12:00:00 GMT",
                "is_folder": False,
            },
            {
                "name": "archive",
                "path": "docs/archive",
                "size": None,
                "modified": None,
                "is_folder": True,
            },
        ]

    def test_an_entity_bomb_is_refused(self):
        with pytest.raises(EntitiesForbidden):
            PRV_Nextcloud_Cloud.entries(ENTITY_BOMB, "")


class TestConfiguration:
    @pytest.mark.parametrize(
        "provider, variables",
        [
            (PRV_S3_Cloud, ["S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_BUCKET"]),
            (PRV_Azure_Cloud, ["AZURE_STORAGE_ACCOUNT", "AZURE_STORAGE_KEY"]),
            (PRV_GCS_Cloud, ["GCS_BUCKET"]),
            (PRV_Dropbox_Cloud, ["DROPBOX_ACCESS_TOKEN"]),
            (PRV_Nextcloud_Cloud, ["NEXTCLOUD_URL", "NEXTCLOUD_APP_PASSWORD"]),
        ],
    )
    async def test_unconfigured_fails_over(
        self, provider_instance, set_env, provider, variables
    ):
        for variable in variables:
            set_env(variable, "")
        with pytest.raises(TransientExternalError, match="not configured"):
            await provider.list(provider_instance(provider), "")

    async def test_gcs_refuses_a_key_that_is_not_one(self, provider_instance):
        instance = provider_instance(
            PRV_GCS_Cloud,
            settings={"bucket": "b", "credentials_json": '{"not": "a key"}'},
        )
        with pytest.raises(InvalidInputExternalError, match="service account key"):
            await PRV_GCS_Cloud.list(instance, "")

    async def test_azure_refuses_an_account_name_that_could_change_the_host(
        self, provider_instance
    ):
        instance = provider_instance(
            PRV_Azure_Cloud,
            api_key="a2V5",
            settings={"account_name": "evil.example#", "container": "c"},
        )
        with pytest.raises(InvalidInputExternalError, match="letters and digits"):
            await PRV_Azure_Cloud.list(instance, "")


class TestRefusedCredentialsLive:
    @pytest.mark.xfail(
        not _online("https://s3.amazonaws.com"), reason="AWS unreachable"
    )
    async def test_s3(self, provider_instance):
        instance = provider_instance(
            PRV_S3_Cloud,
            api_key="AKIAZEPHYREXTEST0000",
            settings={"secret_key": "0" * 40, "bucket": "zephyrex-no-such-bucket"},
        )
        with pytest.raises(AuthExternalError):
            await PRV_S3_Cloud.list(instance, "")

    @pytest.mark.xfail(
        not _online("https://api.dropboxapi.com"), reason="Dropbox unreachable"
    )
    async def test_dropbox(self, provider_instance):
        instance = provider_instance(PRV_Dropbox_Cloud, api_key="not-a-real-token")
        with pytest.raises(AuthExternalError):
            await PRV_Dropbox_Cloud.list(instance, "")

    async def test_azure_unknown_account_fails_over(self, provider_instance):
        instance = provider_instance(
            PRV_Azure_Cloud,
            api_key="a2V5a2V5",
            settings={"account_name": f"zx{uuid.uuid4().hex[:18]}", "container": "c"},
        )
        with pytest.raises(TransientExternalError):
            await PRV_Azure_Cloud.list(instance, "")


@pytest.mark.external_api(provider="s3_test")
async def test_s3_round_trip(provider_instance, sandbox_credentials_for):
    creds = sandbox_credentials_for("s3_test")
    instance = provider_instance(
        PRV_S3_Cloud,
        api_key=creds["S3_ACCESS_KEY_ID"],
        settings={
            "secret_key": creds["S3_SECRET_ACCESS_KEY"],
            "bucket": creds["S3_BUCKET"],
        },
    )
    path = f"zephyrex-test/{uuid.uuid4().hex}.bin"
    body = bytes(range(256))
    await PRV_S3_Cloud.upload(instance, path, body)
    assert await PRV_S3_Cloud.download(instance, path) == body
    listed = await PRV_S3_Cloud.list(instance, "zephyrex-test")
    assert any(entry["path"] == path for entry in listed)
    await PRV_S3_Cloud.delete(instance, path)


@pytest.mark.external_api(provider="nextcloud_test")
async def test_nextcloud_round_trip(provider_instance, sandbox_credentials_for):
    creds = sandbox_credentials_for("nextcloud_test")
    instance = provider_instance(
        PRV_Nextcloud_Cloud,
        api_key=creds["NEXTCLOUD_APP_PASSWORD"],
        settings={
            "base_url": creds["NEXTCLOUD_URL"],
            "username": creds["NEXTCLOUD_USERNAME"],
        },
    )
    path = f"zephyrex-test-{uuid.uuid4().hex}.bin"
    body = bytes(range(256))
    await PRV_Nextcloud_Cloud.upload(instance, path, body)
    assert await PRV_Nextcloud_Cloud.download(instance, path) == body
    assert any(
        entry["path"] == path for entry in await PRV_Nextcloud_Cloud.list(instance, "")
    )
    await PRV_Nextcloud_Cloud.delete(instance, path)
