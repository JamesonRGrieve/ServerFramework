# `erp` extension

Every DocType of an ERPNext (Frappe) site, read and written live through
the site's own REST API, under the site's validation and permissions. The
site's signed webhooks are passed on to outbound webhook subscribers.

Depends on `federation` (its OpenAPI importer lifts DocTypes to models) and
`webhooks` (outbound delivery).

## Instances and access

A provider instance of `erpnext` is one account on one site. Its settings:

| Setting | |
|---|---|
| `base_url` | The site's address (`https://erp.example.com`) |
| `api_key` | API key of the ERPNext user the instance acts as (secret; the instance's `api_key` column) |
| `api_secret` | That user's API secret (secret) |
| `webhook_secret` | The Webhook Secret the site signs this instance's webhooks with, at least 16 characters (secret) |

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

Models are per instance and per request rather than registered in the app's
model registry: each site (and each account on it) has its own DocTypes and
custom fields, an instance may belong to one user, and the registry is fixed
when the app starts.

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
| `FrappeTestServer.py` | The Frappe REST subset in process, for the tests |
| `ERPTestSupport.py` | Test site, accounts and instances |
| `BLL_ERP_test.py`, `EP_ERP_test.py`, `PRV_ERPNext_test.py` | Tests; the live ones (`external_api(provider="erpnext")`) need `ERPNEXT_URL`, `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET` |
