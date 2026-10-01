# SPDX-License-Identifier: AGPL-3.0-or-later
"""Google Cloud Storage: a bucket, with a service account's JSON key (the
``credentials_json`` setting) or, left empty, the host's Application
Default Credentials.
"""

import asyncio
import json
from typing import Any, Callable, ClassVar, Dict, List, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.cloud.EXT_Cloud import AbstractCloudProvider
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency, importable
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class PRV_GCS_Cloud(AbstractCloudProvider):
    name: ClassVar[str] = "gcs"
    friendly_name: ClassVar[str] = "Google Cloud Storage"
    description: ClassVar[str] = "A Google Cloud Storage bucket"
    _env: ClassVar[Dict[str, Any]] = {"GCS_BUCKET": "", "GCS_CREDENTIALS_JSON": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting("bucket", "Bucket name", env="GCS_BUCKET"),
        InstanceSetting(
            "credentials_json",
            "Service account JSON key (empty: Application Default Credentials)",
            env="GCS_CREDENTIALS_JSON",
            secret=True,
        ),
    )
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="google-cloud-storage",
                friendly_name="Google Cloud Storage SDK",
                semver=">=2.10.0",
                reason="GCS provider",
            ),
            PIP_Dependency(
                name="google-api-core",
                friendly_name="Google API core",
                semver=">=2.11.0",
                reason="GCS errors",
            ),
            PIP_Dependency(
                name="google-auth",
                friendly_name="Google auth",
                semver=">=2.0.0",
                reason="GCS credentials",
            ),
        ]
    )

    @classmethod
    def _bucket(cls, instance: ProviderInstanceModel) -> Any:
        if not importable("google.cloud.storage"):
            raise TransientExternalError(
                "google-cloud-storage package not installed", provider=cls.name
            )
        bucket = cls.setting(instance, "bucket")
        if not bucket:
            raise TransientExternalError("GCS bucket not configured", provider=cls.name)
        from google.cloud import storage
        from google.oauth2 import service_account

        key = cls.setting(instance, "credentials_json")
        if key:
            try:
                credentials = service_account.Credentials.from_service_account_info(
                    json.loads(key)
                )
            except (ValueError, KeyError) as exc:
                raise InvalidInputExternalError(
                    "GCS credentials_json is not a service account key",
                    provider=cls.name,
                ) from exc
            client = storage.Client(
                credentials=credentials, project=credentials.project_id
            )
        else:
            client = storage.Client()
        return client.bucket(bucket)

    @classmethod
    async def _call(cls, call: Callable[[], Any]) -> Any:
        from google.api_core import exceptions as gcs
        from google.auth import exceptions as auth

        try:
            return await asyncio.to_thread(call)
        except (gcs.Unauthorized, gcs.Forbidden, auth.GoogleAuthError) as exc:
            raise AuthExternalError(
                "GCS refused the credentials", provider=cls.name
            ) from exc
        except gcs.NotFound as exc:
            raise InvalidInputExternalError(
                "GCS: no such object or bucket", provider=cls.name, upstream_status=404
            ) from exc
        except gcs.GoogleAPIError as exc:
            raise TransientExternalError(
                f"GCS: {type(exc).__name__}", provider=cls.name
            ) from exc

    @classmethod
    async def upload(
        cls, instance: ProviderInstanceModel, path: str, content: bytes
    ) -> Dict[str, Any]:
        bucket = cls._bucket(instance)
        await cls._call(lambda: bucket.blob(path).upload_from_string(content))
        return {"path": path, "size": len(content), "provider": cls.name}

    @classmethod
    async def download(cls, instance: ProviderInstanceModel, path: str) -> bytes:
        bucket = cls._bucket(instance)
        content: bytes = await cls._call(lambda: bucket.blob(path).download_as_bytes())
        return content

    @classmethod
    async def delete(cls, instance: ProviderInstanceModel, path: str) -> None:
        bucket = cls._bucket(instance)
        await cls._call(lambda: bucket.blob(path).delete())

    @classmethod
    async def list(
        cls, instance: ProviderInstanceModel, folder: str
    ) -> List[Dict[str, Any]]:
        bucket = cls._bucket(instance)
        prefix = f"{folder}/" if folder else ""

        def page() -> Tuple[List[Any], List[str]]:
            listing = bucket.list_blobs(prefix=prefix, delimiter="/")
            blobs = list(listing)
            return blobs, sorted(listing.prefixes)

        blobs, prefixes = await cls._call(page)
        return [
            {
                "name": folder_prefix[len(prefix) :].rstrip("/"),
                "path": folder_prefix.rstrip("/"),
                "size": None,
                "modified": None,
                "is_folder": True,
            }
            for folder_prefix in prefixes
        ] + [
            {
                "name": blob.name[len(prefix) :],
                "path": blob.name,
                "size": blob.size,
                "modified": blob.updated.isoformat() if blob.updated else None,
                "is_folder": False,
            }
            for blob in blobs
        ]
