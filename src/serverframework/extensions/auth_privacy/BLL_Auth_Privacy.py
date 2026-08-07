"""Data residency BLL for auth_privacy.

Two responsibilities:

* ``DataRegionModel`` — catalog of regions a tenant may pin data to.
* ``DataResidencyPreferenceModel`` — per-user OR per-team binding from a
  data type ("user_data", "files", "analytics") to a region.

The original "auth_privacy" extension also drafted GDPR consent /
data-deletion / anonymization workflows in EXT_Auth_Privacy.py; those
overlap with the ``privacy`` extension's PII handling and the
``audit_retention`` extension's purge pipeline. This BLL keeps only the
non-overlapping residency state. The consent and deletion request work
is tracked in the audit follow-up.

Pattern reference: ``auth_invitations/BLL_Invitations.py``,
``serverframework.extensions.Residency`` (jurisdiction registry).
"""

from typing import ClassVar, List, Optional, Type

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from serverframework.lib.Pydantic2FastAPI import AuthType, RouteType, RouterMixin
from serverframework.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)


class DataRegionModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """A data region a tenant may pin residency to."""

    Manager: ClassVar[Type["DataRegionManager"]] = None  # type: ignore[assignment]
    name: str = Field(..., description="Region identifier, e.g. 'eu-west-1'")
    description: Optional[str] = Field(None, description="Human-readable description")
    is_available: bool = Field(True, description="Whether new data may be placed here")
    compliance_level: Optional[str] = Field(
        None, description="One of 'gdpr', 'hipaa', etc."
    )

    table_comment: ClassVar[str] = (
        "Catalog of data regions tenants may pin residency to"
    )

    class Create(BaseModel):
        name: str
        description: Optional[str] = None
        is_available: bool = True
        compliance_level: Optional[str] = None

    class Update(BaseModel):
        description: Optional[str] = None
        is_available: Optional[bool] = None
        compliance_level: Optional[str] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        name: Optional[StringSearchModel] = None
        compliance_level: Optional[StringSearchModel] = None
        is_available: Optional[bool] = None


class DataResidencyPreferenceModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Per-user OR per-team mapping of a data-type to a region."""

    Manager: ClassVar[Type["DataResidencyPreferenceManager"]] = None  # type: ignore[assignment]
    user_id: Optional[str] = Field(
        None, description="User this preference belongs to (xor team_id)"
    )
    team_id: Optional[str] = Field(
        None, description="Team this preference belongs to (xor user_id)"
    )
    data_type: str = Field(
        ..., description="Data category: 'user_data' | 'files' | 'analytics' | ..."
    )
    region_id: str = Field(..., description="Reference to ``DataRegionModel.id``")

    table_comment: ClassVar[str] = "Per-tenant data-residency pinning"

    class Create(BaseModel):
        data_type: str
        region_id: str
        user_id: Optional[str] = None
        team_id: Optional[str] = None

        @model_validator(mode="after")
        def _user_xor_team(self):
            if (self.user_id is None) == (self.team_id is None):
                raise HTTPException(
                    status_code=422,
                    detail="exactly one of user_id or team_id must be set",
                )
            return self

    class Update(BaseModel):
        region_id: Optional[str] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        user_id: Optional[StringSearchModel] = None
        team_id: Optional[StringSearchModel] = None
        data_type: Optional[StringSearchModel] = None
        region_id: Optional[StringSearchModel] = None


class DataRegionManager(AbstractBLLManager, RouterMixin):
    _model = DataRegionModel
    prefix: ClassVar[Optional[str]] = "/v1/privacy/data-regions"
    tags: ClassVar[Optional[List[str]]] = ["Data Residency"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    # Region catalog is read-only for normal users; ROOT_ID admin
    # endpoints (e.g. an internal control-plane CLI) seed and maintain
    # the catalog. Non-root callers get GET/LIST/SEARCH only.
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
    ]

    def create_validation(self, entity) -> None:
        from serverframework.database.StaticPermissions import is_root_id

        if not is_root_id(self.requester.id):
            raise HTTPException(
                status_code=403,
                detail="Region catalog is administered by ROOT only",
            )


class DataResidencyPreferenceManager(AbstractBLLManager, RouterMixin):
    _model = DataResidencyPreferenceModel
    prefix: ClassVar[Optional[str]] = "/v1/privacy/data-residency"
    tags: ClassVar[Optional[List[str]]] = ["Data Residency"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    # A user may only set their own residency preference. Team-scoped
    # preferences require team membership; the route layer enforces that
    # in ``create_validation`` once team membership is known.
    _CALLER_OWNED_FIELDS: ClassVar[tuple] = ("user_id",)

    def create_validation(self, entity) -> None:
        # Membership check for team-scoped preferences. A user must be a
        # member of the target team; ROOT bypasses.
        from serverframework.database.StaticPermissions import is_root_id

        if entity.team_id is None or is_root_id(self.requester.id):
            return
        try:
            from serverframework.logic.BLL_Auth import UserTeamModel
        except ImportError:
            raise HTTPException(
                status_code=403,
                detail="Team-scoped residency requires team membership",
            )
        from serverframework.lib.Environment import env

        UTDB = UserTeamModel.DB(self.model_registry.DB.manager.Base)
        if not UTDB.exists(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            user_id=self.requester.id,
            team_id=entity.team_id,
        ):
            raise HTTPException(
                status_code=403,
                detail="Caller is not a member of the target team",
            )


DataRegionModel.Manager = DataRegionManager
DataResidencyPreferenceModel.Manager = DataResidencyPreferenceManager
