# Email Extension

This document describes the Email extension implementation.

> **Extension Architecture**: For general extension patterns, architecture, and concepts, see [EXT.Patterns.md](../EXT.Patterns.md).

The Email extension provides email-sending capabilities through the Provider Rotation System with three concrete providers.

## Overview

The `email` extension exposes a single primary ability — `email_send` — backed by one of three providers selected at runtime. All providers run their inputs through a shared validation helper so the same denial guarantees apply regardless of which provider is active.

## Providers

The extension ships with three concrete providers, all subclasses of `AbstractEmailProvider`:

| Provider | Transport | Use case | Key env vars |
|----------|-----------|----------|--------------|
| `SendGrid` (`SendgridProvider`) | HTTP API (`sendgrid` SDK) | Hosted, batteries-included, marketing & transactional | `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL` |
| `Stalwart` (`StalwartProvider`) | SMTP submission via `aiosmtplib` (port 587 + STARTTLS by default) | Self-hosted Stalwart mail server, full control over deliverability | `STALWART_HOST`, `STALWART_PORT`, `STALWART_USERNAME`, `STALWART_PASSWORD`, `STALWART_FROM_EMAIL`, `STALWART_USE_TLS` |
| `SMTP2go` (`Smtp2goProvider`) | HTTP API (`POST /v3/email/send` via `httpx`) | Hosted SMTP relay with REST front door | `SMTP2GO_API_KEY`, `SMTP2GO_FROM_EMAIL`, `SMTP2GO_API_URL` |

All three live in `extensions/email/PRV_SendGrid_EMail.py` so they share the validation helper and external-model machinery; the file name is historical.

### Provider matrix

`AbstractEmailProvider` declares 16 abstract methods covering the full email lifecycle (read, search, draft, flag, threads, etc.). In practice the three shipped providers are send-only relays; receive-side methods are stubbed with `logger.warning` returns so the abstract contract is satisfied without falsely advertising support:

| Method | SendGrid | Stalwart | SMTP2go |
|--------|----------|----------|---------|
| `send_email` | ✅ implemented | ✅ implemented | ✅ implemented |
| `get_emails` / `search_emails` / `reply_to_email` / `delete_email` / `create_draft_email` / `process_attachments` | ⚠️ stub (warns + empty return) | ⚠️ stub | ⚠️ stub |
| `move_email` / `mark_email_as_read` / `mark_email_as_unread` / `flag_email` / `unflag_email` / `get_email_threads` / `get_thread_messages` / `get_latest_email` / `download_attachment` | inherited abstract (no implementation) | inherited abstract | inherited abstract |

Receive-side support (Gmail / Outlook / IMAP) is intentionally out of scope; if a future extension needs inbox handling, model it as a separate provider that implements the appropriate abstract methods.

## Architecture

### Extension class structure

```python
class EXT_EMail(AbstractStaticExtension):
    name: ClassVar[str] = "email"
    _abilities: ClassVar[Set[str]] = {
        "email_status",     # Meta: report extension status
        "email_config",     # Meta: report effective config
        "email_send",       # Provider ability: send a message
        "email_receive",
        "email_templates",
        "email_tracking",
    }
```

### Abstract provider

```python
class AbstractEmailProvider(AbstractStaticProvider):
    extension_type: ClassVar[str] = "email"

    @classmethod
    def _validate_send_inputs(cls, recipient, subject, body, attachments) -> Optional[str]:
        """Reject CRLF / NUL / oversized / traversal / homograph inputs."""

    @abstractmethod
    @ability("email_send")
    async def send_email(cls, provider_instance, recipient, subject, body, **kwargs) -> str: ...
```

Concrete providers must call `_validate_send_inputs` as the first statement of `send_email`; the inherited helper makes the same denial guarantees apply across every transport.

### Hook-based integration

The extension auto-sends invitation emails via `BLL_Auth.InviteeManager.create` (`AFTER` hook, priority 5):

```python
hook_bll(InviteeManager.create, timing=HookTiming.AFTER, priority=5)(send_invitation_email_hook)
```

The hook resolves the rotation manager `Root_Email`, which iterates configured providers in order and stops at the first success.

## Dependencies

### PIP dependencies

| Package | Floor | Used by |
|---------|-------|---------|
| `sendgrid` | `>=6.10.0` | `SendgridProvider` |
| `aiosmtplib` | `>=3.0.0` | `StalwartProvider` |
| `httpx` | `>=0.27.0` | `Smtp2goProvider` |

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `EMAIL_PROVIDER` | `"sendgrid"` | Default provider name (case-insensitive); used by the rotation system to pick the first provider to try. |
| `SENDGRID_API_KEY` | `""` | SendGrid API key. |
| `SENDGRID_FROM_EMAIL` | `""` | SendGrid verified sender address. |
| `STALWART_HOST` | `""` | Stalwart submission hostname. |
| `STALWART_PORT` | `"587"` | Stalwart submission port (587 for STARTTLS). |
| `STALWART_USERNAME` | `""` | Stalwart SMTP AUTH username. |
| `STALWART_PASSWORD` | `""` | Stalwart SMTP AUTH password (also accepted via `ProviderInstance.api_key`). |
| `STALWART_FROM_EMAIL` | `""` | Default sender for Stalwart-routed mail. |
| `STALWART_USE_TLS` | `"true"` | If `"false"`, disables STARTTLS (only suitable for trusted networks). |
| `SMTP2GO_API_KEY` | `""` | SMTP2go API key. |
| `SMTP2GO_FROM_EMAIL` | `""` | Default sender for SMTP2go-routed mail. |
| `SMTP2GO_API_URL` | `"https://api.smtp2go.com/v3"` | API base URL (override for region pinning). |
| `SMTP_SERVER` / `SMTP_PORT` / `IMAP_SERVER` / `IMAP_PORT` | various | Reserved for future generic SMTP/IMAP providers; not consumed by the shipped providers. |

## Provider Registration

`BLL_EMail.py` walks `_EMAIL_PROVIDER_REGISTRY` once and registers every provider whose credential pair is set:

```python
_EMAIL_PROVIDER_REGISTRY = (
    ("SendGrid", "SendGrid Email Service", "SENDGRID_API_KEY", "SENDGRID_FROM_EMAIL"),
    ("Stalwart", "Stalwart Mail Server",   "STALWART_PASSWORD", "STALWART_FROM_EMAIL"),
    ("SMTP2go", "SMTP2go Email Service",   "SMTP2GO_API_KEY",   "SMTP2GO_FROM_EMAIL"),
)
```

This produces:
- A row in the `Provider` table for each configured provider (`register_email_providers_hook`).
- A `Root_<Name>` row in the `ProviderInstance` table per configured provider, with the API key in `api_key` and the from-address in `model_name` (`register_email_provider_instances_hook`).

To add a fourth provider you only need: a new `PRV_*Provider` class inside `PRV_SendGrid_EMail.py` and one new tuple in the registry.

## Usage

### Sending email via the rotation system

```python
if EXT_EMail.root:
    result = await EXT_EMail.root.rotate(
        AbstractEmailProvider.send_email,
        recipient="user@example.com",
        subject="Welcome!",
        body="Welcome to our platform!",
    )
```

The rotation manager tries each `Root_<Provider>` instance in order until one returns a success string.

### Checking extension status

```python
status = EXT_EMail.get_extension_status()
# {
#     "extension": "email",
#     "version": "1.0.0",
#     "providers_available": 3,
#     "configured": True,
#     "default_provider": "sendgrid",
# }
```

## Security Features

### `_validate_send_inputs`

Every provider's `send_email` calls `AbstractEmailProvider._validate_send_inputs` first. The helper rejects:

- **CRLF in `recipient` or `subject`** — header-injection prevention (no `\r` or `\n`).
- **NUL bytes anywhere** — defends against C-string-truncation smuggling.
- **Subjects > 998 octets** — RFC 5322 §2.1.1 header-line length cap.
- **Bodies > 10 MiB** — DoS guard against unbounded payloads.
- **Malformed addresses** — `parseaddr` must yield a non-empty `local@domain.tld`.
- **Non-ASCII recipients** — Cyrillic / Greek homograph guard; legitimate IDN domains must be Punycode-encoded by the caller.
- **Attachment paths** that are relative, contain `..`, or contain NUL — file-system traversal and SMTP-multipart smuggling guards.

The helper returns `None` on success or an error string suitable for returning directly from `send_email`. Callers should never silently invoke the underlying SDK if the helper rejects.

### Test coverage

`AbstractEmailProviderSecurityTests` (in `extensions/AbstractPRVTest.py`) is a parametrized mixin that exercises every rule above against a `mock_provider_instance` fixture. Every concrete provider's test class inherits the mixin, so the deny matrix runs without requiring a real API key — gaps in any provider become test failures, not silent xfails.

```python
class TestSendgridProvider(AbstractPRVTest, AbstractEmailProviderSecurityTests):
    provider_class = SendgridProvider
```

### API-key handling

- API keys are read from environment or from `ProviderInstance.api_key`; they never appear in log messages (see `lib/Logging.py` redaction patches).
- `bond_instance` returns an `AbstractProviderInstance_SDK` wrapping the SDK or connection config, never the raw key.

## Testing

### Provider tests

```python
class TestStalwartProvider(AbstractPRVTest, AbstractEmailProviderSecurityTests):
    provider_class = StalwartProvider
    expected_services = ["email", "smtp"]
```

Three test classes (one per provider) live in `PRV_SendGrid_EMail_test.py` and inherit the same security mixin. The shipped expectations:

- `pytest src/extensions/email/` → 0 failures, 0 xfails.
- The 9-row `EMAIL_SECURITY_DENY_MATRIX` runs once per provider for 27 deny tests total.

### Extension tests

`EXT_EMail_test.py` covers extension-level surfaces (status / config metadata, env-var registration, hook wiring).

## Troubleshooting

- **No providers registered**: at least one `(*_API_KEY, *_FROM_EMAIL)` pair must be set; check `BLL_EMail.register_email_providers_hook`.
- **Stalwart connection refused**: confirm `STALWART_HOST` is reachable on `STALWART_PORT`; submission ports are typically 465 (TLS), 587 (STARTTLS), or 25 (cleartext, deprecated).
- **SMTP2go 401**: rotate `SMTP2GO_API_KEY`; the API key must include the v3 prefix.
- **CRLF / NUL / oversized rejection**: surfaces as a typed `EmailHeaderInjectionError` / `EmailMalformedAddressError` / `EmailPayloadTooLargeError` (subclasses of `InvalidInputExternalError`); strip those characters from upstream input.

## Typed Errors

Every email-provider entry point raises typed exceptions on failure and returns `SentMessage(id, provider, accepted_at)` on success. Validation failures from `_validate_send_inputs` map to typed `InvalidInputExternalError` subclasses: `EmailHeaderInjectionError` (CRLF in headers), `EmailPayloadTooLargeError` (body > 10 MiB or subject > 998 octets), `EmailMalformedAddressError` (`parseaddr` rejection, homograph guard), `EmailAttachmentTraversalError` (relative/`..`/NUL in attachment paths). Upstream failures map to the canonical hierarchy: 4xx → `InvalidInputExternalError`, 5xx → `TransientExternalError`, 429 → `RateLimitExternalError`, 401/403 → `AuthExternalError`. Security tests assert `with pytest.raises(EmailHeaderInjectionError)` rather than substring-matching error strings.

## Bonded Provider Instance

`AbstractEmailProviderInstance(AbstractProviderInstance)` declares the typed abilities (`send`, `send_bulk`, `list_emails`, `get_email`, `update_email`, `reply`, `download_attachment`, `list_threads`) plus a typed `capabilities: ClassVar[FrozenSet[Capability]]`. `bond_instance` returns the typed instance; `_instance` is declared as a typed `ClassVar` and a mypy gate enforces the contract. Call sites use `bonded.send(message)` rather than `Provider.send(provider_instance, message)`.

## Typed Settings and Credentials

Each abstract provider declares `Settings` as an inner Pydantic model with typed fields, defaults, validators, and `Secret` markers. `_env` is replaced by an `EnvSchema` with typed names, defaults, required flags. Concrete providers extend the abstract:

- `SendgridProvider.Settings(from_email: EmailStr, api_key: Secret[str])`
- `StalwartProvider.Settings(host: str, port: int = 587, username: str, password: Secret[str], use_tls: bool = True, from_email: EmailStr)`
- `Smtp2goProvider.Settings(api_key: Secret[str], from_email: EmailStr, api_url: HttpUrl = "https://api.smtp2go.com/v3")`

The startup check refuses to boot if a required value is missing for any registered provider. `Secret`-marked fields never appear in log output.

Credentials resolve through the `CredentialRef` tier order: OpenBao → environment variable → encrypted database column. The env-var fallback honors the `_TEST` / `_LIVE` discriminator selected by `APP_ENV`. A SendGrid 401 cache-busts the credential and forces a re-resolve; a re-resolved-identical credential transitions the provider to `DOWN` and halts retry until an operator intervenes.

## Idempotent Send and Bulk Send

`send_via_provider` is decorated `@idempotent` so a 5xx retry storm does not double-send the same invitation. The framework's key derivation handles retry safety; the canonical store is the outbox row when the operation enrolls.

`send_bulk_via_provider` is a true batch endpoint. SendGrid uses `personalizations` arrays (up to 1000 recipients), SMTP2go uses `to[]` arrays (up to 1000), Stalwart uses multiple `RCPT TO` against one `MAIL FROM` where the local server allows it. Each per-item rejection surfaces as an individual typed `InvalidInputExternalError` carrying the specific recipient. The provider declares the supported batch size and the framework falls back to a serial loop for providers that don't support batching.

## Authentication Strategies

Each provider declares `default_auth_strategy_name`. Stalwart declares `"basic"` (`BasicAuth` strategy yielding `(username, password)` for the SMTP AUTH handshake). SendGrid and SMTP2go declare `"api_key"` (`APIKeyAuth` injecting `Authorization: Bearer <key>` via the shared HTTP client). A `bond_instance` call resolves `auth_strategy = AuthStrategyRegistry.get(instance.auth_strategy_name or cls.default_auth_strategy_name)` and passes the strategy into the bonded instance. A future Workspace integration registers `OAuth2Auth` and a per-user Stalwart instance with `auth_strategy_name="oauth2"` works without modifying `StalwartProvider`.

## Federation Translators

`AbstractEmailProvider`-level `field_mappings: List[FieldMapping]` declares the `EmailMessage` ↔ provider DTO translation declaratively. `EmailAddress(name, address)` ↔ RFC 5322 mailbox roundtrip is `Compose` / `Decompose`. `Importance` enum ↔ provider-specific headers is `EnumRemap`. Round-trip tests run automatically.

Pagination and search use the homogenization layer: Stalwart declares `paginator = PageTokenPaginator` (or `CursorPaginator` if JMAP) and `query_translator = IMAPSearchTranslator`. SendGrid and SMTP2go's log/messages-search endpoints declare `KeyValueTranslator`. `list_emails(query="from:alice", limit=50)` against Stalwart issues a correct IMAP `SEARCH FROM alice` command without per-provider translation code; cursors round-trip through `next_token` envelopes.

## Inbound Webhooks

SendGrid Event Webhook delivers `bounce`, `delivered`, `open`, `click`, `spam_report`, `unsubscribe` events. SMTP2go has bounce-activity webhooks. SendGrid Inbound Parse delivers received mail through HTTP POST. Stalwart can be configured to POST custom hooks on inbound mail.

Each provider registers `@webhook_handler(EXT_EMail, provider="sendgrid", event="bounce")`-style handlers. Signature verification is mandatory: SendGrid checks ECDSA-SHA256 against `SENDGRID_WEBHOOK_PUBLIC_KEY`, SMTP2go uses bearer-token check on `SMTP2GO_WEBHOOK_SECRET`, Stalwart uses HMAC-SHA256 over body with `STALWART_WEBHOOK_SECRET`. A canonical `EmailDeliveryEvent(message_id, provider, event_type, recipient, timestamp, raw)` model normalizes the payload across providers; downstream consumers (suppression-list hook, bounce-tracking metrics, inbound-parse-routing) bind to the normalized model. Events fan into the AFTER-update hook chain on the corresponding `Email_*Manager` exactly as if originated locally.

## Capability Ladder

Beyond `send` and `list_emails`, each provider opts into a richer set of typed abilities via the `capabilities: ClassVar[FrozenSet[Capability]]` set. Calling an unsupported ability raises `NotSupportedError(provider, capability)` rather than silently returning empty:

- `validate_address` — pre-flight email-address validation (SendGrid `/v3/validations/email`, SMTP2go `/v3/email-validation`).
- `send_with_template` — server-side templates (SendGrid dynamic templates, SMTP2go templates, Stalwart local-file render).
- `list_suppressions` / `add_suppression` / `remove_suppression` — suppression-list management (SendGrid `suppression/*`, SMTP2go `bounces`/`unsubscribes`, Stalwart synth-from-queue).
- `get_stats` / `list_messages` — send statistics and history.

A caller branches on `Capability.VALIDATE_ADDRESS in bonded.capabilities` before invoking. Suppression-list hooks (webhook → `add_suppression`) keep `bounces` current automatically.

## Operational Policies

Per-provider declarations light up the framework's cross-cutting machinery automatically:

- **Rate limit.** SendGrid: `rate_limit = RateLimit(rps=10, burst=20)` (free tier; configurable per instance for paid tiers). SMTP2go: `rate_limit = RateLimit(rps=100, burst=200)`. Stalwart: reads the local server's submission queue limit.
- **Health check.** SendGrid: `GET /v3/scopes` with the API key. SMTP2go: `GET /v3/stats/email_summary`. Stalwart: `NOOP` over a kept SMTP connection. Cached with the configured TTL.
- **Degradation.** Transactional sends (invitation, password-reset, MFA contexts) declare `degradation_policy = FailFast()`. Marketing-tagged sends declare `degradation_policy = QueueAndRetry()` so a SendGrid outage returns 202 with a tracking id and drains from the outbox once the upstream recovers.
- **Residency.** EU/US variants of SendGrid are declared as residency-tagged provider instances; the resolution layer routes EU-jurisdiction tenants to the EU instance.

## Shared HTTP Client

SendGrid's SDK accepts a custom HTTP transport (the underlying `python-http-client` has a `session` setter); the SDK is configured to route through `ProviderHTTPClient` to inherit trace propagation, retry/backoff, the rate-limit token bucket, idempotency-key injection, and log redaction. SMTP2go is direct `httpx` and uses the shared client directly. Stalwart's SMTP transport is exempt from HTTP cross-cutting (SMTP submission is a long-lived TCP stream, not request/response). Credential resolution moves to per-request through `instance.api_key.resolve()` rather than per-bond. Rotating `SENDGRID_API_KEY` in OpenBao takes effect within one renewal cycle without a framework restart.
