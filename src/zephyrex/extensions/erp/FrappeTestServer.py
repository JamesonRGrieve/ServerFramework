# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real Frappe REST server for the tests, in process, on a loopback port.

No ERPNext site runs where the tests do, so this speaks the subset of
Frappe's REST API (version 15) the ERPNext provider uses, as Frappe answers
it, so the provider runs unchanged against it:

- API-key authentication (``Authorization: token <key>:<secret>``,
  ``frappe.auth.validate_auth_via_api_keys``): a bad pair is 401
  ``AuthenticationError``; a DocType the account lacks the right to is 403
  ``PermissionError``;
- ``GET /api/resource/DocType`` (``frappe.api.v1.document_list``) and
  ``GET /api/method/frappe.desk.form.load.getdoctype`` (``{"docs": [the
  DocType, then each child table's]}``);
- ``/api/resource/<DocType>[/<name>]``: list with ``fields``, dictionary
  ``filters``/``or_filters``, ``order_by`` and paging (default sort
  ``modified desc``, page 20); read with child tables nested; insert, with
  mandatory fields checked (417 ``MandatoryError``) and child rows named and
  linked to their parent; save (``update_doc``: the body applied onto the
  stored document, its ``modified`` taken as the version the save is based
  on, 417 ``TimestampMismatchError`` when the stored one differs, 417
  ``UpdateAfterSubmitError`` for a submitted document's fields that do not
  allow it, 417 for a cancelled one); delete (202; 417 for a submitted
  document, ``frappe.model.delete_doc``);
- ``frappe.client.submit`` (the document as sent, held to its ``modified``)
  and ``frappe.client.cancel`` (the stored document, 417
  ``DocstatusTransitionError`` from a draft).

Errors carry ``exc_type`` and ``_server_messages`` as
``frappe.utils.response.report_error`` and ``make_logs`` build them.
:func:`webhook_request` is a webhook as a Frappe Webhook posts it: the body
``frappe.as_json`` writes, signed as
``frappe.integrations.doctype.webhook.webhook.get_webhook_headers`` signs
it.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple, Type
from urllib.parse import parse_qs, unquote, urlsplit

LOOPBACK = "127.0.0.1"
WEBHOOK_SIGNATURE_HEADER = "X-Frappe-Webhook-Signature"
DEFAULT_PAGE_LENGTH = 20
TABLE_TYPES = ("Table", "Table MultiSelect")
NO_VALUE_TYPES = (
    "Section Break",
    "Column Break",
    "Tab Break",
    "HTML",
    "Button",
    "Image",
    "Fold",
    "Heading",
)


class FrappeError(Exception):
    """An exception Frappe raises, with the HTTP status it answers."""

    def __init__(self, status: int, exc_type: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.exc_type = exc_type
        self.message = message


def _validation(exc_type: str, message: str) -> FrappeError:
    """A ``frappe.ValidationError`` (or subclass): HTTP 417."""
    return FrappeError(417, exc_type, message)


def as_json(obj: Any) -> str:
    """``frappe.as_json``: indent 1, sorted keys, ``": "``."""
    return json.dumps(obj, indent=1, sort_keys=True, separators=(",", ": "))


def webhook_signature(secret: str, body: bytes) -> str:
    return base64.b64encode(
        hmac.new(secret.encode("utf8"), body, hashlib.sha256).digest()
    ).decode()


def webhook_request(
    secret: Optional[str], data: Mapping[str, Any]
) -> Tuple[bytes, Dict[str, str]]:
    """The body and headers a Frappe Webhook posts for ``data``
    (``enqueue_webhook``: ``frappe.as_json(data)``), signed with ``secret``
    (unsigned when None)."""
    body = as_json(dict(data)).encode("utf8")
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers[WEBHOOK_SIGNATURE_HEADER] = webhook_signature(secret, body)
    return body, headers


def field_of(
    fieldname: str,
    fieldtype: str = "Data",
    *,
    label: Optional[str] = None,
    options: Optional[str] = None,
    reqd: int = 0,
    allow_on_submit: int = 0,
    read_only: int = 0,
) -> Dict[str, Any]:
    """A DocField as ``getdoctype`` describes it."""
    return {
        "fieldname": fieldname,
        "fieldtype": fieldtype,
        "label": label or fieldname.replace("_", " ").title(),
        "options": options,
        "reqd": reqd,
        "allow_on_submit": allow_on_submit,
        "read_only": read_only,
    }


def doctype_of(
    name: str,
    fields: List[Dict[str, Any]],
    *,
    module: str = "Core",
    custom: int = 0,
    istable: int = 0,
    issingle: int = 0,
    is_submittable: int = 0,
    naming_prefix: Optional[str] = None,
) -> Dict[str, Any]:
    """A DocType's metadata as ``getdoctype`` answers it."""
    return {
        "doctype": "DocType",
        "name": name,
        "module": module,
        "custom": custom,
        "istable": istable,
        "issingle": issingle,
        "is_submittable": is_submittable,
        "autoname": f"{naming_prefix or ''.join(w[0] for w in name.split())}-.#####",
        "fields": fields,
    }


@dataclass
class Account:
    """A Frappe user with an API key and secret, and its rights per
    DocType (``"DocType"`` read lists the DocTypes)."""

    user: str
    api_secret: str
    rights: Dict[str, Set[str]] = field(default_factory=dict)

    def may(self, right: str, doctype: str) -> bool:
        return right in self.rights.get(doctype, set())


class FrappeSite:
    """The site's DocTypes, documents and accounts."""

    def __init__(self) -> None:
        self.doctypes: Dict[str, Dict[str, Any]] = {}
        self.documents: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.accounts: Dict[str, Account] = {}
        self.counters: Dict[str, int] = {}
        self.lock = threading.Lock()
        self._clock = datetime(2026, 1, 1, 9, 0, 0)

    def add_doctype(self, meta: Dict[str, Any]) -> None:
        self.doctypes[meta["name"]] = meta
        self.documents.setdefault(meta["name"], {})

    def add_account(self, api_key: str, account: Account) -> Tuple[str, str]:
        self.accounts[api_key] = account
        return api_key, account.api_secret

    def now(self) -> str:
        """The next ``modified`` (``str(datetime)`` with microseconds, as
        Frappe's JSON handler writes a datetime): every save moves it on."""
        self._clock += timedelta(microseconds=1001)
        return str(self._clock)

    def meta(self, doctype: str) -> Dict[str, Any]:
        meta = self.doctypes.get(doctype)
        if meta is None:
            raise FrappeError(404, "DoesNotExistError", f"DocType {doctype} not found")
        return meta

    def value_fields(self, doctype: str) -> List[Dict[str, Any]]:
        return [
            f
            for f in self.meta(doctype)["fields"]
            if f["fieldtype"] not in NO_VALUE_TYPES
        ]

    def stored(self, doctype: str, name: str) -> Dict[str, Any]:
        self.meta(doctype)
        document = self.documents[doctype].get(name)
        if document is None:
            raise FrappeError(404, "DoesNotExistError", f"{doctype} {name} not found")
        return document


def _missing_mandatory(
    site: FrappeSite, doctype: str, doc: Mapping[str, Any]
) -> List[str]:
    missing = []
    for f in site.value_fields(doctype):
        if not f.get("reqd"):
            continue
        value = doc.get(f["fieldname"])
        if value in (None, "", []):
            missing.append(f"Error: Value missing for {doctype}: {f['label']}")
    return missing


class FrappeServer:
    """Serves a :class:`FrappeSite` on a loopback port while open."""

    def __init__(self, site: FrappeSite) -> None:
        self.site = site
        self.requests: List[Dict[str, Any]] = []
        # Another client's change that lands just before the next request
        # to (method, path): how a test races a save.
        self._before: Dict[Tuple[str, str], Callable[[FrappeSite], None]] = {}
        self._server = ThreadingHTTPServer((LOOPBACK, 0), self._handler())
        self.port = self._server.server_address[1]
        self.host = f"{LOOPBACK}:{self.port}"
        self.base_url = f"http://{self.host}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "FrappeServer":
        self._thread.start()
        return self

    def before(
        self, method: str, path: str, change: Callable[[FrappeSite], None]
    ) -> None:
        """Make ``change`` to the site, once, just before the next ``method``
        request to ``path`` is answered."""
        self._before[(method, path)] = change

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self._server.shutdown()
        self._server.server_close()

    # --- dispatch ---------------------------------------------------------

    def _handler(self) -> Type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                parts = urlsplit(self.path)
                server.requests.append(
                    {
                        "method": self.command,
                        "path": unquote(parts.path),
                        "query": {k: v[0] for k, v in parse_qs(parts.query).items()},
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": raw,
                    }
                )
                try:
                    status, answer = server.answer(
                        self.command,
                        parts.path,
                        {k: v[0] for k, v in parse_qs(parts.query).items()},
                        self.headers.get("Authorization", ""),
                        json.loads(raw) if raw else {},
                    )
                except FrappeError as error:
                    status, answer = error.status, {
                        "exc_type": error.exc_type,
                        "_server_messages": json.dumps(
                            [json.dumps({"message": error.message, "indicator": "red"})]
                        ),
                    }
                body = as_json(answer).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_PUT = do_DELETE = _serve

            def log_message(self, *args: Any) -> None:
                pass

        return Handler

    def _account(self, authorization: str) -> Account:
        kind, _, token = authorization.partition(" ")
        api_key, _, api_secret = token.partition(":")
        account = self.site.accounts.get(api_key)
        if (
            kind.lower() != "token"
            or account is None
            or not hmac.compare_digest(account.api_secret, api_secret)
        ):
            raise FrappeError(401, "AuthenticationError", "Not permitted")
        return account

    def answer(
        self,
        method: str,
        path: str,
        query: Dict[str, str],
        authorization: str,
        body: Dict[str, Any],
    ) -> Tuple[int, Any]:
        account = self._account(authorization)
        segments = [unquote(s) for s in path.split("/") if s]
        with self.site.lock:
            change = self._before.pop((method, unquote(path)), None)
            if change is not None:
                change(self.site)
            if segments[:2] == ["api", "method"] and len(segments) == 3:
                return self._method(segments[2], method, query, body, account)
            if segments[:2] == ["api", "resource"] and len(segments) in (3, 4):
                doctype = segments[2]
                name = segments[3] if len(segments) == 4 else None
                return self._resource(method, doctype, name, query, body, account)
        raise FrappeError(404, "DoesNotExistError", "Not found")

    def _require(self, account: Account, right: str, doctype: str) -> None:
        if not account.may(right, doctype):
            raise FrappeError(403, "PermissionError", f"No permission for {doctype}")

    # --- /api/method -------------------------------------------------------

    def _method(
        self,
        name: str,
        method: str,
        query: Dict[str, str],
        body: Dict[str, Any],
        account: Account,
    ) -> Tuple[int, Any]:
        if name == "frappe.desk.form.load.getdoctype" and method == "GET":
            meta = self.site.meta(query.get("doctype", ""))
            children = [
                self.site.meta(f["options"])
                for f in meta["fields"]
                if f["fieldtype"] in TABLE_TYPES
            ]
            return 200, {"docs": [meta, *children], "user_settings": "{}"}
        if name == "frappe.client.submit" and method in ("POST", "PUT"):
            return 200, {"message": self._submit(dict(body["doc"]), account)}
        if name == "frappe.client.cancel" and method in ("POST", "PUT"):
            return 200, {
                "message": self._cancel(body["doctype"], body["name"], account)
            }
        raise FrappeError(404, "DoesNotExistError", f"Method {name} not found")

    # --- /api/resource -----------------------------------------------------

    def _resource(
        self,
        method: str,
        doctype: str,
        name: Optional[str],
        query: Dict[str, str],
        body: Dict[str, Any],
        account: Account,
    ) -> Tuple[int, Any]:
        if doctype == "DocType" and name is None and method == "GET":
            self._require(account, "read", "DocType")
            return 200, {"data": self._page(list(self.site.doctypes.values()), query)}
        meta = self.site.meta(doctype)
        if name is None and method == "GET":
            self._require(account, "read", doctype)
            if meta["istable"]:
                raise FrappeError(403, "PermissionError", "Parent DocType required")
            return 200, {"data": self._list(doctype, query)}
        if name is None and method == "POST":
            self._require(account, "create", doctype)
            return 200, {"data": self._insert(doctype, body, account)}
        if name is not None and method == "GET":
            self._require(account, "read", doctype)
            return 200, {"data": copy.deepcopy(self.site.stored(doctype, name))}
        if name is not None and method == "PUT":
            self._require(account, "write", doctype)
            return 200, {"data": self._update(doctype, name, body, account)}
        if name is not None and method == "DELETE":
            self._require(account, "delete", doctype)
            document = self.site.stored(doctype, name)
            if meta["is_submittable"] and document["docstatus"] == 1:
                raise _validation(
                    "ValidationError",
                    f"{doctype} {name}: Submitted Record cannot be deleted. "
                    f"You must Cancel it first.",
                )
            del self.site.documents[doctype][name]
            return 202, {"data": "ok"}
        raise FrappeError(404, "DoesNotExistError", "Not found")

    def _page(self, rows: List[Dict[str, Any]], query: Dict[str, str]) -> List[Any]:
        fields = json.loads(query.get("fields") or '["name"]')
        start = int(query.get("limit_start") or 0)
        length = int(query.get("limit_page_length") or DEFAULT_PAGE_LENGTH)
        ordered = sorted(rows, key=lambda row: str(row.get("name")))
        page = ordered[start : start + length] if length else ordered[start:]
        if "*" in fields:
            return [copy.deepcopy(row) for row in page]
        return [{f: row.get(f) for f in fields} for row in page]

    def _matches(
        self, doctype: str, document: Mapping[str, Any], filters: Mapping[str, Any]
    ) -> List[bool]:
        known = {f["fieldname"] for f in self.site.value_fields(doctype)} | set(
            document
        )
        results = []
        for fieldname, condition in filters.items():
            if fieldname not in known:
                raise _validation(
                    "DataError", f"Field not permitted in query: {fieldname}"
                )
            operator, wanted = (
                (condition[0], condition[1])
                if isinstance(condition, list)
                else ("=", condition)
            )
            value = document.get(fieldname)
            tests: Dict[str, Callable[[], bool]] = {
                "=": lambda: value == wanted,
                "!=": lambda: value != wanted,
                ">": lambda: value is not None and value > wanted,
                "<": lambda: value is not None and value < wanted,
                ">=": lambda: value is not None and value >= wanted,
                "<=": lambda: value is not None and value <= wanted,
                "like": lambda: str(wanted).strip("%").lower()
                in str(value or "").lower(),
                "in": lambda: value in wanted,
                "not in": lambda: value not in wanted,
            }
            if operator not in tests:
                raise _validation(
                    "ValidationError", f"Operator {operator} not supported"
                )
            results.append(tests[operator]())
        return results

    def _list(self, doctype: str, query: Dict[str, str]) -> List[Any]:
        filters = json.loads(query.get("filters") or "{}")
        or_filters = json.loads(query.get("or_filters") or "{}")
        tables = {
            f["fieldname"]
            for f in self.site.value_fields(doctype)
            if f["fieldtype"] in TABLE_TYPES
        }
        found = []
        for document in self.site.documents[doctype].values():
            if not all(self._matches(doctype, document, filters)):
                continue
            if or_filters and not any(self._matches(doctype, document, or_filters)):
                continue
            found.append({k: v for k, v in document.items() if k not in tables})
        for clause in reversed((query.get("order_by") or "modified desc").split(",")):
            fieldname, _, direction = clause.strip().partition(" ")
            found.sort(
                key=lambda row: str(row.get(fieldname) or ""),
                reverse=direction.lower() == "desc",
            )
        fields = json.loads(query.get("fields") or '["name"]')
        start = int(query.get("limit_start") or 0)
        length = int(query.get("limit_page_length") or DEFAULT_PAGE_LENGTH)
        page = found[start : start + length]
        if "*" in fields:
            return page
        known = {f["fieldname"] for f in self.site.value_fields(doctype)} | {
            "name",
            "owner",
            "creation",
            "modified",
            "modified_by",
            "docstatus",
            "idx",
        }
        unknown = [f for f in fields if f not in known]
        if unknown:
            raise _validation(
                "DataError", f"Field not permitted in query: {unknown[0]}"
            )
        return [{f: row.get(f) for f in fields} for row in page]

    # --- saving -------------------------------------------------------------

    def _children(
        self, doctype: str, document: Dict[str, Any], account: Account, moment: str
    ) -> None:
        """Name new child rows and link every row to its parent
        (``set_parent_in_children``, ``set_name_in_children``)."""
        for f in self.site.value_fields(doctype):
            if f["fieldtype"] not in TABLE_TYPES:
                continue
            rows = document.get(f["fieldname"]) or []
            child_fields = {
                c["fieldname"] for c in self.site.value_fields(f["options"])
            }
            linked = []
            for index, row in enumerate(rows, start=1):
                kept = {k: v for k, v in row.items() if k in child_fields}
                linked.append(
                    {
                        **kept,
                        "name": row.get("name") or secrets.token_hex(5),
                        "owner": row.get("owner") or account.user,
                        "creation": row.get("creation") or moment,
                        "modified": moment,
                        "modified_by": account.user,
                        "docstatus": document["docstatus"],
                        "idx": index,
                        "parent": document["name"],
                        "parentfield": f["fieldname"],
                        "parenttype": doctype,
                        "doctype": f["options"],
                    }
                )
            document[f["fieldname"]] = linked

    def _mandatory(self, doctype: str, document: Mapping[str, Any]) -> None:
        missing = _missing_mandatory(self.site, doctype, document)
        for f in self.site.value_fields(doctype):
            if f["fieldtype"] in TABLE_TYPES:
                for row in document.get(f["fieldname"]) or []:
                    missing += _missing_mandatory(self.site, f["options"], row)
        if missing:
            raise _validation("MandatoryError", "; ".join(missing))

    def _insert(
        self, doctype: str, body: Dict[str, Any], account: Account
    ) -> Dict[str, Any]:
        meta = self.site.meta(doctype)
        own = {f["fieldname"] for f in self.site.value_fields(doctype)}
        name = body.get("name")
        if not name:
            self.site.counters[doctype] = self.site.counters.get(doctype, 0) + 1
            # A naming series: "ACC-SINV-.#####" names ACC-SINV-00001.
            prefix = meta["autoname"].rsplit("-.", 1)[0]
            name = f"{prefix}-{self.site.counters[doctype]:05d}"
        if name in self.site.documents[doctype]:
            raise FrappeError(
                409, "DuplicateEntryError", f"{doctype} {name} already exists"
            )
        moment = self.site.now()
        document: Dict[str, Any] = {
            **{k: v for k, v in body.items() if k in own},
            "doctype": doctype,
            "name": name,
            "owner": account.user,
            "creation": moment,
            "modified": moment,
            "modified_by": account.user,
            "docstatus": int(body.get("docstatus") or 0),
            "idx": 0,
        }
        self._mandatory(doctype, document)
        self._children(doctype, document, account, moment)
        self.site.documents[doctype][name] = document
        return copy.deepcopy(document)

    def _save(
        self,
        doctype: str,
        stored: Dict[str, Any],
        document: Dict[str, Any],
        account: Account,
    ) -> Dict[str, Any]:
        """``Document.save`` of ``document`` over ``stored``: held to the
        ``modified`` it carries (``check_if_latest``), then to the docstatus
        transition."""
        if str(stored["modified"]) != str(document.get("modified")):
            raise _validation(
                "TimestampMismatchError",
                f"Error: Document has been modified after you have opened it "
                f"({stored['modified']}, {document.get('modified')}). "
                f"Please refresh to get the latest document.",
            )
        before, after = stored["docstatus"], int(document.get("docstatus") or 0)
        if before == 2:
            raise _validation("ValidationError", "Cannot edit cancelled document")
        if before == 1 and after == 0:
            raise _validation(
                "DocstatusTransitionError",
                "Cannot change docstatus from 1 (Submitted) to 0 (Draft)",
            )
        if before == 0 and after == 2:
            raise _validation(
                "DocstatusTransitionError",
                "Cannot change docstatus from 0 (Draft) to 2 (Cancelled)",
            )
        if before == 1 and after == 1:
            allowed = {
                f["fieldname"]
                for f in self.site.value_fields(doctype)
                if f.get("allow_on_submit")
            }
            for f in self.site.value_fields(doctype):
                name = f["fieldname"]
                if name not in allowed and document.get(name) != stored.get(name):
                    raise _validation(
                        "UpdateAfterSubmitError",
                        f"Not allowed to change {f['label']} after submission",
                    )
        if after == 1 and before == 0:
            self._require(account, "submit", doctype)
        if after == 2:
            self._require(account, "cancel", doctype)
        self._mandatory(doctype, document)
        moment = self.site.now()
        own = {f["fieldname"] for f in self.site.value_fields(doctype)}
        saved: Dict[str, Any] = {
            **{k: v for k, v in document.items() if k in own},
            "doctype": doctype,
            "name": stored["name"],
            "owner": stored["owner"],
            "creation": stored["creation"],
            "modified": moment,
            "modified_by": account.user,
            "docstatus": after,
            "idx": stored["idx"],
        }
        self._children(doctype, saved, account, moment)
        self.site.documents[doctype][stored["name"]] = saved
        return copy.deepcopy(saved)

    def _update(
        self, doctype: str, name: str, body: Dict[str, Any], account: Account
    ) -> Dict[str, Any]:
        """``frappe.api.v1.update_doc``: the stored document with the body
        applied (``doc.update(data)``), then saved."""
        stored = self.site.stored(doctype, name)
        changed = {k: v for k, v in body.items() if k != "flags"}
        return self._save(
            doctype, stored, {**copy.deepcopy(stored), **changed}, account
        )

    def _submit(self, document: Dict[str, Any], account: Account) -> Dict[str, Any]:
        """``frappe.client.submit``: ``get_doc(doc).submit()``."""
        doctype = str(document.get("doctype"))
        meta = self.site.meta(doctype)
        stored = self.site.stored(doctype, str(document.get("name")))
        if not meta["is_submittable"]:
            raise _validation("ValidationError", f"{doctype} is not submittable")
        return self._save(doctype, stored, {**document, "docstatus": 1}, account)

    def _cancel(self, doctype: str, name: str, account: Account) -> Dict[str, Any]:
        """``frappe.client.cancel``: ``get_doc(doctype, name).cancel()``."""
        stored = self.site.stored(doctype, name)
        return self._save(
            doctype, stored, {**copy.deepcopy(stored), "docstatus": 2}, account
        )
