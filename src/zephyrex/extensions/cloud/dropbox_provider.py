import os
from typing import Any, Dict, List

from zephyrex.extensions.cloud.PRV_Cloud import AbstractCloudProvider
from zephyrex.lib.Logging import logger


class DropboxProvider(AbstractCloudProvider):
    """
    Cloud storage provider backed by Dropbox.
    Requires the optional ``dropbox`` dependency.

    ``access_key`` holds the Dropbox API access token.
    """

    def get_platform_name(self) -> str:
        return "Dropbox"

    def _client(self) -> Any:
        import dropbox

        return dropbox.Dropbox(self.access_key)

    @staticmethod
    def _build_path(name: str, folder_path: str) -> str:
        folder = folder_path.strip("/")
        return f"/{folder}/{name}" if folder else f"/{name}"

    def upload_file(
        self, file_path: str, content: bytes, folder_path: str = ""
    ) -> Dict[str, Any]:
        import dropbox

        path = self._build_path(file_path, folder_path)
        self._client().files_upload(
            content, path, mode=dropbox.files.WriteMode.overwrite
        )
        return {"success": True, "path": path}

    def download_file(self, file_id: str, destination_path: str = "") -> bytes:
        path = self._build_path(file_id, destination_path)
        _metadata, response = self._client().files_download(path)
        content: bytes = response.content
        return content

    def delete_file(self, file_id: str, folder_path: str = "") -> bool:
        path = self._build_path(file_id, folder_path)
        self._client().files_delete_v2(path)
        return True

    def list_files(self, folder_path: str = "") -> List[Dict[str, Any]]:
        path = f"/{folder_path.strip('/')}" if folder_path else ""
        result = self._client().files_list_folder(path)
        return [
            {"name": entry.name, "path": entry.path_display} for entry in result.entries
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
                    logger.error(f"Failed to sync {relative} to Dropbox: {e}")
                    failed.append(relative)

        return {"synced": synced, "failed": failed}
