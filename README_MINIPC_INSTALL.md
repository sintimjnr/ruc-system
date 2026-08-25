# RUC Mini-PC Installation And Handoff Guide

This guide prepares a future Windows Mini-PC for the RUC System. It is written
for the later handoff step. Do not use it to expose RUC to the public internet.

The current approved source checkpoint is:

```text
65da4863a548e504f2b8a8a781b788e1746f90c9
```

## 1. Install Windows Prerequisites

1. Prepare the Windows Mini-PC.
2. Apply Windows updates.
3. Use a stable local folder such as `D:\RUC_SYSTEM`, or record the chosen
   folder if another path is required.
4. Keep PostgreSQL local to the Mini-PC. Do not expose PostgreSQL to client
   computers.

## 2. Install Python

Install a supported Python 3 version compatible with the current virtual
environment and packages.

Confirm Python is available:

```bat
python --version
```

## 3. Install PostgreSQL

Install PostgreSQL 13 or a tested compatible version.

Install the PostgreSQL command-line tools:

- `psql`
- `pg_dump`
- `pg_restore`

Keep the PostgreSQL administrator password protected.

## 4. Obtain RUC Source

Restore the approved source from GitHub or a trusted source package.

For Git-based restore:

```bat
git clone <APPROVED_RUC_REPOSITORY_URL> D:\RUC_SYSTEM
cd /d D:\RUC_SYSTEM
git checkout 65da4863a548e504f2b8a8a781b788e1746f90c9
```

If a different folder is used, update the operator notes and Task Scheduler
paths later.

## 5. Create The Virtual Environment

From the RUC project folder:

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 6. Create PostgreSQL Runtime Role

Use a PostgreSQL administrative account to create the application runtime role.

Use `setup_ruc_database.example.sql` as a template only. Copy it first, replace
the placeholder password, and keep the real copy out of Git.

The `ruc_app` role must remain:

- login-enabled;
- not a superuser;
- no `CREATEDB`;
- no `CREATEROLE`;
- restricted to the RUC database/schema/table/sequence grants needed by the app.

## 7. Create Or Restore `ruc_system`

For a production transfer, restore from the latest verified Stage 6 PostgreSQL
backup instead of creating empty business tables.

Use a PostgreSQL administrative role for restore. Do not use `ruc_app` for
restore.

After restoring, confirm required tables exist:

- `admins`
- `projects`
- `employees`
- `globe_nlz`
- `telecom_sites`
- `site_assignments`
- `teams`
- `team_memberships`
- `audit_logs`

Reapply grants to `ruc_app` after restore if needed.

## 8. Restore Runtime Files

Restore the Stage 6 runtime archive into the RUC project folder.

Required runtime folders:

- `excel_files`
- `excel_files\master`
- `uploads`
- `static\uploads`
- `id_cards`
- `generated_reports`

Do not restore:

- `.git` from a backup archive;
- `.venv` from another machine;
- runtime lock files;
- Python cache;
- Office lock files.

## 9. Create Protected `.env`

Copy `.env.example` to `.env` and replace placeholders. Do not commit `.env`.

Required values:

- `SECRET_KEY`
- either `DATABASE_URL`, or all of `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`,
  `DB_PASSWORD`

Recommended values:

- `RUC_HOST`
- `RUC_PORT`
- `RUC_THREADS`
- `RUC_LOG_DIR`
- `RUC_LOG_LEVEL`
- `WORKBOOK_LOCK_TIMEOUT_SECONDS`
- `WORKBOOK_STALE_LOCK_SECONDS`
- `LOGIN_RATE_LIMIT_ATTEMPTS`
- `LOGIN_RATE_LIMIT_WINDOW_SECONDS`
- `LOGIN_RATE_LIMIT_BLOCK_SECONDS`
- `SECURITY_AUDIT_THROTTLE_SECONDS`

Optional values:

- `MAX_UPLOAD_BYTES`
- `SESSION_COOKIE_SAMESITE`
- `RUC_SESSION_HOURS`

Future HTTPS or reverse-proxy only:

- `SESSION_COOKIE_SECURE=true`
- `RUC_TRUST_PROXY_HEADERS=true`

Keep `RUC_TRUST_PROXY_HEADERS=false` unless a trusted reverse proxy is actually
configured.

## 10. Verify Database And Tests

Run:

```bat
.venv\Scripts\python.exe test_db.py
RUN_RUC_TESTS.bat
```

Expected:

- database connection passes;
- regression tests pass;
- Flask route count remains 88.

## 11. Start RUC With Waitress

Start production runtime:

```bat
START_RUC.bat
```

Verify:

```text
http://127.0.0.1:5000/healthz
```

Expected response:

```json
{"status":"ok"}
```

## 12. Verify User Access

Verify login and role access:

- Super Admin can access dashboard, users, reports, Master Tracker, and project
  workbooks.
- HR can access personnel, safety, authorized ID workflow, and personnel
  reports.
- HR cannot access Master Tracker or project workbooks.
- Team Leader can access only assigned team/site operations.
- Team Leader cannot access unrelated sites, Master Tracker, project workbooks,
  User Management, Audit Logs, or global ID generation.

## 13. LAN Binding

Default:

```text
RUC_HOST=127.0.0.1
```

Only after approval for private LAN use:

```text
RUC_HOST=0.0.0.0
```

Do not expose RUC to the public internet in this step.

## 14. Windows Firewall

Configure Windows Private-profile firewall rules only after Mini-PC network
approval.

Do not open PostgreSQL port `5432` to staff computers.

## 15. Task Scheduler

Configure Task Scheduler only on the Mini-PC.

Recommended production startup task:

- Program: the local `START_RUC.bat`
- Start in: the RUC project folder
- Run at startup or operator logon, according to local policy

Recommended backup task:

- Program: the local `BACKUP_RUC.bat`
- Schedule: daily off-hours

Recommended verification task:

- Program: `.venv\Scripts\python.exe`
- Arguments: `verify_backup.py`
- Schedule: after the daily backup

## 16. Off-Machine Backup

The local Stage 6 backup is the first copy only.

Before staff reliance, configure one protected off-machine copy:

- encrypted external drive;
- secured NAS;
- encrypted off-site storage.

The actual destination is deferred until the Mini-PC and storage device are
available.

## 17. Recovery Verification

Periodically run:

```bat
verify_backup.py
RESTORE_TEST_RUC.bat --files-only --cleanup-files
```

Temporary database restore requires approved PostgreSQL administrative restore
credentials. Never restore over production `ruc_system` during testing.

## 18. Record Mini-PC Configuration

Record these after the Mini-PC exists:

- Mini-PC name;
- Windows version;
- RUC project path;
- Python version;
- PostgreSQL version;
- backup destination;
- LAN IP address if private LAN access is approved;
- Task Scheduler task names;
- responsible administrator.

## Public Employee Form Policy

For private LAN use, `/form/<project_code>` may remain public within the trusted
LAN under the current CSRF and upload controls.

Residual LAN risk remains: anyone on the LAN who knows or guesses a valid
project code can attempt a submission.

For internet use, the public form is not approved without stronger controls such
as authenticated onboarding, expiring invite links, stronger persistent rate
limiting, anti-bot protection, monitoring, and stricter upload inspection.
