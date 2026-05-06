# `privacy` extension

PII classification metadata, log redaction helpers, right-to-erasure
orchestration, and GDPR/CCPA data export. Formerly `lib/PII.py` in the
core.

## Why this is an extension

Compliance/privacy is not a framework primitive. Deployments without PII
obligations don't carry classification metadata or erasure orchestration.
Extracting privacy keeps the core scaffold neutral on jurisdiction.

## Layout

```
extensions/privacy/
├── __init__.py
├── BLL_PII.py            # PIIClass enum, redact_field, erasure orchestrator
├── EXT_Privacy.py
├── manifest.toml
└── migrations/versions/
```
