# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dropbox, through the Dropbox SDK with an access token (the instance's
API key). Paths are relative to the app's folder or the account root,
as the token's app type decides.
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


class PRV_Dropbox_Cloud(AbstractCloudProvider):
    name: ClassVar[str] = "dropbox"
    friendly_name: ClassVar[str] = "Dropbox"
    description: ClassVar[str] = "A Dropbox account or app folder"
    _env: ClassVar[Dict[str, Any]] = {"DROPBOX_ACCESS_TOKEN": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Dropbox access token",
            env="DROPBOX_ACCESS_TOKEN",
            secret=True,
            field="api_key",
        ),
    )
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="dropbox",
                friendly_name="Dropbox SDK",
                semver=">=11.36.0",
                reason="Dropbox provider",
            )
        ]
    )

    @classmethod
    def _client(cls, instance: ProviderInstanceModel) -> Any:
        if not importable("dropbox"):
            raise TransientExternalError(
                "dropbox package not installed", provider=cls.name
            )
        token = cls.setting(instance, "api_key")
        if not token:
            raise TransientExternalError(
                "Dropbox access token not configured", provider=cls.name
            )
        import dropbox

        # The rotation retries and fails over, not the SDK.
        return dropbox.Dropbox(token, max_retries_on_error=0)

    @classmethod
    async def _call(cls, call: Callable[[], Any]) -> Any:
        from dropbox.exceptions import ApiError, AuthError, DropboxException

        try:
            return await asyncio.to_thread(call)
        except AuthError as exc:
            raise AuthExternalError(
                "Dropbox refused the token", provider=cls.name
            ) from exc
        except ApiError as exc:
            raise InvalidInputExternalError(
                f"Dropbox: {exc.error}", provider=cls.name, upstream_status=409
            ) from exc
        except DropboxException as exc:
            raise TransientExternalError(
                f"Dropbox: {type(exc).__name__}", provider=cls.name
            ) from exc

    @classmethod
    async def upload(
        cls, instance: ProviderInstanceModel, path: str, content: bytes
    ) -> Dict[str, Any]:
        import dropbox

        client = cls._client(instance)
        await cls._call(
            lambda: client.files_upload(
                content, f"/{path}", mode=dropbox.files.WriteMode.overwrite
            )
        )
        return {"path": path, "size": len(content), "provider": cls.name}

    @classmethod
    async def download(cls, instance: ProviderInstanceModel, path: str) -> bytes:
        client = cls._client(instance)
        _, response = await cls._call(lambda: client.files_download(f"/{path}"))
        content: bytes = response.content
        return content

    @classmethod
    async def delete(cls, instance: ProviderInstanceModel, path: str) -> None:
        client = cls._client(instance)
        await cls._call(lambda: client.files_delete_v2(f"/{path}"))

    @classmethod
    async def list(
        cls, instance: ProviderInstanceModel, folder: str
    ) -> List[Dict[str, Any]]:
        import dropbox

        client = cls._client(instance)
        result = await cls._call(
            lambda: client.files_list_folder(f"/{folder}" if folder else "")
        )
        return [
            {
                "name": entry.name,
                "path": entry.path_display.lstrip("/"),
                "size": getattr(entry, "size", None),
                "modified": (
                    entry.server_modified.isoformat()
                    if getattr(entry, "server_modified", None)
                    else None
                ),
                "is_folder": isinstance(entry, dropbox.files.FolderMetadata),
            }
            for entry in result.entries
        ]
