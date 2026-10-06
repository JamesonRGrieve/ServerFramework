# Email Extension

This document describes the Email extension implementation.

> **Extension Architecture**: For general extension patterns, architecture, and concepts, see [EXT.Patterns.md](../EXT.Patterns.md).

The Email extension sends (and reads) mail through the Provider Rotation System, over configured provider instances.

## Overview

The `email` extension's primary ability is `email_send`, served by whichever provider instance the rotation reaches first that can send. All providers run their inputs through a shared validation helper so the same denial guarantees apply regardless of which provider is active.

## Providers

Each provider is a subclass of `AbstractEmailProvider` in its own `PRV_*_EMail.py`:

| Provider (`name`) | Transport | Use case |
|-------------------|-----------|----------|
| `sendgrid` | HTTP API (`/v3/mail/send`) | Hosted, marketing & transactional |
| `smtp2go` | HTTP API (`/v3/email/send`) | Hosted SMTP relay with a REST front door |
| `mailgun` | HTTP API (`/<domain>/messages`) | Hosted, send-only |
| `stalwart` | SMTP submission (`aiosmtplib`, 587 + STARTTLS) | Self-hosted Stalwart server |
| `proxmox_mail_gateway` | SMTP relay + PMG REST API (stats, tracking, quarantine) | Self-hosted mail gateway |
| `imap`, `yahoo`, `pop3` | IMAP or POP3 read, SMTP send | Any mailbox; Yahoo with its hosts as defaults |
| `google`, `microsoft` | Gmail / Microsoft Graph over an OAuth access token | Hosted mailboxes |

Send-only providers answer the receive-side methods with a warning and an empty result, so the abstract contract holds without advertising support; callers branch on `capabilities`.

## Configuration: instance settings

A provider's configuration is its instances' settings (`instance_settings`, the catalogue a client renders): the instance's own `api_key` column or a `ProviderInstanceSetting` row per key, read with `cls.setting(instance, key)`. Every provider declares `from_email` and its credential (`api_key`, `password`, `api_token` or `access_token`), plus what its transport needs (`api_url`, `domain`, `host`/`port`, `smtp_*`, `imap_*`/`pop3_*`, `use_tls`/`use_ssl`). Credentials are declared `secret`: stored encrypted and never returned once written.

Each setting names the environment variable it read before instances carried settings (`SENDGRID_API_KEY`, `STALWART_HOST`, `PMG_API_URL`, …). That variable is a default **for the operator's instances only** — those in the root or system scope, or owned by ROOT or SYSTEM and no team (the `Root_<Provider>` instances the framework seeds from the environment carry the default scope but no owner; `speaks_for_operator`). A user's or team's instance never sends with the operator's credentials, from the operator's address, or through the operator's servers. A provider's `_env` is derived from these declarations; the framework seeds the operator's root instance and its place in the root rotation from it. The extension itself declares no variables.

A user's or team's instance names its own mail servers and API addresses, so they are held to the SSRF guard (`destination`, `mail_server`): an address on the server's own network is refused unless `EGRESS_ALLOWED_HOSTS` lets it through. The operator's are not (a self-hosted mail server is often on the private network). Webhook verification secrets (`SENDGRID_WEBHOOK_PUBLIC_KEY`/`_SECRET`, `SMTP2GO_WEBHOOK_SECRET`, `STALWART_WEBHOOK_SECRET`, `PMG_WEBHOOK_SECRET`) are the operator's, read from the environment: a provider's webhook endpoint is one per provider, not per instance.

Inbound mail (mail received into the app, not listed on request) comes in through the signed endpoint and the IMAP poller described under [Inbound mail](#inbound-mail).

## Architecture

### Extension class structure

```python
class EXT_EMail(AbstractStaticExtension):
    name: ClassVar[str] = "email"
    # Filled by the @ability methods: email_send, email_get, email_search,
    # email_reply, email_draft, the message and thread actions, and
    # email_status (the version and the providers offered).
    _abilities: ClassVar[Set[str]] = set()
```

Mail goes through configured provider instances, so no ability reports the
server's environment.

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

### Inbound mail

`InboundEmail.py` is the hook point received mail enters through. Whatever
receives a message (a provider reading a mailbox, an endpoint a mail server
delivers to) parses it with `InboundEmail.parse(raw, envelope_recipients)`
(sender, every recipient lower-cased, subject, plain-text body, headers; at
most 25 MiB) and calls `await receive_inbound_email(model_registry,
message)`. Listeners registered with `on_inbound_email(extension_name,
listener)` are awaited as `listener(model_registry, message)` in
registration order, but only for an app that loaded `extension_name`: the
table is process-global, and a listener neither runs nor counts in an app
without its extension. One listener's failure is logged and the others
still get the message. The
`ai_agents` extension listens here to fire email triggers. Two sources feed
it: the signed endpoint and the IMAP poller below.

Both sources take their configuration from provider instances in the
**root or system scope** only (which only ROOT and SYSTEM can create or
configure): what they deliver is trusted as the operator's mail.

#### Signed inbound endpoint

A mail server (a Postfix `pipe` transport, a provider's inbound route) POSTs
the raw message, exactly as received, to
`POST /v1/email/inbound/{provider_instance_id}`. The instance is any email
provider instance in the root or system scope, with its
`inbound_signing_secret` setting set (write-only, stored encrypted, at
least 32 characters).

| Header | Value |
|--------|-------|
| `Content-Type` | `message/rfc822` |
| `X-Zephyrex-Timestamp` | Unix seconds at signing |
| `X-Zephyrex-Recipients` | The envelope recipients (`RCPT TO`), comma-separated bare addresses; empty or absent when unknown |
| `X-Zephyrex-Signature` | `sha256=` + lower-case hex HMAC-SHA256 |

The signed bytes are the timestamp, a line feed (`\n`), the
`X-Zephyrex-Recipients` value exactly as sent (empty when absent), a line
feed, then the raw body, keyed by the secret:

```
signature = "sha256=" + hex(HMAC_SHA256(secret, timestamp + "\n" + recipients + "\n" + body))
```

A header value cannot contain a line feed, so the recipients cannot be
shifted into the body (or back) under the same signature. From a shell:

```sh
ts=$(date +%s); rcpt="$RECIPIENT"
sig=$( { printf '%s\n%s\n' "$ts" "$rcpt"; cat message.eml; } |
       openssl dgst -sha256 -hmac "$SECRET" -r | cut -d' ' -f1)
curl -sf -X POST "https://app.example.org/v1/email/inbound/$INSTANCE_ID" \
  -H 'Content-Type: message/rfc822' -H "X-Zephyrex-Timestamp: $ts" \
  -H "X-Zephyrex-Recipients: $rcpt" -H "X-Zephyrex-Signature: sha256=$sig" \
  --data-binary @message.eml
```

Answers:

- **200** `{"message_id", "listeners"}`: parsed and handed to the listeners.
- **401** `Signature verification failed`, whatever the reason: an unknown,
  disabled, deleted or user-scoped instance, no or a short secret, a wrong
  signature, a timestamp more than 300 seconds from the server's clock, or
  a signature already used within those 300 seconds (replay cache). The
  answer says nothing about which instances exist.
- **413**: a body over 25 MiB. The app-wide `MAX_REQUEST_BODY_BYTES` cap
  (10 MiB by default) applies first; raise it to take larger mail.
- **400**: a signed delivery whose body is empty or whose recipients are not
  bare addresses (at most 100).

The route is rate-limited (300 a minute per client address), takes no
session or token (one sent is ignored), and is open to cross-site POSTs and
non-JSON bodies.

#### IMAP poller

`SVC_InboundIMAP.InboundIMAPService` runs with the background services
(`RUN_BACKGROUND_SERVICES=true`). Every 5 seconds it polls the mailboxes
that are due: root- or system-scoped, enabled instances of the `imap`
provider (or `yahoo`, which defaults the host) whose `inbound_enabled` is
`true`.

| Setting | Default | Meaning |
|---------|---------|---------|
| `inbound_enabled` | `false` | `true` to poll this mailbox |
| `inbound_host`, `inbound_port` | —, `993` | The IMAP server |
| `inbound_security` | `tls` | `tls` (implicit), `starttls`, or `plain` |
| `inbound_allow_plaintext` | `false` | `plain` is refused unless this is `true` **and** the host is loopback |
| `inbound_ca_certificate` | — | PEM CA to verify the server against instead of the system store |
| `inbound_username`, `inbound_password` | — | The account; the password is write-only and encrypted |
| `inbound_mailbox` | `INBOX` | The mailbox read |
| `inbound_poll_seconds` | `60` | 10 to 86400 |
| `inbound_after` | `seen` | `seen` (flag `\Seen`; only unseen mail is read), `move` (to `inbound_move_to`; MOVE, or COPY + UID EXPUNGE with UIDPLUS) or `delete` (UID EXPUNGE, needs UIDPLUS) |

The certificate and host name are always verified, and credentials are
never sent before the link is encrypted (a server not offering STARTTLS is
refused). Each socket operation times out after 30 seconds.

Each poll reads at most 50 messages above the mailbox's cursor
(`email_inbound_mailboxes`: its UIDVALIDITY and the highest UID taken),
oldest first; with more waiting, the next poll is immediate. A message is
fetched with `BODY.PEEK[]`, claimed by a compare-and-set of the cursor (of
two workers, one delivers it), parsed, handed to the listeners, and then
flagged, moved or deleted. A UID at or below the cursor is never delivered
again. When UIDVALIDITY changes the cursor starts over under the new value;
mail already delivered stays out by its `\Seen` flag, or by having been
moved or deleted. A message over 25 MiB is passed over and left in place.

A failed poll (unreachable, refused sign-in, a server missing what
`inbound_after` needs) is logged without credentials, recorded as the
cursor's `last_error`, and retried after twice the interval each time, up to
an hour. Other mailboxes and the service carry on.

### Invitation email

When `InvitationManager` adds an invitee it calls `BLL_EMail.send_invitation_email_hook`, which queues the invitation through `EXT_EMail.send_invitation_email`: the root rotation sends it, through whichever providers the operator configured there, failing over attempt by attempt in the background.

## Dependencies

Each provider declares its own pip dependencies (`sendgrid`, `aiosmtplib`, `httpx`, `requests`, the Google client libraries); the extension's `zephyrex[email]` extra and `manifest.toml` list their union (`pip_requirements`).

## Usage

### Sending email via the rotation system

```python
sent = await EXT_EMail.send_email(recipient="user@example.com", subject="Welcome!", body="...")
# or, without waiting on the provider:
EXT_EMail.queue_email("user@example.com", "Welcome!", "...")
```

`send_email` rotates `send_via_provider` over the root rotation's instances; a provider that cannot send raises a typed error and the rotation moves on to the next.

### Checking extension status

```python
EXT_EMail.get_extension_status()
# {"extension": "email", "version": "1.1.0", "providers": ["google", "imap", ...]}
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

### Credential handling

- Credentials are the instance's own (its `api_key` column or a secret setting row), else, for the operator's instances only, the environment; they never appear in log messages (see `lib/Logging.py` redaction patches).
- `bond_instance` returns an `AbstractProviderInstance_SDK` wrapping the SDK or connection config, never the raw key.

## Testing

Providers are tested against real protocol servers in process, configured on real provider instances: `IMAPTestServer` (IMAP4rev1), and `MailAPITestServers` (SendGrid's and SMTP2go's send APIs and PMG's REST API, each recording what it was sent). `EmailTestSupport.email_instance` makes an instance of any scope and owner with settings. `PRV_EMail_InstanceSettings_test.py` holds each provider's declared settings to the variables it reads, its credentials to `secret`, and every user's and team's instance to its own settings (and to the SSRF guard). Each provider's test class in `PRV_SendGrid_EMail_test.py` also inherits `AbstractEmailProviderSecurityTests`, the input-denial matrix.

## Troubleshooting

- **Nothing sends**: the root rotation needs an instance that can send; an operator instance with no settings of its own reads the provider's variables (`SMTP2GO_API_KEY`, …).
- **Stalwart connection refused**: confirm the instance's `host` is reachable on its `port`; submission ports are typically 465 (TLS), 587 (STARTTLS), or 25 (cleartext, deprecated).
- **A user's instance cannot reach its server**: an address on the server's own network is refused for a user's or team's instance; add it to `EGRESS_ALLOWED_HOSTS` if it is meant to be reached.
- **CRLF / NUL / oversized rejection**: surfaces as a typed `EmailHeaderInjectionError` / `EmailMalformedAddressError` / `EmailPayloadTooLargeError` (subclasses of `InvalidInputExternalError`); strip those characters from upstream input.

## Typed Errors

Every email-provider entry point raises typed exceptions on failure and returns `SentMessage(id, provider, accepted_at)` on success. Validation failures from `_validate_send_inputs` map to typed `InvalidInputExternalError` subclasses: `EmailHeaderInjectionError` (CRLF in headers), `EmailPayloadTooLargeError` (body > 10 MiB or subject > 998 octets), `EmailMalformedAddressError` (`parseaddr` rejection, homograph guard), `EmailAttachmentTraversalError` (relative/`..`/NUL in attachment paths). Upstream failures map to the canonical hierarchy: 4xx → `InvalidInputExternalError`, 5xx → `TransientExternalError`, 429 → `RateLimitExternalError`, 401/403 → `AuthExternalError`. Security tests assert `with pytest.raises(EmailHeaderInjectionError)` rather than substring-matching error strings.

## Bonded Provider Instance

`AbstractEmailProviderInstance(AbstractProviderInstance)` declares the typed abilities (`send`, `send_bulk`, `list_emails`, `get_email`, `update_email`, `reply`, `download_attachment`, `list_threads`) plus a typed `capabilities: ClassVar[FrozenSet[Capability]]`. `bond_instance` returns the typed instance; `_instance` is declared as a typed `ClassVar` and a mypy gate enforces the contract. Call sites use `bonded.send(message)` rather than `Provider.send(provider_instance, message)`.

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
