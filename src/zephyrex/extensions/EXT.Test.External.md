# External-API Test Contract

The framework's most-emphasized testing principle is no mocks: real
implementations, real databases, real server connections. The principle
is reconciled with external-federation tests via two pytest markers
and a small fixture.

## The two markers

### `@pytest.mark.external_api(provider="stripe")`

Marks a test that requires real (sandbox) credentials. The test:

- **Runs end-to-end against the sandbox** when the provider's env vars
  are set to non-empty values.
- **Is auto-xfailed** (strict=False) when any required env var is
  missing. The xfail reason names the missing variables so the operator
  knows what to set.

```python
@pytest.mark.external_api(provider="stripe")
def test_stripe_customer_create(sandbox_credentials_for):
    creds = sandbox_credentials_for("stripe")
    # creds is {"STRIPE_API_KEY": "sk_test_...", "STRIPE_WEBHOOK_SECRET": "whsec_..."}
    # The marker has already xfail-skipped this test if those env vars
    # weren't populated, so we can use them directly.
    client = stripe.Client(api_key=creds["STRIPE_API_KEY"])
    customer = client.customers.create(email="test@example.com")
    assert customer.id.startswith("cus_")
```

### `@pytest.mark.external_smoke`

Marks a test that **deliberately runs without credentials**. Its purpose
is to verify that the framework's configuration-failure path surfaces
correctly — that a missing credential produces a clean, actionable
error rather than a stack trace or a silent fallback.

```python
@pytest.mark.external_smoke
def test_missing_stripe_key_surfaces(monkeypatch):
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="STRIPE_API_KEY"):
        Stripe_Provider.bond_instance({})
```

CI in branches without secrets runs the smoke set only; CI in protected
branches with secrets runs both.

## Registered providers

The following providers ship pre-registered. Each row's env vars must
**all** be set for the corresponding `external_api` marker to run the
test rather than xfail it.

| Provider   | Required env vars                                |
|------------|--------------------------------------------------|
| `stripe`   | `STRIPE_API_KEY`, `STRIPE_WEBHOOK_SECRET`        |
| `sendgrid` | `SENDGRID_API_KEY`                               |
| `twilio`   | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`        |
| `github`   | `GITHUB_TOKEN`                                   |

## Registering a new provider

Extensions that ship their own external-API tests register their
provider's env-var requirements at conftest import time:

```python
# src/extensions/myext/conftest.py
from src.conftest import register_external_api_provider

register_external_api_provider(
    "myext_upstream",
    ["MYEXT_UPSTREAM_API_KEY", "MYEXT_UPSTREAM_REGION"],
)
```

After that, tests in the extension can use
`@pytest.mark.external_api(provider="myext_upstream")` and the auto-xfail
machinery picks them up.

## Sandbox-credential management policy

- **Ownership.** Each upstream's sandbox credentials are owned by the
  team that maintains the corresponding extension. The framework
  maintainers own the credentials for any framework-shipped provider.
- **Rotation.** Sandbox keys rotate at minimum every 90 days, immediately
  on personnel change, and on any suspected exposure.
- **Scope.** Sandbox keys are issued by the upstream specifically for
  test use and are **scoped to test-only operations** — they cannot
  affect production data. Production credentials are never permitted in
  the test environment.

## What replaces the "mock the rotation system" pattern

The previous documentation in `PRV.External.md` instructed authors to
"mock external API calls in tests using provider rotation patterns."
That pattern is **removed**. The replacement is:

1. **Sandbox-credential tests** marked `external_api` — the default path
   for any test exercising real upstream behavior.
2. **Smoke tests** marked `external_smoke` — the default path for any
   test that deliberately verifies missing-credential handling.
3. **`PRV_Fake_*` providers** — opt-in offline-CI fixtures, used only
   when sandbox access is unavailable for a particular CI environment
   (e.g., contributor PRs from forks). When used, the fake provider is a
   real class (not a mock), implements the provider contract, and is
   wired through the registry exactly like any other provider.

No test in the suite uses `unittest.mock` against an external API.

## Acceptance criteria

- Running the test suite without any external credentials configured
  produces xfail-pass results for all `external_api`-marked tests with a
  clear reason.
- Running the same suite with sandbox credentials configured produces
  real-pass results from real upstream calls.
- No test in the suite uses a mock for an external API.

## Federation matrix tests

> **Detailed reference:** [../lib/LIB.Federation.md](../lib/LIB.Federation.md#matrix-homologation-testing) | **Extension creation:** [EXT.Patterns.md](EXT.Patterns.md#step-5-external-federation-optional)

Extensions that federate an external upstream — REST or GraphQL — get 4 quadrants × 5 CRUD = 20 cells of homologation coverage automatically via the programmatic test generator. The fixtures follow the same credentials-gating contract as `external_api`-marked tests:

- **In-process upstreams** (the default) bind to a tiny FastAPI ASGI app. These run on every CI branch, including PRs from forks, and are deterministic. The fixture's `requires_credentials` is False.
- **Live upstreams** activate when `requires_credentials=True` and the fixture's `credentials_present()` callable returns True. The same env-var check that gates `external_api` markers gates the matrix's live runs (e.g., Stripe's matrix runs live when `STRIPE_API_KEY` is set; otherwise pytest auto-xfails the suite).

The bundled `EXT_Payment` (Stripe) and `EXT_EMail` (SendGrid) extensions ship `federation_matrix_fixtures` classmethods showing both shapes — see their `EXT_*.py` files for reference. Adding a new external extension's matrix is a single classmethod and zero new test files; the generator picks the fixture up at collection time.
