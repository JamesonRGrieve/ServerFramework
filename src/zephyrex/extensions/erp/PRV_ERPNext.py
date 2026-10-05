# SPDX-License-Identifier: AGPL-3.0-or-later
"""ERPNext (any Frappe site), through Frappe's REST API, as the user whose
API key and secret the instance holds (``Authorization: token
<api_key>:<api_secret>``, ``frappe.auth.validate_auth_via_api_keys``).

What is called, as Frappe (version 15) defines it:

- ``GET /api/resource/DocType``: the DocTypes the user is shown, paged with
  ``limit_start``/``limit_page_length`` (``frappe.api.v1.document_list``,
  answering ``{"data": [...]}``);
- ``GET /api/method/frappe.desk.form.load.getdoctype?doctype=X``: X's
  metadata with custom fields merged in, then each of its child tables'
  (``get_meta_bundle``), answering ``{"docs": [...]}``;
- ``/api/resource/<DocType>``: ``GET`` lists (``fields``, ``filters``,
  ``or_filters`` as JSON, ``order_by``, ``limit_start``,
  ``limit_page_length``), ``POST`` inserts; ``/api/resource/<DocType>/<name>``:
  ``GET`` reads the document with its child tables, ``PUT`` saves onto it,
  ``DELETE`` deletes it (202);
- ``POST /api/method/frappe.client.submit`` with ``{"doc": ...}`` and
  ``POST /api/method/frappe.client.cancel`` with ``{"doctype", "name"}``,
  answering ``{"message": <document>}``: the document's own ``submit`` and
  ``cancel``, so a DocType that queues a large submission still does.

A save is held to the version it was read at by sending that ``modified``
with it: ``update_doc`` applies the body onto the document it loaded
``for_update``, ``Document.set_user_and_timestamp`` takes the body's
``modified`` as ``_original_modified``, and ``check_if_latest`` raises
``TimestampMismatchError`` (HTTP 417) when the stored one differs. A
submission sends the document as read, ``modified`` included, and is
checked the same way.

Webhooks are verified as Frappe signs them
(``frappe.integrations.doctype.webhook.webhook.get_webhook_headers``):
``X-Frappe-Webhook-Signature`` is the base64 HMAC-SHA256 of the exact body
sent, keyed by the Webhook's secret.
"""

import base64
import hashlib
import hmac
import json
import re
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Set, Tuple
from urllib.parse import quote

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.erp.EXT_ERP import (
    AbstractERPProvider,
    Document,
    ERPConfigurationError,
)
from zephyrex.extensions.ExternalErrors import TransientExternalError
from zephyrex.lib.Credentials import register_secret
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

WEBHOOK_SIGNATURE_HEADER = "X-Frappe-Webhook-Signature"
# A shorter webhook secret is open to guessing from one captured delivery.
MIN_WEBHOOK_SECRET_LENGTH = 16
# The DocType catalogue is read in pages this long, at most this many.
DOCTYPE_PAGE_LENGTH = 500
MAX_DOCTYPE_PAGES = 40
DOCTYPE_FIELDS = (
    "name",
    "module",
    "custom",
    "istable",
    "issingle",
    "is_submittable",
)
# How much of the ERP's reason for a refusal is passed on.
MAX_REFUSAL_CHARACTERS = 300
_TAG = re.compile(r"<[^>]+>")
_EXC_TYPE = re.compile(r'"exc_type"\s*:\s*"([A-Za-z_][A-Za-z0-9_]*)"')


def webhook_signature(secret: str, body: bytes) -> str:
    """The ``X-Frappe-Webhook-Signature`` Frappe sends with ``body``."""
    digest = hmac.new(secret.encode("utf8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _server_messages(body: Mapping[str, Any]) -> List[str]:
    """The messages of ``_server_messages``: a JSON list of JSON objects,
    each with a ``message`` (``frappe.utils.response.make_logs``)."""
    raw = body.get("_server_messages")
    if not isinstance(raw, str):
        return []
    try:
        entries = json.loads(raw)
    except ValueError:
        return []
    messages: List[str] = []
    for entry in entries if isinstance(entries, list) else []:
        try:
            decoded = json.loads(entry) if isinstance(entry, str) else entry
        except ValueError:
            decoded = entry
        message = decoded.get("message") if isinstance(decoded, dict) else decoded
        if isinstance(message, str) and message.strip():
            messages.append(message)
    return messages


def frappe_refusal(payload: Any) -> str:
    """Frappe's reason in an error body: its messages, else the exception's
    type. The body may be cut short, so it is read as text when it is no
    longer JSON."""
    text = str(payload or "")
    try:
        body = json.loads(text)
    except ValueError:
        body = None
    reasons: List[str] = []
    if isinstance(body, dict):
        reasons = _server_messages(body)
        if not reasons and isinstance(body.get("exc_type"), str):
            reasons = [body["exc_type"]]
    if not reasons:
        reasons = _EXC_TYPE.findall(text)[:1]
    cleaned = "; ".join(_TAG.sub("", reason).strip() for reason in reasons)
    return cleaned[:MAX_REFUSAL_CHARACTERS]


class PRV_ERPNext(AbstractERPProvider):
    name: ClassVar[str] = "erpnext"
    friendly_name: ClassVar[str] = "ERPNext"
    description: ClassVar[str] = "An account on an ERPNext (Frappe) site"
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
    # No environment fallbacks: a user's or team's instance without its own
    # key must not act with the operator's.
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting("base_url", "The site's address (https://erp.example.com)"),
        InstanceSetting(
            "api_key",
            "API key of the ERPNext user the instance acts as",
            secret=True,
            field="api_key",
        ),
        InstanceSetting("api_secret", "That user's API secret", secret=True),
        InstanceSetting(
            "webhook_secret",
            f"The Webhook Secret the site signs this instance's webhooks with "
            f"(at least {MIN_WEBHOOK_SECRET_LENGTH} characters)",
            secret=True,
        ),
    )

    @classmethod
    def refusal_detail(cls, payload: Any) -> str:
        return frappe_refusal(payload)

    @classmethod
    def _base(cls, instance: ProviderInstanceModel) -> str:
        base = cls.required(instance, "base_url").rstrip("/")
        if not base.startswith(("https://", "http://")):
            raise ERPConfigurationError(
                "ERPNext base_url is an http(s) address", provider=cls.name
            )
        return base

    @classmethod
    def _headers(cls, instance: ProviderInstanceModel) -> Dict[str, str]:
        api_key = cls.required(instance, "api_key")
        api_secret = cls.required(instance, "api_secret")
        # An upstream error that echoes the credential is scrubbed.
        register_secret(api_secret)
        return {
            "Authorization": f"token {api_key}:{api_secret}",
            "Accept": "application/json",
        }

    @classmethod
    async def _call(
        cls,
        instance: ProviderInstanceModel,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        body: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        answer = await cls.http().request(
            method,
            cls._base(instance) + path,
            headers=cls._headers(instance),
            params=params,
            json=dict(body) if body is not None else None,
        )
        if not isinstance(answer, dict):
            raise TransientExternalError(
                "ERPNext answered without a JSON object", provider=cls.name
            )
        return answer

    @classmethod
    def _member(cls, answer: Mapping[str, Any], key: str) -> Any:
        if key not in answer:
            raise TransientExternalError(
                f"ERPNext answered without {key!r}", provider=cls.name
            )
        return answer[key]

    @classmethod
    def _document(cls, answer: Mapping[str, Any], key: str) -> Document:
        document = cls._member(answer, key)
        if not isinstance(document, dict):
            raise TransientExternalError(
                "ERPNext answered without a document", provider=cls.name
            )
        return document

    @classmethod
    def _documents(cls, answer: Mapping[str, Any], key: str) -> List[Document]:
        documents = cls._member(answer, key)
        if not isinstance(documents, list) or not all(
            isinstance(document, dict) for document in documents
        ):
            raise TransientExternalError(
                "ERPNext answered without a list of documents", provider=cls.name
            )
        return documents

    @staticmethod
    def _resource(doctype: str, name: Optional[str] = None) -> str:
        path = f"/api/resource/{quote(doctype, safe='')}"
        return path if name is None else f"{path}/{quote(name, safe='')}"

    @classmethod
    async def doctypes(cls, instance: ProviderInstanceModel) -> List[Document]:
        found: List[Document] = []
        for page in range(MAX_DOCTYPE_PAGES):
            rows = cls._documents(
                await cls._call(
                    instance,
                    "GET",
                    "/api/resource/DocType",
                    params={
                        "fields": json.dumps(list(DOCTYPE_FIELDS)),
                        "order_by": "name asc",
                        "limit_start": page * DOCTYPE_PAGE_LENGTH,
                        "limit_page_length": DOCTYPE_PAGE_LENGTH,
                    },
                ),
                "data",
            )
            found.extend(rows)
            if len(rows) < DOCTYPE_PAGE_LENGTH:
                break
        return found

    @classmethod
    async def doctype_bundle(
        cls, instance: ProviderInstanceModel, doctype: str
    ) -> List[Document]:
        docs = cls._documents(
            await cls._call(
                instance,
                "GET",
                "/api/method/frappe.desk.form.load.getdoctype",
                params={"doctype": doctype},
            ),
            "docs",
        )
        if not docs or docs[0].get("name") != doctype:
            raise TransientExternalError(
                f"ERPNext did not describe {doctype!r}", provider=cls.name
            )
        return docs

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
        params: Dict[str, Any] = {
            "fields": json.dumps(list(fields)),
            "limit_start": start,
            "limit_page_length": page_length,
        }
        if filters:
            params["filters"] = json.dumps(dict(filters))
        if or_filters:
            params["or_filters"] = json.dumps(dict(or_filters))
        if order_by:
            params["order_by"] = order_by
        return cls._documents(
            await cls._call(instance, "GET", cls._resource(doctype), params=params),
            "data",
        )

    @classmethod
    async def get_document(
        cls, instance: ProviderInstanceModel, doctype: str, name: str
    ) -> Document:
        return cls._document(
            await cls._call(instance, "GET", cls._resource(doctype, name)), "data"
        )

    @classmethod
    async def create_document(
        cls, instance: ProviderInstanceModel, doctype: str, data: Mapping[str, Any]
    ) -> Document:
        return cls._document(
            await cls._call(instance, "POST", cls._resource(doctype), body=data),
            "data",
        )

    @classmethod
    async def update_document(
        cls,
        instance: ProviderInstanceModel,
        doctype: str,
        name: str,
        data: Mapping[str, Any],
        version: Optional[str],
    ) -> Document:
        body = dict(data) if version is None else {**data, "modified": version}
        return cls._document(
            await cls._call(instance, "PUT", cls._resource(doctype, name), body=body),
            "data",
        )

    @classmethod
    async def delete_document(
        cls, instance: ProviderInstanceModel, doctype: str, name: str
    ) -> None:
        await cls._call(instance, "DELETE", cls._resource(doctype, name))

    @classmethod
    async def submit_document(
        cls, instance: ProviderInstanceModel, document: Mapping[str, Any]
    ) -> Document:
        return cls._document(
            await cls._call(
                instance,
                "POST",
                "/api/method/frappe.client.submit",
                body={"doc": dict(document)},
            ),
            "message",
        )

    @classmethod
    async def cancel_document(
        cls, instance: ProviderInstanceModel, doctype: str, name: str
    ) -> Document:
        return cls._document(
            await cls._call(
                instance,
                "POST",
                "/api/method/frappe.client.cancel",
                body={"doctype": doctype, "name": name},
            ),
            "message",
        )

    @classmethod
    def verified_webhook(
        cls, instance: ProviderInstanceModel, body: bytes, headers: Mapping[str, str]
    ) -> Optional[str]:
        secret = cls.setting(instance, "webhook_secret")
        if not secret or len(secret) < MIN_WEBHOOK_SECRET_LENGTH:
            return None
        given = headers.get(WEBHOOK_SIGNATURE_HEADER.lower(), "")
        expected = webhook_signature(secret, body)
        if not given or not hmac.compare_digest(
            expected.encode("ascii"), given.encode("utf-8", "replace")
        ):
            return None
        return given
