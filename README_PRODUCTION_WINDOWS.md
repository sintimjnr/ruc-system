# RUC System Windows Production Runtime

This guide runs RUC on a Windows Mini-PC or local server using Flask, local PostgreSQL, local Excel files, local uploads, and Waitress.

Do not expose PostgreSQL to client computers. Users should connect to the RUC web application only.

## 1. Prepare The Mini-PC

1. Copy or clone the approved RUC source into a stable folder such as `D:\RUC_SYSTEM`.
2. Keep the existing local PostgreSQL database on the Mini-PC.
3. Keep operational folders local: `excel_files`, `uploads`, `static/uploads`, `id_cards`, `generated_reports`, `backups`, and `runtime`.
4. Use one RUC server instance for this deployment.

## 2. Install Requirements

From the RUC project folder:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Stage 5 uses Waitress as the Windows production WSGI server. The existing `Procfile` is not the Windows Mini-PC startup path.

## 3. Configure `.env`

Copy `.env.example` to `.env` and fill in local values:

```powershell
Copy-Item .env.example .env
notepad .env
```

Use the dedicated runtime PostgreSQL role, normally `ruc_app`. Do not use the `postgres` superuser for normal application runtime.

Keep these defaults for local HTTP testing:

```text
RUC_HOST=127.0.0.1
RUC_PORT=5000
RUC_THREADS=4
SESSION_COOKIE_SECURE=false
RUC_TRUST_PROXY_HEADERS=false
RUC_DEBUG=false
RUC_LOG_DIR=logs
RUC_LOG_LEVEL=INFO
```

Do not put real passwords or `SECRET_KEY` values into source files or documentation.

## 4. Test The Launcher

Run:

```powershell
.\START_RUC.bat
```

Open:

```text
http://127.0.0.1:5000/healthz
```

Expected response:

```json
{"status":"ok"}
```

Then open:

```text
http://127.0.0.1:5000
```

## 5. LAN Access

Localhost mode is the safe default:

```text
RUC_HOST=127.0.0.1
```

For authorized computers on the same private network, explicitly set:

```text
RUC_HOST=0.0.0.0
```

LAN users should browse to:

```text
http://<MINI-PC-IP>:<RUC_PORT>
```

Find the Mini-PC IP with:

```powershell
ipconfig
```

Use a DHCP reservation or static LAN IP so users do not have to change bookmarks after reboot.

## 6. Windows Firewall

If LAN mode is enabled, allow inbound TCP traffic for the RUC web port on the Private network profile only.

Do not open PostgreSQL port `5432` to client computers.

## 7. Task Scheduler

Create one scheduled task for one RUC instance.

Suggested settings:

1. Trigger: At startup, or at logon if simpler for the operator.
2. Program/script: `D:\RUC_SYSTEM\START_RUC.bat`
3. Start in: `D:\RUC_SYSTEM`
4. Run whether user is logged on or not where practical.
5. Restart on failure.
6. Avoid launching a second parallel instance.

If the task fails, check the technical log in `logs/ruc.log`.

## 8. Stop Or Restart RUC

If running in a terminal, press `Ctrl+C`.

If running through Task Scheduler:

1. End the scheduled task.
2. Confirm the RUC port is no longer listening.
3. Start the task again.

## 9. Logs

Technical runtime logs are written to:

```text
logs/ruc.log
```

The technical log rotates automatically. It is separate from the PostgreSQL `audit_logs` table.

Never log or share passwords, database URLs with credentials, `SECRET_KEY`, CSRF tokens, session cookies, or uploaded private document contents.

## 10. Cookies And HTTPS

For HTTP localhost or trusted LAN testing:

```text
SESSION_COOKIE_SECURE=false
```

For future HTTPS deployment:

```text
SESSION_COOKIE_SECURE=true
```

Do not enable Secure cookies while serving plain HTTP, because browsers will stop sending the session cookie.

## 11. Reverse Proxy

No reverse proxy is required for the first Mini-PC setup.

Keep:

```text
RUC_TRUST_PROXY_HEADERS=false
```

Only enable proxy-header trust later if a trusted reverse proxy is deliberately configured.

## 12. Backups

Stage 4 workbook backups protect individual workbook writes, but they are not a full disaster-recovery plan.

Schedule regular backups for:

- PostgreSQL database
- `excel_files`
- `uploads`
- `static/uploads`
- `id_cards`
- generated reports that must be retained

Keep backups on storage separate from the Mini-PC when practical.
