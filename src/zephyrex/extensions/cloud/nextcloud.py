import os
from typing import Any, Dict, List
from urllib.parse import quote
from xml.etree import ElementTree

from zephyrex.extensions.cloud.PRV_Cloud import AbstractCloudProvider
from zephyrex.lib.Logging import logger

_WEBDAV_NS = "DAV:"
_PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8" ?>'
    '<d:propfind xmlns:d="DAV:">'
    "<d:prop>"
    "<d:displayname/><d:getcontentlength/><d:getlastmodified/><d:resourcetype/>"
    "</d:prop>"
    "</d:propfind>"
)
_REQUEST_TIMEOUT_SECONDS = 30


class NextcloudProvider(AbstractCloudProvider):
    """
    Cloud storage provider backed by a self-hosted Nextcloud instance.
    Requires the optional ``requests`` dependency.

    ``access_key`` holds the Nextcloud username; ``secret_key`` holds an
    app password (Settings > Security > Devices & sessions > App passwords).
    The instance base URL (e.g. ``https://cloud.example.com``) is read from
    the ``base_url`` setting. Files are addressed via WebDAV
    (``/remote.php/dav/files/<user>/``); public share links use the OCS
    Share API.
    """

    def get_platform_name(self) -> str:
        return "Nextcloud"

    @property
    def base_url(self) -> str:
        return str(self.settings.get("base_url", "")).rstrip("/")

    def _session(self) -> Any:
        import requests

        session = requests.Session()
        session.auth = (self.access_key, self.secret_key)
        return session

    @staticmethod
    def _build_path(name: str, folder_path: str) -> str:
        folder = folder_path.strip("/")
        return f"{folder}/{name}" if folder else name

    @staticmethod
    def _quote_path(path: str) -> str:
        return "/".join(quote(part) for part in path.split("/") if part)

    def _dav_url(self, name: str, folder_path: str) -> str:
        path = self._build_path(name, folder_path)
        return (
            f"{self.base_url}/remote.php/dav/files/{quote(self.access_key)}"
            f"/{self._quote_path(path)}"
        )

    def upload_file(
        self, file_path: str, content: bytes, folder_path: str = ""
    ) -> Dict[str, Any]:
        url = self._dav_url(file_path, folder_path)
        response = self._session().put(
            url, data=content, timeout=_REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return {"success": True, "path": self._build_path(file_path, folder_path)}

    def download_file(self, file_id: str, destination_path: str = "") -> bytes:
        url = self._dav_url(file_id, destination_path)
        response = self._session().get(url, timeout=_REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        content: bytes = response.content
        return content

    def delete_file(self, file_id: str, folder_path: str = "") -> bool:
        url = self._dav_url(file_id, folder_path)
        response = self._session().delete(url, timeout=_REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        return True

    def list_files(self, folder_path: str = "") -> List[Dict[str, Any]]:
        folder = folder_path.strip("/")
        request_path = f"/remote.php/dav/files/{quote(self.access_key)}/"
        if folder:
            request_path += f"{self._quote_path(folder)}/"

        response = self._session().request(
            "PROPFIND",
            f"{self.base_url}{request_path}",
            data=_PROPFIND_BODY,
            headers={"Depth": "1", "Content-Type": "application/xml"},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return self._parse_propfind(response.content, request_path)

    @staticmethod
    def _parse_propfind(body: bytes, request_path: str) -> List[Dict[str, Any]]:
        root = ElementTree.fromstring(body)
        request_path = request_path.rstrip("/")
        files: List[Dict[str, Any]] = []

        for response_el in root.findall(f"{{{_WEBDAV_NS}}}response"):
            href = (response_el.findtext(f"{{{_WEBDAV_NS}}}href") or "").rstrip("/")
            if href == request_path:
                continue  # the queried collection itself, not a child entry

            propstat = response_el.find(f"{{{_WEBDAV_NS}}}propstat")
            prop = (
                propstat.find(f"{{{_WEBDAV_NS}}}prop") if propstat is not None else None
            )
            if prop is None:
                continue

            resourcetype = prop.find(f"{{{_WEBDAV_NS}}}resourcetype")
            is_directory = (
                resourcetype is not None
                and resourcetype.find(f"{{{_WEBDAV_NS}}}collection") is not None
            )
            name = (
                prop.findtext(f"{{{_WEBDAV_NS}}}displayname") or href.rsplit("/", 1)[-1]
            )

            entry: Dict[str, Any] = {
                "name": name,
                "path": href,
                "is_directory": is_directory,
            }
            size_text = prop.findtext(f"{{{_WEBDAV_NS}}}getcontentlength")
            if size_text is not None:
                entry["size"] = int(size_text)
            files.append(entry)

        return files

    def create_share_link(
        self, file_id: str, folder_path: str = "", public: bool = True
    ) -> Dict[str, Any]:
        """
        Create a Nextcloud share via the OCS Share API (public link by
        default). Nextcloud-specific capability, not part of the abstract
        cloud provider contract.
        """
        share_path = f"/{self._build_path(file_id, folder_path)}"
        response = self._session().post(
            f"{self.base_url}/ocs/v2.php/apps/files_sharing/api/v1/shares",
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
            data={"path": share_path, "shareType": 3 if public else 0},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json().get("ocs", {}).get("data", {})
        return {
            "success": True,
            "url": data.get("url", ""),
            "token": data.get("token", ""),
        }

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
                    logger.error(f"Failed to sync {relative} to Nextcloud: {e}")
                    failed.append(relative)

        return {"synced": synced, "failed": failed}
