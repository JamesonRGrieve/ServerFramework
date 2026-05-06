# `backup_restore` extension

Backup commands, archival targets, and the restore-drill runner.
Formerly `lib/Backup.py` and `lib/_restore_drill_runner.py` in the core.

## Why this is an extension

Backup strategy is deployment-specific (storage target, RTO/RPO,
encryption, schedule). The framework should not assume any of this; it
provides DB session management as a core primitive and lets deployments
opt into the backup machinery.

## Layout

```
extensions/backup_restore/
├── __init__.py
├── BLL_Backup.py                  # BackupCommand, BackupTarget, RestoreDrillService
├── BLL_RestoreDrillRunner.py      # `python -m ...` driver invoked by CI workflow
├── EXT_BackupRestore.py
├── manifest.toml
└── migrations/versions/
```

CI workflow `.github/workflows/restore-drill.yml` invokes
`python -m serverframework.extensions.backup_restore.BLL_RestoreDrillRunner`
on push to main and on the monthly cron.
