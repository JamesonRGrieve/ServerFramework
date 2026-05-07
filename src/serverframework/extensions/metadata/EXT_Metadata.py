"""metadata extension definition.

Owns the unified `metadata` table. Hooks let core BLL_Auth talk to the
extension without importing it.
"""

from typing import Any, ClassVar, Dict, List, Optional, Type

from serverframework.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
)


def _list_preferences(user_id: str, model_registry) -> Dict[str, str]:
    """Return user metadata as a flat ``{key: value}`` map. Used by
    ``UserManager.login`` for the preferences part of the login response."""
    from serverframework.extensions.metadata.BLL_Metadata import MetadataModel
    from serverframework.lib.Environment import env

    items = MetadataModel.DB(model_registry.DB.manager.Base).list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        user_id=user_id,
    )
    return {item.key: item.value for item in items or []}


def _list_user_metadata(user_id: str, model_registry) -> List[Any]:
    """Return raw MetadataModel rows for ``user_id``."""
    from serverframework.extensions.metadata.BLL_Metadata import MetadataModel
    from serverframework.lib.Environment import env

    return (
        MetadataModel.DB(model_registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            user_id=user_id,
        )
        or []
    )


def _user_manager_factory(
    requester_id: str,
    target_id: Optional[str],
    model_registry: Any,
    **kw,
):
    from serverframework.extensions.metadata.BLL_Metadata import (
        UserMetadataManager,
    )

    return UserMetadataManager(
        requester_id=requester_id,
        target_id=target_id,
        model_registry=model_registry,
        **kw,
    )


def _team_manager_factory(
    requester_id: str,
    target_team_id: Optional[str],
    model_registry: Any,
    **kw,
):
    from serverframework.extensions.metadata.BLL_Metadata import (
        TeamMetadataManager,
    )

    return TeamMetadataManager(
        requester_id=requester_id,
        target_team_id=target_team_id,
        model_registry=model_registry,
        **kw,
    )


def _create_user_metadata(
    user_id: str,
    key: str,
    value: str,
    model_registry: Any,
    *,
    requester_id: Optional[str] = None,
) -> None:
    from serverframework.extensions.metadata.BLL_Metadata import (
        UserMetadataManager,
    )
    from serverframework.lib.Environment import env

    with UserMetadataManager(
        requester_id=requester_id or env("ROOT_ID"),
        model_registry=model_registry,
    ) as mgr:
        mgr.create(user_id=user_id, key=key, value=str(value))


def _update_user_metadata(
    id: str,
    value: Any,
    model_registry: Any,
    *,
    requester_id: Optional[str] = None,
) -> None:
    from serverframework.extensions.metadata.BLL_Metadata import (
        UserMetadataManager,
    )
    from serverframework.lib.Environment import env

    with UserMetadataManager(
        requester_id=requester_id or env("ROOT_ID"),
        model_registry=model_registry,
    ) as mgr:
        mgr.update(id=id, value=str(value))


class MetadataExtension(AbstractStaticExtension):
    name: ClassVar[str] = "metadata"
    description: ClassVar[str] = (
        "Free-form key/value metadata for users and teams (Scope #3)"
    )
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def models(cls) -> List[Type]:
        from serverframework.extensions.metadata.BLL_Metadata import MetadataModel

        return [MetadataModel]

    @classmethod
    def on_load(cls) -> None:
        from serverframework.logic.BLL_Auth import register_metadata_hooks

        register_metadata_hooks(
            list_preferences=_list_preferences,
            list_user_metadata=_list_user_metadata,
            user_manager_factory=_user_manager_factory,
            team_manager_factory=_team_manager_factory,
            create_user_metadata=_create_user_metadata,
            update_user_metadata=_update_user_metadata,
        )
