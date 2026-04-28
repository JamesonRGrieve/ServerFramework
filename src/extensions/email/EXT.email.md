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
- **CRLF / NUL / oversized rejection**: returned as `"Failed to send email: ..."`; this is the helper, not the SDK. Strip those characters from upstream input.
