# SPDX-License-Identifier: AGPL-3.0-or-later
"""Azure Blob Storage: a container of a storage account. The instance's
API key is the account key; its ``account_name`` and ``container``
settings name the account and container.
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

CONNECT_TIMEOUT_SECONDS = 10


class PRV_Azure_Cloud(AbstractCloudProvider):
    name: ClassVar[str] = "azure_blob"
    friendly_name: ClassVar[str] = "Azure Blob Storage"
    description: ClassVar[str] = "A container in an Azure storage account"
    _env: ClassVar[Dict[str, Any]] = {
        "AZURE_STORAGE_ACCOUNT": "",
        "AZURE_STORAGE_KEY": "",
        "AZURE_STORAGE_CONTAINER": "",
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "account_name", "Storage account name", env="AZURE_STORAGE_ACCOUNT"
        ),
        InstanceSetting(
            "api_key",
            "Storage account key",
            env="AZURE_STORAGE_KEY",
            secret=True,
            field="api_key",
        ),
        InstanceSetting("container", "Container name", env="AZURE_STORAGE_CONTAINER"),
    )
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="azure-storage-blob",
                friendly_name="Azure Storage Blob SDK",
                semver=">=12.19.0",
                reason="Azure Blob provider",
            ),
            PIP_Dependency(
                name="azure-core",
                friendly_name="Azure SDK core",
                semver=">=1.29.0",
                reason="Azure client errors",
            ),
        ]
    )

    @classmethod
    def _container(cls, instance: ProviderInstanceModel) -> Any:
        if not importable("azure.storage.blob"):
            raise TransientExternalError(
                "azure-storage-blob package not installed", provider=cls.name
            )
        account, key = cls.setting(instance, "account_name"), cls.setting(
            instance, "api_key"
        )
        container = cls.setting(instance, "container")
        if not (account and key and container):
            raise TransientExternalError(
                "Azure account, key and container not configured", provider=cls.name
            )
        if not account.isalnum():
            raise InvalidInputExternalError(
                "Azure account_name is letters and digits", provider=cls.name
            )
        from azure.storage.blob import BlobServiceClient

        # The rotation retries and fails over; the SDK's own retries would
        # hold a dead account for minutes first.
        service = BlobServiceClient(
            account_url=f"https://{account}.blob.core.windows.net",
            credential=key,
            retry_total=0,
            connection_timeout=CONNECT_TIMEOUT_SECONDS,
        )
        return service.get_container_client(container)

    @classmethod
    async def _call(cls, call: Callable[[], Any]) -> Any:
        from azure.core.exceptions import (
            AzureError,
            ClientAuthenticationError,
            ResourceNotFoundError,
        )

        try:
            return await asyncio.to_thread(call)
        except ClientAuthenticationError as exc:
            raise AuthExternalError(
                "Azure refused the account key", provider=cls.name
            ) from exc
        except ResourceNotFoundError as exc:
            raise InvalidInputExternalError(
                "Azure: no such blob or container",
                provider=cls.name,
                upstream_status=404,
            ) from exc
        except AzureError as exc:
            raise TransientExternalError(
                f"Azure: {type(exc).__name__}", provider=cls.name
            ) from exc

    @classmethod
    async def upload(
        cls, instance: ProviderInstanceModel, path: str, content: bytes
    ) -> Dict[str, Any]:
        container = cls._container(instance)
        await cls._call(lambda: container.upload_blob(path, content, overwrite=True))
        return {"path": path, "size": len(content), "provider": cls.name}

    @classmethod
    async def download(cls, instance: ProviderInstanceModel, path: str) -> bytes:
        container = cls._container(instance)
        content: bytes = await cls._call(
            lambda: container.download_blob(path).readall()
        )
        return content

    @classmethod
    async def delete(cls, instance: ProviderInstanceModel, path: str) -> None:
        container = cls._container(instance)
        await cls._call(lambda: container.delete_blob(path))

    @classmethod
    async def list(
        cls, instance: ProviderInstanceModel, folder: str
    ) -> List[Dict[str, Any]]:
        container = cls._container(instance)
        prefix = f"{folder}/" if folder else ""
        items = await cls._call(
            lambda: list(container.walk_blobs(name_starts_with=prefix, delimiter="/"))
        )
        entries = []
        for item in items:
            is_folder = item.name.endswith("/")
            entries.append(
                {
                    "name": item.name[len(prefix) :].rstrip("/"),
                    "path": item.name.rstrip("/"),
                    "size": None if is_folder else getattr(item, "size", None),
                    "modified": (
                        item.last_modified.isoformat()
                        if not is_folder and getattr(item, "last_modified", None)
                        else None
                    ),
                    "is_folder": is_folder,
                }
            )
        return entries
