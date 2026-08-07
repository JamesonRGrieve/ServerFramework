# `webhooks` extension

Inbound webhook handler registry, router factory, and replay protection.

This was previously `endpoints/Webhook.py` in the core. Inbound webhooks are
an integration pattern, not a framework primitive — many deployments emit
only outbound events. The extraction makes the inbound surface optional.

## Surface

- `BLL_Webhooks.webhook_handler(extension_class, provider, event=None)` —
  decorator. Registers a handler under
  `(extension_name, provider, event_or_None)`.
- `BLL_Webhooks.WebhookContext` — the value handlers receive.
- `EP_Webhooks.create_webhook_router()` — returns an `APIRouter` with two
  POST routes: `/webhook/{extension}/{provider}` and
  `/webhook/{extension}/{provider}/{event}`. Both are rate-limited to
  100/min per IP. The host application is responsible for `include_router`.

## Verification + replay protection

For a `(extension, provider)` pair with at least one registered handler,
the dispatcher requires the provider class to expose
`verify_signature(headers, body) -> bool`. Missing verification → 401.

A provider may opt into replay protection by exposing both:

- `replay_window_seconds: int` (class attribute)
- `extract_replay_keys(headers, body) -> (epoch_seconds: int, nonce: str)`

The dispatcher then rejects deliveries with timestamps outside the window
or whose `(extension, provider, nonce)` has already been seen inside the
window. The dedup cache is in-memory; horizontal deployments must replace
it with a distributed cache (Redis / `DistributedCounter`).

## File layout

```
extensions/webhooks/
├── __init__.py          # Re-exports
├── BLL_Webhooks.py      # Registry, dispatch helpers, replay cache
├── EP_Webhooks.py       # Router factory
├── EXT_Webhooks.py      # Extension class (AbstractStaticExtension)
├── manifest.toml
├── EXT.webhooks.md
└── migrations/versions/ # Empty — no DB models
```
