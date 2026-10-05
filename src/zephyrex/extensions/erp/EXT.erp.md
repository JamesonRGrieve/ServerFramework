# `erp` extension

Every DocType of an ERPNext (Frappe) site, read and written live through
the site's own REST API, under the site's validation and permissions. The
site's signed webhooks are passed on to outbound webhook subscribers.

Depends on `federation` (its OpenAPI importer lifts DocTypes to models; its
typed models serve the operator's DocTypes) and `webhooks` (outbound
delivery).

## Instances and access

A provider instance of `erpnext` is one account on one site. Its settings:

| Setting | |
|---|---|
| `base_url` | The site's address (`https://erp.example.com`) |
| `api_key` | API key of the ERPNext user the instance acts as (secret; the instance's `api_key` column) |
| `api_secret` | That user's API secret (secret) |
| `webhook_secret` | The Webhook Secret the site signs this instance's webhooks with, at least 16 characters (secret) |
| `typed_doctypes` | Comma-separated DocTypes an operator instance serves as typed models at boot; empty types every DocType. The generic document API serves all of them either way |

Secrets are stored encrypted and never returned. There are no environment
fallbacks, so an instance without its own key never acts with another's.

Who may use an instance (`BLL_ERP.can_use`) follows its scope:

- a root- or system-scoped instance (which only ROOT and SYSTEM configure)
  serves everyone;
- a user- or team-scoped instance serves whoever the framework lets see it
  (its user, its team's members);
- ROOT and SYSTEM may use any enabled instance.

Anyone else is answered 404, as for an instance that does not exist. A
disabled instance serves no one. The site then applies its own permissions
for the instance's account; its refusal (403) is passed on.

## Coverage: DocTypes become models

Nothing is written per DocType. The catalogue is the site's own
(`GET /api/resource/DocType`, every DocType the account is shown, standard
and custom). A DocType's schema is read from the site
(`frappe.desk.form.load.getdoctype`, custom fields merged in) whenever a
request needs it, and kept for that request only.

`BLL_ERP.lift_doctype` translates the DocType and its child tables into
OpenAPI component schemas and lifts them through
`federation.BLL_Federation_REST.openapi_to_pydantic_models`: one Pydantic
model per DocType, each child table a list of its child DocType's model.
Field types follow Frappe's (`Int`/`Check` integer; `Float`, `Currency`,
`Percent`, `Rating`, `Duration` number; `JSON`/`Geolocation` any; tables
nested; layout fields dropped; everything else text). The model is the
DocType's published JSON Schema and checks every create and update before
it is sent: a field the DocType does not have, a value of the wrong type, a
child row naming a field its DocType lacks, and the fields only the site
sets (`owner`, `creation`, `modified`, `modified_by`, `docstatus`,
`doctype`, a row's `parent`/`parentfield`/`parenttype`) are refused with
422. Existing child rows are addressed by their `name`.

Child tables are read and written inside their parent document. A child
DocType is not written directly (400). A Single DocType's one document is
named after the DocType.

For the generic API these models are per instance and per request: each
site (and each account on it) has its own DocTypes and custom fields, and an
instance may belong to one user. The operator's instances' DocTypes are
also registered as typed models when the app boots (below).

## Operations

`ERPManager` serves them on REST and as GraphQL fields (`erp_*`); the
`EXT_ERP` abilities (`erp_doctypes`, `erp_schema`, `erp_list`, `erp_get`,
`erp_create`, `erp_update`, `erp_delete`, `erp_submit`, `erp_cancel`) serve
server code, acting for the `requester_id` they name under the same rules.
Each REST route is a JWT-authenticated POST whose body names the instance
(a DocType or document name is free text, so it travels in the body):

| Route | Does | Site call |
|---|---|---|
| `POST /v1/erp/doctypes` | The catalogue | `GET /api/resource/DocType` |
| `POST /v1/erp/schema` | Fields, child tables, JSON Schema | `getdoctype` |
| `POST /v1/erp/document/list` | A page of documents (`fields`, `filters`, `or_filters` as Frappe's dictionary filters, `order_by`, `start`, `page_length` ≤ 500) | `GET /api/resource/<DocType>` |
| `POST /v1/erp/document/get` | One document with its child tables | `GET /api/resource/<DocType>/<name>` |
| `POST /v1/erp/document/create` | Create | `POST /api/resource/<DocType>` |
| `POST /v1/erp/document/update` | Save onto a document | `PUT /api/resource/<DocType>/<name>` |
| `POST /v1/erp/document/delete` | Delete | `DELETE /api/resource/<DocType>/<name>` |
| `POST /v1/erp/document/submit` | Submit a draft of a submittable DocType | `frappe.client.submit` |
| `POST /v1/erp/document/cancel` | Cancel a submitted document | `frappe.client.cancel` |

A document comes back as `{provider_instance_id, doctype, name, docstatus,
updated_at, data}`: `data` is every field the account may read, as the site
answered. A list row carries its own fields (child tables come with `get`).
On GraphQL a JSON object (`data`, `filters`) travels as JSON text, as
everywhere on that surface.

Submit and cancel call the document's own `submit`/`cancel` on the site, so
a DocType that queues a large submission still does. Every refusal carries
the site's own reason (its `_server_messages`), never its raw body:
404 not found, 403 not permitted to the account, 409 a duplicate, 422 any
other validation, 502 the site unreachable or refusing the instance's
credentials, 503 an instance missing a setting. All requests go through
`ProviderHTTPClient` (SSRF guard, timeouts); the API secret is registered
for redaction, so an error echoing it is scrubbed.

## Versions (If-Match)

A document's version is its `modified`, carried as `updated_at` and as the
REST answer's `ETag`. A save names the version it was read at in `If-Match`
(REST) or `if_match` (GraphQL and abilities); `*` names any version. With
`IF_MATCH_REQUIRED` (the default) a save that names none is refused with
428 before anything is sent.

- **Update** sends the version with the change. Frappe's `update_doc` loads
  the document `for_update`, applies the body, takes the body's `modified`
  as the version the save is based on and refuses it
  (`TimestampMismatchError`) when the stored one differs, so the check and
  the write happen under the site's row lock.
- **Submit** sends the document as read just before (its `modified`
  included); the site's same check refuses it if the document changed in
  between.
- **Delete** and **cancel** are checked against the document read just
  before the call; the site offers no version check for them.

A refusal from a document that has moved on is answered 412 with the
document as it now is (and its ETag); any other refusal is the site's.

## Typed DocTypes (client contract)

Every enabled root- or system-scoped instance (`BLL_ERP_Typed`) is a
federated source (`federation/EXT.federation.md`): when the app boots, its
catalogue is read and each parent DocType is registered as a typed,
table-less model on REST and GraphQL, beside the generic API, which is
unchanged. A user's or a team's instance, a disabled one, and one whose site
cannot be read at boot are never in the shared schema; the last is logged
with the reason (`… its typed models are not served, the catalogue could not
be read: 502 ERPNext is not reachable at that address`) and the app boots
with the generic API.

Each typed operation goes through `ERPDocuments` for the requester, so the
generic API's rules hold exactly: `can_use`, checked on every call (an
instance disabled or rescoped since boot answers 404), the DocType's live
schema for every write, the site's permissions and reasons, versions, and
no credential ever answered. Nothing is stored here.

### Names

Every name follows from the instance id and the DocType name alone:

| | Rule | `Sales Invoice` on instance `3f2a9c1d-…` |
|---|---|---|
| namespace | `erpnext_` + the first 8 hex digits of the instance id, lower case | `erpnext_3f2a9c1d` |
| slug | the DocType name's ASCII letter/digit runs, lower case, joined by `_` (`t_` before a leading digit; `_2`, `_3`… for a slug two DocTypes share, by DocType name ascending) | `sales_invoice` |
| `NS` | the namespace's words, each with its first letter capitalized, joined | `Erpnext3f2a9c1d` |
| `Slug` | the slug's words, likewise | `SalesInvoice` |
| REST | `POST /v1/federated/<namespace>/<slug>/<operation>` | `/v1/federated/erpnext_3f2a9c1d/sales_invoice/get` |
| GraphQL field | `<namespace>_<slug>_<operation>` camelCased: the first word as is, each other word with its first letter capitalized and the rest lower case | `erpnext3f2a9c1dSalesInvoiceGet` |
| record type | `<NS><Slug>Type` | `Erpnext3f2a9c1dSalesInvoiceType` |
| child row type | `<NS><Slug><Child>Type`, `Child` the child DocType name's words, each first letter capitalized, the rest as written | `Erpnext3f2a9c1dSalesInvoiceSalesInvoiceItemType` |
| write input | `<NS><Slug>WriteInput`; a child row's `<NS><Slug><Child>WriteInput` | `Erpnext3f2a9c1dSalesInvoiceWriteInput`, `Erpnext3f2a9c1dSalesInvoiceSalesInvoiceItemWriteInput` |
| operation input | `<NS><Slug><Op>ArgsInputInput`, `Op` one of `List`, `Get`, `Create`, `Update`, `Delete` | `Erpnext3f2a9c1dSalesInvoiceUpdateArgsInputInput` |
| list page type | `<NS><Slug>PageType` | `Erpnext3f2a9c1dSalesInvoicePageType` |

A custom DocType `Route Plan (EU) - 2026` on the same instance is slug
`route_plan_eu_2026`, REST `/v1/federated/erpnext_3f2a9c1d/route_plan_eu_2026/create`,
GraphQL `erpnext3f2a9c1dRoutePlanEu2026Create`, type
`Erpnext3f2a9c1dRoutePlanEu2026Type`. Letters outside ASCII are dropped
from names (`Café Order` is `caf_order`). An instance id starting
`ab12cd34` gives namespace `erpnext_ab12cd34`, `NS` `ErpnextAb12cd34`.

Fields keep their Frappe fieldnames on REST and are camelCased on GraphQL
(`customer_name` → `customerName`). A fieldname a model cannot hold (not an
identifier, or a Pydantic `model_*` name) is not in the typed model; the
generic API reads and writes it. A child table is a type of its parents
only; a Single DocType is served `get` and `update` only, its document named
after the DocType.

### Operations and payloads

All are JWT-authenticated POSTs with a JSON body. `list` and `get` are
GraphQL queries; `create`, `update`, `delete` mutations, each taking
`input`.

| Operation | Body (`input`) | Answer |
|---|---|---|
| `list` | `{filters?, order_by?, start = 0, page_length = 20 (1-500)}`, `filters` as the generic API's (Frappe dictionary filters) | `{items: [record], start, page_length}`; rows carry their own fields (child tables come with `get`) |
| `get` | `{name}` | the record; `ETag` its version |
| `create` | `{data: <write payload>}` | the record as saved; `ETag` |
| `update` | `{name, data: <write payload>, if_match?}`, `If-Match` header on REST | the record as saved; `ETag` |
| `delete` | `{name, if_match?}`, `If-Match` header on REST | `{key, deleted: true}` |

The record holds every field of the DocType, then `name`, `owner`,
`creation`, `modified`, `modified_by`, `docstatus` (0 draft, 1 submitted, 2
cancelled), `idx`, `doctype`, and `updated_at`: the document's version (its
`modified`, as the generic API's `updated_at`), which is its ETag
(`"<updated_at>"`). A child table field is a list of child rows, each with
the child DocType's fields, `name`, `idx` and `parent`, `parentfield`,
`parenttype`. Values are typed as the generic API's model: `Int`, `Long
Int`, `Check` integer; `Float`, `Currency`, `Percent`, `Rating`, `Duration`
number; `JSON`, `Geolocation` any; everything else text. Every field is
optional (the site enforces what is mandatory).

The write payload is the record without what the site sets: `owner`,
`creation`, `modified`, `modified_by`, `docstatus`, `doctype`, `updated_at`,
a document's `idx`, a row's `parent`/`parentfield`/`parenttype`. A field it
lacks, or a value of the wrong type, is 422 before anything is sent; the
site's live schema checks it again. Existing child rows are addressed by
their `name`. Only the fields given are sent (on GraphQL, a field left out
or null is not given). Submit and cancel are the generic API's.

A save names the version it was read at: `If-Match: "<updated_at>"` (or
`if_match`, which GraphQL uses). None is 428 (`IF_MATCH_REQUIRED`); a stale
one 412 with `{detail, current: <record>}` and the current ETag.

### Errors

| Status | When |
|---|---|
| 401 | no signed-in user |
| 404 | the instance is not one the requester may use (or no longer enabled), or no such document |
| 403 | the site does not permit it to the instance's account (its reason) |
| 409 | a duplicate name |
| 412 | the document has changed since the version named |
| 422 | a body outside the typed model, or the site's validation refused it (its reason) |
| 428 | a save that names no version |
| 429 | the site is rate limiting (`Retry-After`) |
| 502 | the site is unreachable or refuses the instance's credentials, or answered a document outside the DocType's schema |
| 503 | the instance lacks a setting |

On GraphQL a refusal is an entry of `errors` and the field is null.

### Discovery

`GET /v1/federated/catalogue` (GraphQL `federatedCatalogue`) lists every
typed model the requester may use: per type, `namespace`, `source`,
`source_reference` (the instance's `provider_instance_id`), `name` (the
DocType), `slug`, `key_field` (`name`), `version_field` (`updated_at`),
`operations`, `rest` (each operation's path), `graphql_type`,
`graphql_write_input`, `graphql_fields`, `graphql_inputs`, and the record's
and write payload's JSON Schema. The instance's DocTypes are its rows whose
`source_reference` is its id.

An operator instance whose `typed_doctypes` names DocTypes types only those
(a full site has hundreds, each a few routes and GraphQL fields, read at
boot); a name the site does not show is logged and skipped. Left empty, it
types every parent DocType the account is shown.

The catalogue is the boot's: a DocType added to the site later is reachable
only through the generic API until the app restarts, as is a new operator
instance; a field added to a DocType is not in its typed model until then
(the generic API serves it), and one removed is refused by the site's live
check.

## Webhooks

Configure a Webhook on the site for each DocType and document event to pass
on:

- Request URL: `https://<server>/v1/erp/webhook/<provider_instance_id>`
- Request Structure: JSON, with a body naming the event, the document and
  its version, e.g.
  `{"event": "on_submit", "doctype": "{{ doc.doctype }}", "name": "{{ doc.name }}", "modified": "{{ doc.modified }}"}`
  (`event` is one of `after_insert`, `on_update`, `on_submit`, `on_cancel`,
  `on_trash`, `on_update_after_submit`, `on_change`)
- Enable Security, with the instance's `webhook_secret` as the Webhook
  Secret.

Frappe signs a webhook with `X-Frappe-Webhook-Signature`: the base64
HMAC-SHA256 of the exact body, keyed by the secret
(`webhook.get_webhook_headers`). The endpoint takes no session and answers
every refusal with the same 401: an unknown, deleted or disabled instance,
one without a strong secret, a missing or wrong signature. Frappe signs no
timestamp, so a body already taken for the instance is refused for 24 hours
(the shared replay cache); including `modified` keeps two real events from
having the same body. A signed body naming no event or document is 400.
Bodies are capped at 1 MiB; 300 deliveries per minute per address.

A webhook taken is passed on as the event `erp.<event>` with the body
`{provider, provider_instance_id, event, doctype, name, data}` (`data`:
the site's body) through `webhooks.BLL_WebhookDelivery.dispatch_webhook_event`,
whose audience is `can_use`: only subscribers who may use the instance it
came from receive it. Subscribers re-read the document through the routes
above for its current state.

## Files

| File | |
|---|---|
| `EXT_ERP.py` | Extension class, abilities, and the provider contract (`AbstractERPProvider`) |
| `PRV_ERPNext.py` | ERPNext over Frappe's REST API; webhook signatures |
| `BLL_ERP.py` | Access, the DocType lift, documents and versions, webhook intake, routes |
| `BLL_ERP_Typed.py` | The operator's instances as federated sources: DocTypes as typed models |
| `FrappeTestServer.py` | The Frappe REST subset in process, for the tests |
| `ERPTestSupport.py` | Test site, accounts and instances |
| `BLL_ERP_test.py`, `EP_ERP_test.py`, `EP_ERP_Typed_test.py`, `PRV_ERPNext_test.py` | Tests; the live ones (`external_api(provider="erpnext")`) need `ERPNEXT_URL`, `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET` |
