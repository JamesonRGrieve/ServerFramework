# Payment Extension

Payments, customer records, subscriptions and signed notifications through
Stripe, Square, PayPal, Helcim and Moneris, on the static extension API.
General extension patterns: [EXT.Patterns.md](../EXT.Patterns.md).

## Accounts

Each provider instance is one merchant account. Its settings
(`instance_settings`) are read with `cls.setting`; secrets are write-only,
with the provider's environment variables as the fallback a single-account
deployment uses — for the operator's (root- or system-scoped) instances
only, such as the seeded `Root_<Provider>`; a user's or team's account never
charges through the operator's. Every provider has an `api_base` (the sandbox address, for
one). All calls go through `cls.http()` (SSRF-guarded, typed errors).

| Provider | Settings | Offers |
|---|---|---|
| `stripe` | `api_key`, `webhook_secret` | payments, capture, refunds, customers, subscriptions, notifications |
| `square` | `api_key`, `location_id`, `webhook_signature_key`, `webhook_notification_url` | the same; a subscription ends only at the period's end |
| `paypal` | `client_id`, `api_key` (secret), `webhook_id`, `return_url`, `cancel_url` | orders (approved by the payer), capture, refunds, subscriptions without customer records, notifications verified by PayPal |
| `helcim` | `api_key`, `verifier_token` | purchases and pre-authorizations of card tokens (the payer's IP required), capture, refunds, notifications |
| `moneris` | `api_key`, `merchant_id` | immediate payments of Moneris tokens, completion, refunds |

What a provider cannot do is left out of its `_abilities` (so
`rotate_capable` never picks it) and refused with the reason
(`PermanentExternalError`).

## Abilities

All but `webhook_process` take a required `requester_id` and act through
`as_requester` (400 without a requester, 503 before an app runs).

- `customer_create`, `customer_get`: the requester's customer record, made
  under their own email on the first account that keeps customers, and
  linked to them.
- `payment_create`, `payment_get`, `payment_list`: a charge for the
  requester, on the account holding their customer record or the first
  that can (`rotate_capable`). `capture=False` only authorizes.
- `payment_capture`, `payment_refund`: ROOT or SYSTEM only.
- `subscription_create`, `subscription_get`, `subscription_list`,
  `subscription_cancel`, `subscription_status`.
- `webhook_process(instance, payload, headers)`: verifies a notification as
  the provider signs it, then reads the payment or subscription it names
  again from the provider and updates its record.

A request that moves money is sent once with an idempotency key; a failure
that leaves its outcome unknown is permanent, never retried on another
account.

## Records

- `users.external_payment_id`, `users.payment_instance_id`: the customer
  link, written by the server only (`UserManager.update` refuses both from
  a user, 403). A link counts only with both set.
- `payments`, `payment_subscriptions`: owned by the user they were made for
  (the requester, whatever the caller names; ROOT and SYSTEM may name
  another), never moved by an update, read-only over REST, mirrored from
  the provider on every read.

## Login check

`BLL_Payment.require_active_subscription(user_id, model_registry)` refuses
(402) a user whose subscriptions are all inactive, each read again from its
provider (the last record when the provider cannot answer in time). Users
with none, ROOT and SYSTEM pass; `DISABLE_SUBSCRIPTION_VALIDATION=true`
turns it off. `UserManager.login` is a static method that manager hooks
cannot reach, so payment registers it as its login check
(`register_login_check("payment", …)`), which core runs on every sign-in
(password, MFA, every sign-in extension and grant) in an app that loaded
payment, before any session is issued.

## Tests

Provider wire tests run against local servers answering as each API does;
live tests are marked `external_api(provider=…)` and xfail without
credentials.
