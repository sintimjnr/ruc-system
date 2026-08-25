import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


BACKUP_FORMAT_VERSION = "ruc-stage6-disaster-v1"
BASE_DIR = Path(__file__).resolve().parent
DISASTER_BACKUP_ROOT = BASE_DIR / "backups" / "disaster"
COMPLETION_MARKER = "BACKUP_COMPLETE.marker"
INCOMPLETE_MARKER = "BACKUP_IN_PROGRESS.marker"
EXPECTED_CATEGORIES = [
    "excel_files",
    "uploads",
    "static/uploads",
    "id_cards",
    "generated_reports",
]
RETENTION_POLICY = {
    "daily": 7,
    "weekly": 4,
    "monthly": 6,
}
WARNING_FREE_BYTES = 10 * 1024 * 1024 * 1024
CRITICAL_FREE_BYTES = 5 * 1024 * 1024 * 1024
PG_DUMP_FALLBACK = r"C:\Program Files\PostgreSQL\13\bin\pg_dump.exe"
PG_RESTORE_FALLBACK = r"C:\Program Files\PostgreSQL\13\bin\pg_restore.exe"
TIMESTAMP_PATTERN = re.compile(r"^\d{8}_\d{6}$")


class BackupError(RuntimeError):
    pass


def now_timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def iso_now():
    return datetime.now().isoformat(timespec="seconds")


def load_dotenv_if_present(path=None):
    env_path = Path(path or BASE_DIR / ".env")

    if not env_path.exists():
        return False

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key or key.startswith("#"):
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        os.environ.setdefault(key, value)

    return True


def find_executable(name, env_name=None, fallback=None):
    configured = os.environ.get(env_name or "", "").strip() if env_name else ""

    candidates = [
        configured,
        shutil.which(name),
        fallback,
    ]

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate

    raise BackupError(f"{name} was not found. Install PostgreSQL tools or configure {env_name}.")


def run_command(command, env=None):
    return subprocess.run(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def redacted_error(prefix):
    return f"{prefix}. See the PostgreSQL tool output locally; credentials were not printed."


def sha256_file(path):
    digest = hashlib.sha256()

    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def get_git_commit():
    result = run_command(["git", "rev-parse", "HEAD"])

    if result.returncode != 0:
        return "UNKNOWN"

    return result.stdout.strip()


def parse_database_url(database_url):
    parsed = urlparse(database_url)

    if parsed.scheme not in {"postgres", "postgresql"}:
        raise BackupError("DATABASE_URL must use a PostgreSQL scheme.")

    database = unquote(parsed.path.lstrip("/"))

    if not database:
        raise BackupError("DATABASE_URL must include a database name.")

    env = os.environ.copy()

    if parsed.hostname:
        env["PGHOST"] = parsed.hostname
    if parsed.port:
        env["PGPORT"] = str(parsed.port)
    if parsed.username:
        env["PGUSER"] = unquote(parsed.username)
    if parsed.password:
        env["PGPASSWORD"] = unquote(parsed.password)

    env["PGDATABASE"] = database

    query = parse_qs(parsed.query or "")
    sslmode = query.get("sslmode", [""])[0]

    if sslmode:
        env["PGSSLMODE"] = sslmode

    return env, database


def get_database_dump_context():
    load_dotenv_if_present()
    database_url = os.environ.get("DATABASE_URL", "").strip()

    if database_url:
        env, database = parse_database_url(database_url)
        return env, {
            "database": database,
            "host": env.get("PGHOST", ""),
            "port": env.get("PGPORT", ""),
            "user": env.get("PGUSER", ""),
            "source": "DATABASE_URL",
        }

    required = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    missing = [name for name in required if not os.environ.get(name)]

    if missing:
        raise BackupError(
            "Database configuration is incomplete. Set DATABASE_URL or DB_HOST, "
            "DB_PORT, DB_NAME, DB_USER, and DB_PASSWORD in .env."
        )

    env = os.environ.copy()
    env["PGPASSWORD"] = os.environ["DB_PASSWORD"]

    return env, {
        "database": os.environ["DB_NAME"],
        "host": os.environ["DB_HOST"],
        "port": os.environ["DB_PORT"],
        "user": os.environ["DB_USER"],
        "source": "DB_*",
    }


def get_disk_space(path):
    target = Path(path)

    while not target.exists() and target != target.parent:
        target = target.parent

    total, used, free = shutil.disk_usage(target)
    free_percent = (free / total) * 100 if total else 0

    if free_percent < 10 or free < CRITICAL_FREE_BYTES:
        level = "CRITICAL"
    elif free_percent < 20 or free < WARNING_FREE_BYTES:
        level = "WARNING"
    else:
        level = "OK"

    return {
        "path": str(target),
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "free_percent": round(free_percent, 2),
        "level": level,
        "warning_policy": "below 20 percent free OR below 10 GB",
        "critical_policy": "below 10 percent free OR below 5 GB",
    }


def temporary_or_excluded_file(path):
    name = path.name

    if name.startswith("~$") or name.startswith(".~ruc_tmp_"):
        return True

    if name.endswith((".tmp", ".tmp.xlsx", ".lock", ".pyc", ".pyo")):
        return True

    if "__pycache__" in path.parts:
        return True

    return False


def zip_directory_entry(zip_file, arcname):
    info = zipfile.ZipInfo(arcname.rstrip("/") + "/")
    info.external_attr = 0o40755 << 16
    zip_file.writestr(info, "")


def create_runtime_archive(archive_path):
    category_stats = {}
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for category in EXPECTED_CATEGORIES:
            source = BASE_DIR / category
            archive_category = category.replace("\\", "/")
            zip_directory_entry(archive, archive_category)
            stats = {
                "source_exists": source.exists(),
                "files": 0,
                "bytes": 0,
                "excluded_temporary_files": 0,
            }

            if source.exists():
                for path in sorted(source.rglob("*")):
                    if path.is_dir():
                        continue

                    if temporary_or_excluded_file(path):
                        stats["excluded_temporary_files"] += 1
                        continue

                    arcname = path.relative_to(BASE_DIR).as_posix()
                    archive.write(path, arcname)
                    stats["files"] += 1
                    stats["bytes"] += path.stat().st_size

            category_stats[category] = stats

    with zipfile.ZipFile(archive_path, "r") as archive:
        bad_member = archive.testzip()
        names = archive.namelist()

    if bad_member is not None:
        raise BackupError(f"Runtime archive verification failed at {bad_member}.")

    if not names:
        raise BackupError("Runtime archive is empty.")

    return {
        "relative_path": f"files/{archive_path.name}",
        "filename": archive_path.name,
        "size_bytes": archive_path.stat().st_size,
        "sha256": sha256_file(archive_path),
        "method": "zip deflated",
        "categories": category_stats,
        "archive_entries": len(names),
        "verification": {
            "zip_open": "PASS",
            "zip_test": "PASS",
            "archive_not_empty": "PASS",
        },
    }


def create_database_backup(database_path):
    pg_dump = find_executable("pg_dump", "PG_DUMP_EXE", PG_DUMP_FALLBACK)
    pg_restore = find_executable("pg_restore", "PG_RESTORE_EXE", PG_RESTORE_FALLBACK)
    env, db_info = get_database_dump_context()
    database_path.parent.mkdir(parents=True, exist_ok=True)

    command = [
        pg_dump,
        "--format=custom",
        "--no-password",
        "--file",
        str(database_path),
        "--host",
        db_info["host"],
        "--port",
        str(db_info["port"]),
        "--username",
        db_info["user"],
        "--dbname",
        db_info["database"],
    ]
    result = run_command(command, env=env)

    if result.returncode != 0:
        if database_path.exists():
            database_path.unlink()
        raise BackupError(redacted_error("pg_dump failed"))

    if not database_path.exists() or database_path.stat().st_size <= 0:
        raise BackupError("PostgreSQL backup is missing or zero-byte.")

    verify = run_command([pg_restore, "--list", str(database_path)], env=env)

    if verify.returncode != 0:
        raise BackupError(redacted_error("pg_restore --list failed"))

    return {
        "relative_path": f"database/{database_path.name}",
        "filename": database_path.name,
        "size_bytes": database_path.stat().st_size,
        "sha256": sha256_file(database_path),
        "method": "pg_dump custom format",
        "database_name": db_info["database"],
        "host": db_info["host"],
        "port": str(db_info["port"]),
        "dump_role": db_info["user"],
        "config_source": db_info["source"],
        "pg_dump": Path(pg_dump).name,
        "pg_restore": Path(pg_restore).name,
        "verification": {
            "backup_exists": "PASS",
            "non_zero": "PASS",
            "pg_restore_list": "PASS",
            "toc_lines": len(verify.stdout.splitlines()),
        },
    }


def manifest_path(backup_set):
    return backup_set / "manifest.json"


def write_manifest(backup_set, manifest):
    manifest_path(backup_set).write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def safe_backup_set_dir(path):
    resolved_root = DISASTER_BACKUP_ROOT.resolve()
    resolved_path = path.resolve()

    if resolved_path == resolved_root:
        raise BackupError("Refusing to operate on the disaster backup root itself.")

    if resolved_root not in resolved_path.parents:
        raise BackupError("Refusing to operate outside backups/disaster.")

    if not TIMESTAMP_PATTERN.match(resolved_path.name):
        raise BackupError("Backup set directory does not use the approved timestamp format.")


def read_manifest(path):
    return json.loads(manifest_path(path).read_text(encoding="utf-8"))


def verified_backup_sets(root):
    sets = []

    if not root.exists():
        return sets

    for child in root.iterdir():
        if not child.is_dir() or not TIMESTAMP_PATTERN.match(child.name):
            continue

        marker = child / COMPLETION_MARKER
        manifest_file = manifest_path(child)

        if not marker.exists() or not manifest_file.exists():
            continue

        try:
            manifest = read_manifest(child)
        except (OSError, json.JSONDecodeError):
            continue

        if manifest.get("status") != "SUCCESS":
            continue

        sets.append(child)

    return sorted(sets, reverse=True)


def select_retention_keep_sets(sets):
    keep = set()
    sorted_sets = sorted(sets, reverse=True)

    keep.update(sorted_sets[: RETENTION_POLICY["daily"]])

    weekly_seen = set()
    for backup_set in sorted_sets:
        stamp = datetime.strptime(backup_set.name, "%Y%m%d_%H%M%S")
        week_key = stamp.strftime("%G-W%V")

        if week_key not in weekly_seen and len(weekly_seen) < RETENTION_POLICY["weekly"]:
            keep.add(backup_set)
            weekly_seen.add(week_key)

    monthly_seen = set()
    for backup_set in sorted_sets:
        stamp = datetime.strptime(backup_set.name, "%Y%m%d_%H%M%S")
        month_key = stamp.strftime("%Y-%m")

        if month_key not in monthly_seen and len(monthly_seen) < RETENTION_POLICY["monthly"]:
            keep.add(backup_set)
            monthly_seen.add(month_key)

    return keep


def apply_retention(current_backup_set, dry_run=False):
    sets = verified_backup_sets(DISASTER_BACKUP_ROOT)
    keep = select_retention_keep_sets(sets)
    deleted = []
    candidates = []

    if len(sets) <= 1:
        return {
            "policy": RETENTION_POLICY,
            "mode": "dry-run" if dry_run else "active",
            "verified_sets_seen": len(sets),
            "kept_sets": [backup_set.name for backup_set in sets],
            "delete_candidates": [],
            "deleted_sets": [],
            "safety": "No deletion because there is only one or zero verified Stage 6 backup sets.",
        }

    for backup_set in sets:
        if backup_set == current_backup_set or backup_set in keep:
            continue

        safe_backup_set_dir(backup_set)
        candidates.append(backup_set.name)

        if not dry_run:
            shutil.rmtree(backup_set)
            deleted.append(backup_set.name)

    return {
        "policy": RETENTION_POLICY,
        "mode": "dry-run" if dry_run else "active",
        "verified_sets_seen": len(sets),
        "kept_sets": sorted([backup_set.name for backup_set in keep], reverse=True),
        "delete_candidates": candidates,
        "deleted_sets": deleted,
        "safety": (
            "Retention is limited to verified Stage 6 disaster backup sets with "
            "manifest status SUCCESS and a completion marker."
        ),
    }


def create_backup_set(destination_root=DISASTER_BACKUP_ROOT, skip_retention=False, dry_run_retention=False):
    destination_root = Path(destination_root)
    timestamp = now_timestamp()
    backup_set = destination_root / timestamp
    safe_backup_set_dir(backup_set)
    backup_set.mkdir(parents=True, exist_ok=False)

    incomplete_marker = backup_set / INCOMPLETE_MARKER
    incomplete_marker.write_text(iso_now(), encoding="utf-8")

    manifest = {
        "backup_format_version": BACKUP_FORMAT_VERSION,
        "timestamp": timestamp,
        "created_at": iso_now(),
        "baseline_commit": get_git_commit(),
        "backup_set": str(backup_set),
        "status": "IN_PROGRESS",
        "included_categories": EXPECTED_CATEGORIES,
        "excluded": [
            ".env",
            ".git",
            ".venv",
            "backups",
            "logs",
            "runtime locks",
            "Python cache",
            "temporary workbook files",
            "Office lock files",
        ],
        "security_notes": {
            "env_included": False,
            "credentials_printed": False,
            "logs_included": False,
            "contains_sensitive_runtime_data": True,
            "recommended_storage": "restricted NTFS access plus encrypted off-machine copy",
        },
    }
    write_manifest(backup_set, manifest)

    try:
        disk_space = get_disk_space(destination_root)
        manifest["disk_space"] = disk_space

        if disk_space["level"] == "CRITICAL":
            raise BackupError(
                "Disk space is CRITICAL. Backup stopped before creating a misleading backup set."
            )

        database_path = backup_set / "database" / f"ruc_system_{timestamp}.backup"
        runtime_archive_path = backup_set / "files" / f"ruc_runtime_files_{timestamp}.zip"

        manifest["database"] = create_database_backup(database_path)
        manifest["runtime_archive"] = create_runtime_archive(runtime_archive_path)
        manifest["completion_marker"] = COMPLETION_MARKER
        manifest["verification_status"] = "PASS"
        manifest["completed_at"] = iso_now()
        manifest["status"] = "SUCCESS"
        write_manifest(backup_set, manifest)

        (backup_set / COMPLETION_MARKER).write_text(iso_now(), encoding="utf-8")

        if incomplete_marker.exists():
            incomplete_marker.unlink()

        if skip_retention:
            retention = {
                "policy": RETENTION_POLICY,
                "mode": "skipped",
                "deleted_sets": [],
                "safety": "Retention was explicitly skipped for this run.",
            }
        else:
            retention = apply_retention(backup_set, dry_run=dry_run_retention)

        manifest["retention"] = retention
        write_manifest(backup_set, manifest)
        return backup_set, manifest

    except Exception as exc:
        manifest["status"] = "FAILED"
        manifest["verification_status"] = "FAIL"
        manifest["failed_at"] = iso_now()
        manifest["error"] = str(exc)
        write_manifest(backup_set, manifest)
        (backup_set / "BACKUP_FAILED.marker").write_text(iso_now(), encoding="utf-8")
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Create a verified local RUC Stage 6 disaster-recovery backup set."
    )
    parser.add_argument(
        "--destination",
        default=str(DISASTER_BACKUP_ROOT),
        help="Backup root. Defaults to backups/disaster under the RUC project.",
    )
    parser.add_argument(
        "--skip-retention",
        action="store_true",
        help="Create the backup without applying Stage 6 retention.",
    )
    parser.add_argument(
        "--dry-run-retention",
        action="store_true",
        help="Evaluate retention without deleting old verified Stage 6 sets.",
    )
    args = parser.parse_args(argv)

    try:
        backup_set, manifest = create_backup_set(
            destination_root=Path(args.destination),
            skip_retention=args.skip_retention,
            dry_run_retention=args.dry_run_retention,
        )
    except Exception as exc:
        print(f"RUC backup FAILED: {exc}")
        return 1

    print("RUC backup PASSED")
    print(f"Backup set: {backup_set}")
    print(f"Database backup size: {manifest['database']['size_bytes']} bytes")
    print(f"Runtime archive size: {manifest['runtime_archive']['size_bytes']} bytes")
    print(f"Manifest: {manifest_path(backup_set)}")
    print(f"Disk space: {manifest['disk_space']['level']} ({manifest['disk_space']['free_percent']}% free)")
    print(f"Retention mode: {manifest['retention']['mode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
