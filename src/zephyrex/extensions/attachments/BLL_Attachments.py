# SPDX-License-Identifier: AGPL-3.0-or-later
"""Attachments: files stored in the framework's cloud storage and attached to any record.

- ``/v1/asset``: an uploaded file (a picture, a document). It is made only by
  ``POST /v1/asset/upload`` (the raw bytes, its ``Content-Type`` and ``?name=``), stored through
  the ``cloud`` extension's provider rotation, and read back by
  ``GET /v1/asset/{asset_id}/content``. Its uploader reads and deletes it; anyone who can view a
  record it is attached to can read its content too, so a picture shows wherever its record does.
- ``/v1/attachment``: an asset attached to a record by ``target_entity_type`` (the snake-case
  name of the record's model, e.g. ``location`` for ``LocationModel``), ``target_entity_id`` and a
  ``role`` (``primary_image``, ``header_image``, ``document``, ...). Attaching needs one's own
  asset and EDIT on the record. ``GET /v1/attachment/of`` lists a record's attachments to anyone
  who can view the record. The attacher or an editor of the record removes one.

Records are found through the app's own managers, never another extension's internals, and each
check is the record manager's own (``get`` for VIEW, the permission layer's EDIT check).
"""

import hashlib
import re
import uuid
from typing import Any, ClassVar, Dict, List, Optional

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel as RouteModel
from pydantic import Field

from zephyrex.lib.ContentNegotiation import skip_negotiation
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.RecordTarget import can_edit as _can_edit
from zephyrex.lib.RecordTarget import can_view as _can_view
from zephyrex.lib.RequestBody import capped_body
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.AbstractLogicManager.ownership import server_side
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin, RouteType
from zephyrex.pydantic2.registry import BaseModel

# The largest file accepted: a picture or a short document.
MAX_ASSET_BYTES = 10 * 1024 * 1024
# What may be uploaded: pictures and PDFs. Anything else is refused (415).
ALLOWED_CONTENT_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif", "application/pdf"}
)
MAX_NAME = 200
ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

# A file goes up as its own bytes and comes back as them: content negotiation (JSON, YAML, ...)
# would refuse an image body (415) and a browser's `Accept: image/*` (406).
skip_negotiation(r"/v1/asset/upload")
skip_negotiation(r"/v1/asset/[^/]+/content")


class AssetModel(ApplicationModel, UpdateMixinModel, metaclass=ModelMeta):
    name: str = Field(..., description="The file's name")
    content_type: str = Field(..., description="Its media type")
    size: int = Field(..., description="Its length in bytes")
    sha256: str = Field(..., description="The SHA-256 of its bytes, in hex")
    provider: str = Field(..., description="The cloud provider holding it")
    path: str = Field(..., description="Its path in that provider's store")

    table_comment: ClassVar[str] = "Uploaded files, stored in cloud storage"

    class Create(BaseModel):
        # Set by the server: an asset is made only by the upload route.
        name: str
        content_type: str
        size: int
        sha256: str
        provider: str
        path: str

    class Update(BaseModel):
        pass

    class Search(ApplicationModel.Search):
        name: Optional[StringSearchModel] = None


class AssetUploaded(RouteModel):
    asset: AssetModel


class AssetManager(AbstractBLLManager, RouterMixin):
    _model = AssetModel

    prefix: ClassVar[Optional[str]] = "/v1/asset"
    tags: ClassVar[Optional[List[str]]] = ["Attachments"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.DELETE,
    ]

    @custom_route(
        method="POST",
        path="/upload",
        output_model=AssetUploaded,
        authentication_type="jwt",
        raw_body=True,
        max_body_bytes=MAX_ASSET_BYTES,
        status_code=201,
        openapi_tags=("Attachments",),
        summary="Upload a file (its raw bytes, its Content-Type, and ?name=)",
        expose_in=(ExposeIn.REST,),
    )
    async def upload_route(self, request: Request, name: str = "") -> AssetUploaded:
        content_type = (
            request.headers.get("content-type", "").split(";")[0].strip().lower()
        )
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise HTTPException(status_code=415, detail="Upload a picture or a PDF")
        cleaned = (name or "").strip()
        if not cleaned or len(cleaned) > MAX_NAME:
            raise HTTPException(
                status_code=422,
                detail=f"A name of 1 to {MAX_NAME} characters is required",
            )
        content = await capped_body(
            request, MAX_ASSET_BYTES, f"A file may be at most {MAX_ASSET_BYTES} bytes"
        )
        if not content:
            raise HTTPException(status_code=422, detail="The file is empty")
        from zephyrex.extensions.cloud.EXT_Cloud import EXT_Cloud

        path = f"attachments/{self.requester.id}/{uuid.uuid4().hex}"
        stored = await EXT_Cloud.upload_file(path=path, content=content)
        asset = self.create(
            name=cleaned,
            content_type=content_type,
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            provider=str(stored["provider"]),
            path=str(stored.get("path", path)),
        )
        return AssetUploaded(asset=asset)

    def readable(self, asset_id: str) -> AssetModel:
        """The asset, if the requester uploaded it or can view a record it is attached to;
        otherwise the same 404 as for one that doesn't exist."""
        root = AssetManager(
            requester_id=env("SYSTEM_ID"), model_registry=self.model_registry
        )
        try:
            asset: AssetModel = self.get(id=asset_id)
            return asset
        except HTTPException as error:
            if error.status_code not in (403, 404):
                raise
        links = AttachmentManager(
            requester_id=env("SYSTEM_ID"), model_registry=self.model_registry
        ).list(asset_id=asset_id)
        for link in links:
            if _can_view(
                self.model_registry,
                str(self.requester.id),
                link.target_entity_type,
                link.target_entity_id,
            ):
                found: AssetModel = root.get(id=asset_id)
                return found
        raise HTTPException(status_code=404, detail="Asset not found")

    @custom_route(
        method="GET",
        path="/{asset_id}/content",
        authentication_type="jwt",
        response_class=Response,
        openapi_tags=("Attachments",),
        summary="The file's bytes, to its uploader or anyone who can view a record it is attached to",
        expose_in=(ExposeIn.REST,),
    )
    async def content_route(self, asset_id: str) -> Response:
        asset = self.readable(asset_id)
        from zephyrex.extensions.cloud.EXT_Cloud import EXT_Cloud

        content = await EXT_Cloud.download_file(asset.provider, asset.path)
        return Response(
            content=content,
            media_type=asset.content_type,
            headers={
                "Cache-Control": "private, max-age=3600",
                "X-Content-Type-Options": "nosniff",
            },
        )

    def delete(self, id: str) -> None:
        """The uploader deletes an asset; its attachments go with it."""
        asset = self.get(id=id)
        root = AttachmentManager(
            requester_id=env("SYSTEM_ID"), model_registry=self.model_registry
        )
        for link in root.list(asset_id=asset.id):
            root.delete_row(link.id)
        super().delete(id)


class AttachmentModel(
    ApplicationModel, UpdateMixinModel, AssetModel.Reference, metaclass=ModelMeta
):
    target_entity_type: str = Field(
        ..., description="The record's type: its model's snake-case name"
    )
    target_entity_id: str = Field(..., description="The record's id")
    role: str = Field(
        ...,
        description="What the asset is to the record (primary_image, document, ...)",
    )

    table_comment: ClassVar[str] = "Assets attached to records by type and id"

    class Create(BaseModel, AssetModel.Reference.ID):
        target_entity_type: str
        target_entity_id: str
        role: str = "primary_image"

    class Update(BaseModel):
        pass

    class Search(ApplicationModel.Search, AssetModel.Reference.ID.Search):
        target_entity_type: Optional[StringSearchModel] = None
        target_entity_id: Optional[StringSearchModel] = None
        role: Optional[StringSearchModel] = None


def _exists(manager: Any, id: str) -> bool:
    try:
        manager.get(id=id)
    except HTTPException as error:
        if error.status_code == 404:
            return False
        raise
    return True


class AttachmentList(RouteModel):
    attachments: List[AttachmentModel]


class AttachmentManager(AbstractBLLManager, RouterMixin):
    _model = AttachmentModel

    prefix: ClassVar[Optional[str]] = "/v1/attachment"
    tags: ClassVar[Optional[List[str]]] = ["Attachments"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.CREATE,
        RouteType.GET,
        RouteType.DELETE,
    ]

    def create(self, **kwargs: Any) -> Any:
        """One's own asset attached to a record one may edit."""
        if kwargs.get("entities") is not None:
            raise HTTPException(status_code=422, detail="Attach one asset at a time")
        role = str(kwargs.get("role") or "primary_image")
        if not ROLE_PATTERN.match(role):
            raise HTTPException(
                status_code=422, detail="role is a short snake_case name"
            )
        requester = str(self.requester.id)
        # Someone else's asset reads as missing.
        AssetManager(requester_id=requester, model_registry=self.model_registry).get(
            id=kwargs["asset_id"]
        )
        target_type, target_id = (
            kwargs["target_entity_type"],
            kwargs["target_entity_id"],
        )
        if not server_side(requester) and not _can_edit(
            self.model_registry, requester, target_type, target_id
        ):
            # The same 404 whether the record is missing or merely not the requester's to change.
            raise HTTPException(status_code=404, detail="Record not found")
        return super().create(
            target_entity_type=target_type,
            target_entity_id=target_id,
            asset_id=kwargs["asset_id"],
            role=role,
        )

    @custom_route(
        method="GET",
        path="/of",
        output_model=AttachmentList,
        authentication_type="jwt",
        openapi_tags=("Attachments",),
        summary="A record's attachments, to anyone who can view the record (optionally one role)",
        expose_in=(ExposeIn.REST,),
    )
    def of_route(
        self, target_entity_type: str, target_entity_id: str, role: str = ""
    ) -> AttachmentList:
        if not _can_view(
            self.model_registry,
            str(self.requester.id),
            target_entity_type,
            target_entity_id,
        ):
            raise HTTPException(status_code=404, detail="Record not found")
        filters: Dict[str, Any] = {
            "target_entity_type": target_entity_type,
            "target_entity_id": target_entity_id,
        }
        if role:
            filters["role"] = role
        rows = AttachmentManager(
            requester_id=env("SYSTEM_ID"), model_registry=self.model_registry
        ).list(**filters)
        # Only rows whose file is still there: a deleted asset's attachments never show.
        assets = AssetManager(
            requester_id=env("SYSTEM_ID"), model_registry=self.model_registry
        )
        rows = [row for row in rows if _exists(assets, row.asset_id)]
        return AttachmentList(
            attachments=sorted(rows, key=lambda row: str(row.created_at))
        )

    def delete(self, id: str) -> None:
        """The attacher, or anyone who may edit the record, removes an attachment."""
        root = AttachmentManager(
            requester_id=env("SYSTEM_ID"), model_registry=self.model_registry
        )
        link = root.get(id=id)
        requester = str(self.requester.id)
        mine = str(getattr(link, "created_by_user_id", "")) == requester
        if not (
            server_side(requester)
            or mine
            or _can_edit(
                self.model_registry,
                requester,
                link.target_entity_type,
                link.target_entity_id,
            )
        ):
            raise HTTPException(status_code=404, detail="Attachment not found")
        root.delete_row(id)

    def delete_row(self, id: str) -> None:
        """Delete the attachment row itself, once removal is authorized."""
        super().delete(id)
