# SPDX-License-Identifier: AGPL-3.0-or-later
"""Amazon S3, or any S3-compatible store (MinIO, Ceph, Backblaze B2, …)
through the ``endpoint_url`` setting. The instance's API key is the
access key id; its ``secret_key`` the secret access key.
"""

import asyncio
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

DEFAULT_REGION = "us-east-1"
CONNECT_TIMEOUT_SECONDS = 10
_AUTH_ERRORS = {
    "InvalidAccessKeyId",
    "SignatureDoesNotMatch",
    "AccessDenied",
    "ExpiredToken",
    "InvalidToken",
}
_MISSING = {"NoSuchKey", "NoSuchBucket", "404"}


class PRV_S3_Cloud(AbstractCloudProvider):
    name: ClassVar[str] = "s3"
    friendly_name: ClassVar[str] = "Amazon S3"
    description: ClassVar[str] = "Amazon S3 or an S3-compatible store"
    _env: ClassVar[Dict[str, Any]] = {
        "S3_ACCESS_KEY_ID": "",
        "S3_SECRET_ACCESS_KEY": "",
        "S3_BUCKET": "",
        "S3_REGION": DEFAULT_REGION,
        "S3_ENDPOINT_URL": "",
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Access key id",
            env="S3_ACCESS_KEY_ID",
            secret=True,
            field="api_key",
        ),
        InstanceSetting(
            "secret_key", "Secret access key", env="S3_SECRET_ACCESS_KEY", secret=True
        ),
        InstanceSetting("bucket", "Bucket name", env="S3_BUCKET"),
        InstanceSetting("region", "Region", env="S3_REGION", default=DEFAULT_REGION),
        InstanceSetting(
            "endpoint_url",
            "Endpoint of an S3-compatible store (empty for AWS)",
            env="S3_ENDPOINT_URL",
        ),
    )
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="boto3",
                friendly_name="AWS SDK for Python",
                semver=">=1.26.0",
                reason="Amazon S3 provider",
            ),
            PIP_Dependency(
                name="botocore",
                friendly_name="AWS SDK core",
                semver=">=1.29.0",
                reason="S3 client errors",
            ),
        ]
    )

    @classmethod
    def _client(cls, instance: ProviderInstanceModel) -> Tuple[Any, str]:
        """``(boto3 S3 client, bucket)``."""
        if not importable("boto3"):
            raise TransientExternalError(
                "boto3 package not installed", provider=cls.name
            )
        key_id, secret = cls.setting(instance, "api_key"), cls.setting(
            instance, "secret_key"
        )
        bucket = cls.setting(instance, "bucket")
        if not (key_id and secret and bucket):
            raise TransientExternalError(
                "S3 credentials and bucket not configured", provider=cls.name
            )
        import boto3
        from botocore.config import Config

        return (
            boto3.client(
                "s3",
                aws_access_key_id=key_id,
                aws_secret_access_key=secret,
                region_name=cls.setting(instance, "region"),
                endpoint_url=cls.setting(instance, "endpoint_url") or None,
                # The rotation retries and fails over, not the SDK.
                config=Config(
                    retries={"max_attempts": 1},
                    connect_timeout=CONNECT_TIMEOUT_SECONDS,
                ),
            ),
            bucket,
        )

    @classmethod
    async def _call(cls, call: Callable[[], Any]) -> Any:
        """A blocking S3 call, off the event loop, its failures typed."""
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return await asyncio.to_thread(call)
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code, detail = str(error.get("Code", "")), error.get("Message", "")
            if code in _AUTH_ERRORS:
                raise AuthExternalError(f"S3: {code}", provider=cls.name) from exc
            if code in _MISSING:
                raise InvalidInputExternalError(
                    f"S3: {code}", provider=cls.name, upstream_status=404
                ) from exc
            raise TransientExternalError(
                f"S3: {code} {detail}", provider=cls.name
            ) from exc
        except BotoCoreError as exc:
            raise TransientExternalError(str(exc), provider=cls.name) from exc

    @classmethod
    async def upload(
        cls, instance: ProviderInstanceModel, path: str, content: bytes
    ) -> Dict[str, Any]:
        client, bucket = cls._client(instance)
        await cls._call(
            lambda: client.put_object(Bucket=bucket, Key=path, Body=content)
        )
        return {"path": path, "size": len(content), "provider": cls.name}

    @classmethod
    async def download(cls, instance: ProviderInstanceModel, path: str) -> bytes:
        client, bucket = cls._client(instance)
        answer = await cls._call(lambda: client.get_object(Bucket=bucket, Key=path))
        body: bytes = await asyncio.to_thread(answer["Body"].read)
        return body

    @classmethod
    async def delete(cls, instance: ProviderInstanceModel, path: str) -> None:
        client, bucket = cls._client(instance)
        await cls._call(lambda: client.delete_object(Bucket=bucket, Key=path))

    @classmethod
    async def list(
        cls, instance: ProviderInstanceModel, folder: str
    ) -> List[Dict[str, Any]]:
        client, bucket = cls._client(instance)
        prefix = f"{folder}/" if folder else ""
        answer = await cls._call(
            lambda: client.list_objects_v2(Bucket=bucket, Prefix=prefix, Delimiter="/")
        )
        folders = [
            {
                "name": entry["Prefix"][len(prefix) :].rstrip("/"),
                "path": entry["Prefix"].rstrip("/"),
                "size": None,
                "modified": None,
                "is_folder": True,
            }
            for entry in answer.get("CommonPrefixes", [])
        ]
        files = [
            {
                "name": entry["Key"][len(prefix) :],
                "path": entry["Key"],
                "size": entry.get("Size"),
                "modified": (
                    entry["LastModified"].isoformat()
                    if entry.get("LastModified")
                    else None
                ),
                "is_folder": False,
            }
            for entry in answer.get("Contents", [])
        ]
        return folders + files
