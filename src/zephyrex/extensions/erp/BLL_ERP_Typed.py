# SPDX-License-Identifier: AGPL-3.0-or-later
"""The DocTypes of the operator's ERP instances as typed models.

Each enabled root- or system-scoped instance of an ERP provider is a
federated source (``federation.BLL_Federation_Typed``): when the app boots
its catalogue is read (every DocType the instance's account is shown, then
each parent DocType's bundle) and every parent DocType is served as a typed,
table-less model on REST (``/v1/federated/<namespace>/<doctype>/...``) and
GraphQL, alongside the generic document API (``BLL_ERP.ERPManager``), which
is unchanged. A child table is a nested type of its parents; a Single
DocType is read and saved, never listed, made or deleted. Submit and cancel
are the generic API's.

Only the operator's instances are federated: a user's or a team's DocTypes
never enter the shared schema. The catalogue is a snapshot: a DocType added
to the site later appears after a restart. An instance the site cannot be
reached for at boot is left out, logged with the reason; the generic API
serves it as ever.

Every typed operation goes through :class:`BLL_ERP.ERPDocuments` for the
requester, so it is held to exactly the generic API's rules: who may use the
instance (``can_use``, checked live: an instance disabled or rescoped since
boot answers 404), the DocType's live schema for every write, the
document's ``modified`` as its version (ETag; a stale save 412, an unnamed
one 428), the site's own permissions and reasons, and never a credential.

**Namespace.** ``<provider>_<first 8 hex digits of the instance id>``
(``erpnext_3f2a9c1d``): stable for the instance's life, whatever it is
renamed to, and distinct among instances.
"""

import asyncio
from typing import Any, Dict, List, Mapping, Optional, Sequence

from zephyrex.extensions.erp.BLL_ERP import (
    SYSTEM_FIELDS,
    ERPDocument,
    ERPDocuments,
    ERPInstance,
    ListQuery,
    _erp_provider_named,
    doctype_openapi,
    refusal,
)
from zephyrex.extensions.ExternalErrors import BaseExternalError
from zephyrex.extensions.federation.BLL_Federation_Typed import (
    AbstractFederatedSource,
    FederatedCall,
    FederatedQuery,
    FederatedSession,
    FederatedType,
    Operation,
    StaleRecord,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.lib.Preconditions import IfMatch, PreconditionFailed
from zephyrex.logic.BLL_Providers import (
    OPERATOR_SCOPES,
    ProviderInstanceModel,
    ProviderModel,
)

NAMESPACE_ID_DIGITS = 8
# DocType bundles read at once while the catalogue is read at boot.
BUNDLE_CONCURRENCY = 8
KEY_FIELD = "name"
# A typed document carries its version (its ``modified``) as ``updated_at``,
# as the generic API's documents do; it is the document's ETag.
VERSION_FIELD = "updated_at"
SINGLE_OPERATIONS = frozenset({Operation.GET, Operation.UPDATE})
_SCHEMA_REF = "#/components/schemas/"
_DEFINITION_REF = "#/$defs/"


def instance_namespace(provider_name: str, instance_id: str) -> str:
    """The instance's namespace: ``<provider>_<8 hex digits of its id>``."""
    digits = "".join(ch for ch in str(instance_id).lower() if ch.isalnum())
    return f"{provider_name}_{digits[:NAMESPACE_ID_DIGITS]}"


def _definition_refs(schema: Any, definitions: Mapping[str, str]) -> Any:
    """``schema`` with its component references naming definitions."""
    if isinstance(schema, Mapping):
        ref = schema.get("$ref")
        if isinstance(ref, str) and ref.startswith(_SCHEMA_REF):
            return {"$ref": _DEFINITION_REF + definitions[ref[len(_SCHEMA_REF) :]]}
        return {
            key: _definition_refs(value, definitions) for key, value in schema.items()
        }
    if isinstance(schema, list):
        return [_definition_refs(value, definitions) for value in schema]
    return schema


def doctype_json_schema(bundle: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The bundle's DocType (then its child tables) as one JSON Schema: the
    DocType a record, each child DocType a definition, the fields only the
    site sets read-only (and a document's ``idx``: only a child row's is
    written)."""
    components: Dict[str, Any] = doctype_openapi(bundle)["components"]["schemas"]
    doctypes = {component["title"]: name for name, component in components.items()}
    definitions = {name: doctype for doctype, name in doctypes.items()}
    root = doctypes[str(bundle[0].get("name"))]
    tables = {str(meta.get("name")) for meta in bundle if meta.get("istable")}

    def described(component: Mapping[str, Any]) -> Dict[str, Any]:
        read_only = SYSTEM_FIELDS | (
            frozenset() if component["title"] in tables else {"idx"}
        )
        properties = {
            name: (
                {**_definition_refs(value, definitions), "readOnly": True}
                if name in read_only
                else _definition_refs(value, definitions)
            )
            for name, value in component["properties"].items()
        }
        return {"type": "object", "title": component["title"], "properties": properties}

    record = described(components[root])
    record["properties"][VERSION_FIELD] = {
        "type": "string",
        "readOnly": True,
        "description": "The document's version (its modified): what If-Match names",
    }
    return {
        **record,
        "$defs": {
            definitions[name]: described(component)
            for name, component in components.items()
            if name != root
        },
    }


def typed_record(document: ERPDocument) -> Dict[str, Any]:
    """A document as its typed record: its fields, and its version as
    ``updated_at``."""
    return {**document.data, VERSION_FIELD: document.updated_at}


class ERPTypedSession(FederatedSession):
    """The requester's documents in one instance, as the generic API
    serves them (:class:`ERPDocuments`)."""

    def __init__(self, documents: ERPDocuments) -> None:
        self.documents = documents

    async def list(
        self, type_name: str, query: FederatedQuery
    ) -> Sequence[Mapping[str, Any]]:
        checked = ListQuery.checked(
            type_name,
            None,
            query.filters,
            None,
            query.order_by,
            query.start,
            query.page_length,
        )
        return [
            typed_record(document) for document in await self.documents.list(checked)
        ]

    async def get(self, type_name: str, key: Any) -> Mapping[str, Any]:
        return typed_record(await self.documents.get(type_name, key))

    async def create(
        self, type_name: str, data: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return typed_record(await self.documents.create(type_name, data))

    async def update(
        self,
        type_name: str,
        key: Any,
        data: Mapping[str, Any],
        expected: Optional[IfMatch],
    ) -> Mapping[str, Any]:
        try:
            saved = await self.documents.update_expecting(
                type_name, key, data, expected
            )
        except PreconditionFailed as stale:
            raise StaleRecord(typed_record(stale.extra["current"])) from None
        return typed_record(saved)

    async def delete(
        self, type_name: str, key: Any, expected: Optional[IfMatch]
    ) -> None:
        try:
            await self.documents.delete_expecting(type_name, key, expected)
        except PreconditionFailed as stale:
            raise StaleRecord(typed_record(stale.extra["current"])) from None


class ERPFederatedSource(AbstractFederatedSource):
    """One operator instance of an ERP provider."""

    def __init__(self, instance: ERPInstance) -> None:
        self.instance = instance
        self._namespace = instance_namespace(instance.provider.name, instance.id)

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def reference(self) -> Optional[str]:
        """The instance's ``provider_instance_id``."""
        return self.instance.id

    @property
    def title(self) -> str:
        return f"{self.instance.provider.friendly_name} {self.instance.model.name}"

    async def _type(self, info: Mapping[str, Any]) -> Optional[FederatedType]:
        """A parent DocType as a federated type; None (logged) for one the
        site would not describe."""
        doctype = str(info.get("name"))
        provider = self.instance.provider
        try:
            bundle = await provider.doctype_bundle(self.instance.model, doctype)
        except BaseExternalError as exc:
            logger.warning(
                "%s: DocType %s is not served: %s",
                self.title,
                doctype,
                refusal(provider, exc).detail,
            )
            return None
        return FederatedType(
            name=doctype,
            json_schema=doctype_json_schema(bundle),
            key_field=KEY_FIELD,
            version_field=VERSION_FIELD,
            operations=(
                SINGLE_OPERATIONS if info.get("issingle") else frozenset(Operation)
            ),
            description=f"{info.get('module') or 'ERP'} DocType {doctype}",
        )

    async def catalogue(self) -> Sequence[FederatedType]:
        """Every parent DocType the instance's account is shown, read as
        the instance's account (no requester: the app is booting)."""
        provider = self.instance.provider
        try:
            rows = await provider.doctypes(self.instance.model)
        except BaseExternalError as exc:
            raise refusal(provider, exc) from exc
        parents = [row for row in rows if not row.get("istable")]
        gate = asyncio.Semaphore(BUNDLE_CONCURRENCY)

        async def described(info: Mapping[str, Any]) -> Optional[FederatedType]:
            async with gate:
                return await self._type(info)

        found = await asyncio.gather(*(described(info) for info in parents))
        return [federated for federated in found if federated is not None]

    def session(self, call: FederatedCall) -> FederatedSession:
        return ERPTypedSession(
            ERPDocuments.for_requester(
                call.model_registry, call.requester_id, self.instance.id
            )
        )


def operator_instances(model_registry: Any) -> List[ERPInstance]:
    """The enabled, undeleted root- and system-scoped instances of the
    extension's providers, oldest first."""
    from zephyrex.extensions.erp.EXT_ERP import EXT_ERP

    base, root = model_registry.DB.manager.Base, env("ROOT_ID")
    provider_db = ProviderModel.DB(base)
    names = [provider.name for provider in EXT_ERP.providers]
    providers: List[ProviderModel] = provider_db.list(
        requester_id=root,
        model_registry=model_registry,
        return_type="dto",
        override_dto=ProviderModel,
        filters=[provider_db.name.in_(names)],
    )
    by_id = {str(provider.id): provider for provider in providers}
    if not by_id:
        return []
    instance_db = ProviderInstanceModel.DB(base)
    instances: List[ProviderInstanceModel] = instance_db.list(
        requester_id=root,
        model_registry=model_registry,
        return_type="dto",
        override_dto=ProviderInstanceModel,
        filters=[
            instance_db.provider_id.in_(list(by_id)),
            instance_db.scope.in_(sorted(OPERATOR_SCOPES)),
            instance_db.deleted_at.is_(None),
        ],
    )
    found: List[ERPInstance] = []
    for instance in sorted(
        instances, key=lambda row: (str(row.created_at), str(row.id))
    ):
        provider = _erp_provider_named(by_id[str(instance.provider_id)].name)
        if instance.enabled is not False and provider is not None:
            found.append(ERPInstance(model=instance, provider=provider))
    return found


def erp_federated_sources(model_registry: Any) -> List[ERPFederatedSource]:
    """The extension's federated sources: its operator instances."""
    return [
        ERPFederatedSource(instance) for instance in operator_instances(model_registry)
    ]
