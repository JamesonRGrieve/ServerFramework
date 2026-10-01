# SPDX-License-Identifier: AGPL-3.0-or-later
"""Nextcloud, over WebDAV (``/remote.php/dav/files/<user>/``).

The instance's ``base_url`` setting is the Nextcloud address, its
``username`` the account, and its API key an app password (Settings ›
Security › Devices & sessions). A private address must be listed in
``EGRESS_ALLOWED_HOSTS``. The server's XML answers are parsed with
defusedxml, so a hostile server cannot expand entities.
"""

import base64
from typing import Any, ClassVar, Dict, List, Tuple
from urllib.parse import quote, unquote

from defusedxml.ElementTree import fromstring as xml_fromstring

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.cloud.EXT_Cloud import AbstractCloudProvider
from zephyrex.extensions.ExternalErrors import TransientExternalError
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

_DAV = "{DAV:}"
_PROPFIND = (
    '<?xml version="1.0" encoding="utf-8" ?>'
    '<d:propfind xmlns:d="DAV:"><d:prop>'
    "<d:getcontentlength/><d:getlastmodified/><d:resourcetype/>"
    "</d:prop></d:propfind>"
)


class PRV_Nextcloud_Cloud(AbstractCloudProvider):
    name: ClassVar[str] = "nextcloud"
    friendly_name: ClassVar[str] = "Nextcloud"
    description: ClassVar[str] = "A Nextcloud account's files, over WebDAV"
    _env: ClassVar[Dict[str, Any]] = {
        "NEXTCLOUD_URL": "",
        "NEXTCLOUD_USERNAME": "",
        "NEXTCLOUD_APP_PASSWORD": "",
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "base_url",
            "Nextcloud address (https://cloud.example.com)",
            env="NEXTCLOUD_URL",
        ),
        InstanceSetting("username", "Account username", env="NEXTCLOUD_USERNAME"),
        InstanceSetting(
            "api_key",
            "App password",
            env="NEXTCLOUD_APP_PASSWORD",
            secret=True,
            field="api_key",
        ),
    )

    @classmethod
    def _account(cls, instance: ProviderInstanceModel) -> Tuple[str, Dict[str, str]]:
        """``(the account's WebDAV root, auth headers)``."""
        base, user = cls.setting(instance, "base_url"), cls.setting(
            instance, "username"
        )
        password = cls.setting(instance, "api_key")
        if not (base and user and password):
            raise TransientExternalError(
                "Nextcloud base_url, username and app password not configured",
                provider=cls.name,
            )
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        return (
            f"{base.rstrip('/')}/remote.php/dav/files/{quote(user, safe='')}",
            {"Authorization": f"Basic {token}"},
        )

    @staticmethod
    def _quoted(path: str) -> str:
        return "/".join(quote(part, safe="") for part in path.split("/") if part)

    @classmethod
    async def upload(
        cls, instance: ProviderInstanceModel, path: str, content: bytes
    ) -> Dict[str, Any]:
        root, headers = cls._account(instance)
        await cls.http().request(
            "PUT",
            f"{root}/{cls._quoted(path)}",
            content=content,
            headers=headers,
            raw=True,
        )
        return {"path": path, "size": len(content), "provider": cls.name}

    @classmethod
    async def download(cls, instance: ProviderInstanceModel, path: str) -> bytes:
        root, headers = cls._account(instance)
        response = await cls.http().get(
            f"{root}/{cls._quoted(path)}", headers=headers, raw=True
        )
        content: bytes = response.content
        return content

    @classmethod
    async def delete(cls, instance: ProviderInstanceModel, path: str) -> None:
        root, headers = cls._account(instance)
        await cls.http().delete(
            f"{root}/{cls._quoted(path)}", headers=headers, raw=True
        )

    @classmethod
    async def list(
        cls, instance: ProviderInstanceModel, folder: str
    ) -> List[Dict[str, Any]]:
        root, headers = cls._account(instance)
        url = f"{root}/{cls._quoted(folder)}/" if folder else f"{root}/"
        response = await cls.http().request(
            "PROPFIND",
            url,
            content=_PROPFIND.encode(),
            headers={**headers, "Depth": "1", "Content-Type": "application/xml"},
            raw=True,
        )
        return cls.entries(response.content, folder)

    @classmethod
    def entries(cls, body: bytes, folder: str) -> List[Dict[str, Any]]:
        """``folder``'s children from a PROPFIND (Depth: 1) answer."""
        entries = []
        for item in xml_fromstring(body).findall(f"{_DAV}response"):
            href = unquote((item.findtext(f"{_DAV}href") or "").rstrip("/"))
            relative = href.split("/remote.php/dav/files/", 1)[-1].split("/", 1)
            path = relative[1] if len(relative) > 1 else ""
            if path == folder:
                continue  # the folder itself, not one of its children
            prop = item.find(f"{_DAV}propstat/{_DAV}prop")
            if prop is None:
                continue
            kind = prop.find(f"{_DAV}resourcetype")
            is_folder = kind is not None and kind.find(f"{_DAV}collection") is not None
            size = prop.findtext(f"{_DAV}getcontentlength")
            entries.append(
                {
                    "name": path.rsplit("/", 1)[-1],
                    "path": path,
                    "size": int(size) if size and not is_folder else None,
                    "modified": prop.findtext(f"{_DAV}getlastmodified"),
                    "is_folder": is_folder,
                }
            )
        return entries
