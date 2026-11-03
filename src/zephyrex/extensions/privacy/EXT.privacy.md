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
├── Erasure.py            # the erasers an account deleting itself runs
├── Purge.py              # the daily purge of what was deleted
├── EXT_Privacy.py
└── manifest.toml
```

## Erasure on account deletion

An account deleting itself (`DELETE /v1/user`) first runs the eraser each
loaded extension registered (`register_eraser`), in reverse registration
order. When they all succeed, its sign-in records go last: the erasers
registered with `signs_out=True` (auth_session's, which revokes and deletes
the account's sessions), then its credentials. A failed eraser keeps the
account, signed in, to try again.

## Purge

A delete is soft: the row is hidden but stays. Set
`PRIVACY_PURGE_DELETED_AFTER_DAYS` (a whole number of days) and a daily
service removes, for good, every row in every table deleted longer ago than
that. A deleted row that anything still points at stays (a live record filed
under it, a payment record naming its account, including through a foreign
key only the database declares), so no `ON DELETE CASCADE` ever takes a live
row with it. Unset, deletions stay soft for ever.
