"""metadata extension definition.

Registers the `Metadata` model and the cross-cutting "preferences" lookup
hook that BLL_Auth's `register()` and `login()` consult when stitching a
user's preferences into the authn response.
"""

from typing import ClassVar, Dict, List, Optional, Type

from serverframework.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
)


def _list_preferences(user_id: str, model_registry) -> Dict[str, str]:
    """Return user metadata as a flat ``{key: value}`` map. Used by
    `BLL_Auth.UserManager.login` to attach preferences to the login response
    when this extension is loaded."""
    from serverframework.lib.Environment import env
    from serverframework.logic.BLL_Auth import MetadataModel

    items = MetadataModel.DB(model_registry.DB.manager.Base).list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        user_id=user_id,
    )
    return {item.key: item.value for item in items or []}


class MetadataExtension(AbstractStaticExtension):
    name: ClassVar[str] = "metadata"
    description: ClassVar[str] = (
        "Free-form key/value metadata for users and teams (Scope #3)"
    )
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def models(cls) -> List[Type]:
        from serverframework.logic.BLL_Auth import MetadataModel

        return [MetadataModel]

    @classmethod
    def on_load(cls) -> None:
        from serverframework.logic.BLL_Auth import register_metadata_hooks

        register_metadata_hooks(list_preferences=_list_preferences)
