"""Polymorphic label/tag attachment BLL.

Replaces the original per-entity join tables (``AgentLabel``,
``PromptLabel``, ``ChainLabel``, …) with a single polymorphic
``LabelLinkModel`` row keyed by ``(target_type, target_id)``. The
deduplication of join tables is documented in the audit and reduces this
extension to two tables instead of seven.

Pattern reference: ``metadata/BLL_Metadata.py`` (parallel polymorphic
key/value attachment) and ``auth_invitations/BLL_Invitations.py``.
"""

from typing import ClassVar, List, Optional, Type

from pydantic import BaseModel, Field

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


class LabelManager(AbstractBLLManager, RouterMixin):
    _model = LabelModel
    prefix: ClassVar[Optional[str]] = "/v1/labels"
    tags: ClassVar[Optional[List[str]]] = ["Labels"]
    auth_type: ClassVar[AuthType] = AuthType.JWT


class LabelLinkManager(AbstractBLLManager, RouterMixin):
    _model = LabelLinkModel
    prefix: ClassVar[Optional[str]] = "/v1/label-links"
    tags: ClassVar[Optional[List[str]]] = ["Labels"]
    auth_type: ClassVar[AuthType] = AuthType.JWT


LabelModel.Manager = LabelManager
LabelLinkModel.Manager = LabelLinkManager
