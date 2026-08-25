# RUC System Telecom/Safety Upgrade

This upgrade extends the existing local Flask + PostgreSQL + OpenPyXL + Pillow RUC System. It does not replace the current application, existing tables, project Excel files, master tracker, uploads, or ID-card workflow.

## 1. Back Up PostgreSQL

Open PowerShell and run:

```powershell
$env:PGPASSWORD="YOUR_LOCAL_DB_PASSWORD"
& "C:\Program Files\PostgreSQL\13\bin\pg_dump.exe" -h localhost -U postgres -d ruc_system -F c -f "D:\RUC_SYSTEM\backups\ruc_system_before_telecom_safety.backup"
```

If you use environment variables for the local DB password, replace the placeholder with your current local `DB_PASSWORD`.

## 2. Run The Migration

```powershell
$env:PGPASSWORD="YOUR_LOCAL_DB_PASSWORD"
& "C:\Program Files\PostgreSQL\13\bin\psql.exe" -h localhost -U postgres -d ruc_system -f "D:\RUC_SYSTEM\db_upgrade_telecom_safety.sql"
```

The migration is written with `IF NOT EXISTS` and guarded constraints so it can be run again safely.

For Phase 2 Telecom Site / DUID Management, run the additional idempotent migration:

```powershell
$env:PGPASSWORD="YOUR_LOCAL_DB_PASSWORD"
& "C:\Program Files\PostgreSQL\13\bin\psql.exe" -h localhost -U postgres -d ruc_system -f "D:\RUC_SYSTEM\db_phase2_telecom_sites.sql"
```

This creates the `telecom_sites` operational table without modifying `globe_nlz`, `planning_reference`, or the master tracker workbook.

For Phase 3 Personnel & Safety Dossier Management, run:

```powershell
$env:PGPASSWORD="YOUR_LOCAL_DB_PASSWORD"
& "C:\Program Files\PostgreSQL\13\bin\psql.exe" -h localhost -U postgres -d ruc_system -f "D:\RUC_SYSTEM\db_phase3_personnel_safety.sql"
```

This keeps the existing `safety_documents` table, adds current/history metadata, and preserves uploaded safety document records.

For Phase 4 Daily Site Operations & Attendance, run:

```powershell
$env:PGPASSWORD="YOUR_LOCAL_DB_PASSWORD"
& "C:\Program Files\PostgreSQL\13\bin\psql.exe" -h localhost -U postgres -d ruc_system -f "D:\RUC_SYSTEM\db_phase4_daily_operations.sql"
```

This adds `daily_site_logs`, `daily_attendance`, and `daily_log_files` for local daily field reports without modifying tracker/reference data.

## 3. Configure Local Secrets

The app requires local configuration from environment variables or a local `.env` file. Do not store real passwords or secrets in source code.

Recommended local setup:

```powershell
Copy-Item .env.example .env
notepad .env
```

In `.env`, enter either a local `DATABASE_URL` or all individual `DB_*` values, and set a long random `SECRET_KEY`.

Individual PostgreSQL variables:

```powershell
$env:DB_HOST="localhost"
$env:DB_PORT="5432"
$env:DB_NAME="ruc_system"
$env:DB_USER="RUC_USER"
$env:DB_PASSWORD="YOUR_LOCAL_DB_PASSWORD"
$env:SECRET_KEY="GENERATE_A_LONG_RANDOM_SECRET"
```

Or use `DATABASE_URL` instead of the individual `DB_*` values:

```powershell
$env:DATABASE_URL="postgresql://RUC_USER:RUC_PASSWORD@localhost:5432/ruc_system"
$env:SECRET_KEY="GENERATE_A_LONG_RANDOM_SECRET"
```

Never commit `.env`.

## 4. Test Database Connection

```powershell
python test_db.py
```

Expected output:

```text
Database connected successfully!
```

## 5. Start Flask Locally

```powershell
python app.py
```

Open:

```text
http://localhost:5000
```

## 6. Application Smoke Test

After logging in as an admin:

1. Open Dashboard and confirm the original project list appears.
2. Create a project and confirm an Excel workbook appears in `D:\RUC_SYSTEM\excel_files`.
3. Open a project form and submit an employee with photo, NBI, WAH, certificate, signature, and optional First Aid details.
4. Search employees by name, employee ID, project, DUID, role, and safety status.
5. Edit an employee and confirm the safety statuses recalculate from expiry dates.
6. Open Safety Documents and confirm NBI, WAH, and First Aid metadata is listed.
7. Open Sites and confirm existing `globe_nlz.du_id` records are shown.
8. Open a Site Detail page from a DUID.
9. Register or edit an operational site record and confirm tracker/reference fields remain unchanged.
10. Create a Site Assignment using an existing DUID.
11. Create and update a Telecom Task from the global task page or Site Detail.
12. Create and update a Permit To Work.
13. Record a Toolbox Talk with attendance.
14. Create and update an Incident Report with attachments.
15. Generate an ID card and confirm employee ID, telecom role, DUID, and safety badge appear.
16. Print the ID preview.
17. Download the Master Tracker and upload a valid updated tracker.
18. Open Search or Safety Compliance and click View Dossier for an employee.
19. Confirm the dossier shows current NBI, WAH, First Aid, assignment, and legacy RUC files.
20. Replace a safety document from Edit Employee and confirm the old safety document appears as history.
21. Try assigning a missing/expired worker to a DUID and confirm the safety warning appears before final confirmation.
22. Open Daily Operations and confirm daily report filters load.
23. From Site Detail, create a Daily Report with work completed, blockers, next-day plan, attendance, and site evidence.
24. Confirm the Daily Report detail page shows site, report, attendance, safety snapshots, and evidence files.
25. Edit the Daily Report and confirm existing evidence remains available.
26. Confirm the site operational stage/progress updates from the report while `NLZ_MASTER_TRACKER.xlsx` remains unchanged.
27. Export Daily Operations to Excel.

## Notes

- Uploaded operational files remain local under `D:\RUC_SYSTEM\static\uploads\projects`.
- Existing legacy uploads remain under `D:\RUC_SYSTEM\uploads`.
- Existing project workbooks keep the current `<project_code>.xlsx` naming convention.
- The master tracker keeps its original sheets; the app adds/updates a `RUC SAFETY` sheet when employee safety data is submitted.
- Daily report evidence remains local under `D:\RUC_SYSTEM\static\uploads\projects\<project_code>\sites\<duid>\daily_logs\<YYYY-MM-DD>`.
- Daily report data can sync into `DAILY LOGS` and `ATTENDANCE` sheets in an existing project workbook when the daily log is tied to a project.
