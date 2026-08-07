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

from zephyrex.lib.Logging import logger
from zephyrex.lib.Pydantic import BaseModel
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)


class MetadataModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
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

        search_params = {"key": {"value": key}}
        if user_id:
            search_params["user_id"] = {"value": user_id}
        if team_id:
            search_params["team_id"] = {"value": team_id}

        existing = self.search(search_params)

        exact_matches = []
        if existing:
            for record in existing:
                matches_user = (user_id is None) or (
                    getattr(record, "user_id", None) == user_id
                )
                matches_team = (team_id is None) or (
                    getattr(record, "team_id", None) == team_id
                )
                matches_key = record.key == key
                if matches_user and matches_team and matches_key:
                    exact_matches.append(record)

        # Filter out system-created records when setting user preferences
        if exact_matches and user_id and not team_id:
            from zephyrex.lib.Environment import env

            user_owned_records = []
            for record in exact_matches:
                created_by = getattr(record, "created_by_user_id", None)
                created_by_system = created_by in [env("ROOT_ID"), env("SYSTEM_ID")]
                if not created_by_system:
                    user_owned_records.append(record)
            exact_matches = user_owned_records

        if exact_matches and len(exact_matches) > 0:
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
        search_params = {"key": {"value": key}}
        if user_id:
            search_params["user_id"] = {"value": user_id}
        if team_id:
            search_params["team_id"] = {"value": team_id}

        results = self.search(search_params)

        exact_matches = []
        if results:
            for record in results:
                matches_user = (user_id is None) or (
                    getattr(record, "user_id", None) == user_id
                )
                matches_team = (team_id is None) or (
                    getattr(record, "team_id", None) == team_id
                )
                matches_key = record.key == key
                if matches_user and matches_team and matches_key:
                    exact_matches.append(record)

        if exact_matches and user_id and not team_id:
            from zephyrex.lib.Environment import env

            user_owned_records = []
            for record in exact_matches:
                created_by_system = getattr(record, "created_by_user_id", None) in [
                    env("ROOT_ID"),
                    env("SYSTEM_ID"),
                ]
                if not created_by_system:
                    user_owned_records.append(record)
            exact_matches = user_owned_records

        if exact_matches and len(exact_matches) > 0:
            return exact_matches[0].value  # type: ignore[no-any-return]

        return None


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

        results = self.search({"user_id": {"value": self.target_user_id}})

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
