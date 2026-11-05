# SPDX-License-Identifier: AGPL-3.0-or-later
"""Google Drive, as the signed-in user: their Google Docs and text files.

A delegated provider (``AbstractDelegatedCloudProvider``): every call uses
the requester's own Google access token, which ``oauth_consumer`` holds and
refreshes. The deployment's Google sign-in must ask for Drive read access:
the ``oauth_google`` instance's scopes include
``https://www.googleapis.com/auth/drive.readonly``, and ``offline`` is
``true`` so the token can be refreshed. A Google Doc reads as Markdown
(Drive's own export); a plain-text or Markdown file reads as itself.
"""

import re
from typing import Any, ClassVar

from zephyrex.extensions.cloud.EXT_Cloud import (
    AbstractDelegatedCloudProvider,
    CloudFile,
    CloudFileContent,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.ProviderHTTPClient import ClientPolicy, ProviderHTTPClient

DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
GOOGLE_DOC_MIME_TYPE = "application/vnd.google-apps.document"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
# Files read as they are; a Google Doc is exported to Markdown.
TEXT_MIME_TYPES = frozenset({"text/plain", "text/markdown"})
READABLE_MIME_TYPES = TEXT_MIME_TYPES | {GOOGLE_DOC_MIME_TYPE}
EXPORT_MIME_TYPE = "text/markdown"
FILE_FIELDS = "id,name,mimeType,modifiedTime,starred,size"
LIST_FIELDS = f"nextPageToken,files({FILE_FIELDS})"
# One page of a listing; Drive allows up to 1000.
PAGE_SIZE = 200
# Drive file ids are URL-safe base64-ish names.
FILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
HTTP_TIMEOUT_SECONDS = 30.0


def _quoted(value: str) -> str:
    """``value`` as a Drive query string literal."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def file_query(folder: str, search: str | None) -> str:
    """The Drive ``q`` for readable files (and folders) in ``folder``."""
    kinds = " or ".join(
        f"mimeType = {_quoted(kind)}"
        for kind in sorted(READABLE_MIME_TYPES | {FOLDER_MIME_TYPE})
    )
    clauses = [f"({kinds})", "trashed = false"]
    if folder:
        clauses.append(f"{_quoted(_file_id(folder))} in parents")
    if search:
        clauses.append(f"name contains {_quoted(search)}")
    return " and ".join(clauses)


def _file_id(path: str) -> str:
    if not FILE_ID_PATTERN.match(path):
        raise InvalidInputExternalError(f"{path!r} is not a Google Drive file id")
    return path


def cloud_file(item: dict[str, Any]) -> CloudFile:
    """One Drive ``files`` entry as a ``CloudFile``."""
    mime_type = item.get("mimeType")
    size = item.get("size")
    return CloudFile(
        path=str(item["id"]),
        name=str(item.get("name") or ""),
        mime_type=mime_type,
        modified_at=item.get("modifiedTime"),
        size=int(size) if size is not None else None,
        starred=bool(item.get("starred", False)),
        is_folder=mime_type == FOLDER_MIME_TYPE,
    )


class PRV_GoogleDrive(AbstractDelegatedCloudProvider):
    name: ClassVar[str] = "google_drive"
    friendly_name: ClassVar[str] = "Google Drive"
    identity_provider: ClassVar[str] = "google"
    required_scope: ClassVar[str] = DRIVE_READONLY_SCOPE

    @classmethod
    def _http(cls) -> ProviderHTTPClient:
        return ProviderHTTPClient(
            policy=ClientPolicy(timeout=HTTP_TIMEOUT_SECONDS), provider_name=cls.name
        )

    @staticmethod
    def _auth(access_token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {access_token}"}

    @classmethod
    async def list(
        cls, access_token: str, folder: str, search: str | None
    ) -> list[CloudFile]:
        found: list[CloudFile] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "q": file_query(folder, search),
                "fields": LIST_FIELDS,
                "orderBy": "folder,modifiedTime desc",
                "pageSize": PAGE_SIZE,
            }
            if page_token:
                params["pageToken"] = page_token
            page = await cls._http().get(
                DRIVE_FILES_URL, params=params, headers=cls._auth(access_token)
            )
            found.extend(cloud_file(item) for item in page.get("files", []))
            page_token = page.get("nextPageToken")
            if not page_token:
                return found

    @classmethod
    async def read(cls, access_token: str, path: str) -> CloudFileContent:
        file_url = f"{DRIVE_FILES_URL}/{_file_id(path)}"
        headers = cls._auth(access_token)
        described = cloud_file(
            await cls._http().get(
                file_url, params={"fields": FILE_FIELDS}, headers=headers
            )
        )
        if described.mime_type == GOOGLE_DOC_MIME_TYPE:
            response = await cls._http().get(
                f"{file_url}/export",
                params={"mimeType": EXPORT_MIME_TYPE},
                headers=headers,
                raw=True,
            )
        elif described.mime_type in TEXT_MIME_TYPES:
            response = await cls._http().get(
                file_url, params={"alt": "media"}, headers=headers, raw=True
            )
        else:
            raise InvalidInputExternalError(
                f"{described.name!r} is not a Google Doc or a text file"
            )
        return CloudFileContent(
            **described.model_dump(), content=response.content.decode("utf-8")
        )
