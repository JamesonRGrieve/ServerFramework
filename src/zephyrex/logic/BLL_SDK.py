# SPDX-License-Identifier: AGPL-3.0-or-later
"""Download the client SDKs this server generated.

Each loaded ``meta_sdk_<language>`` extension writes its SDK into the
directory its ``SDK_<LANG>_OUTPUT_DIR`` names when the registry commits. A
language is offered here once that directory holds generated files, as
``zephyrex-sdk-<language>.zip``. The archive is built deterministically
(sorted entries, fixed timestamps), so its SHA-256 changes only when the
SDK does. Symbolic links are never followed into the archive.
"""

import hashlib
import io
import zipfile
from pathlib import Path
from typing import ClassVar, List, Optional

from fastapi import HTTPException, Response
from pydantic import BaseModel

from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.Hooks import SDKTarget, sdk_targets_for
from zephyrex.logic.AbstractLogicManager import AbstractBLLManager
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin

# The earliest timestamp a zip entry can carry; every entry gets it.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
_ZIP_FILE_MODE = 0o644 << 16


class SDKArtifact(BaseModel):
    language: str
    extension: str
    version: str
    filename: str
    size: int
    sha256: str


class SDKList(BaseModel):
    sdks: List[SDKArtifact]


def sdk_archive(directory: Path) -> Optional[bytes]:
    """The zip of every regular file under ``directory``, or None when there
    is nothing generated there."""
    if not directory.is_dir():
        return None
    files = sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    if not files:
        return None
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in files:
            entry = zipfile.ZipInfo(
                path.relative_to(directory).as_posix(), date_time=_ZIP_EPOCH
            )
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = _ZIP_FILE_MODE
            archive.writestr(entry, path.read_bytes())
    return buffer.getvalue()


def archive_filename(language: str) -> str:
    return f"zephyrex-sdk-{language}.zip"


class SDKManager(AbstractBLLManager, RouterMixin):
    """Custom routes only: the SDKs live on disk, not in a table."""

    prefix: ClassVar[Optional[str]] = "/v1/sdk"
    tags: ClassVar[Optional[List[str]]] = ["SDK"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List]] = []

    def _targets(self) -> dict[str, SDKTarget]:
        return sdk_targets_for(self.model_registry.loaded_extension_names())

    @staticmethod
    def _archive(target: SDKTarget) -> Optional[bytes]:
        directory = env(target.output_dir_env)
        return sdk_archive(Path(directory)) if directory else None

    @custom_route(
        method="GET",
        path="",
        output_model=SDKList,
        authentication_type="jwt",
        openapi_tags=("SDK",),
        summary="The generated client SDKs available for download",
    )
    def list_route(self) -> SDKList:
        from zephyrex import get_framework_version

        version = get_framework_version()
        sdks = []
        for extension, target in sorted(self._targets().items()):
            archive = self._archive(target)
            if archive is None:
                continue
            sdks.append(
                SDKArtifact(
                    language=target.language,
                    extension=extension,
                    version=version,
                    filename=archive_filename(target.language),
                    size=len(archive),
                    sha256=hashlib.sha256(archive).hexdigest(),
                )
            )
        return SDKList(sdks=sdks)

    @custom_route(
        method="GET",
        path="/{language}/download",
        authentication_type="jwt",
        expose_in=(ExposeIn.REST,),
        response_class=Response,
        openapi_tags=("SDK",),
        summary="Download one generated client SDK as a zip",
    )
    def download_route(self, language: str) -> Response:
        target = next(
            (t for t in self._targets().values() if t.language == language), None
        )
        archive = self._archive(target) if target is not None else None
        if archive is None:
            raise HTTPException(status_code=404, detail="No SDK for that language")
        return Response(
            content=archive,
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{archive_filename(language)}"'
                )
            },
        )
