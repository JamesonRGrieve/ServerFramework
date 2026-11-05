# SPDX-License-Identifier: AGPL-3.0-or-later
"""A directory on the server's own disk, as an object store: for a
self-hosted deployment with no object storage, and for development.

The instance's ``directory`` setting (else ``CLOUD_LOCAL_DIRECTORY``) is
the store's root; it is created on first write. Every path is normalised by
``object_path`` and then must still resolve inside the root, so neither
``..`` nor a symlink planted in the store can reach outside it.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.cloud.EXT_Cloud import AbstractCloudProvider
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


    name: ClassVar[str] = "local_disk"
    friendly_name: ClassVar[str] = "Local disk"
    description: ClassVar[str] = "A directory on the server's own disk"
    _env: ClassVar[dict[str, Any]] = {"CLOUD_LOCAL_DIRECTORY": ""}
    instance_settings: ClassVar[tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "directory",
            "The store's root directory on the server",
            env="CLOUD_LOCAL_DIRECTORY",
        ),
    )

    @classmethod
    def _root(cls, instance: ProviderInstanceModel) -> Path:
        directory = cls.setting(instance, "directory")
        if not directory:
            raise TransientExternalError(
                "Local disk directory not configured", provider=cls.name
            )
        return Path(str(directory)).resolve()

    @classmethod
    def _inside(cls, instance: ProviderInstanceModel, path: str) -> Path:
        """``path`` under the root, refused if it resolves anywhere else."""
        root = cls._root(instance)
        target = (root / path).resolve()
        if target != root and root not in target.parents:
            raise InvalidInputExternalError(
                f"{path!r} is outside the store", provider=cls.name
            )
        return target

    @classmethod
    async def upload(
        cls, instance: ProviderInstanceModel, path: str, content: bytes
    ) -> dict[str, Any]:
        target = cls._inside(instance, path)

        def write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(f".{target.name}.partial")
            partial.write_bytes(content)
            partial.replace(target)

        await asyncio.to_thread(write)
        return {"path": path, "size": len(content), "provider": cls.name}

    @classmethod
    async def download(cls, instance: ProviderInstanceModel, path: str) -> bytes:
        target = cls._inside(instance, path)
        if not target.is_file():
            raise PermanentExternalError(f"{path!r} not found", provider=cls.name)
        return await asyncio.to_thread(target.read_bytes)

    @classmethod
    async def delete(cls, instance: ProviderInstanceModel, path: str) -> None:
        target = cls._inside(instance, path)
        if not target.is_file():
            raise PermanentExternalError(f"{path!r} not found", provider=cls.name)
        await asyncio.to_thread(target.unlink)

    @classmethod
    async def list(
        cls, instance: ProviderInstanceModel, folder: str
    ) -> list[dict[str, Any]]:
        directory = cls._inside(instance, folder) if folder else cls._root(instance)
        if not directory.is_dir():
            return []
        root = cls._root(instance)
        entries = []
        for child in sorted(directory.iterdir()):
            if child.name.endswith(".partial") or child.is_symlink():
                continue
            stat = child.stat()
            entries.append(
                {
                    "name": child.name,
                    "path": child.relative_to(root).as_posix(),
                    "size": stat.st_size if child.is_file() else None,
                    "modified": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                    "is_folder": child.is_dir(),
                }
            )
        return entries
