# RUC Operator Checklist

This checklist is for the person responsible for checking the local RUC System.

## Daily Checks

- Open RUC in the browser.
- Confirm the login page loads.
- Confirm `/healthz` returns healthy.
- Confirm staff can log in with the expected role.
- Confirm no one reports workbook busy errors that persist after Excel is
  closed.
- Confirm disk space is not low.

## Weekly Checks

- Run `test_db.py`.
- Run `RUN_RUC_TESTS.bat`.
- Confirm the latest Stage 6 backup has `BACKUP_COMPLETE.marker`.
- Run `verify_backup.py` against the latest backup.
- Confirm the off-machine backup copy exists when a destination has been
  configured.
- Review `logs\ruc.log` or the current log folder for repeated critical startup,
  database, workbook, or backup errors.
- Confirm `runtime\locks` is not accumulating stale files.

## Backup Check

A backup is acceptable only when:

- the backup folder has `BACKUP_COMPLETE.marker`;
- `manifest.json` status is `SUCCESS`;
- `verify_backup.py` passes;
- the database backup is non-zero;
- the runtime archive opens;
- the backup has been copied to approved off-machine storage when configured.

Do not rely on a zero-byte backup.

## Disk Space Warning

Warning level:

- below 20 percent free, or
- below 10 GB free.

Critical level:

- below 10 percent free, or
- below 5 GB free.

At critical level, stop and ask for technical review. Do not delete business
records or uploaded documents to make space without approval.

## What Not To Delete Manually

Do not manually delete:

- `excel_files`;
- `excel_files\master`;
- `uploads`;
- `static\uploads`;
- `id_cards`;
- `generated_reports`;
- `backups`;
- `.env`;
- PostgreSQL data folders;
- project workbooks;
- Master Tracker;
- employee photos or safety documents.

Temporary folders such as `runtime\locks` should be reviewed before cleanup.
Do not delete active lock files while RUC is running.

## When To Ask For Help

Ask for technical help when:

- RUC does not start;
- `/healthz` is not healthy;
- `test_db.py` fails;
- backups fail or do not verify;
- disk space is critical;
- Excel workbooks repeatedly report busy or corrupted;
- users see access denied unexpectedly;
- uploaded documents are missing;
- any credential or `.env` file may have been exposed.
