# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cloud object storage: upload, download, delete and list files on Amazon
S3 (or an S3-compatible store), Azure Blob Storage, Google Cloud Storage,
Dropbox or Nextcloud.

A stored file lives with one provider, so an upload reports which
provider took it, and every other operation names its provider and runs
only on that provider's instances. A path is relative, ``/``-separated,
and may not climb out of the store's root.
"""

from abc import abstractmethod
from typing import Any, ClassVar, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

# The largest file one upload carries in memory (bytes).
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


def object_path(path: str, *, folder: bool = False) -> str:
    """``path`` normalised to ``a/b/c``: relative, without empty, ``.`` or
    ``..`` parts. Refused when it would climb out of the store's root, or
    when it names nothing and a file is meant."""
    parts = [
        part for part in path.replace("\\", "/").split("/") if part not in ("", ".")
    ]
    if ".." in parts:
        raise InvalidInputExternalError(f"{path!r} climbs out of the store's root")
    if not parts and not folder:
        raise InvalidInputExternalError("A file path is required")
    return "/".join(parts)


class AbstractCloudProvider(AbstractStaticProvider):
    """An object store. Every ability takes the rotated instance and a
    normalised path (see ``object_path``)."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[Set[str]] = {
        "upload_file",
        "download_file",
        "delete_file",
        "list_files",
    }
    _env: ClassVar[Dict[str, Any]] = {}

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    @abstractmethod
    async def upload(
        cls, instance: ProviderInstanceModel, path: str, content: bytes
    ) -> Dict[str, Any]:
        """Store ``content`` at ``path``, replacing what is there."""

    @classmethod
    @abstractmethod
    async def download(cls, instance: ProviderInstanceModel, path: str) -> bytes:
        """The content stored at ``path``."""

    @classmethod
    @abstractmethod
    async def delete(cls, instance: ProviderInstanceModel, path: str) -> None:
        """Remove ``path``."""

    @classmethod
    @abstractmethod
    async def list(
        cls, instance: ProviderInstanceModel, folder: str
    ) -> List[Dict[str, Any]]:
        """``folder``'s entries: each a ``name``, ``path``, ``size``,
        ``modified`` and ``is_folder``."""

    @classmethod
    def services(cls) -> List[str]:
        return ["cloud_storage"]


class EXT_Cloud(AbstractStaticExtension):
    name: ClassVar[str] = "cloud"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Cloud object storage on S3, Azure Blob, Google Cloud Storage, Dropbox "
        "and Nextcloud"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = AbstractCloudProvider._abilities

    @classmethod
    def get_required_permissions(cls) -> List[str]:
        return ["cloud:read", "cloud:write"]

    @classmethod
    @ability("upload_file")
    async def upload_file(
        cls, path: str, content: bytes, provider: Optional[str] = None
    ) -> Dict[str, Any]:
        """Store ``content`` at ``path``: on ``provider``, else on the first
        healthy provider of the rotation. The answer names the provider."""
        if len(content) > MAX_UPLOAD_BYTES:
            raise InvalidInputExternalError(
                f"A file may be at most {MAX_UPLOAD_BYTES} bytes"
            )
        normalised = object_path(path)
        stored: Dict[str, Any]
        if provider is None:
            stored = await cls.rotate_provider("upload", normalised, content)
        else:
            stored = await cls.rotate_provider_for(
                provider, "upload", normalised, content
            )
        return stored

    @classmethod
    @ability("download_file")
    async def download_file(cls, provider: str, path: str) -> bytes:
        content: bytes = await cls.rotate_provider_for(
            provider, "download", object_path(path)
        )
        return content

    @classmethod
    @ability("delete_file")
    async def delete_file(cls, provider: str, path: str) -> Dict[str, Any]:
        normalised = object_path(path)
        await cls.rotate_provider_for(provider, "delete", normalised)
        return {"deleted": True, "path": normalised, "provider": provider}

    @classmethod
    @ability("list_files")
    async def list_files(cls, provider: str, folder: str = "") -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = await cls.rotate_provider_for(
            provider, "list", object_path(folder, folder=True)
        )
        return found
