# `audit_retention` extension

Retention policy management, archival to object storage, and legal-hold
workflows. Formerly under `extensions/Retention*.py` + the admin endpoint
under `endpoints/EP_Retention_Admin.py` in the core.

## Why this is an extension

Retention is a compliance/operational concern, not a framework primitive.
Single-region, low-regulation deployments do not need jurisdictional
windows or legal holds. The extension makes it opt-in and keeps the core
free of regulatory assumptions.

## Layout

```
extensions/audit_retention/
├── __init__.py
├── BLL_RetentionPolicy.py      # Policy entity + window parsing
├── BLL_RetentionService.py     # Apply policy, place / release legal holds
├── BLL_RetentionArchive.py     # Archival targets (S3 / GCS / local)
├── EP_Retention_Admin.py       # Admin endpoints for legal hold release
├── EXT_AuditRetention.py       # Extension class
├── manifest.toml
└── migrations/versions/
```
