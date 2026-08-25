import argparse
import json
import sys
import zipfile
from pathlib import Path

from backup_ruc import (
    BACKUP_FORMAT_VERSION,
    COMPLETION_MARKER,
    DISASTER_BACKUP_ROOT,
    EXPECTED_CATEGORIES,
    PG_RESTORE_FALLBACK,
    find_executable,
    run_command,
    sha256_file,
)


def latest_backup_set():
    if not DISASTER_BACKUP_ROOT.exists():
        return None

    candidates = [
        path
        for path in DISASTER_BACKUP_ROOT.iterdir()
        if path.is_dir() and (path / "manifest.json").exists()
    ]

    return sorted(candidates, reverse=True)[0] if candidates else None


def load_manifest(backup_set):
    manifest_file = backup_set / "manifest.json"

    if not manifest_file.exists():
        raise ValueError("manifest.json is missing.")

    return json.loads(manifest_file.read_text(encoding="utf-8"))


def resolve_relative_file(backup_set, section):
    relative_path = section.get("relative_path")

    if not relative_path:
        raise ValueError("manifest is missing a relative file path.")

    resolved = (backup_set / relative_path).resolve()
    backup_set_resolved = backup_set.resolve()

    if backup_set_resolved != resolved and backup_set_resolved not in resolved.parents:
        raise ValueError("manifest path points outside the backup set.")

    return resolved


def check_checksum(path, expected_hash):
    if not expected_hash:
        raise ValueError(f"missing checksum for {path.name}.")

    actual_hash = sha256_file(path)

    if actual_hash != expected_hash:
        raise ValueError(f"checksum mismatch for {path.name}.")

    return actual_hash


def verify_backup_set(backup_set):
    backup_set = Path(backup_set)
    manifest = load_manifest(backup_set)
    checks = []

    def passed(name, details="PASS"):
        checks.append({"name": name, "status": "PASS", "details": details})

    def failed(name, details):
        checks.append({"name": name, "status": "FAIL", "details": details})

    try:
        if manifest.get("backup_format_version") != BACKUP_FORMAT_VERSION:
            raise ValueError("unexpected backup format version.")
        passed("manifest_format")

        if manifest.get("status") != "SUCCESS":
            raise ValueError("manifest status is not SUCCESS.")
        passed("manifest_status")

        marker = backup_set / COMPLETION_MARKER
        if not marker.exists():
            raise ValueError("completion marker is missing.")
        passed("completion_marker")

        database = manifest.get("database") or {}
        database_path = resolve_relative_file(backup_set, database)

        if not database_path.exists() or database_path.stat().st_size <= 0:
            raise ValueError("database backup is missing or zero-byte.")
        passed("database_file")

        check_checksum(database_path, database.get("sha256"))
        passed("database_checksum")

        pg_restore = find_executable("pg_restore", "PG_RESTORE_EXE", PG_RESTORE_FALLBACK)
        restore_list = run_command([pg_restore, "--list", str(database_path)])

        if restore_list.returncode != 0:
            raise ValueError("pg_restore --list failed.")
        passed("pg_restore_list", f"{len(restore_list.stdout.splitlines())} TOC lines")

        runtime_archive = manifest.get("runtime_archive") or {}
        runtime_archive_path = resolve_relative_file(backup_set, runtime_archive)

        if not runtime_archive_path.exists() or runtime_archive_path.stat().st_size <= 0:
            raise ValueError("runtime archive is missing or zero-byte.")
        passed("runtime_archive_file")

        check_checksum(runtime_archive_path, runtime_archive.get("sha256"))
        passed("runtime_archive_checksum")

        with zipfile.ZipFile(runtime_archive_path, "r") as archive:
            bad_member = archive.testzip()
            names = archive.namelist()

        if bad_member is not None:
            raise ValueError(f"runtime archive failed zip test at {bad_member}.")
        passed("runtime_archive_open")

        if not names:
            raise ValueError("runtime archive is empty.")
        passed("runtime_archive_not_empty", f"{len(names)} entries")

        represented = set()

        for name in names:
            normalized = name.strip("/")
            for category in EXPECTED_CATEGORIES:
                if normalized == category or normalized.startswith(category + "/"):
                    represented.add(category)

        missing_categories = [
            category for category in EXPECTED_CATEGORIES if category not in represented
        ]

        if missing_categories:
            raise ValueError(
                "runtime archive is missing categories: " + ", ".join(missing_categories)
            )
        passed("runtime_categories", ", ".join(sorted(represented)))

    except Exception as exc:
        failed("backup_set", str(exc))

    passed_all = all(check["status"] == "PASS" for check in checks)
    return {
        "status": "PASS" if passed_all else "FAIL",
        "backup_set": str(backup_set),
        "checks": checks,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Verify a RUC Stage 6 disaster-recovery backup set."
    )
    parser.add_argument(
        "backup_set",
        nargs="?",
        help="Path to a Stage 6 backup set. Defaults to the latest set.",
    )
    args = parser.parse_args(argv)

    backup_set = Path(args.backup_set) if args.backup_set else latest_backup_set()

    if backup_set is None:
        print("RUC backup verification FAILED")
        print("No Stage 6 backup sets were found.")
        return 1

    result = verify_backup_set(backup_set)

    print(f"RUC backup verification {result['status']}")
    print(f"Backup set: {result['backup_set']}")

    for check in result["checks"]:
        print(f"{check['status']}: {check['name']} - {check['details']}")

    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
