# RUC Backup, Verification, and Recovery

This guide covers the local disaster-recovery tools for the RUC System on the
Windows Mini-PC.

These tools protect the data needed to recover RUC after PostgreSQL corruption,
workbook corruption, accidental file deletion, source damage, or Mini-PC
replacement.

## Important Rules

- Do not store RUC backups in Git.
- Do not place `.env` inside ordinary backup archives.
- Do not share backup files publicly.
- Treat backups as sensitive because they may contain employee details, photos,
  safety documents, operational records, application accounts, and audit logs.
- A same-disk backup under `D:\RUC_SYSTEM\backups` is only the first copy. It is
  not enough protection against disk failure, theft, ransomware, or total
  machine loss.

## What The Stage 6 Backup Includes

Each backup set includes:

- PostgreSQL `ruc_system` database dump using `pg_dump` custom format.
- `excel_files`, including `excel_files/master/NLZ_MASTER_TRACKER.xlsx`.
- `uploads`.
- `static/uploads`.
- `id_cards`.
- `generated_reports`.
- `manifest.json` with safe metadata, sizes, checksums, and verification status.

Each backup set excludes:

- `.env`.
- `.git`.
- `.venv`.
- `backups` itself.
- `logs`.
- `runtime` locks.
- Python cache files.
- temporary workbook files and Office lock files.

## Backup Location

Local backup sets are written to:

```text
D:\RUC_SYSTEM\backups\disaster\YYYYMMDD_HHMMSS\
```

Each successful set contains:

```text
database\
files\
manifest.json
BACKUP_COMPLETE.marker
```

Failed or incomplete backups do not receive `BACKUP_COMPLETE.marker`.

## Manual Backup

From `D:\RUC_SYSTEM`, run:

```bat
BACKUP_RUC.bat
```

The backup passes only when:

- disk space is not critical;
- `pg_dump` succeeds;
- the database dump exists and is non-zero;
- `pg_restore --list` can read the dump;
- the runtime ZIP archive opens successfully;
- expected runtime folders are represented;
- checksums are written into the manifest.

## Manual Verification

To verify the latest backup:

```bat
.venv\Scripts\python.exe verify_backup.py
```

To verify a specific backup set:

```bat
.venv\Scripts\python.exe verify_backup.py D:\RUC_SYSTEM\backups\disaster\YYYYMMDD_HHMMSS
```

Verification checks:

- manifest exists;
- manifest status is `SUCCESS`;
- completion marker exists;
- database dump exists and is non-zero;
- database dump checksum matches;
- `pg_restore --list` succeeds;
- runtime archive exists and is non-zero;
- runtime archive checksum matches;
- runtime archive opens and is not empty;
- expected categories are represented.

## Restore Test

Restore testing must never overwrite production.

The safe restore-test targets are:

```text
Temporary database:
ruc_system_recovery_test_YYYYMMDD_HHMMSS

Temporary file directory:
D:\RUC_SYSTEM_RECOVERY_TEST\YYYYMMDD_HHMMSS
```

To perform a file-only restore test and remove the temporary extracted copy
after validation:

```bat
RESTORE_TEST_RUC.bat --files-only --cleanup-files
```

To test temporary database restore, configure approved administrative
PostgreSQL restore credentials outside Git, then run:

```bat
RESTORE_TEST_RUC.bat --cleanup-files --cleanup-db
```

Required temporary database restore settings:

```text
RUC_RESTORE_ADMIN_USER
RUC_RESTORE_ADMIN_PASSWORD
```

Optional settings:

```text
RUC_RESTORE_DB_HOST
RUC_RESTORE_DB_PORT
RUC_RESTORE_MAINTENANCE_DB
```

Do not use the application runtime user `ruc_app` for restore testing.

## .env Recovery Policy

`.env` remains ignored by Git and excluded from ordinary Stage 6 backups.

Keep a separate protected recovery copy of `.env` using one of these models:

- encrypted external drive controlled by the administrator;
- encrypted secured network storage;
- administrator-only encrypted off-machine storage.

Do not print `.env` contents in tickets, chat, screenshots, or reports.

If `.env` is lost, the application may not start until the database settings and
`SECRET_KEY` are restored or recreated. If credentials are suspected exposed,
rotate them before returning the system to staff use.

## Off-Machine Backup Policy

After a local backup passes, copy the entire timestamped backup set to at least
one protected off-machine destination.

Recommended destinations:

- encrypted external drive;
- secured NAS folder with restricted access;
- encrypted off-site storage.

Do not use public file sharing. Do not upload backups to an unmanaged cloud
account.

## Retention Policy

The Stage 6 backup script applies retention only to verified Stage 6 disaster
backup sets under:

```text
D:\RUC_SYSTEM\backups\disaster
```

Default retention:

- 7 daily backups;
- 4 weekly backups;
- 6 monthly backups.

Safety rules:

- never delete the only known-good backup;
- never delete unverified backups just because a newer attempt exists;
- never delete Stage 4 workbook safety backups;
- never delete historical source backups;
- never delete manually created PostgreSQL backups;
- never delete unrelated backup folders.

## Disk Space Policy

Before backup creation, the script checks available disk space.

Warning:

- below 20 percent free, or
- below 10 GB free.

Critical:

- below 10 percent free, or
- below 5 GB free.

At critical level the backup fails safely. Do not delete business files to make
space without a separate review.

## Windows Task Scheduler

Do not create scheduled tasks until the administrator approves the timing and
destination policy.

Recommended task:

```text
Name: RUC Database/File Disaster Backup
Program: D:\RUC_SYSTEM\BACKUP_RUC.bat
Schedule: Daily during off-hours
Run whether user is logged on or not: administrator decision
```

Recommended verification task:

```text
Name: RUC Backup Verification
Program: D:\RUC_SYSTEM\.venv\Scripts\python.exe
Arguments: D:\RUC_SYSTEM\verify_backup.py
Schedule: After the backup task
```

Keep the production server task separate from backup tasks. The server-instance
lock in `run_production.py` helps prevent accidental duplicate RUC server
startup, but backup jobs should not start the server.

## Backup While RUC Is Running

PostgreSQL backups can run while RUC is online.

File backups should be scheduled during quiet hours. Stage 4 workbook locking
and atomic replacement reduce the risk of half-written workbook files, but a
backup may still capture a moment before or after a staff update. Avoid backup
windows during heavy Excel/document upload activity.

## Complete Mini-PC Recovery Order

Use this order after complete machine replacement:

1. Prepare the replacement Windows PC.
2. Install supported Python.
3. Install PostgreSQL.
4. Recover the approved RUC source from GitHub or a trusted source checkpoint.
5. Recreate the virtual environment.
6. Install `requirements.txt`.
7. Restore the protected `.env` configuration.
8. Create or verify PostgreSQL roles, including `ruc_app`.
9. Restore the PostgreSQL database from the latest verified custom-format dump.
10. Restore runtime files from the Stage 6 file archive.
11. Verify Windows folder permissions and file paths.
12. Run `test_db.py`.
13. Start the Waitress production runtime.
14. Verify `/healthz`.
15. Verify Super Admin login.
16. Verify HR access rules.
17. Verify Team Leader scoped access.
18. Verify Master Tracker, project workbooks, uploads, ID cards, and reports.
19. Resume staff access.

## Production Restore Warning

Never casually restore over production `ruc_system`.

Always test in a temporary recovery database first when practical. Production
replacement should be done only after confirming the chosen backup is the
correct recovery point and after preserving the damaged current state for
forensic or rollback review if possible.
