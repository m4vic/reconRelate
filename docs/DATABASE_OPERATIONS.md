# Database operations runbook

ReconRelate uses the path in `RECONRELATE_DB_PATH`. Database commands do not initialize recon
providers or an LLM.

## Check

```powershell
reconrelate db check
reconrelate db check --json
```

The check runs SQLite integrity and foreign-key checks and verifies the required ReconRelate tables
and applied migration versions. Stop writes and restore a known-good backup if the status is not
`ok`.

## Backup

```powershell
reconrelate db backup
reconrelate db backup --out D:\backups\reconrelate.sqlite
```

Backups use SQLite's online backup API, are written through a temporary file, and are checked before
being moved into place. Existing output files are protected unless `--force` is supplied.

## Restore

First inspect the candidate, then restore it:

```powershell
$env:RECONRELATE_DB_PATH='D:\reconrelate\active.sqlite'
reconrelate db check
reconrelate db restore D:\backups\reconrelate.sqlite --yes
reconrelate db check
```

Restore rejects corrupt or non-ReconRelate databases. If the active database exists, ReconRelate
creates and verifies a timestamped `.pre-restore.*.bak` beside it before replacement. Keep this path
until the restored runs and reports have been inspected. To roll back, restore that safety backup
with the same command.

Do not run a scan or another process writing the same database during restore.

## Retention

Retention requires an explicit ISO-8601 cutoff and previews by default:

```powershell
reconrelate db retention --before 2025-01-01
reconrelate db retention --before 2025-01-01 --cache-before 2026-01-01
```

Apply only after reviewing the counts:

```powershell
reconrelate db retention --before 2025-01-01 --cache-before 2026-01-01 --apply --yes
```

An automatic verified backup is created before any matched data is deleted. Run deletion removes
the run's graph, observations, claims, and evidence in one transaction. Cache retention is optional
and independent. ReconRelate deliberately has no implicit retention period yet because projects may
have different authorization and privacy requirements.
