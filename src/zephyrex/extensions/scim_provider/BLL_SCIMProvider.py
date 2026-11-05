# SPDX-License-Identifier: AGPL-3.0-or-later
"""What each SCIM target holds, and the hooks that keep it current.

``scim_links`` maps a framework user or team to its record on one target
(``scim_target`` provider instance): the remote id, its last ETag, a
digest of what was last pushed, and whether a push is pending.
``scim_sync_logs`` records every remote write. Both are children of their
target (``permission_references``): whoever can see the target sees its
links and log. They are read-only over the API; the sync writes them.

``POST /v1/scim_link/reconcile`` brings a target the caller can see fully
in line with the framework (see ``SCIMSync``).

Hooks flag a push when a user's credentials are first set (a user's
registration writes them), a user is updated or deleted, a team is
created, updated or deleted, or a membership changes. The flag is a
database row written in the request, so nothing is lost if the push
itself fails or the process stops: the push runs on the app's event loop
right after, and pending links are retried on the next push or
reconcile.
"""

from datetime import datetime
from typing import Any, Callable, ClassVar, Dict, Iterable, List, Optional, Type

from pydantic import BaseModel as RouteModel
from pydantic import Field

from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    HookContext,
    HookTiming,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
    hook_bll,
)
from zephyrex.logic.BLL_Auth import (
    TeamManager,
    UserCredentialManager,
    UserManager,
    UserTeamManager,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceManager, ProviderInstanceModel
from zephyrex.pydantic2.fastapi import RouterMixin
from zephyrex.pydantic2.fastapi.types import RouteType
from zephyrex.pydantic2.registry import BaseModel

READ_ONLY = [RouteType.GET, RouteType.LIST, RouteType.SEARCH]
EXTENSION_NAME = "scim_provider"


class ScimLinkModel(
    ApplicationModel,
    UpdateMixinModel,
    ProviderInstanceModel.Reference,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["ScimLinkManager"]]
    resource_type: str = Field(..., description="'User' or 'Group'")
    local_id: str = Field(..., description="The framework user's or team's id")
    remote_id: Optional[str] = Field(None, description="Its id on the target")
    remote_name: Optional[str] = Field(
        None, description="The userName or displayName last pushed"
    )
    remote_etag: Optional[str] = Field(None, description="Its version on the target")
    pushed_hash: Optional[str] = Field(None, description="Digest of what was pushed")
    pending: bool = Field(True, description="Whether a push is due")
    status: str = Field(
        "pending", description="pending, synced, deactivated, deleted or error"
    )
    last_synced_at: Optional[datetime] = Field(None, description="Last push")
    last_error: Optional[str] = Field(None, description="Why the last push failed")

    table_comment: ClassVar[str] = "Framework users and teams as held by SCIM targets"
    permission_references: ClassVar[List[str]] = ["provider_instance"]

    class Create(BaseModel):
        provider_instance_id: str
        resource_type: str
        local_id: str
        remote_id: Optional[str] = None
        remote_name: Optional[str] = None
        remote_etag: Optional[str] = None
        pushed_hash: Optional[str] = None
        pending: bool = True
        status: str = "pending"
        last_synced_at: Optional[datetime] = None
        last_error: Optional[str] = None

    class Update(BaseModel):
        remote_id: Optional[str] = None
        remote_name: Optional[str] = None
        remote_etag: Optional[str] = None
        pushed_hash: Optional[str] = None
        pending: Optional[bool] = None
        status: Optional[str] = None
        last_synced_at: Optional[datetime] = None
        last_error: Optional[str] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        provider_instance_id: Optional[StringSearchModel] = None
        resource_type: Optional[StringSearchModel] = None
        local_id: Optional[StringSearchModel] = None
        remote_id: Optional[StringSearchModel] = None
        status: Optional[StringSearchModel] = None
        pending: Optional[bool] = None


class ScimSyncLogModel(
    ApplicationModel,
    UpdateMixinModel,
    ProviderInstanceModel.Reference,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["ScimSyncLogManager"]]
    resource_type: str = Field(..., description="'User' or 'Group'")
    operation: str = Field(
        ..., description="create, link, patch, replace, deactivate, delete or push"
    )
    local_id: str = Field(..., description="The framework user's or team's id")
    remote_id: Optional[str] = Field(None, description="Its id on the target")
    status: str = Field(..., description="success or error")
    error_detail: Optional[str] = Field(None, description="Why it failed")

    table_comment: ClassVar[str] = "Every write made to a SCIM target"
    permission_references: ClassVar[List[str]] = ["provider_instance"]

    class Create(BaseModel):
        provider_instance_id: str
        resource_type: str
        operation: str
        local_id: str
        remote_id: Optional[str] = None
        status: str
        error_detail: Optional[str] = None

    class Update(BaseModel):
        error_detail: Optional[str] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        provider_instance_id: Optional[StringSearchModel] = None
        resource_type: Optional[StringSearchModel] = None
        operation: Optional[StringSearchModel] = None
        local_id: Optional[StringSearchModel] = None
        status: Optional[StringSearchModel] = None
        created_at: Optional[DateSearchModel] = None


class ReconcileRequest(RouteModel):
    provider_instance_id: str = Field(..., min_length=1)


class SyncSummary(RouteModel):
    target: str
    users: Dict[str, int]
    groups: Dict[str, int]


class ScimSyncLogManager(AbstractBLLManager, RouterMixin):
    _model = ScimSyncLogModel
    routes_to_register: ClassVar[Optional[List[RouteType]]] = READ_ONLY


class ScimLinkManager(AbstractBLLManager, RouterMixin):
    _model = ScimLinkModel
    routes_to_register: ClassVar[Optional[List[RouteType]]] = READ_ONLY

    @custom_route(
        method="POST",
        path="/reconcile",
        input_model=ReconcileRequest,
        output_model=SyncSummary,
        authentication_type="jwt",
        openapi_tags=("SCIM",),
        summary="Bring a SCIM target fully in line with the framework's users and teams",
        expose_in=(ExposeIn.REST,),
    )
    async def reconcile_route(self, body: ReconcileRequest) -> SyncSummary:
        from zephyrex.extensions.scim_provider.EXT_SCIMProvider import EXT_SCIMProvider

        # The caller must be able to see the target (404 otherwise).
        ProviderInstanceManager(
            model_registry=self.model_registry, requester_id=self.requester.id
        ).get(id=body.provider_instance_id)
        summary = await EXT_SCIMProvider.reconcile(
            self.model_registry, body.provider_instance_id
        )
        return SyncSummary(**summary)


# ----- hooks ------------------------------------------------------------------


def _loaded(context: HookContext) -> bool:
    extensions = getattr(context.manager.model_registry, "extension_registry", None)
    return extensions is not None and EXTENSION_NAME in extensions.extension_names


def _flag(context: HookContext, resource_type: str, local_ids: Iterable[Any]) -> None:
    """Flag a push of ``local_ids`` to every target, and start it."""
    from zephyrex.extensions.scim_provider.EXT_SCIMProvider import EXT_SCIMProvider
    from zephyrex.extensions.scim_provider.SCIMSync import mark_pending

    registry = context.manager.model_registry
    targets = mark_pending(registry, resource_type, local_ids)
    if targets:
        EXT_SCIMProvider.schedule(registry, targets)


def _target_id(context: HookContext) -> Optional[str]:
    """The id a get/update/delete acted on."""
    found = context.kwargs.get("id") or (context.args[0] if context.args else None)
    return str(found) if found else None


def _observer(handler: Callable[[HookContext], None]) -> Callable[[HookContext], None]:
    """``handler`` as a hook that runs only in apps that load this
    extension, and never breaks the operation it observes."""

    def run(context: HookContext) -> None:
        if not _loaded(context):
            return
        try:
            handler(context)
        except Exception:
            logger.exception("scim_provider could not flag a push")

    run.__name__ = handler.__name__
    run.__doc__ = handler.__doc__
    return run


@hook_bll(UserCredentialManager.create, timing=HookTiming.AFTER, blocking=False)
@_observer
def scim_user_registered(context: HookContext) -> None:
    """Registration writes the user's first credentials; the user row is
    written raw, so this is where a new user is seen."""
    _flag(context, "User", [context.kwargs.get("user_id")])


@hook_bll(UserManager.update, timing=HookTiming.AFTER, blocking=False)
@_observer
def scim_user_updated(context: HookContext) -> None:
    _flag(context, "User", [_target_id(context)])


@hook_bll(UserManager.delete, timing=HookTiming.AFTER, blocking=False)
@_observer
def scim_user_deleted(context: HookContext) -> None:
    _flag(context, "User", [_target_id(context) or context.manager.requester.id])


@hook_bll(TeamManager.create, timing=HookTiming.AFTER, blocking=False)
@_observer
def scim_team_created(context: HookContext) -> None:
    _flag(context, "Group", [getattr(context.result, "id", None)])


@hook_bll(TeamManager.update, timing=HookTiming.AFTER, blocking=False)
@_observer
def scim_team_updated(context: HookContext) -> None:
    _flag(context, "Group", [_target_id(context)])


@hook_bll(TeamManager.delete, timing=HookTiming.AFTER, blocking=False)
@_observer
def scim_team_deleted(context: HookContext) -> None:
    _flag(context, "Group", [_target_id(context)])


@hook_bll(UserTeamManager.update, timing=HookTiming.BEFORE, blocking=False)
@hook_bll(UserTeamManager.delete, timing=HookTiming.BEFORE, blocking=False)
@_observer
def scim_membership_before_change(context: HookContext) -> None:
    """Note whose membership is changing, while the row still says."""
    membership_id = _target_id(context)
    if membership_id is None:
        return
    from zephyrex.lib.Environment import env

    membership = UserTeamManager(
        model_registry=context.manager.model_registry, requester_id=env("ROOT_ID")
    ).get(id=membership_id)
    context.condition_data["scim_membership"] = (
        str(membership.user_id),
        str(membership.team_id),
    )


@hook_bll(UserTeamManager.create, timing=HookTiming.AFTER, blocking=False)
@hook_bll(UserTeamManager.update, timing=HookTiming.AFTER, blocking=False)
@hook_bll(UserTeamManager.delete, timing=HookTiming.AFTER, blocking=False)
@_observer
def scim_membership_changed(context: HookContext) -> None:
    """A membership changes a group's members, and (for a target scoped to
    a team) whether its user is given to the target at all."""
    noted = context.condition_data.get("scim_membership")
    if noted is None:
        result = context.result
        noted = (
            str(getattr(result, "user_id", "") or context.kwargs.get("user_id") or ""),
            str(getattr(result, "team_id", "") or context.kwargs.get("team_id") or ""),
        )
    user_id, team_id = noted
    _flag(context, "User", [user_id])
    _flag(context, "Group", [team_id])
