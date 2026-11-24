"""Canonical metadata model and managers (Scope #3 — moved from core).

Owns:
- ``MetadataModel`` — unified user/team metadata table.
- ``MetadataManager`` — base preference-setting/getting logic.
- ``UserMetadataManager`` / ``TeamMetadataManager`` — filtered managers.
- ``RecordMetadataManager`` — a record's own key/values (``/v1/record_metadata``): rows that
  belong to a record by ``target_entity_type`` + ``target_entity_id`` (a character's height, a
  city's population), read by anyone who can view the record and written by its editors; a
  private row is read by its editors only.

Hooks registered with core when this module is imported so the
``BLL_Auth.UserManager`` registration / login flow can talk to this
extension without importing it directly.
"""

from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type

from fastapi import HTTPException, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import Field

from pydantic import BaseModel as RouteModel

from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.RecordTarget import can_edit, can_view
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin, RouteType
from zephyrex.pydantic2.fastapi.limits import MAX_BATCH_SIZE
from zephyrex.pydantic2.registry import BaseModel
from zephyrex.pydantic2.sqlalchemy.indexes import TableIndex
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

    # A record's own row (instead of a user's or team's): see RecordMetadataManager.
    target_entity_type: Optional[str] = Field(
        None, description="The record's type, its model's snake-case name"
    )
    target_entity_id: Optional[str] = Field(None, description="The record's id")
    value_type: Optional[str] = Field(
        None, description="How to read the value: text, number, boolean, date or link"
    )
    ordinal: int = Field(0, description="Its place among the record's rows")
    is_private: bool = Field(False, description="Read by the record's editors only")
    # The lookups the migrations index (001: by user or team and key; 002: by
    # record), declared so the model and the migrated schema stay in step.
    table_indexes: ClassVar[Tuple[TableIndex, ...]] = (
        TableIndex(name="ix_metadata_user_key", columns=("user_id", "key")),
        TableIndex(name="ix_metadata_team_key", columns=("team_id", "key")),
        TableIndex(
            name="ix_metadata_target",
            columns=("target_entity_type", "target_entity_id"),
        ),
    )

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = "Unified metadata table for users and teams"
    seed_data: ClassVar[List[Dict[str, Any]]] = []

    class Create(BaseModel):
        user_id: Optional[str] = Field(None, description="User ID if user metadata")
        team_id: Optional[str] = Field(None, description="Team ID if team metadata")
        key: str = Field(..., description="Metadata key")
        value: Optional[str] = Field(None, description="Metadata value")
        target_entity_type: Optional[str] = None
        target_entity_id: Optional[str] = None
        value_type: Optional[str] = None
        ordinal: int = 0
        is_private: bool = False

    class Update(BaseModel):
        value: Optional[str] = Field(None, description="Metadata value")
        key: Optional[str] = None
        value_type: Optional[str] = None
        ordinal: Optional[int] = None
        is_private: Optional[bool] = None

    class Search(ApplicationModel.Search):
        user_id: Optional[StringSearchModel] = None
        team_id: Optional[StringSearchModel] = None
        key: Optional[StringSearchModel] = None
        value: Optional[StringSearchModel] = None
        target_entity_type: Optional[StringSearchModel] = None
        target_entity_id: Optional[StringSearchModel] = None


class MetadataManager(AbstractBLLManager):
    _model = MetadataModel

    def create_validation(self, entity):
        """Validate metadata creation: a user's and/or team's row, or a record's, never both."""
        target = getattr(entity, "target_entity_type", None) or getattr(
            entity, "target_entity_id", None
        )
        if target:
            if entity.user_id or entity.team_id:
                raise HTTPException(
                    status_code=400,
                    detail="A record's metadata belongs to the record, not a user or team",
                )
            return
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
# A record's own key/values (W4 in Zephyrex RPG Wiki: a character's height, a city's population).
# ---------------------------------------------------------------------------

VALUE_TYPES = ("text", "number", "boolean", "date", "link")
MAX_RECORD_KEYS = 200


class RecordMetadataList(RouteModel):
    metadata: List[MetadataModel]


class RecordMetadataWrite(RouteModel):
    target_entity_type: str
    target_entity_id: str
    key: str = Field(..., min_length=1, max_length=100)
    value: Optional[str] = Field(None, max_length=10_000)
    value_type: Optional[str] = None
    ordinal: int = 0
    is_private: bool = False


class RecordMetadataEnvelope(RouteModel):
    """One row (``record_metadata``) or a batch (``record_metadatas``), each
    batch row validated and reported on its own."""

    record_metadata: Optional[RecordMetadataWrite] = None
    record_metadatas: Optional[List[Any]] = Field(None, max_length=MAX_BATCH_SIZE)


class RecordMetadataChange(RouteModel):
    key: Optional[str] = Field(None, min_length=1, max_length=100)
    value: Optional[str] = Field(None, max_length=10_000)
    value_type: Optional[str] = None
    ordinal: Optional[int] = None
    is_private: Optional[bool] = None


class RecordMetadataChangeEnvelope(RouteModel):
    record_metadata: RecordMetadataChange


class RecordMetadataOne(RouteModel):
    record_metadata: MetadataModel


class RecordMetadataBatchChange(RouteModel):
    """Each row's own change: ``{"id", "if_match"?, ...fields}``."""

    record_metadatas: List[Any] = Field(..., max_length=MAX_BATCH_SIZE)


class RecordMetadataPlural(RouteModel):
    record_metadatas: List[MetadataModel]


class RecordMetadataManager(AbstractBLLManager, RouterMixin):
    """A record's rows, under the record's own access: VIEW reads them (private ones need EDIT),
    EDIT writes them. Rows are read and stored as SYSTEM (which, unlike ROOT, never sees deleted rows), so the user/team permission paths never reach them;
    every route here checks the record first, through its own manager (lib.RecordTarget).

    Custom routes only, over no table of its own (an action manager, as SDKManager): router
    generation reaches a model's routes through ``Model.Manager``, and MetadataModel's is the
    route-less MetadataManager, which the user/team hooks rely on."""

    prefix: ClassVar[Optional[str]] = "/v1/record_metadata"
    tags: ClassVar[Optional[List[str]]] = ["Metadata"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    @property
    def versions_table(self) -> str:
        """The rows these routes write are the metadata table's, so a
        PATCH's or DELETE's If-Match holds the row its path names."""
        return str(self._root().DB.__tablename__)

    def visible_current(self, id: str) -> Any:
        """The row as it now stands, for a 412: only a row's editors write
        it, and they may read all of its record's rows."""
        return self._root().visible_current(id)

    def _root(self) -> MetadataManager:
        from zephyrex.lib.Environment import env

        return MetadataManager(
            requester_id=env("SYSTEM_ID"), model_registry=self.model_registry
        )

    def _require_edit(self, target_entity_type: str, target_entity_id: str) -> None:
        requester = str(self.requester.id)
        if not can_edit(
            self.model_registry, requester, target_entity_type, target_entity_id
        ):
            # The same 404 whether the record is missing or merely not the requester's to change.
            raise HTTPException(status_code=404, detail="Record not found")

    @staticmethod
    def _check_type(value_type: Optional[str]) -> None:
        if value_type is not None and value_type not in VALUE_TYPES:
            raise HTTPException(
                status_code=422, detail=f"value_type is one of {', '.join(VALUE_TYPES)}"
            )

    @custom_route(
        method="GET",
        path="/of",
        output_model=RecordMetadataList,
        authentication_type="jwt",
        openapi_tags=("Metadata",),
        summary="A record's key/values, in order, to anyone who can view it (private ones to its editors)",
        expose_in=(ExposeIn.REST,),
    )
    def of_route(
        self, target_entity_type: str, target_entity_id: str
    ) -> RecordMetadataList:
        requester = str(self.requester.id)
        if not can_view(
            self.model_registry, requester, target_entity_type, target_entity_id
        ):
            raise HTTPException(status_code=404, detail="Record not found")
        rows = self._root().list(
            target_entity_type=target_entity_type, target_entity_id=target_entity_id
        )
        if any(row.is_private for row in rows) and not can_edit(
            self.model_registry, requester, target_entity_type, target_entity_id
        ):
            rows = [row for row in rows if not row.is_private]
        return RecordMetadataList(
            metadata=sorted(rows, key=lambda row: (row.ordinal, row.key))
        )

    @custom_route(
        method="POST",
        path="",
        output_model=RecordMetadataOne,
        input_model=RecordMetadataEnvelope,
        authentication_type="jwt",
        status_code=201,
        openapi_tags=("Metadata",),
        summary="Add a key/value to a record one may edit (one row per key), or a "
        "batch of them with each row's outcome",
        expose_in=(ExposeIn.REST,),
    )
    def add_route(
        self,
        record_metadata: Optional[RecordMetadataWrite] = None,
        record_metadatas: Optional[List[Any]] = None,
    ) -> Any:
        if (record_metadata is None) == (record_metadatas is None):
            raise HTTPException(
                status_code=422,
                detail="Send either 'record_metadata' (one row) or "
                "'record_metadatas' (a batch)",
            )
        if record_metadatas is not None:
            return self._add_batch(record_metadatas)
        return RecordMetadataOne(record_metadata=self._add(record_metadata))

    def _add_batch(self, rows: List[Any]) -> JSONResponse:
        """Every row attempted and reported in order, as a plural create is:
        201 when all were added, 207 with each row's outcome when some
        failed, and the first row's error when all did."""
        from zephyrex.pydantic2.fastapi.routes import (
            _BATCH_CREATED,
            _BATCH_ERROR,
            _batch_item_error,
        )

        outcomes: List[Dict[str, Any]] = []
        added: List[MetadataModel] = []
        for index, row in enumerate(rows):
            try:
                created = self._add(row)
            except Exception as err:  # this row's outcome; the batch goes on
                outcomes.append(
                    {"index": index, "outcome": _BATCH_ERROR, **_batch_item_error(err)}
                )
                continue
            outcomes.append(
                {"index": index, "outcome": _BATCH_CREATED, "id": created.id}
            )
            added.append(created)
        if rows and not added:
            raise HTTPException(
                status_code=outcomes[0]["status_code"], detail=outcomes[0]["error"]
            )
        records = [row.model_dump() for row in added]
        if len(added) < len(rows):
            errors = [o for o in outcomes if o["outcome"] == _BATCH_ERROR]
            return JSONResponse(
                status_code=207,
                content=jsonable_encoder(
                    {
                        "message": "Partial success",
                        "results": outcomes,
                        "errors": errors,
                        "successful_creates": len(added),
                        "failed_creates": len(errors),
                        "record_metadatas": records,
                    }
                ),
            )
        return JSONResponse(
            status_code=201, content=jsonable_encoder({"record_metadatas": records})
        )

    def _add(self, record_metadata: Any) -> MetadataModel:
        if not isinstance(record_metadata, (dict, RecordMetadataWrite)):
            raise HTTPException(
                status_code=422, detail="Each batch item must be an object"
            )
        fields = RecordMetadataWrite.model_validate(
            record_metadata.model_dump()
            if isinstance(record_metadata, RecordMetadataWrite)
            else record_metadata
        )
        self._check_type(fields.value_type)
        self._require_edit(fields.target_entity_type, fields.target_entity_id)
        root = self._root()
        existing = root.list(
            target_entity_type=fields.target_entity_type,
            target_entity_id=fields.target_entity_id,
        )
        if any(row.key == fields.key for row in existing):
            raise HTTPException(
                status_code=409, detail=f"The record already has {fields.key!r}"
            )
        if len(existing) >= MAX_RECORD_KEYS:
            raise HTTPException(
                status_code=422, detail=f"A record has at most {MAX_RECORD_KEYS} keys"
            )
        row: MetadataModel = root.create(**fields.model_dump())
        return row

    def _row(self, id: str) -> MetadataModel:
        row: MetadataModel = self._root().get(id=id)
        if not row.target_entity_type or not row.target_entity_id:
            raise HTTPException(status_code=404, detail="Not found")
        self._require_edit(row.target_entity_type, row.target_entity_id)
        return row

    @custom_route(
        method="PATCH",
        path="/{id}",
        output_model=RecordMetadataOne,
        input_model=RecordMetadataChangeEnvelope,
        authentication_type="jwt",
        openapi_tags=("Metadata",),
        summary="Change a record's key/value (its editors)",
        expose_in=(ExposeIn.REST,),
    )
    def change_route(
        self, id: str, record_metadata: RecordMetadataChange
    ) -> RecordMetadataOne:
        return RecordMetadataOne(record_metadata=self._change(id, record_metadata))

    def _change(self, id: str, record_metadata: Any) -> MetadataModel:
        row = self._row(id)
        # The body may arrive with every field present (None for those not sent): only real values change.
        changes = {
            name: value
            for name, value in RecordMetadataChange.model_validate(record_metadata)
            .model_dump()
            .items()
            if value is not None
        }
        self._check_type(changes.get("value_type"))
        if "key" in changes and changes["key"] != row.key:
            siblings = self._root().list(
                target_entity_type=row.target_entity_type,
                target_entity_id=row.target_entity_id,
            )
            if any(other.key == changes["key"] for other in siblings):
                raise HTTPException(
                    status_code=409, detail=f"The record already has {changes['key']!r}"
                )
        updated: MetadataModel = self._root().update(id=id, **changes)
        return updated

    @custom_route(
        method="PUT",
        path="",
        output_model=RecordMetadataPlural,
        input_model=RecordMetadataBatchChange,
        authentication_type="jwt",
        openapi_tags=("Metadata",),
        summary="Change many records' key/values, each row its own change, with "
        "each row's outcome",
        expose_in=(ExposeIn.REST,),
    )
    def change_batch_route(self, record_metadatas: List[Any]) -> JSONResponse:
        """Every row attempted and reported in order, as the generic per-row
        batch update is: each held to its own If-Match on the metadata table
        (stale 412, none 428 when required); 200 when all changed, 207 with
        each row's outcome when some failed, the first row's error when all
        did."""
        from zephyrex.lib.Preconditions import expect_versions
        from zephyrex.pydantic2.fastapi.routes import (
            _BATCH_ERROR,
            _BATCH_UPDATED,
            _batch_item_error,
        )

        outcomes: List[Dict[str, Any]] = []
        changed: List[MetadataModel] = []
        for index, row in enumerate(record_metadatas):
            row_id = row.get("id") if isinstance(row, dict) else None
            try:
                if not isinstance(row, dict) or not row_id:
                    raise HTTPException(
                        status_code=422,
                        detail="Each batch item must be an object with an 'id'",
                    )
                fields = {k: v for k, v in row.items() if k not in ("id", "if_match")}
                with expect_versions(self, {row_id: row.get("if_match")}):
                    updated = self._change(row_id, fields)
            except Exception as err:  # this row's outcome; the batch goes on
                outcome: Dict[str, Any] = {"index": index, "outcome": _BATCH_ERROR}
                if row_id:
                    outcome["id"] = row_id
                outcomes.append({**outcome, **_batch_item_error(err)})
                continue
            outcomes.append({"index": index, "outcome": _BATCH_UPDATED, "id": row_id})
            changed.append(updated)
        if record_metadatas and not changed:
            raise HTTPException(
                status_code=outcomes[0]["status_code"], detail=outcomes[0]["error"]
            )
        records = [row.model_dump() for row in changed]
        if len(changed) < len(record_metadatas):
            errors = [o for o in outcomes if o["outcome"] == _BATCH_ERROR]
            return JSONResponse(
                status_code=207,
                content=jsonable_encoder(
                    {
                        "message": "Partial success",
                        "results": outcomes,
                        "errors": errors,
                        "successful_updates": len(changed),
                        "failed_updates": len(errors),
                        "record_metadatas": records,
                    }
                ),
            )
        return JSONResponse(
            status_code=200, content=jsonable_encoder({"record_metadatas": records})
        )

    @custom_route(
        method="DELETE",
        path="/{id}",
        authentication_type="jwt",
        response_class=Response,
        openapi_tags=("Metadata",),
        summary="Remove a record's key/value (its editors)",
        expose_in=(ExposeIn.REST,),
    )
    def remove_route(self, id: str) -> Response:
        self._row(id)
        self._root().delete(id=id)
        return Response(status_code=204)


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
        requester_id=env("SYSTEM_ID"),
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
            requester_id=env("SYSTEM_ID"),
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
        requester_id=requester_id or env("SYSTEM_ID"),
        model_registry=model_registry,
    ) as mgr:
        mgr.create(user_id=user_id, key=key, value=str(value))


def _update_user_metadata(id, value, model_registry, *, requester_id=None):
    if not _registry_has_metadata(model_registry):
        return
    from zephyrex.lib.Environment import env

    with UserMetadataManager(
        requester_id=requester_id or env("SYSTEM_ID"),
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
    # Core not importable yet (framework-bootstrap import window).
    pass


__all__ = [
    "MetadataModel",
    "MetadataManager",
    "TeamMetadataManager",
    "UserMetadataManager",
]
