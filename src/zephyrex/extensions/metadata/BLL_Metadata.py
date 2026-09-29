"""Canonical metadata model and managers (Scope #3 — moved from core).

Owns:
- ``MetadataModel`` — unified user/team metadata table.
- ``MetadataManager`` — base preference-setting/getting logic.
- ``UserMetadataManager`` / ``TeamMetadataManager`` — filtered managers.

Hooks registered with core via ``EXT_Metadata.on_load`` so the
``BLL_Auth.UserManager`` registration / login flow can talk to this
extension without importing it directly.
"""

from typing import Any, ClassVar, Dict, List, Optional, Type

from fastapi import HTTPException
from pydantic import Field

from zephyrex.pydantic2.registry import BaseModel
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)


class MetadataModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["MetadataManager"]] = None  # type: ignore[assignment]
    # Foreign key fields (optional since metadata can be user-only, team-only, or both)
    user_id: Optional[str] = Field(None, description="Optional foreign key to User")
    team_id: Optional[str] = Field(None, description="Optional foreign key to Team")

    key: str = Field(..., description="Metadata key")
    value: Optional[str] = Field(None, description="Metadata value")

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = "Unified metadata table for users and teams"
    seed_data: ClassVar[List[Dict[str, Any]]] = []

    class Create(BaseModel):
        user_id: Optional[str] = Field(None, description="User ID if user metadata")
        team_id: Optional[str] = Field(None, description="Team ID if team metadata")
        key: str = Field(..., description="Metadata key")
        value: Optional[str] = Field(None, description="Metadata value")

    class Update(BaseModel):
        value: Optional[str] = Field(None, description="Metadata value")

    class Search(ApplicationModel.Search):
        user_id: Optional[StringSearchModel] = None
        team_id: Optional[StringSearchModel] = None
        key: Optional[StringSearchModel] = None
        value: Optional[StringSearchModel] = None


class MetadataManager(AbstractBLLManager):
    _model = MetadataModel

    def create_validation(self, entity):
        """Validate metadata creation"""
        if not entity.user_id and not entity.team_id:
            raise HTTPException(
                status_code=400, detail="Either user_id or team_id must be provided"
            )

    def _preference_rows(
        self, key: str, user_id: Optional[str], team_id: Optional[str]
    ) -> List[MetadataModel]:
        """The rows holding ``key`` for exactly this user and/or team.

        A user's own preference excludes rows the system created for them
        (ROOT/SYSTEM seeded values), which the user overrides rather than
        edits.
        """
        from zephyrex.lib.Environment import env

        owner: Dict[str, Any] = {}
        if user_id:
            owner["user_id"] = user_id
        if team_id:
            owner["team_id"] = team_id
        rows = self.search(key=key, **owner)
        if user_id and not team_id:
            system_ids = {env("ROOT_ID"), env("SYSTEM_ID")}
            rows = [
                row
                for row in rows
                if getattr(row, "created_by_user_id", None) not in system_ids
            ]
        return rows

    def set_preference(
        self,
        key: str,
        value: str,
        user_id: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """Set or update a metadata preference"""
        if not user_id and not team_id:
            raise HTTPException(
                status_code=400, detail="Either user_id or team_id is required"
            )

        exact_matches = self._preference_rows(key, user_id, team_id)
        if exact_matches:
            record = exact_matches[0]
            if user_id and not team_id:
                with MetadataManager(
                    requester_id=user_id, model_registry=self.model_registry
                ) as temp_mgr:
                    temp_mgr.update(id=record.id, value=value)
            else:
                self.update(id=record.id, value=value)
            return {"key": key, "value": value, "action": "updated"}
        else:
            create_args = {"key": key, "value": value}
            if user_id:
                create_args["user_id"] = user_id
            if team_id:
                create_args["team_id"] = team_id
            if user_id and not team_id:
                with MetadataManager(
                    requester_id=user_id, model_registry=self.model_registry
                ) as temp_mgr:
                    temp_mgr.create(**create_args)
            else:
                self.create(**create_args)
            return {"key": key, "value": value, "action": "created"}

    def get_preference(
        self, key: str, user_id: Optional[str] = None, team_id: Optional[str] = None
    ) -> Optional[str]:
        """Get a metadata preference value"""
        exact_matches = self._preference_rows(key, user_id, team_id)
        return exact_matches[0].value if exact_matches else None


class TeamMetadataManager(MetadataManager):
    """Filters by team_id by default."""

    def create_validation(self, entity):
        if not hasattr(entity, "team_id") or not entity.team_id:
            raise HTTPException(
                status_code=400, detail="team_id is required for team metadata"
            )
        super().create_validation(entity)

    def set_preference(self, key: str, value: str) -> Dict[str, str]:  # type: ignore[override]
        if not self.target_team_id:
            raise HTTPException(status_code=400, detail="Team ID is required")
        return super().set_preference(key, value, team_id=self.target_team_id)

    def get_preference(self, key: str) -> Optional[str]:  # type: ignore[override]
        if not self.target_team_id:
            raise HTTPException(status_code=400, detail="Team ID is required")
        return super().get_preference(key, team_id=self.target_team_id)


class UserMetadataManager(MetadataManager):
    """Filters by user_id by default."""

    def create_validation(self, entity):
        if not hasattr(entity, "user_id") or not entity.user_id:
            raise HTTPException(
                status_code=400, detail="user_id is required for user metadata"
            )
        super().create_validation(entity)

    def set_preference(self, key: str, value: str) -> Dict[str, str]:  # type: ignore[override]
        if not self.target_user_id:
            raise HTTPException(status_code=400, detail="User ID is required")
        return super().set_preference(key, value, user_id=self.target_user_id)

    def get_preference(self, key: str) -> Optional[str]:  # type: ignore[override]
        if not self.target_user_id:
            raise HTTPException(status_code=400, detail="User ID is required")
        return super().get_preference(key, user_id=self.target_user_id)

    def get_preferences(self) -> Dict[str, str]:
        if not self.target_user_id:
            raise HTTPException(status_code=400, detail="User ID is required")

        results = self.search(user_id=self.target_user_id)

        preferences = {}
        for metadata in results:
            preferences[metadata.key] = metadata.value
        return preferences


MetadataModel.Manager = MetadataManager


# ---------------------------------------------------------------------------
# Hook registration (Scope #3). Runs at module-import time, mirroring the
# `PasswordlessGrantRegistry.register(...)` pattern used by auth_magic_link.
# When this BLL file is imported (which happens during ModelRegistry's
# `_auto_bind_models` walk for any APP_EXTENSIONS that includes "metadata"),
# every consumer of metadata in core BLL_Auth picks up the canonical
# extension implementation through these typed callables.
# ---------------------------------------------------------------------------


def _registry_has_metadata(model_registry) -> bool:
    """Hooks are registered globally at module-import, but multiple test
    registries (one per fixture) may not all have MetadataModel bound. The
    hooks are safe no-ops against any registry that didn't load the
    metadata extension."""
    try:
        model_registry.apply(MetadataModel)
        return True
    except (TypeError, KeyError, AttributeError):
        return False


def _list_preferences(user_id: str, model_registry) -> Dict[str, str]:
    if not _registry_has_metadata(model_registry):
        return {}
    from zephyrex.lib.Environment import env

    items = MetadataModel.DB(model_registry.DB.manager.Base).list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        user_id=user_id,
    )
    return {item.key: item.value for item in items or []}


def _list_user_metadata(user_id: str, model_registry):
    if not _registry_has_metadata(model_registry):
        return []
    from zephyrex.lib.Environment import env

    return (
        MetadataModel.DB(model_registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            user_id=user_id,
        )
        or []
    )


def _user_manager_factory(requester_id, target_id, model_registry, **kw):
    if not _registry_has_metadata(model_registry):
        return None
    return UserMetadataManager(
        requester_id=requester_id,
        target_id=target_id,
        model_registry=model_registry,
        **kw,
    )


def _team_manager_factory(requester_id, target_team_id, model_registry, **kw):
    if not _registry_has_metadata(model_registry):
        return None
    return TeamMetadataManager(
        requester_id=requester_id,
        target_team_id=target_team_id,
        model_registry=model_registry,
        **kw,
    )


def _create_user_metadata(user_id, key, value, model_registry, *, requester_id=None):
    if not _registry_has_metadata(model_registry):
        return
    from zephyrex.lib.Environment import env

    with UserMetadataManager(
        requester_id=requester_id or env("ROOT_ID"),
        model_registry=model_registry,
    ) as mgr:
        mgr.create(user_id=user_id, key=key, value=str(value))


def _update_user_metadata(id, value, model_registry, *, requester_id=None):
    if not _registry_has_metadata(model_registry):
        return
    from zephyrex.lib.Environment import env

    with UserMetadataManager(
        requester_id=requester_id or env("ROOT_ID"),
        model_registry=model_registry,
    ) as mgr:
        mgr.update(id=id, value=str(value))


try:
    from zephyrex.logic.BLL_Auth import register_metadata_hooks

    register_metadata_hooks(
        list_preferences=_list_preferences,
        list_user_metadata=_list_user_metadata,
        user_manager_factory=_user_manager_factory,
        team_manager_factory=_team_manager_factory,
        create_user_metadata=_create_user_metadata,
        update_user_metadata=_update_user_metadata,
    )
except ImportError:
    # Core not importable (e.g. extension imported in isolation for tests).
    # The EXT class's `on_load` is a manual fallback for that path.
    pass


__all__ = [
    "MetadataModel",
    "MetadataManager",
    "TeamMetadataManager",
    "UserMetadataManager",
]
