import os
from typing import Any, Dict, List

from zephyrex.extensions.cloud.PRV_Cloud import AbstractCloudProvider
from zephyrex.lib.Logging import logger


class AzureBlobProvider(AbstractCloudProvider):
    """
    Cloud storage provider backed by Azure Blob Storage.
    Requires the optional ``azure-storage-blob`` dependency.

    ``access_key`` holds the storage account connection string;
    ``bucket_name`` is used as the container name.
    """

    def get_platform_name(self) -> str:
        return "Azure Blob Storage"

    def _container_client(self) -> Any:
        from azure.storage.blob import BlobServiceClient

        service_client = BlobServiceClient.from_connection_string(self.access_key)
        return service_client.get_container_client(self.bucket_name)

    @staticmethod
    def _build_blob_name(name: str, folder_path: str) -> str:
        return f"{folder_path.strip('/')}/{name}" if folder_path else name

    def upload_file(
        self, file_path: str, content: bytes, folder_path: str = ""
    ) -> Dict[str, Any]:
        blob_name = self._build_blob_name(file_path, folder_path)
        self._container_client().upload_blob(
            name=blob_name, data=content, overwrite=True
        )
        return {"success": True, "blob_name": blob_name}

    def download_file(self, file_id: str, destination_path: str = "") -> bytes:
        blob_name = self._build_blob_name(file_id, destination_path)
        downloader = self._container_client().download_blob(blob_name)
        content: bytes = downloader.readall()
        return content

    def delete_file(self, file_id: str, folder_path: str = "") -> bool:
        blob_name = self._build_blob_name(file_id, folder_path)
        self._container_client().delete_blob(blob_name)
        return True

    def list_files(self, folder_path: str = "") -> List[Dict[str, Any]]:
        blobs = self._container_client().list_blobs(
            name_starts_with=folder_path or None
        )
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
                        f"Failed to sync {relative} to Azure Blob Storage: {e}"
                    )
                    failed.append(relative)

        return {"synced": synced, "failed": failed}
