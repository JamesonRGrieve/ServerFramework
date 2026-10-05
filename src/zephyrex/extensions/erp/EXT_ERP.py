# SPDX-License-Identifier: AGPL-3.0-or-later
"""ERP data for the server runtime, read and written live in the ERP.

Each provider instance is one account in one ERP (an ERPNext site and the
user whose API key it holds). Everything that account can reach is
reachable here: every DocType the site describes, standard or custom, found
from the site's own metadata, with child tables nested inside their parent
document. Nothing is kept locally: every read goes to the ERP, and the
ERP's own validation and permissions decide every write.

An instance serves whoever may use it (``BLL_ERP.can_use``): an operator's
(root- or system-scoped) instance serves everyone; a user's or a team's
serves only those who can see it. The abilities below act for the user
``requester_id`` names, under that rule; ``BLL_ERP.ERPManager`` serves the
same operations on REST and GraphQL, and ``BLL_ERP.ERPWebhookManager`` takes
the ERP's signed webhooks and passes them on to outbound subscribers who
may use the instance they came from.

A save names the version it was based on (the document's ``modified``,
carried as ``updated_at`` and as its ETag) and is refused with 412 when the
document has since changed (see ``BLL_ERP.ERPDocuments``).
"""

from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Set

from fastapi import HTTPException

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import PermanentExternalError
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

ERP_REQUEST_TIMEOUT_SECONDS = 30.0

Document = Dict[str, Any]


class ERPConfigurationError(PermanentExternalError):
    """The instance lacks a setting it cannot work without, or holds one it
    cannot work with."""


class AbstractERPProvider(AbstractStaticProvider):
    """An ERP; each instance is one account in it. The operations take the
    instance and speak the ERP's wire protocol; access, versions and the
    schema lift are the extension's (``BLL_ERP``), the same for every ERP."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[Set[str]] = set()
    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    http_timeout_seconds: ClassVar[float] = ERP_REQUEST_TIMEOUT_SECONDS

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def required(cls, instance: ProviderInstanceModel, key: str) -> str:
        """A setting no call can go without."""
        value = cls.setting(instance, key)
        if not value:
            raise ERPConfigurationError(
                f"{cls.friendly_name} {key} is not configured", provider=cls.name
            )
        return str(value)

    @classmethod
    def cannot(cls, what: str) -> PermanentExternalError:
        return PermanentExternalError(
            f"{cls.friendly_name} cannot {what}", provider=cls.name
        )

    @classmethod
    def refusal_detail(cls, payload: Any) -> str:
        """The ERP's own reason in a refusal's body, where it gives one."""
        return ""

    # The operations. Every DocType is reached through these; none is
    # special-cased.

    @classmethod
    async def doctypes(cls, instance: ProviderInstanceModel) -> List[Document]:
        """Every DocType the account is shown: ``name``, ``module``,
        ``custom``, ``istable``, ``issingle``, ``is_submittable``."""
        raise cls.cannot("describe its DocTypes")

    @classmethod
    async def doctype_bundle(
        cls, instance: ProviderInstanceModel, doctype: str
    ) -> List[Document]:
        """The DocType's metadata, then that of each child table it holds."""
        raise cls.cannot("describe a DocType")

    @classmethod
    async def list_documents(
        cls,
        instance: ProviderInstanceModel,
        doctype: str,
        *,
        fields: Sequence[str],
        filters: Optional[Mapping[str, Any]],
        or_filters: Optional[Mapping[str, Any]],
        order_by: Optional[str],
        start: int,
        page_length: int,
    ) -> List[Document]:
        raise cls.cannot("list documents")

    @classmethod
    async def get_document(
        cls, instance: ProviderInstanceModel, doctype: str, name: str
    ) -> Document:
        raise cls.cannot("read a document")

    @classmethod
    async def create_document(
        cls, instance: ProviderInstanceModel, doctype: str, data: Mapping[str, Any]
    ) -> Document:
        raise cls.cannot("create a document")

    @classmethod
    async def update_document(
        cls,
        instance: ProviderInstanceModel,
        doctype: str,
        name: str,
        data: Mapping[str, Any],
        version: Optional[str],
    ) -> Document:
        """Save ``data`` onto the document. With a ``version`` the ERP itself
        refuses the save when the document is no longer at it."""
        raise cls.cannot("update a document")

    @classmethod
    async def delete_document(
        cls, instance: ProviderInstanceModel, doctype: str, name: str
    ) -> None:
        raise cls.cannot("delete a document")

    @classmethod
    async def submit_document(
        cls, instance: ProviderInstanceModel, document: Mapping[str, Any]
    ) -> Document:
        """Submit ``document`` as read: the ERP refuses it when the document
        has changed since."""
        raise cls.cannot("submit a document")

    @classmethod
    async def cancel_document(
        cls, instance: ProviderInstanceModel, doctype: str, name: str
    ) -> Document:
        raise cls.cannot("cancel a document")

    @classmethod
    def verified_webhook(
        cls, instance: ProviderInstanceModel, body: bytes, headers: Mapping[str, str]
    ) -> Optional[str]:
        """The webhook's signature when the instance signed ``body`` (headers
        keyed lower-case), else None."""
        return None


class EXT_ERP(AbstractStaticExtension):
    name: ClassVar[str] = "erp"
    friendly_name: ClassVar[str] = "ERP"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Every DocType of an ERPNext site, read and written live under the "
        "site's own validation and permissions, and its signed webhooks "
        "passed on to subscribers"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="federation",
                friendly_name="Federation",
                reason="DocTypes are lifted to models by the federation's "
                "OpenAPI importer",
            ),
            EXT_Dependency(
                name="webhooks",
                friendly_name="Webhooks",
                reason="ERP webhooks are passed on to outbound webhook subscribers",
            ),
        ]
    )
    _abilities: ClassVar[Set[str]] = {
        "erp_doctypes",
        "erp_schema",
        "erp_list",
        "erp_get",
        "erp_create",
        "erp_update",
        "erp_delete",
        "erp_submit",
        "erp_cancel",
    }

    @classmethod
    def _documents(cls, requester_id: str, provider_instance_id: str) -> Any:
        """The documents of the instance, for ``requester_id`` (404 when they
        may not use it)."""
        from zephyrex.extensions.erp.BLL_ERP import ERPDocuments
        from zephyrex.pydantic2.registry import ModelRegistry

        if not requester_id or not str(requester_id).strip():
            raise HTTPException(
                status_code=400, detail="erp: requester_id names the user acted for"
            )
        registry = ModelRegistry.attached()
        if registry is None:
            raise HTTPException(status_code=503, detail="erp: no running app to act in")
        return ERPDocuments.for_requester(registry, requester_id, provider_instance_id)

    @classmethod
    @ability("erp_doctypes")
    async def erp_doctypes(
        cls, requester_id: str, provider_instance_id: str
    ) -> List[Dict[str, Any]]:
        """Every DocType the instance's account is shown."""
        documents = cls._documents(requester_id, provider_instance_id)
        return [info.model_dump() for info in await documents.doctypes()]

    @classmethod
    @ability("erp_schema")
    async def erp_schema(
        cls, requester_id: str, provider_instance_id: str, doctype: str
    ) -> Dict[str, Any]:
        """A DocType's fields, child tables and JSON Schema, as the site
        describes it now."""
        documents = cls._documents(requester_id, provider_instance_id)
        described: Dict[str, Any] = (await documents.schema(doctype)).described()
        return described

    @classmethod
    @ability("erp_list")
    async def erp_list(
        cls,
        requester_id: str,
        provider_instance_id: str,
        doctype: str,
        fields: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
        or_filters: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
        start: int = 0,
        page_length: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """A page of a DocType's documents (their own fields; a document's
        child tables come with ``erp_get``)."""
        from zephyrex.extensions.erp.BLL_ERP import ListQuery

        documents = cls._documents(requester_id, provider_instance_id)
        found = await documents.list(
            ListQuery.checked(
                doctype, fields, filters, or_filters, order_by, start, page_length
            )
        )
        return [document.model_dump() for document in found]

    @classmethod
    @ability("erp_get")
    async def erp_get(
        cls, requester_id: str, provider_instance_id: str, doctype: str, name: str
    ) -> Dict[str, Any]:
        """One document with its child tables."""
        documents = cls._documents(requester_id, provider_instance_id)
        found: Dict[str, Any] = (await documents.get(doctype, name)).model_dump()
        return found

    @classmethod
    @ability("erp_create")
    async def erp_create(
        cls,
        requester_id: str,
        provider_instance_id: str,
        doctype: str,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """A new document, as the ERP saved it."""
        documents = cls._documents(requester_id, provider_instance_id)
        made: Dict[str, Any] = (await documents.create(doctype, data)).model_dump()
        return made

    @classmethod
    @ability("erp_update")
    async def erp_update(
        cls,
        requester_id: str,
        provider_instance_id: str,
        doctype: str,
        name: str,
        data: Dict[str, Any],
        if_match: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save ``data`` onto a document read at ``if_match`` (its
        ``updated_at``); 412 when it has changed since."""
        documents = cls._documents(requester_id, provider_instance_id)
        saved: Dict[str, Any] = (
            await documents.update(doctype, name, data, if_match)
        ).model_dump()
        return saved

    @classmethod
    @ability("erp_delete")
    async def erp_delete(
        cls,
        requester_id: str,
        provider_instance_id: str,
        doctype: str,
        name: str,
        if_match: Optional[str] = None,
    ) -> Dict[str, Any]:
        documents = cls._documents(requester_id, provider_instance_id)
        await documents.delete(doctype, name, if_match)
        return {"doctype": doctype, "name": name, "deleted": True}

    @classmethod
    @ability("erp_submit")
    async def erp_submit(
        cls,
        requester_id: str,
        provider_instance_id: str,
        doctype: str,
        name: str,
        if_match: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit a draft of a submittable DocType."""
        documents = cls._documents(requester_id, provider_instance_id)
        submitted: Dict[str, Any] = (
            await documents.submit(doctype, name, if_match)
        ).model_dump()
        return submitted

    @classmethod
    @ability("erp_cancel")
    async def erp_cancel(
        cls,
        requester_id: str,
        provider_instance_id: str,
        doctype: str,
        name: str,
        if_match: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cancel a submitted document."""
        documents = cls._documents(requester_id, provider_instance_id)
        cancelled: Dict[str, Any] = (
            await documents.cancel(doctype, name, if_match)
        ).model_dump()
        return cancelled
