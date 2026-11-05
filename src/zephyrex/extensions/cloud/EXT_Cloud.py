# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cloud object storage: upload, download, delete and list files on Amazon
S3 (or an S3-compatible store), Azure Blob Storage, Google Cloud Storage,
Dropbox or Nextcloud.

A stored file lives with one provider, so an upload reports which
provider took it, and every other operation names its provider and runs
only on that provider's instances. A path is relative, ``/``-separated,
and may not climb out of the store's root.

Delegated providers (``AbstractDelegatedCloudProvider``: Google Drive) are
the other kind: a signed-in user's own files, reached with their own access
token, and served to that user on ``/v1/cloud`` (``BLL_Cloud``).
"""

import builtins
from abc import abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

# The largest file one upload carries in memory (bytes).
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


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


    """An object store. Every ability takes the rotated instance and a
    normalised path (see ``object_path``)."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[set[str]] = {
        "upload_file",
        "download_file",
        "delete_file",
        "list_files",
    }
    _env: ClassVar[dict[str, Any]] = {}

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    @abstractmethod
    async def upload(
        cls, instance: ProviderInstanceModel, path: str, content: bytes
    ) -> dict[str, Any]:
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
    ) -> list[dict[str, Any]]:
        """``folder``'s entries: each a ``name``, ``path``, ``size``,
        ``modified`` and ``is_folder``."""

    @classmethod
    def services(cls) -> builtins.list[str]:
        return ["cloud_storage"]


class CloudFile(BaseModel):
    """A file in a user's own store, as the routes list it."""

    path: str = Field(..., description="The file's id or path at its provider")
    name: str
    mime_type: str | None = None
    modified_at: str | None = Field(None, description="ISO 8601")
    size: int | None = None
    starred: bool = False
    is_folder: bool = False


class CloudFileContent(CloudFile):
    content: str = Field(..., description="The file as text (a document, exported)")


    """A user's **own** files at a service they signed in to (Google Drive, …),
    reached with their access token from ``oauth_consumer``: never a
    deployment-wide credential, and never another user's files.

    Unlike an ``AbstractCloudProvider`` (the deployment's object store,
    rotated across instances), a delegated provider acts for one requester,
    so it is what the ``/v1/cloud`` routes serve. A subclass registers itself
    by ``name`` and names the identity provider (``oauth_consumer``'s public
    name) and the scope its calls need.
    """

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    identity_provider: ClassVar[str] = ""
    required_scope: ClassVar[str] = ""
    registry: ClassVar[dict[str, type["AbstractDelegatedCloudProvider"]]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.name:
            AbstractDelegatedCloudProvider.registry[cls.name] = cls

    @classmethod
    @abstractmethod
    async def list(
        cls, access_token: str, folder: str, search: str | None
    ) -> list[CloudFile]:
        """The readable files in ``folder`` ("" for everywhere), newest first,
        whose name contains ``search`` when it is given."""

    @classmethod
    @abstractmethod
    async def read(cls, access_token: str, path: str) -> CloudFileContent:
        """The file at ``path``, as text."""


    name: ClassVar[str] = "cloud"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Cloud object storage on S3, Azure Blob, Google Cloud Storage, Dropbox "
        "and Nextcloud"
    )

    _env: ClassVar[dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="oauth_consumer",
                friendly_name="OAuth sign-in",
                optional=True,
                reason="The user's own access token for a delegated provider (Google Drive)",
            )
        ]
    )
    _abilities: ClassVar[set[str]] = AbstractCloudProvider._abilities

    @classmethod
    def get_required_permissions(cls) -> list[str]:
        return ["cloud:read", "cloud:write"]

    @classmethod
    @ability("upload_file")
    async def upload_file(
        cls, path: str, content: bytes, provider: str | None = None
    ) -> dict[str, Any]:
        """Store ``content`` at ``path``: on ``provider``, else on the first
        healthy provider of the rotation. The answer names the provider."""
        if len(content) > MAX_UPLOAD_BYTES:
            raise InvalidInputExternalError(
                f"A file may be at most {MAX_UPLOAD_BYTES} bytes"
            )
        normalised = object_path(path)
        stored: dict[str, Any]
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
    async def delete_file(cls, provider: str, path: str) -> dict[str, Any]:
        normalised = object_path(path)
        await cls.rotate_provider_for(provider, "delete", normalised)
        return {"deleted": True, "path": normalised, "provider": provider}

    @classmethod
    @ability("list_files")
    async def list_files(cls, provider: str, folder: str = "") -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = await cls.rotate_provider_for(
            provider, "list", object_path(folder, folder=True)
        )
        return found
