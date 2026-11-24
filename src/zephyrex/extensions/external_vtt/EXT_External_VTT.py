# SPDX-License-Identifier: AGPL-3.0-or-later
"""An external virtual tabletop's world, read and written as its GM: the
fight in progress, what is said and rolled at the table, the actors, the
scenes and their tokens, the journals.

A provider instance is one world (the MCP server that keeps its GM session),
so every
ability names the world it acts on (the instance's id) and runs on that
instance alone: failing over to another world is never right. Writes are
held to the document types a GM's table needs from outside (a narration in
the chat, a token moved, the turn advanced). Who approves a write follows
the world instance's mode: in ``assistant`` mode a human GM runs the table,
so every write names the GM who approved it (``approved_by``, server-only:
never a model's to fill), and one without is refused; in ``campaign`` mode
the app's AI is the GM, so writes need no approval.
"""

from abc import abstractmethod
from typing import Any, ClassVar, Dict, FrozenSet, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.external_vtt.FoundryBackend import FoundryBackend
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

Doc = Dict[str, Any]

# What can be read: the world's documents the app follows.
READABLE_TYPES: FrozenSet[str] = frozenset(
    {
        "Actor",
        "ChatMessage",
        "Combat",
        "Folder",
        "Item",
        "JournalEntry",
        "Scene",
        "User",
    }
)
# Embedded documents read through their parent.
READABLE_EMBEDDED: FrozenSet[str] = frozenset(
    {"Combatant", "Item", "JournalEntryPage", "Token"}
)
# What can be created, updated and deleted: a narration or an NPC's line; a
# person (an actor) and their token on a scene; a fight, its combatants, its
# turns; a session's report (a journal and its pages); a scene's picture.
CREATABLE_TYPES: FrozenSet[str] = frozenset(
    {
        "Actor",
        "ChatMessage",
        "Combat",
        "Combatant",
        "JournalEntry",
        "JournalEntryPage",
        "Token",
    }
)
UPDATABLE_TYPES: FrozenSet[str] = frozenset(
    {"Actor", "Combat", "Combatant", "Scene", "Token"}
)
DELETABLE_TYPES: FrozenSet[str] = frozenset({"Combat", "Combatant", "Token"})
# Embedded documents, written through their parent.
EMBEDDED_TYPES: FrozenSet[str] = frozenset({"Combatant", "JournalEntryPage", "Token"})
# The game settings the app reads and sets: the world's clock.
SETTINGS: FrozenSet[str] = frozenset({"core.time"})
# The largest batch one call takes.
MAX_DOCUMENTS = 100

WRITE_ABILITIES: FrozenSet[str] = frozenset(
    {
        "vtt_create_documents",
        "vtt_update_documents",
        "vtt_delete_documents",
        "vtt_set_setting",
    }
)


def checked_parent(document_type: str, parent_uuid: Optional[str]) -> None:
    if document_type in EMBEDDED_TYPES and not parent_uuid:
        raise InvalidInputExternalError(
            f"a {document_type} is written through its parent (parent_uuid)"
        )


def checked_setting(key: str) -> str:
    if key not in SETTINGS:
        raise InvalidInputExternalError(
            f"{key!r} is not a setting the app uses; one of {', '.join(sorted(SETTINGS))}"
        )
    return key


def checked_type(document_type: str, allowed: FrozenSet[str], verb: str) -> str:
    if document_type not in allowed:
        raise InvalidInputExternalError(
            f"{document_type!r} cannot be {verb}; one of {', '.join(sorted(allowed))}"
        )
    return document_type


def checked_documents(docs: Any, need_id: bool) -> List[Doc]:
    """A non-empty list of objects, at most ``MAX_DOCUMENTS``, each with an
    ``_id`` when it updates one."""
    if not isinstance(docs, list) or not docs:
        raise InvalidInputExternalError("documents must be a non-empty list")
    if len(docs) > MAX_DOCUMENTS:
        raise InvalidInputExternalError(
            f"at most {MAX_DOCUMENTS} documents per call, not {len(docs)}"
        )
    for doc in docs:
        if not isinstance(doc, dict):
            raise InvalidInputExternalError("each document must be an object")
        if need_id and not isinstance(doc.get("_id"), str):
            raise InvalidInputExternalError("each update names its document's _id")
    return docs


class AbstractVTTProvider(AbstractStaticProvider):
    """A virtual tabletop. Every method takes the world's instance first."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[Set[str]] = {
        "vtt_status",
        "vtt_list_documents",
        "vtt_get_document",
        "vtt_list_embedded",
        "vtt_get_setting",
        *WRITE_ABILITIES,
    }
    _env: ClassVar[Dict[str, Any]] = {}

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def services(cls) -> List[str]:
        return ["external_vtt"]

    @classmethod
    @abstractmethod
    async def status(cls, instance: ProviderInstanceModel) -> Doc:
        """The world's title, system, version and whether writes are on."""

    @classmethod
    @abstractmethod
    async def list_documents(
        cls,
        instance: ProviderInstanceModel,
        document_type: str,
        name_filter: str,
        fields: List[str],
    ) -> List[Doc]:
        """The world's documents of a type, each with ``fields`` (all when
        empty), whose name contains ``name_filter``."""

    @classmethod
    @abstractmethod
    async def get_document(
        cls, instance: ProviderInstanceModel, uuid: str, embedded: bool
    ) -> Doc:
        """One document by uuid, with its embedded documents when asked."""

    @classmethod
    @abstractmethod
    async def list_embedded(
        cls,
        instance: ProviderInstanceModel,
        parent_uuid: str,
        embedded_name: str,
        fields: List[str],
    ) -> List[Doc]:
        """A document's embedded documents of one kind (a scene's tokens)."""

    @classmethod
    @abstractmethod
    async def create_documents(
        cls,
        instance: ProviderInstanceModel,
        document_type: str,
        docs: List[Doc],
        parent_uuid: Optional[str],
        approved: bool = False,
    ) -> List[str]:
        """Create documents; their ids."""

    @classmethod
    @abstractmethod
    async def update_documents(
        cls,
        instance: ProviderInstanceModel,
        document_type: str,
        updates: List[Doc],
        parent_uuid: Optional[str],
        approved: bool = False,
    ) -> List[str]:
        """Update documents by ``_id``; the ids updated."""

    @classmethod
    @abstractmethod
    async def delete_documents(
        cls,
        instance: ProviderInstanceModel,
        document_type: str,
        ids: List[str],
        parent_uuid: Optional[str],
        approved: bool = False,
    ) -> Doc:
        """Delete documents by id, each backed up first."""

    @classmethod
    @abstractmethod
    async def game_setting(cls, instance: ProviderInstanceModel, key: str) -> Any:
        """A game setting's value."""

    @classmethod
    @abstractmethod
    async def set_game_setting(
        cls,
        instance: ProviderInstanceModel,
        key: str,
        value: Any,
        approved: bool = False,
    ) -> Any:
        """Set a game setting."""


class EXT_External_VTT(FoundryBackend, AbstractStaticExtension):
    name: ClassVar[str] = "external_vtt"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "An external virtual tabletop's world (Foundry VTT), read and "
        "written as its GM"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="mcp_client",
                friendly_name="MCP client",
                reason="A world is reached through its foundry-mcp server's tools",
            ),
        ]
    )
    _abilities: ClassVar[Set[str]] = AbstractVTTProvider._abilities

    @classmethod
    def get_required_permissions(cls) -> List[str]:
        return ["external_vtt:read", "external_vtt:write"]

    @classmethod
    async def _on_world(
        cls,
        world_id: str,
        method_name: str,
        *args: Any,
        as_user: Optional[str] = None,
    ) -> Any:
        """``method_name`` on the world's own instance, and no other."""
        if not world_id:
            raise InvalidInputExternalError("world_id names the world's instance")
        return await cls.rotate_among(
            [provider.name for provider in cls.providers],
            method_name,
            *args,
            as_user=as_user,
            instance_ids=[world_id],
        )

    @classmethod
    @ability("vtt_status")
    async def vtt_status(cls, world_id: str, as_user: Optional[str] = None) -> Doc:
        status: Doc = await cls._on_world(world_id, "status", as_user=as_user)
        return status

    @classmethod
    @ability("vtt_list_documents")
    async def vtt_list_documents(
        cls,
        world_id: str,
        document_type: str,
        name_filter: str = "",
        fields: Optional[List[str]] = None,
        as_user: Optional[str] = None,
    ) -> List[Doc]:
        checked_type(document_type, READABLE_TYPES, "read")
        docs: List[Doc] = await cls._on_world(
            world_id,
            "list_documents",
            document_type,
            name_filter,
            list(fields or []),
            as_user=as_user,
        )
        return docs

    @classmethod
    @ability("vtt_get_document")
    async def vtt_get_document(
        cls,
        world_id: str,
        uuid: str,
        embedded: bool = False,
        as_user: Optional[str] = None,
    ) -> Doc:
        document_type = uuid.split(".", 1)[0]
        checked_type(document_type, READABLE_TYPES, "read")
        doc: Doc = await cls._on_world(
            world_id, "get_document", uuid, embedded, as_user=as_user
        )
        return doc

    @classmethod
    @ability("vtt_list_embedded")
    async def vtt_list_embedded(
        cls,
        world_id: str,
        parent_uuid: str,
        embedded_name: str,
        fields: Optional[List[str]] = None,
        as_user: Optional[str] = None,
    ) -> List[Doc]:
        checked_type(embedded_name, READABLE_EMBEDDED, "read")
        docs: List[Doc] = await cls._on_world(
            world_id,
            "list_embedded",
            parent_uuid,
            embedded_name,
            list(fields or []),
            as_user=as_user,
        )
        return docs

    @classmethod
    @ability("vtt_create_documents")
    async def vtt_create_documents(
        cls,
        world_id: str,
        document_type: str,
        documents: List[Doc],
        parent_uuid: Optional[str] = None,
        as_user: Optional[str] = None,
        approved_by: Optional[str] = None,
    ) -> List[str]:
        checked_type(document_type, CREATABLE_TYPES, "created")
        checked_parent(document_type, parent_uuid)
        ids: List[str] = await cls._on_world(
            world_id,
            "create_documents",
            document_type,
            checked_documents(documents, need_id=False),
            parent_uuid,
            bool(approved_by),
            as_user=approved_by or as_user,
        )
        return ids

    @classmethod
    @ability("vtt_update_documents")
    async def vtt_update_documents(
        cls,
        world_id: str,
        document_type: str,
        updates: List[Doc],
        parent_uuid: Optional[str] = None,
        as_user: Optional[str] = None,
        approved_by: Optional[str] = None,
    ) -> List[str]:
        checked_type(document_type, UPDATABLE_TYPES, "updated")
        checked_parent(document_type, parent_uuid)
        ids: List[str] = await cls._on_world(
            world_id,
            "update_documents",
            document_type,
            checked_documents(updates, need_id=True),
            parent_uuid,
            bool(approved_by),
            as_user=approved_by or as_user,
        )
        return ids

    @classmethod
    @ability("vtt_delete_documents")
    async def vtt_delete_documents(
        cls,
        world_id: str,
        document_type: str,
        ids: List[str],
        parent_uuid: Optional[str] = None,
        as_user: Optional[str] = None,
        approved_by: Optional[str] = None,
    ) -> Doc:
        """Delete documents by id; the world's server backs each up first."""
        checked_type(document_type, DELETABLE_TYPES, "deleted")
        checked_parent(document_type, parent_uuid)
        if not isinstance(ids, list) or not ids or len(ids) > MAX_DOCUMENTS:
            raise InvalidInputExternalError(f"ids is a list of 1-{MAX_DOCUMENTS} ids")
        result: Doc = await cls._on_world(
            world_id,
            "delete_documents",
            document_type,
            [str(one) for one in ids],
            parent_uuid,
            bool(approved_by),
            as_user=approved_by or as_user,
        )
        return result

    @classmethod
    @ability("vtt_get_setting")
    async def vtt_get_setting(
        cls, world_id: str, key: str, as_user: Optional[str] = None
    ) -> Any:
        return await cls._on_world(
            world_id, "game_setting", checked_setting(key), as_user=as_user
        )

    @classmethod
    @ability("vtt_set_setting")
    async def vtt_set_setting(
        cls,
        world_id: str,
        key: str,
        value: Any,
        as_user: Optional[str] = None,
        approved_by: Optional[str] = None,
    ) -> Any:
        return await cls._on_world(
            world_id,
            "set_game_setting",
            checked_setting(key),
            value,
            bool(approved_by),
            as_user=approved_by or as_user,
        )
