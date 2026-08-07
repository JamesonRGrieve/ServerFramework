# `quota` extension

Unified per-user / per-team / system quota model and atomic-decrement
helpers. Formerly `logic/Quota.py` in the core.

## Why this is an extension

A generic server framework should not assume that deployments enforce
usage caps. Some products bill per-call (no caps); others enforce
hard quotas. This extension is the opt-in path for the cap-enforcing
shape.

## Optional dependency: `billing`

USD-denominated caps (e.g. "stop charging this team after $50/month")
require a cost model that translates calls into dollars. The `billing`
extension provides that model. When `billing` is not loaded, only the
unit-denominated quotas (calls, tokens, bytes, messages, rows) work;
USD caps degrade to a no-op.

## Surface

- `Quota` — the entity model. Fields: scope, period, period_key, owner_id,
  unit, limit, remaining.
- `derive_period_key(period, now=None)` — deterministic period key in UTC.
- `QuotaExhaustedError` — raised by `try_decrement` when remaining < n.
- `try_decrement(quota, n=1)` — atomic; returns `True` on success, raises
  on exhaustion.

## Integration

`BLL_Providers.resolve_provider_instance` calls into this extension to
gate provider rotation by quota. Imports are lazy (try/except ImportError
inside the rotation hot path) so providers continue to function when
the extension is not loaded.
