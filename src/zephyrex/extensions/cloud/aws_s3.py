import os
from typing import Any, Dict, List

from zephyrex.extensions.cloud.PRV_Cloud import AbstractCloudProvider
from zephyrex.lib.Logging import logger


class AWSS3Provider(AbstractCloudProvider):
    """
    Cloud storage provider backed by Amazon S3.
    Requires the optional ``boto3`` dependency.
    """

    def get_platform_name(self) -> str:
        return "AWS S3"

    def _client(self) -> Any:
        import boto3

        return boto3.client(
            "s3",
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
        )

    @staticmethod
    def _build_key(name: str, folder_path: str) -> str:
        return f"{folder_path.strip('/')}/{name}" if folder_path else name

    def upload_file(
        self, file_path: str, content: bytes, folder_path: str = ""
    ) -> Dict[str, Any]:
        key = self._build_key(file_path, folder_path)
        self._client().put_object(Bucket=self.bucket_name, Key=key, Body=content)
        return {"success": True, "key": key}

    def download_file(self, file_id: str, destination_path: str = "") -> bytes:
        key = self._build_key(file_id, destination_path)
        response = self._client().get_object(Bucket=self.bucket_name, Key=key)
        body: bytes = response["Body"].read()
        return body

    def delete_file(self, file_id: str, folder_path: str = "") -> bool:
        key = self._build_key(file_id, folder_path)
        self._client().delete_object(Bucket=self.bucket_name, Key=key)
        return True

    def list_files(self, folder_path: str = "") -> List[Dict[str, Any]]:
        list_kwargs: Dict[str, Any] = {"Bucket": self.bucket_name}
        if folder_path:
            list_kwargs["Prefix"] = folder_path
        response = self._client().list_objects_v2(**list_kwargs)
        return [
            {
                "key": obj["Key"],
                "size": obj["Size"],
                "last_modified": obj["LastModified"].isoformat(),
            }
            for obj in response.get("Contents", [])
        ]

    def sync_files(self, local_path: str = "", remote_path: str = "") -> Dict[str, Any]:
        synced: List[str] = []
        failed: List[str] = []

        if not local_path or not os.path.isdir(local_path):
            return {"synced": synced, "failed": failed}

        for root, _dirs, files in os.walk(local_path):
            for name in files:
                full_path = os.path.join(root, name)
                relative = os.path.relpath(full_path, local_path)
                try:
                    with open(full_path, "rb") as fh:
                        self.upload_file(relative, fh.read(), remote_path)
                    synced.append(relative)
                except Exception as e:
                    logger.error(f"Failed to sync {relative} to AWS S3: {e}")
                    failed.append(relative)

        return {"synced": synced, "failed": failed}
