# `webhooks` extension

Webhooks in both directions: an inbound handler registry and router with
mandatory signature verification and optional replay protection, and signed
outbound delivery to endpoints users subscribe.

Webhooks are an integration pattern, not a framework primitive, so the
extension is optional.

## Inbound

- `BLL_Webhooks.webhook_handler(extension_class, provider, event=None)` is a
  decorator that registers a handler under
  `(extension_name, provider, event_or_None)`.
- `BLL_Webhooks.WebhookContext` is the value handlers receive.
- `EP_Webhooks.create_webhook_router()` returns an `APIRouter` with two
  POST routes: `/webhook/{extension}/{provider}` and
  `/webhook/{extension}/{provider}/{event}`. Both are rate-limited to
  100/min per IP. The host application is responsible for `include_router`.

### Verification and replay protection

For a `(extension, provider)` pair with at least one registered handler,
the dispatcher requires the provider class to expose
`verify_signature(headers, body) -> bool`. Missing verification is a 401.

A provider may opt into replay protection by exposing both:

- `replay_window_seconds: int` (class attribute)
- `extract_replay_keys(headers, body) -> (epoch_seconds: int, nonce: str)`

The dispatcher then rejects deliveries with timestamps outside the window
or whose `(extension, provider, nonce)` has already been seen inside the
window. The dedup cache is in-memory; horizontal deployments must replace
it with a distributed cache (Redis / `DistributedCounter`).

## Outbound

A **subscription** (`WebhookSubscriptionModel`, `/v1/webhook-subscription`)
belongs to the user who creates it: a `target_url` (absolute http(s), no
credentials), the `event_types` it wants (space- or comma-separated, `*` for
all) and a `secret` of at least 16 characters. The secret is stored
encrypted and never returned. The routes are the generated JWT CRUD routes,
owner-scoped and held to If-Match like every save.

Code raises an event with:

```python
dispatch_webhook_event(
    model_registry, "label.attached", body,
    about_model=LabelModel, about_id=label.id,
)
```

Each active, undeleted subscription that wants the event type gets a
**delivery** (`WebhookDeliveryModel`) only when its owner can read the record
the event is about, so no one hears of a record they could not see.
Deliveries inherit their subscription's access: the owner reads them at
`/v1/webhook-delivery` (GET, list and search only); only the server writes
them.

`SVC_WebhookDelivery` runs `deliver_due` every few seconds. Each due
delivery is claimed with a compare-and-set on its attempt count, so of
several workers one sends it. It is POSTed through the shared client (SSRF
guard, TLS policy) with these headers:

| Header | Value |
|---|---|
| `X-Webhook-Signature` | `sha256=<hex>`, HMAC-SHA256 of the exact body, keyed by the secret |
| `X-Webhook-Event` | the event type |
| `X-Webhook-Delivery` | the delivery id |

A 2xx marks it delivered. Any other answer, a network failure or a refused
destination waits `2 × 2^(attempts−1)` seconds, capped at an hour. After
five attempts the delivery is dead-lettered, as is one whose subscription
was deleted, deactivated or lost its secret.

## File layout

```
extensions/webhooks/
├── __init__.py              # Re-exports
├── BLL_Webhooks.py          # Inbound registry, dispatch helpers, replay cache
├── EP_Webhooks.py           # Inbound router factory
├── BLL_WebhookDelivery.py   # Outbound models, managers, dispatch, delivery
├── SVC_WebhookDelivery.py   # Background worker that sends due deliveries
├── EXT_Webhooks.py          # Extension class (AbstractStaticExtension)
├── manifest.toml
├── EXT.webhooks.md
└── migrations/versions/     # 001: webhook_subscriptions, webhook_deliveries
```
