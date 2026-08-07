"""Polymorphic label/tag attachment BLL.

Replaces the original per-entity join tables (``AgentLabel``,
``PromptLabel``, ``ChainLabel``, …) with a single polymorphic
``LabelLinkModel`` row keyed by ``(target_type, target_id)``. The
deduplication of join tables is documented in the audit and reduces this
extension to two tables instead of seven.

Convenience routes:
* POST /v1/labels/{label_id}/attach/{target_type}/{target_id}
* DELETE /v1/labels/{label_id}/attach/{target_type}/{target_id}

These wrap CRUD on ``LabelLinkModel`` with the polymorphic shape so
clients don't have to construct the link row by hand.

Pattern reference: ``metadata/BLL_Metadata.py`` (parallel polymorphic
key/value attachment) and ``auth_invitations/BLL_Invitations.py``.
"""

from typing import ClassVar, List, Optional, Type

from fastapi import HTTPException
from pydantic import BaseModel, Field

from serverframework.lib.CustomRoute import ExposeIn, custom_route
from serverframework.lib.Environment import env
from serverframework.lib.Pydantic2FastAPI import AuthType, RouterMixin
from serverframework.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)


class LabelModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """A label/tag the system can attach to other entities."""

    Manager: ClassVar[Type["LabelManager"]] = None
    name: str = Field(..., description="Lowercase kebab-case identifier")
    description: Optional[str] = Field(None, description="Optional description")
    color: Optional[str] = Field(None, description="Optional display colour")

    table_comment: ClassVar[str] = "Polymorphic label catalog"

    class Create(BaseModel):
        name: str
        description: Optional[str] = None
        color: Optional[str] = None

    class Update(BaseModel):
        description: Optional[str] = None
        color: Optional[str] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        name: Optional[StringSearchModel] = None


class LabelLinkModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """An attachment of a ``LabelModel`` to another entity."""

    Manager: ClassVar[Type["LabelLinkManager"]] = None
    label_id: str = Field(..., description="Reference to LabelModel")
    target_type: str = Field(
        ..., description="Logical type name of the labelled entity"
    )
    target_id: str = Field(..., description="ID of the labelled entity")

    table_comment: ClassVar[str] = (
        "Polymorphic label attachment; dedupes the legacy AgentLabel/"
        "PromptLabel/ChainLabel/... join tables"
    )

    class Create(BaseModel):
        label_id: str
        target_type: str
        target_id: str

    class Update(BaseModel):
        pass

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        label_id: Optional[StringSearchModel] = None
        target_type: Optional[StringSearchModel] = None
        target_id: Optional[StringSearchModel] = None


class LabelAttachRequest(BaseModel):
    """Body shape for label-attach is empty; everything comes from the path."""

    pass


class LabelAttachResponse(BaseModel):
    label_id: str
    target_type: str
    target_id: str
    link_id: str
    attached: bool = True


class LabelDetachResponse(BaseModel):
    label_id: str
    target_type: str
    target_id: str
    detached: bool


class LabelManager(AbstractBLLManager, RouterMixin):
    _model = LabelModel
    prefix: ClassVar[Optional[str]] = "/v1/labels"
    tags: ClassVar[Optional[List[str]]] = ["Labels"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def _attach(self, label_id: str, target_type: str, target_id: str) -> str:
        """Idempotently link ``label_id`` to ``(target_type, target_id)``."""
        LinkDB = LabelLinkModel.DB(self.model_registry.DB.manager.Base)
        existing = (
            LinkDB.list(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                filters=[
                    LinkDB.label_id == label_id,
                    LinkDB.target_type == target_type,
                    LinkDB.target_id == target_id,
                ],
                return_type="dto",
                override_dto=LabelLinkModel,
            )
            or []
        )
        if existing:
            return existing[0].id
        LabelDB = LabelModel.DB(self.model_registry.DB.manager.Base)
        if (
            LabelDB.get(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                id=label_id,
                return_type="dto",
                override_dto=LabelModel,
            )
            is None
        ):
            raise HTTPException(status_code=404, detail="Label not found")
        link = LinkDB.create(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=LabelLinkModel,
            label_id=label_id,
            target_type=target_type,
            target_id=target_id,
        )
        return link.id

    def _detach(self, label_id: str, target_type: str, target_id: str) -> bool:
        LinkDB = LabelLinkModel.DB(self.model_registry.DB.manager.Base)
        existing = (
            LinkDB.list(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                filters=[
                    LinkDB.label_id == label_id,
                    LinkDB.target_type == target_type,
                    LinkDB.target_id == target_id,
                ],
                return_type="dto",
                override_dto=LabelLinkModel,
            )
            or []
        )
        if not existing:
            return False
        for row in existing:
            LinkDB.delete(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                id=row.id,
            )
        return True

    @custom_route(
        method="POST",
        path="/{label_id}/attach/{target_type}/{target_id}",
        input_model=LabelAttachRequest,
        output_model=LabelAttachResponse,
        authentication_type="jwt",
        openapi_tags=("Labels",),
        summary="Attach a label to an entity (polymorphic)",
        expose_in=(ExposeIn.REST,),
    )
    async def attach_route(
        self,
        label_id: str,
        target_type: str,
        target_id: str,
        body: LabelAttachRequest,
    ) -> LabelAttachResponse:
        del body
        link_id = self._attach(label_id, target_type, target_id)
        return LabelAttachResponse(
            label_id=label_id,
            target_type=target_type,
            target_id=target_id,
            link_id=link_id,
        )

    @custom_route(
        method="DELETE",
        path="/{label_id}/attach/{target_type}/{target_id}",
        output_model=LabelDetachResponse,
        authentication_type="jwt",
        openapi_tags=("Labels",),
        summary="Detach a label from an entity (polymorphic)",
        expose_in=(ExposeIn.REST,),
    )
    async def detach_route(
        self, label_id: str, target_type: str, target_id: str
    ) -> LabelDetachResponse:
        detached = self._detach(label_id, target_type, target_id)
        return LabelDetachResponse(
            label_id=label_id,
            target_type=target_type,
            target_id=target_id,
            detached=detached,
        )


class LabelLinkManager(AbstractBLLManager, RouterMixin):
    _model = LabelLinkModel
    prefix: ClassVar[Optional[str]] = "/v1/label-links"
    tags: ClassVar[Optional[List[str]]] = ["Labels"]
    auth_type: ClassVar[AuthType] = AuthType.JWT


LabelModel.Manager = LabelManager
LabelLinkModel.Manager = LabelLinkManager
