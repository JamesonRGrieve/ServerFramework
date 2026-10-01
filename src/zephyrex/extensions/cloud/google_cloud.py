import os
from typing import Any, Dict, List

from zephyrex.extensions.cloud.PRV_Cloud import AbstractCloudProvider
from zephyrex.lib.Logging import logger


class GoogleCloudProvider(AbstractCloudProvider):
    """
    Cloud storage provider backed by Google Cloud Storage.
    Requires the optional ``google-cloud-storage`` dependency.

    ``access_key`` holds the path to a service account JSON key file. When
    empty, the client falls back to Application Default Credentials.
    """

    def get_platform_name(self) -> str:
        return "Google Cloud Storage"

    def _client(self) -> Any:
        from google.cloud import storage

        if self.access_key:
            return storage.Client.from_service_account_json(self.access_key)
        return storage.Client()

    def _bucket(self) -> Any:
        return self._client().bucket(self.bucket_name)

    @staticmethod
    def _build_blob_name(name: str, folder_path: str) -> str:
        return f"{folder_path.strip('/')}/{name}" if folder_path else name

    def upload_file(
        self, file_path: str, content: bytes, folder_path: str = ""
    ) -> Dict[str, Any]:
        blob_name = self._build_blob_name(file_path, folder_path)
        blob = self._bucket().blob(blob_name)
        blob.upload_from_string(content)
        return {"success": True, "blob_name": blob_name}

    def download_file(self, file_id: str, destination_path: str = "") -> bytes:
        blob_name = self._build_blob_name(file_id, destination_path)
        blob = self._bucket().blob(blob_name)
        content: bytes = blob.download_as_bytes()
        return content

    def delete_file(self, file_id: str, folder_path: str = "") -> bool:
        blob_name = self._build_blob_name(file_id, folder_path)
        self._bucket().blob(blob_name).delete()
        return True

    def list_files(self, folder_path: str = "") -> List[Dict[str, Any]]:
        blobs = self._client().list_blobs(self.bucket_name, prefix=folder_path or None)
        return [{"name": blob.name, "size": blob.size} for blob in blobs]

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
                    logger.error(
                        f"Failed to sync {relative} to Google Cloud Storage: {e}"
                    )
                    failed.append(relative)

        return {"synced": synced, "failed": failed}
