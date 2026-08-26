from flask import Flask, render_template, request, redirect, session, send_file, url_for, flash, jsonify
import psycopg2
from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from PIL import Image, ImageDraw, ImageFont
from contextlib import contextmanager
import hashlib
import json
import os
import random
import secrets
from werkzeug.utils import secure_filename
import shutil
from datetime import datetime, date, timedelta, time, timezone
import time as time_module
from werkzeug.security import generate_password_hash, check_password_hash
from flask import send_from_directory
from functools import wraps
import uuid
from io import BytesIO
import threading
import zipfile
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


class ConfigurationError(RuntimeError):
    pass


def load_local_env():

    env_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), ".env")

    if not os.path.exists(env_path):
        return

    try:
        from dotenv import load_dotenv
    except ImportError:
        load_dotenv = None

    if load_dotenv:
        load_dotenv(env_path, override=False)
        return

    with open(env_path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            if line.lower().startswith("export "):
                line = line[7:].strip()

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in {"'", '"'}
            ):
                value = value[1:-1]

            if key and key not in os.environ:
                os.environ[key] = value


def require_config(name):

    value = os.environ.get(name)

    if value is None or value == "":
        raise ConfigurationError(
            f"Missing required configuration: {name}. "
            "Copy .env.example to .env and set local values."
        )

    return value


def get_secret_key():

    return require_config("SECRET_KEY")


def get_database_config():

    database_url = os.environ.get("DATABASE_URL", "").strip()

    if database_url:
        return {"dsn": database_url}

    required_names = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    missing = [
        name
        for name in required_names
        if os.environ.get(name) is None or os.environ.get(name) == ""
    ]

    if missing:
        raise ConfigurationError(
            "Missing database configuration. Set DATABASE_URL or set DB_HOST, "
            "DB_PORT, DB_NAME, DB_USER, and DB_PASSWORD in .env."
        )

    return {
        "host": os.environ["DB_HOST"],
        "port": os.environ["DB_PORT"],
        "database": os.environ["DB_NAME"],
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
    }


load_local_env()


def config_bool(name, default=False):

    value = os.environ.get(name)

    if value is None or value == "":
        return default

    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def config_int(name, default, minimum=None, maximum=None):

    raw_value = os.environ.get(name)

    try:
        value = int(raw_value) if raw_value not in {None, ""} else int(default)
    except (TypeError, ValueError):
        value = int(default)

    if minimum is not None:
        value = max(value, minimum)

    if maximum is not None:
        value = min(value, maximum)

    return value


def config_samesite(name="SESSION_COOKIE_SAMESITE", default="Lax"):

    value = clean_config_value(os.environ.get(name), default)
    normalized = value.capitalize()

    if normalized not in {"Lax", "Strict", "None"}:
        return default

    return normalized


def clean_config_value(value, default=""):

    value = str(value or "").strip()
    return value or default


#############################################
# FIND COLUMN BY HEADER NAME
#############################################


def find_column(sheet, header_name):

    for col in range(1, sheet.max_column + 1):

        value = sheet.cell(row=1, column=col).value

        if value and str(value).strip().upper() == header_name.upper():
            return col

    return None


#############################################
# VALIDATE MASTER TRACKER (GLOBE NLZ)
#############################################


def validate_tracker(old_file, new_file):

    old_wb = None
    new_wb = None

    try:
        old_wb = load_workbook(old_file, read_only=True, data_only=True)
        new_wb = load_workbook(new_file, read_only=True, data_only=True)

        # Check sheet exists
        if "GLOBE NLZ" not in new_wb.sheetnames:
            return "Sheet 'GLOBE NLZ' is missing"

        old_ws = old_wb["GLOBE NLZ"]
        new_ws = new_wb["GLOBE NLZ"]

        #################################
        # CHECK COLUMN HEADERS
        #################################

        for col in range(1, old_ws.max_column + 1):

            old_header = old_ws.cell(row=1, column=col).value
            new_header = new_ws.cell(row=1, column=col).value

            if old_header != new_header:
                return f"Column changed: {old_header}"

        #################################
        # CHECK ROWS WERE NOT DELETED
        #################################

        if new_ws.max_row < old_ws.max_row:
            return "Rows were deleted from the tracker"

        #################################
        # FIND DU ID COLUMN
        #################################

        du_col_old = find_column(old_ws, "DU ID")
        du_col_new = find_column(new_ws, "DU ID")

        if not du_col_old or not du_col_new:
            return "DU ID column missing"

        #################################
        # CHECK DU ID INTEGRITY
        #################################

        old_du_counts = {}
        new_du_counts = {}

        # Collect old DU IDs
        for row in range(2, old_ws.max_row + 1):

            du = old_ws.cell(row=row, column=du_col_old).value

            if du:
                du = str(du).strip()
                old_du_counts[du] = old_du_counts.get(du, 0) + 1

        # Collect new DU IDs
        for row in range(2, new_ws.max_row + 1):

            du = new_ws.cell(row=row, column=du_col_new).value

            if du:

                du = str(du).strip()
                new_du_counts[du] = new_du_counts.get(du, 0) + 1

                if new_du_counts[du] > max(old_du_counts.get(du, 0), 1):
                    return f"Duplicate DU ID detected: {du}"

        #################################
        # CHECK FOR MISSING DU IDs
        #################################

        missing_du = set(old_du_counts) - set(new_du_counts)

        if missing_du:
            return f"Missing DU IDs detected: {list(missing_du)[:5]}"

        return "OK"
    finally:
        if old_wb is not None:
            old_wb.close()

        if new_wb is not None:
            new_wb.close()


#############################################
# BACKUP FILE
#############################################


def backup_file(file_path, backup_folder):

    os.makedirs(backup_folder, exist_ok=True)

    if os.path.exists(file_path):

        time_stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

        file_name = os.path.basename(file_path)

        new_name = time_stamp + "_" + uuid.uuid4().hex[:8] + "_" + file_name

        backup_path = os.path.join(backup_folder, new_name)

        shutil.copy2(file_path, backup_path)

        return backup_path

    return None


def restore_workbook_backup(workbook_path, backup_path):

    if backup_path and os.path.exists(backup_path):
        shutil.copy2(backup_path, workbook_path)
        return True

    return False


#############################################
# FLASK APP
#############################################

app = Flask(__name__, static_folder=None)
app.secret_key = get_secret_key()
app.config["MAX_CONTENT_LENGTH"] = int(
    os.environ.get("MAX_UPLOAD_BYTES", 16 * 1024 * 1024)
)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=config_samesite(),
    SESSION_COOKIE_SECURE=config_bool("SESSION_COOKIE_SECURE", False),
    PERMANENT_SESSION_LIFETIME=timedelta(
        hours=config_int("RUC_SESSION_HOURS", 8, minimum=1, maximum=24)
    ),
    SESSION_REFRESH_EACH_REQUEST=True,
)

CSRF_SESSION_KEY = "_ruc_csrf_token"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER_NAMES = ("X-CSRFToken", "X-CSRF-Token")
CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
LOGIN_RATE_LIMIT_ATTEMPTS = config_int(
    "LOGIN_RATE_LIMIT_ATTEMPTS", 5, minimum=1, maximum=50
)
LOGIN_RATE_LIMIT_WINDOW_SECONDS = config_int(
    "LOGIN_RATE_LIMIT_WINDOW_SECONDS", 60, minimum=10, maximum=3600
)
LOGIN_RATE_LIMIT_BLOCK_SECONDS = config_int(
    "LOGIN_RATE_LIMIT_BLOCK_SECONDS", 300, minimum=30, maximum=86400
)
SECURITY_AUDIT_THROTTLE_SECONDS = config_int(
    "SECURITY_AUDIT_THROTTLE_SECONDS", 60, minimum=0, maximum=3600
)
login_rate_limit_state = {}
security_audit_throttle_state = {}

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
LEGACY_UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
STATIC_UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
EXCEL_DIR = os.path.join(BASE_DIR, "excel_files")
MASTER_TRACKER_PATH = os.path.join(EXCEL_DIR, "master", "NLZ_MASTER_TRACKER.xlsx")
ID_CARD_DIR = os.path.join(BASE_DIR, "id_cards")
ID_TEMPLATE_DIR = os.path.join(BASE_DIR, "id_templates")
GENERATED_REPORTS_DIR = os.path.join(BASE_DIR, "generated_reports")

ALLOWED_UPLOAD_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "pdf",
}
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg"}
ALLOWED_DOCUMENT_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}
UPLOAD_CATEGORIES = {
    "photo",
    "nbi",
    "wah",
    "first_aid",
    "signatures",
    "certificates",
    "permits",
    "incidents",
    "daily_photos",
    "daily_documents",
    "other",
}
LEGACY_UPLOAD_CATEGORIES = {
    "photos": "photo",
    "nbi": "nbi",
    "certificates": "certificates",
    "signatures": "signatures",
    "secid": "secid",
    "wah": "wah",
}
CATEGORY_UPLOAD_EXTENSIONS = {
    "photo": ALLOWED_IMAGE_EXTENSIONS,
    "signatures": ALLOWED_IMAGE_EXTENSIONS,
    "nbi": ALLOWED_DOCUMENT_EXTENSIONS,
    "wah": ALLOWED_DOCUMENT_EXTENSIONS,
    "first_aid": ALLOWED_DOCUMENT_EXTENSIONS,
    "certificates": ALLOWED_DOCUMENT_EXTENSIONS,
    "secid": ALLOWED_DOCUMENT_EXTENSIONS,
    "permits": ALLOWED_DOCUMENT_EXTENSIONS,
    "incidents": ALLOWED_DOCUMENT_EXTENSIONS,
    "daily_photos": ALLOWED_IMAGE_EXTENSIONS,
    "daily_documents": ALLOWED_DOCUMENT_EXTENSIONS,
    "other": ALLOWED_DOCUMENT_EXTENSIONS,
}

TELECOM_TASK_TYPES = [
    "Civil",
    "Rigging",
    "Tower Installation",
    "Antenna Installation",
    "Integration",
    "Testing",
    "Maintenance",
    "Inspection",
]
TASK_PRIORITIES = ["LOW", "MEDIUM", "HIGH", "URGENT"]
TASK_STATUSES = [
    "PENDING",
    "IN PROGRESS",
    "BLOCKED",
    "COMPLETED",
    "OPEN",
    "CLOSED",
    "CANCELLED",
]
PERMIT_STATUSES = ["PENDING", "ACTIVE", "EXPIRED", "CLOSED", "CANCELLED"]
INCIDENT_STATUSES = ["OPEN", "INVESTIGATING", "RESOLVED", "CLOSED", "CANCELLED"]
INCIDENT_SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
DOCUMENT_TYPES = ["NBI", "WAH", "FIRST_AID"]
SITE_STAGES = [
    "Planning",
    "Survey",
    "Civil",
    "Installation",
    "Rigging",
    "Integration",
    "Testing",
    "PAT",
    "Completed",
    "On Hold",
]
SITE_STATUSES = ["Not Started", "Active", "On Hold", "Blocked", "Completed"]
ACCESS_STATUSES = ["VALID", "EXPIRING SOON", "EXPIRED", "MISSING"]
PAT_STATUSES = [
    "MISSING",
    "PENDING",
    "IN PROGRESS",
    "PASSED",
    "PASSED WITH PUNCHLIST",
    "FAILED",
    "WAIVED",
]
EXPIRING_SOON_DAYS = 30
DAILY_ATTENDANCE_STATUSES = ["Present", "Absent", "Late", "Excused"]
DAILY_BLOCKER_CATEGORIES = [
    "",
    "Access Issue",
    "Weather",
    "Material Shortage",
    "Permit Issue",
    "Power Issue",
    "Technical Issue",
    "Safety Issue",
    "Manpower Issue",
    "Customer/TowerCo Dependency",
    "Other",
]
DAILY_FILE_TYPES = ["PHOTO", "DOCUMENT"]
PUNCHLIST_PRIORITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
PUNCHLIST_STATUSES = ["OPEN", "IN PROGRESS", "RECTIFIED", "VERIFIED", "CLOSED"]
PUNCHLIST_FILE_TYPES = ["BEFORE", "AFTER", "GENERAL", "DOCUMENT"]
PUNCHLIST_UNRESOLVED_STATUSES = ["OPEN", "IN PROGRESS", "RECTIFIED"]
PAT_RESULTS = ["PENDING", "PASSED", "PASSED WITH PUNCHLIST", "FAILED"]
ACCEPTANCE_STATUSES = ["NOT READY", "READY", "ACCEPTED", "REJECTED"]
RUNTIME_DIR = os.path.join(BASE_DIR, "runtime")
WORKBOOK_LOCK_DIR = os.path.join(RUNTIME_DIR, "locks")
MASTER_TRACKER_STATE_PATH = os.path.join(RUNTIME_DIR, "master_tracker_state.json")
MASTER_TRACKER_PENDING_REMINDER_HOURS = config_int(
    "MASTER_TRACKER_PENDING_REMINDER_HOURS", 24, minimum=1, maximum=720
)
WORKBOOK_TEMP_PREFIX = ".~ruc_tmp_"
WORKBOOK_LOCK_TIMEOUT_SECONDS = config_int(
    "WORKBOOK_LOCK_TIMEOUT_SECONDS", 30, minimum=1, maximum=300
)
WORKBOOK_LOCK_POLL_SECONDS = 0.1
WORKBOOK_STALE_LOCK_SECONDS = config_int(
    "WORKBOOK_STALE_LOCK_SECONDS", 3600, minimum=30, maximum=86400
)
PROJECT_WORKBOOK_REQUIRED_SHEETS = (
    "ACCESS INFO",
    "2X2",
    "NBI",
    "CERTIFICATES",
    "eSignature",
    "SEC ID",
    "WAH CERT",
    "ID",
)
MASTER_TRACKER_REQUIRED_SHEETS = ("GLOBE NLZ",)
WORKBOOK_THREAD_LOCKS = {}
WORKBOOK_THREAD_LOCKS_GUARD = threading.Lock()


class WorkbookSafetyError(RuntimeError):

    def __init__(self, message, user_message=None):

        super().__init__(message)
        self.user_message = user_message or message


class WorkbookBusyError(WorkbookSafetyError):
    pass


class WorkbookValidationError(WorkbookSafetyError):
    pass


def current_timestamp():

    return datetime.now().replace(microsecond=0)


def parse_iso_datetime(value):

    if not value:
        return None

    try:
        normalized = str(value).strip()

        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"

        parsed = datetime.fromisoformat(normalized)

        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)

        return parsed.replace(microsecond=0)
    except (TypeError, ValueError):
        return None


def format_display_datetime(value):

    parsed = parse_iso_datetime(value) if not isinstance(value, datetime) else value

    if parsed:
        return parsed.strftime("%d %b %Y %H:%M")

    return ""


def file_sha256(file_path):

    digest = hashlib.sha256()

    with open(file_path, "rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def read_json_file(file_path):

    try:
        with open(file_path, "r", encoding="utf-8") as json_file:
            data = json.load(json_file)

        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def write_json_file_atomic(file_path, data):

    folder_path = os.path.dirname(file_path)
    os.makedirs(folder_path, exist_ok=True)
    temp_path = os.path.join(
        folder_path,
        f"{WORKBOOK_TEMP_PREFIX}{uuid.uuid4().hex}_{os.path.basename(file_path)}",
    )

    try:
        with open(temp_path, "w", encoding="utf-8") as json_file:
            json.dump(data, json_file, indent=2, sort_keys=True)
            json_file.write("\n")

        os.replace(temp_path, file_path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def master_tracker_file_snapshot(tracker_path=None):

    tracker_path = tracker_path or MASTER_TRACKER_PATH

    if not os.path.exists(tracker_path):
        return {"exists": False}

    modified_at = datetime.fromtimestamp(os.path.getmtime(tracker_path)).replace(
        microsecond=0
    )
    checksum = file_sha256(tracker_path)

    return {
        "exists": True,
        "filename": os.path.basename(tracker_path),
        "checksum": checksum,
        "short_checksum": checksum[:12],
        "modified_at": modified_at.isoformat(),
        "modified_at_display": format_display_datetime(modified_at),
        "size": os.path.getsize(tracker_path),
    }


def master_tracker_version_label(checksum, timestamp):

    parsed = parse_iso_datetime(timestamp) or current_timestamp()
    short_checksum = clean_text(checksum)[:8] or "unknown"
    return f"MT-{parsed.strftime('%Y%m%d-%H%M')}-{short_checksum}"


def get_master_tracker_status(
    tracker_path=None,
    state_path=None,
    now=None,
):

    tracker_path = tracker_path or MASTER_TRACKER_PATH
    state_path = state_path or MASTER_TRACKER_STATE_PATH
    now = now or current_timestamp()
    snapshot = master_tracker_file_snapshot(tracker_path)

    if not snapshot.get("exists"):
        return {
            "status": "MISSING",
            "is_pending": False,
            "is_overdue": False,
            "version_label": "Unavailable",
            "approved_at_display": "",
            "last_downloaded_at_display": "",
            "last_uploaded_at_display": "",
            "last_rejected_upload_at_display": "",
            "last_downloaded_by": "",
            "reminder_hours": MASTER_TRACKER_PENDING_REMINDER_HOURS,
            "metadata_warning": False,
        }

    metadata_warning = os.path.exists(state_path) and not read_json_file(state_path)
    state = read_json_file(state_path)
    approved_checksum = state.get("approved_checksum")
    approved_at = state.get("approved_at")

    if approved_checksum != snapshot["checksum"] or not approved_at:
        approved_checksum = snapshot["checksum"]
        approved_at = snapshot["modified_at"]

    last_downloaded_at = parse_iso_datetime(state.get("last_downloaded_at"))
    last_uploaded_at = parse_iso_datetime(state.get("last_uploaded_at"))
    downloaded_checksum = state.get("downloaded_checksum")
    accepted_after_download = bool(
        last_downloaded_at and last_uploaded_at and last_uploaded_at >= last_downloaded_at
    )
    pending = bool(
        last_downloaded_at
        and downloaded_checksum == snapshot["checksum"]
        and not accepted_after_download
    )

    pending_hours = 0

    if pending:
        pending_hours = max((now - last_downloaded_at).total_seconds() / 3600, 0)

    is_overdue = pending and pending_hours >= MASTER_TRACKER_PENDING_REMINDER_HOURS

    return {
        "status": "UPDATE PENDING" if pending else "CURRENT",
        "is_pending": pending,
        "is_overdue": is_overdue,
        "pending_hours": round(pending_hours, 1),
        "version_label": master_tracker_version_label(approved_checksum, approved_at),
        "approved_checksum": approved_checksum,
        "approved_short_checksum": approved_checksum[:12],
        "approved_at": approved_at,
        "approved_at_display": format_display_datetime(approved_at),
        "last_downloaded_at": state.get("last_downloaded_at"),
        "last_downloaded_at_display": format_display_datetime(
            state.get("last_downloaded_at")
        ),
        "last_downloaded_by": clean_text(state.get("last_downloaded_by")),
        "last_uploaded_at": state.get("last_uploaded_at"),
        "last_uploaded_at_display": format_display_datetime(state.get("last_uploaded_at")),
        "last_rejected_upload_at": state.get("last_rejected_upload_at"),
        "last_rejected_upload_at_display": format_display_datetime(
            state.get("last_rejected_upload_at")
        ),
        "reminder_hours": MASTER_TRACKER_PENDING_REMINDER_HOURS,
        "metadata_warning": metadata_warning,
    }


def read_master_tracker_state(state_path=None):

    state_path = state_path or MASTER_TRACKER_STATE_PATH
    return read_json_file(state_path)


def write_master_tracker_state(state, state_path=None):

    state_path = state_path or MASTER_TRACKER_STATE_PATH
    safe_state = dict(state or {})
    safe_state["schema_version"] = 1
    write_json_file_atomic(state_path, safe_state)
    return safe_state


def record_master_tracker_download_for_edit(
    admin_id=None,
    username=None,
    tracker_path=None,
    state_path=None,
    now=None,
):

    tracker_path = tracker_path or MASTER_TRACKER_PATH
    state_path = state_path or MASTER_TRACKER_STATE_PATH
    snapshot = master_tracker_file_snapshot(tracker_path)

    if not snapshot.get("exists"):
        return get_master_tracker_status(tracker_path, state_path, now)

    now = now or current_timestamp()
    state = read_master_tracker_state(state_path)
    approved_checksum = state.get("approved_checksum")
    approved_at = state.get("approved_at")

    if approved_checksum != snapshot["checksum"] or not approved_at:
        approved_checksum = snapshot["checksum"]
        approved_at = snapshot["modified_at"]

    state.update(
        {
            "tracker_filename": snapshot["filename"],
            "approved_checksum": approved_checksum,
            "approved_at": approved_at,
            "approved_size": snapshot["size"],
            "last_downloaded_at": now.isoformat(),
            "last_downloaded_by": clean_text(username),
            "last_downloaded_admin_id": admin_id,
            "downloaded_checksum": snapshot["checksum"],
            "last_checked_at": now.isoformat(),
        }
    )
    write_master_tracker_state(state, state_path)
    return get_master_tracker_status(tracker_path, state_path, now)


def record_master_tracker_upload_accepted(
    admin_id=None,
    username=None,
    tracker_path=None,
    state_path=None,
    now=None,
):

    tracker_path = tracker_path or MASTER_TRACKER_PATH
    state_path = state_path or MASTER_TRACKER_STATE_PATH
    snapshot = master_tracker_file_snapshot(tracker_path)

    if not snapshot.get("exists"):
        return get_master_tracker_status(tracker_path, state_path, now)

    now = now or current_timestamp()
    state = read_master_tracker_state(state_path)
    state.update(
        {
            "tracker_filename": snapshot["filename"],
            "approved_checksum": snapshot["checksum"],
            "approved_at": now.isoformat(),
            "approved_size": snapshot["size"],
            "last_uploaded_at": now.isoformat(),
            "last_uploaded_by": clean_text(username),
            "last_uploaded_admin_id": admin_id,
            "last_checked_at": now.isoformat(),
        }
    )
    write_master_tracker_state(state, state_path)
    return get_master_tracker_status(tracker_path, state_path, now)


def record_master_tracker_upload_rejected(
    reason,
    admin_id=None,
    username=None,
    tracker_path=None,
    state_path=None,
    now=None,
):

    tracker_path = tracker_path or MASTER_TRACKER_PATH
    state_path = state_path or MASTER_TRACKER_STATE_PATH
    now = now or current_timestamp()
    state = read_master_tracker_state(state_path)
    snapshot = master_tracker_file_snapshot(tracker_path)

    if snapshot.get("exists") and (
        state.get("approved_checksum") != snapshot["checksum"]
        or not state.get("approved_at")
    ):
        state["approved_checksum"] = snapshot["checksum"]
        state["approved_at"] = snapshot["modified_at"]
        state["approved_size"] = snapshot["size"]
        state["tracker_filename"] = snapshot["filename"]

    state.update(
        {
            "last_rejected_upload_at": now.isoformat(),
            "last_rejected_upload_by": clean_text(username),
            "last_rejected_upload_admin_id": admin_id,
            "last_rejected_upload_reason": clean_text(reason)[:240],
            "last_checked_at": now.isoformat(),
        }
    )
    write_master_tracker_state(state, state_path)
    return get_master_tracker_status(tracker_path, state_path, now)


def workbook_error_message(exc):

    if isinstance(exc, WorkbookBusyError):
        return (
            "The workbook is currently busy or open in another process. "
            "Please close it if needed and try again."
        )

    if isinstance(exc, WorkbookSafetyError):
        return exc.user_message

    return "The workbook could not be updated safely. Please try again."


def canonical_workbook_path(workbook_path):

    return os.path.normcase(os.path.abspath(workbook_path))


def workbook_lock_path(workbook_path):

    digest = hashlib.sha256(canonical_workbook_path(workbook_path).encode("utf-8")).hexdigest()
    return os.path.join(WORKBOOK_LOCK_DIR, digest + ".lock")


def get_workbook_thread_lock(workbook_path):

    canonical_path = canonical_workbook_path(workbook_path)

    with WORKBOOK_THREAD_LOCKS_GUARD:
        lock = WORKBOOK_THREAD_LOCKS.get(canonical_path)

        if lock is None:
            lock = threading.Lock()
            WORKBOOK_THREAD_LOCKS[canonical_path] = lock

    return lock


def read_workbook_lock_metadata(lock_file_path):

    metadata = {}

    try:
        with open(lock_file_path, "r", encoding="utf-8") as lock_file:
            for line in lock_file.read(2048).splitlines():
                if "=" not in line:
                    continue

                key, value = line.split("=", 1)
                metadata[key.strip()] = value.strip()
    except OSError:
        pass

    return metadata


def process_is_running(pid):

    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None

    if pid <= 0:
        return None

    if pid == os.getpid():
        return True

    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            synchronize = 0x00100000
            query_limited_information = 0x1000
            wait_timeout = 0x00000102
            error_access_denied = 5

            handle = kernel32.OpenProcess(
                synchronize | query_limited_information,
                False,
                pid,
            )

            if not handle:
                return True if ctypes.get_last_error() == error_access_denied else False

            try:
                return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return None

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None

    return True


def workbook_lock_is_stale(lock_file_path):

    metadata = read_workbook_lock_metadata(lock_file_path)
    process_state = process_is_running(metadata.get("pid"))

    if process_state is False:
        return True

    try:
        lock_age = time_module.time() - os.path.getmtime(lock_file_path)
    except OSError:
        return False

    return process_state is None and lock_age > WORKBOOK_STALE_LOCK_SECONDS


def remove_stale_workbook_lock(lock_file_path):

    if not os.path.exists(lock_file_path):
        return True

    if not workbook_lock_is_stale(lock_file_path):
        return False

    try:
        os.remove(lock_file_path)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


@contextmanager
def workbook_write_lock(workbook_path, timeout=None):

    timeout = float(timeout if timeout is not None else WORKBOOK_LOCK_TIMEOUT_SECONDS)
    deadline = time_module.monotonic() + timeout
    thread_lock = get_workbook_thread_lock(workbook_path)

    if not thread_lock.acquire(timeout=timeout):
        raise WorkbookBusyError(
            f"Timed out waiting for workbook lock: {os.path.basename(workbook_path)}"
        )

    lock_file_path = workbook_lock_path(workbook_path)
    lock_fd = None

    try:
        os.makedirs(WORKBOOK_LOCK_DIR, exist_ok=True)

        while True:
            try:
                lock_fd = os.open(lock_file_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                metadata = (
                    f"pid={os.getpid()}\n"
                    f"created_at={datetime.now().isoformat(timespec='seconds')}\n"
                    f"workbook={os.path.basename(workbook_path)}\n"
                )
                os.write(lock_fd, metadata.encode("utf-8"))
                break
            except FileExistsError as exc:
                if remove_stale_workbook_lock(lock_file_path):
                    continue

                if time_module.monotonic() >= deadline:
                    raise WorkbookBusyError(
                        f"Timed out waiting for workbook lock file: {os.path.basename(workbook_path)}"
                    ) from exc

                remaining = max(0.0, deadline - time_module.monotonic())
                time_module.sleep(min(WORKBOOK_LOCK_POLL_SECONDS, remaining))

        yield
    finally:
        if lock_fd is not None:
            os.close(lock_fd)

        try:
            if os.path.exists(lock_file_path):
                os.remove(lock_file_path)
        except OSError:
            pass

        thread_lock.release()


def verify_workbook_sheets(wb, expected_sheets):

    missing = [sheet for sheet in (expected_sheets or []) if sheet not in wb.sheetnames]

    if missing:
        raise WorkbookValidationError(
            "Workbook is missing required sheet(s): " + ", ".join(missing)
        )


def validate_workbook_file(workbook_path, expected_sheets=None):

    try:
        wb = load_workbook(workbook_path, read_only=True, data_only=False)
        try:
            verify_workbook_sheets(wb, expected_sheets)
        finally:
            wb.close()
    except WorkbookSafetyError:
        raise
    except Exception as exc:
        raise WorkbookValidationError(
            f"Workbook validation failed for {os.path.basename(workbook_path)}.",
            user_message="The workbook file could not be validated after saving.",
        ) from exc


def save_workbook_atomic_locked(
    wb,
    workbook_path,
    expected_sheets=None,
    backup_folder=None,
    backup_existing=True,
    before_replace=None,
):

    verify_workbook_sheets(wb, expected_sheets)

    folder_path = os.path.dirname(workbook_path)
    os.makedirs(folder_path, exist_ok=True)
    temp_path = os.path.join(
        folder_path,
        f"{WORKBOOK_TEMP_PREFIX}{uuid.uuid4().hex}_{os.path.basename(workbook_path)}",
    )
    backup_path = None

    try:
        if backup_existing and os.path.exists(workbook_path):
            backup_path = backup_file(workbook_path, backup_folder or folder_path)

        wb.save(temp_path)
        validate_workbook_file(temp_path, expected_sheets)

        if before_replace:
            before_replace(temp_path, backup_path)

        os.replace(temp_path, workbook_path)

        try:
            validate_workbook_file(workbook_path, expected_sheets)
        except WorkbookSafetyError:
            restore_workbook_backup(workbook_path, backup_path)
            raise

        return {
            "backup_path": backup_path,
            "workbook_path": workbook_path,
            "expected_sheets": tuple(expected_sheets or ()),
        }
    except PermissionError as exc:
        raise WorkbookBusyError(
            f"Workbook is locked by another process: {os.path.basename(workbook_path)}"
        ) from exc
    except WorkbookSafetyError:
        raise
    except Exception as exc:
        raise WorkbookSafetyError(
            f"Workbook save failed for {os.path.basename(workbook_path)}.",
            user_message="The workbook could not be saved safely.",
        ) from exc
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def update_persistent_workbook(
    workbook_path,
    mutator,
    expected_sheets=None,
    backup_folder=None,
    operation="workbook update",
    timeout=None,
    before_replace=None,
):

    with workbook_write_lock(workbook_path, timeout=timeout):
        if not os.path.exists(workbook_path):
            raise WorkbookSafetyError(
                f"Workbook missing for {operation}: {os.path.basename(workbook_path)}",
                user_message="The workbook is missing. Please restore or rebuild it before continuing.",
            )

        try:
            wb = load_workbook(workbook_path)
        except PermissionError as exc:
            raise WorkbookBusyError(
                f"Workbook is locked by another process: {os.path.basename(workbook_path)}"
            ) from exc
        except Exception as exc:
            raise WorkbookValidationError(
                f"Workbook could not be opened for {operation}: {os.path.basename(workbook_path)}",
                user_message="The workbook could not be opened safely.",
            ) from exc

        try:
            mutator(wb)
            return save_workbook_atomic_locked(
                wb,
                workbook_path,
                expected_sheets=expected_sheets,
                backup_folder=backup_folder,
                backup_existing=True,
                before_replace=before_replace,
            )
        finally:
            wb.close()


def restore_persistent_workbook_backup(workbook_path, backup_path, expected_sheets=None, timeout=None):

    if not backup_path:
        return False

    with workbook_write_lock(workbook_path, timeout=timeout):
        if not os.path.exists(backup_path):
            return False

        folder_path = os.path.dirname(workbook_path)
        temp_path = os.path.join(
            folder_path,
            f"{WORKBOOK_TEMP_PREFIX}restore_{uuid.uuid4().hex}_{os.path.basename(workbook_path)}",
        )

        try:
            shutil.copy2(backup_path, temp_path)
            validate_workbook_file(temp_path, expected_sheets)
            os.replace(temp_path, workbook_path)
            validate_workbook_file(workbook_path, expected_sheets)
            return True
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass


def restore_workbook_results(*results):

    restored = []

    for result in reversed([item for item in results if item]):
        try:
            if restore_persistent_workbook_backup(
                result.get("workbook_path"),
                result.get("backup_path"),
                result.get("expected_sheets"),
            ):
                restored.append(result.get("workbook_path"))
        except WorkbookSafetyError:
            continue

    return restored


def create_persistent_workbook(
    workbook_path,
    workbook_factory,
    expected_sheets=None,
    operation="workbook create",
    timeout=None,
):

    with workbook_write_lock(workbook_path, timeout=timeout):
        if os.path.exists(workbook_path):
            raise WorkbookSafetyError(
                f"Workbook already exists for {operation}: {os.path.basename(workbook_path)}",
                user_message="A workbook already exists for this project code.",
            )

        wb = workbook_factory()

        try:
            return save_workbook_atomic_locked(
                wb,
                workbook_path,
                expected_sheets=expected_sheets,
                backup_existing=False,
            )
        finally:
            wb.close()


def replace_persistent_workbook_file(
    target_path,
    candidate_path,
    validator=None,
    expected_sheets=None,
    backup_folder=None,
    operation="workbook replace",
    timeout=None,
):

    with workbook_write_lock(target_path, timeout=timeout):
        if not os.path.exists(target_path):
            raise WorkbookSafetyError(
                f"Workbook missing for {operation}: {os.path.basename(target_path)}",
                user_message="The existing workbook could not be found.",
            )

        folder_path = os.path.dirname(target_path)
        temp_path = os.path.join(
            folder_path,
            f"{WORKBOOK_TEMP_PREFIX}{uuid.uuid4().hex}_{os.path.basename(target_path)}",
        )
        backup_path = None

        try:
            if validator:
                result = validator(target_path, candidate_path)

                if result != "OK":
                    raise WorkbookValidationError(
                        result,
                        user_message=result,
                    )

            validate_workbook_file(candidate_path, expected_sheets)
            backup_path = backup_file(target_path, backup_folder or folder_path)
            shutil.copy2(candidate_path, temp_path)
            validate_workbook_file(temp_path, expected_sheets)
            os.replace(temp_path, target_path)

            try:
                validate_workbook_file(target_path, expected_sheets)
            except WorkbookSafetyError:
                restore_workbook_backup(target_path, backup_path)
                raise

            return {
                "backup_path": backup_path,
                "workbook_path": target_path,
                "expected_sheets": tuple(expected_sheets or ()),
            }
        except PermissionError as exc:
            raise WorkbookBusyError(
                f"Workbook is locked by another process: {os.path.basename(target_path)}"
            ) from exc
        except WorkbookSafetyError:
            raise
        except Exception as exc:
            raise WorkbookSafetyError(
                f"Workbook replacement failed for {os.path.basename(target_path)}.",
                user_message="The workbook could not be replaced safely.",
            ) from exc
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

FINAL_ROLE_LABELS = {
    "super_admin": "SUPER ADMIN",
    "hr": "HR",
    "team_leader": "TEAM LEADER",
}
LEGACY_ROLE_ALIASES = {
    "admin": "hr",
    "project_manager": "hr",
    "site_supervisor": "team_leader",
    "viewer": "hr",
}
ROLE_LABELS = {
    **FINAL_ROLE_LABELS,
    "admin": "HR (LEGACY ADMIN)",
    "project_manager": "HR (LEGACY PROJECT MANAGER)",
    "site_supervisor": "TEAM LEADER (LEGACY SITE SUPERVISOR)",
    "viewer": "HR (LEGACY VIEWER)",
}
ROLE_FORM_LABELS = FINAL_ROLE_LABELS
ROLE_PERMISSIONS = {
    "super_admin": {
        "view",
        "manage_users",
        "manage_team_leader_accounts",
        "manage_teams",
        "transfer_team_members",
        "view_audit_logs",
        "manage_projects",
        "delete_projects",
        "manage_personnel",
        "assign_employee_projects",
        "manage_sites",
        "manage_assignments",
        "manage_safety",
        "manage_operations",
        "manage_attendance",
        "manage_tasks",
        "manage_permits",
        "manage_toolbox",
        "manage_incidents",
        "manage_punchlist",
        "manage_pat",
        "view_pat",
        "manage_acceptance",
        "generate_ids",
        "view_id_cards",
        "view_master_tracker",
        "manage_master_tracker",
        "view_project_workbooks",
        "export_reports",
        "reset_system",
    },
    "hr": {
        "view",
        "manage_team_leader_accounts",
        "manage_personnel",
        "assign_employee_projects",
        "manage_safety",
        "generate_ids",
        "view_id_cards",
        "export_reports",
    },
    "team_leader": {
        "view",
        "team_leader_portal",
        "view_team",
        "manage_attendance",
        "manage_operations",
        "manage_tasks",
        "manage_toolbox",
        "manage_incidents",
        "manage_punchlist",
        "view_pat",
        "view_id_cards",
        "export_reports",
    },
}
LAST_SUPER_ADMIN_MESSAGE = "At least one active Super Admin must remain."


@app.route("/static/<path:filename>", endpoint="static")
def static_files(filename):

    normalized = filename.replace("\\", "/")

    if (
        normalized == "."
        or normalized.startswith("../")
        or "/../" in normalized
        or normalized.startswith("..")
    ):
        return render_error_page(400, "Bad Request", "The requested static path is invalid.")

    normalized = os.path.normpath(normalized).replace("\\", "/")

    if normalized.startswith("uploads/") and not validate_session_account():
        return redirect("/")

    if normalized.startswith("uploads/"):
        denied = authorize_stored_file_access("static/" + normalized)

        if denied:
            return denied

    if not normalized.startswith(("css/", "images/", "js/", "uploads/")):
        return render_error_page(400, "Bad Request", "The requested static path is invalid.")

    return send_from_directory(safe_abs_path("static"), normalized)


def role_label(role):

    return ROLE_LABELS.get(role, str(role or "").upper() or "UNKNOWN")


def effective_role(role):

    return LEGACY_ROLE_ALIASES.get(role, role)


def current_effective_role():

    return effective_role(session.get("role"))


def is_super_admin_role(role=None):

    return effective_role(role if role is not None else session.get("role")) == "super_admin"


def is_hr_role(role=None):

    return effective_role(role if role is not None else session.get("role")) == "hr"


def is_team_leader_role(role=None):

    return effective_role(role if role is not None else session.get("role")) == "team_leader"


def has_permission(role, permission):

    return permission in ROLE_PERMISSIONS.get(effective_role(role), set())


def can(permission):

    return has_permission(session.get("role"), permission)


def initials_for_name(name):

    words = [
        clean_text(part)
        for part in clean_text(name).replace("_", " ").split()
        if clean_text(part)
    ]

    if not words:
        return "U"

    if len(words) == 1:
        return words[0][:2].upper()

    return (words[0][0] + words[-1][0]).upper()


def compose_person_name(first_name, middle_name=None, last_name=None):

    return clean_text(
        " ".join(
            part
            for part in (
                clean_text(first_name),
                clean_text(middle_name),
                clean_text(last_name),
            )
            if part
        )
    )


def current_user_profile():

    profile = {
        "admin_id": session.get("admin_id"),
        "username": session.get("admin"),
        "role": session.get("role"),
        "role_label": role_label(session.get("role")),
        "display_name": session.get("admin") or "User",
        "initials": initials_for_name(session.get("admin") or "User"),
        "avatar_url": None,
        "employee_id": session.get("employee_id"),
        "profile_url": None,
        "change_password_url": None,
    }

    admin_id = session.get("admin_id")

    if not admin_id:
        return profile

    conn = None
    cursor = None

    try:
        conn = connect_db()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT a.id,
                   a.username,
                   a.role,
                   a.active,
                   a.employee_id,
                   e.first_name,
                   e.middle_name,
                   e.last_name,
                   e.photo
            FROM admins a
            LEFT JOIN employees e ON a.employee_id = e.id
            WHERE a.id=%s
            """,
            (admin_id,),
        )
        account = row_to_dict(cursor)

        if account:
            display_name = account_display_name(account) or account.get("username")
            profile.update(
                {
                    "admin_id": account.get("id"),
                    "username": account.get("username"),
                    "role": account.get("role"),
                    "role_label": role_label(account.get("role")),
                    "display_name": display_name,
                    "initials": initials_for_name(display_name),
                    "employee_id": account.get("employee_id"),
                }
            )

            employee_id = account.get("employee_id")
            photo = account.get("photo")
            photo_authorized = bool(employee_id and photo) and (
                is_super_admin_role(account.get("role"))
                or is_hr_role(account.get("role"))
                or (
                    is_team_leader_role(account.get("role"))
                    and team_leader_has_employee(cursor, employee_id, admin_id=admin_id)
                )
            )

            if photo_authorized:
                profile["avatar_url"] = url_for(
                    "employee_file", employee_id=employee_id, file_kind="photo"
                )

    except Exception:
        profile["avatar_url"] = None

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    try:
        profile["profile_url"] = url_for("profile")
    except Exception:
        profile["profile_url"] = None

    return profile


def get_request_ip():

    if config_bool("RUC_TRUST_PROXY_HEADERS", False):
        forwarded_for = request.headers.get("X-Forwarded-For", "")

        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

    return request.remote_addr


def audit_event(
    action,
    entity_type=None,
    entity_id=None,
    description=None,
    conn=None,
    username_snapshot=None,
    role_snapshot=None,
    admin_id=None,
):

    db = conn
    owns_connection = db is None
    cursor = None

    try:
        if db is None:
            db = connect_db()

        cursor = db.cursor()
        cursor.execute(
            """
            INSERT INTO audit_logs (
                admin_id,
                username_snapshot,
                role_snapshot,
                action,
                entity_type,
                entity_id,
                description,
                ip_address,
                http_method,
                route
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                admin_id if admin_id is not None else session.get("admin_id"),
                username_snapshot if username_snapshot is not None else session.get("admin"),
                role_snapshot if role_snapshot is not None else session.get("role"),
                action,
                entity_type,
                str(entity_id) if entity_id is not None else None,
                description,
                get_request_ip(),
                request.method,
                request.endpoint or request.path,
            ),
        )

        if owns_connection:
            db.commit()

    except Exception:
        if owns_connection and db:
            db.rollback()

    finally:
        if cursor:
            cursor.close()
        if owns_connection and db:
            db.close()


def ensure_csrf_token():

    token = session.get(CSRF_SESSION_KEY)

    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token

    return token


def csrf_token():

    return ensure_csrf_token()


def submitted_csrf_token():

    token = request.form.get(CSRF_FORM_FIELD)

    if token:
        return token

    for header_name in CSRF_HEADER_NAMES:
        token = request.headers.get(header_name)

        if token:
            return token

    return ""


def csrf_token_is_valid():

    expected = session.get(CSRF_SESSION_KEY)
    supplied = submitted_csrf_token()

    if not expected or not supplied:
        return False

    return secrets.compare_digest(str(expected), str(supplied))


def security_audit_key(action):

    return "|".join(
        [
            clean_text(action),
            clean_text(get_request_ip()),
            clean_text(request.path),
        ]
    )


def audit_security_event_once(action, description, entity_type="security", entity_id=None):

    if SECURITY_AUDIT_THROTTLE_SECONDS <= 0:
        audit_event(action, entity_type, entity_id, description)
        return

    now = time_module.time()
    key = security_audit_key(action)
    last_seen = security_audit_throttle_state.get(key, 0)

    if now - last_seen < SECURITY_AUDIT_THROTTLE_SECONDS:
        return

    security_audit_throttle_state[key] = now
    audit_event(action, entity_type, entity_id, description)


def render_error_page(status_code, title, message):

    try:
        if session.get("admin_id"):
            fallback_url = url_for("dashboard")
        else:
            fallback_url = url_for("login")
    except Exception:
        fallback_url = "/"

    return (
        render_template(
            "error.html",
            status_code=status_code,
            title=title,
            message=message,
            back_url=fallback_url,
        ),
        status_code,
    )


@app.before_request
def enforce_csrf_for_state_changes():

    if request.url_rule is None:
        return None

    if request.method in CSRF_SAFE_METHODS:
        return None

    if csrf_token_is_valid():
        return None

    audit_security_event_once(
        "CSRF_REJECTED",
        "Rejected a state-changing request with a missing or invalid CSRF token.",
    )
    return render_error_page(
        400,
        "Bad Request",
        "Your form session expired or the request could not be verified. Please reload the page and try again.",
    )


def login_rate_limit_key():

    user_agent = clean_text(request.headers.get("User-Agent", ""))[:120]
    return f"{get_request_ip()}|{user_agent}"


def login_rate_limit_status():

    now = time_module.time()
    key = login_rate_limit_key()
    entry = login_rate_limit_state.get(
        key,
        {
            "failed_at": [],
            "blocked_until": 0,
        },
    )

    if entry.get("blocked_until", 0) > now:
        retry_after = max(1, int(entry["blocked_until"] - now))
        login_rate_limit_state[key] = entry
        return True, retry_after

    recent_failures = [
        failed_at
        for failed_at in entry.get("failed_at", [])
        if now - failed_at <= LOGIN_RATE_LIMIT_WINDOW_SECONDS
    ]
    entry["failed_at"] = recent_failures
    entry["blocked_until"] = 0
    login_rate_limit_state[key] = entry

    return False, 0


def record_login_failure():

    now = time_module.time()
    key = login_rate_limit_key()
    entry = login_rate_limit_state.get(
        key,
        {
            "failed_at": [],
            "blocked_until": 0,
        },
    )
    entry["failed_at"] = [
        failed_at
        for failed_at in entry.get("failed_at", [])
        if now - failed_at <= LOGIN_RATE_LIMIT_WINDOW_SECONDS
    ]
    entry["failed_at"].append(now)

    if len(entry["failed_at"]) >= LOGIN_RATE_LIMIT_ATTEMPTS:
        entry["blocked_until"] = now + LOGIN_RATE_LIMIT_BLOCK_SECONDS
        login_rate_limit_state[key] = entry
        return True, LOGIN_RATE_LIMIT_BLOCK_SECONDS

    login_rate_limit_state[key] = entry
    return False, 0


def clear_login_failures():

    login_rate_limit_state.pop(login_rate_limit_key(), None)


def rate_limited_response(retry_after=None):

    response, status_code = render_error_page(
        429,
        "Too Many Login Attempts",
        "Too many failed login attempts. Please wait briefly before trying again.",
    )
    response = app.make_response((response, status_code))

    if retry_after:
        response.headers["Retry-After"] = str(int(retry_after))

    return response


SENSITIVE_CACHE_ENDPOINTS = {
    "employee_file",
    "safety_document_file",
    "daily_log_file",
    "punchlist_file",
    "pat_document_file",
    "id_cards",
    "uploaded_file",
    "photos",
    "master_tracker",
    "open_excel",
    "daily_operations_export",
    "site_completion_report_pdf",
    "site_completion_report_excel",
    "project_management_report_pdf",
    "project_management_report_excel",
    "personnel_safety_report_export",
    "daily_operations_report_export",
    "punchlist_management_report_export",
    "pat_acceptance_report_export",
    "site_handover_package",
    "audit_logs",
    "users",
    "profile",
}


@app.after_request
def apply_security_headers(response):

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

    if request.endpoint in SENSITIVE_CACHE_ENDPOINTS or (
        session.get("admin_id") and response.mimetype == "text/html"
    ):
        response.headers["Cache-Control"] = "no-store, private, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    return response


@app.errorhandler(400)
def handle_bad_request(error):

    return render_error_page(400, "Bad Request", "The request could not be processed.")


@app.errorhandler(403)
def handle_forbidden(error):

    return render_error_page(403, "Access Denied", "You do not have permission to access this page.")


@app.errorhandler(404)
def handle_not_found(error):

    return render_error_page(404, "Page Not Found", "The page you requested could not be found.")


@app.errorhandler(405)
def handle_method_not_allowed(error):

    return render_error_page(405, "Method Not Allowed", "This action is not available from that request method.")


@app.errorhandler(429)
def handle_too_many_requests(error):

    return render_error_page(429, "Too Many Requests", "Please wait briefly before trying again.")


@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(error):

    return render_error_page(413, "File Too Large", "The uploaded file is larger than the allowed limit.")


@app.errorhandler(Exception)
def handle_unexpected_error(error):

    if isinstance(error, HTTPException):
        status_code = error.code or 500
        title = error.name or "Request Error"
        return render_error_page(
            status_code,
            title,
            "The request could not be completed.",
        )

    audit_security_event_once(
        "APP_ERROR",
        "An unexpected application error occurred.",
        entity_type="route",
        entity_id=request.path,
    )
    return render_error_page(
        500,
        "Unexpected Server Error",
        "Something went wrong while processing the request. Please try again or contact the system administrator.",
    )


def validate_session_account():

    admin_id = session.get("admin_id")
    username = session.get("admin")

    if not admin_id and not username:
        return None

    conn = connect_db()
    cursor = conn.cursor()

    if admin_id:
        cursor.execute(
            """
            SELECT id, username, role, active, employee_id
            FROM admins
            WHERE id=%s
            """,
            (admin_id,),
        )
    else:
        cursor.execute(
            """
            SELECT id, username, role, active, employee_id
            FROM admins
            WHERE username=%s
            """,
            (username,),
        )

    account = cursor.fetchone()
    cursor.close()
    conn.close()

    if not account or not account[3]:
        session.clear()
        return None

    session["admin_id"] = account[0]
    session["admin"] = account[1]
    session["role"] = account[2]
    session["employee_id"] = account[4]
    return account


def access_denied(message="You do not have permission to perform this action."):

    audit_event(
        "AUTH_ACCESS_DENIED",
        "route",
        request.path,
        f"Denied access to {request.path}",
    )
    return render_template("access_denied.html", message=message), 403


def login_required(view):

    @wraps(view)
    def wrapped(*args, **kwargs):

        if not validate_session_account():
            return redirect("/")

        return view(*args, **kwargs)

    return wrapped


def permission_required(*permissions):

    def decorator(view):

        @wraps(view)
        def wrapped(*args, **kwargs):

            if not validate_session_account():
                return redirect("/")

            role = session.get("role")

            if not all(has_permission(role, permission) for permission in permissions):
                return access_denied()

            return view(*args, **kwargs)

        return wrapped

    return decorator


def any_permission_required(*permissions):

    def decorator(view):

        @wraps(view)
        def wrapped(*args, **kwargs):

            if not validate_session_account():
                return redirect("/")

            role = session.get("role")

            if not any(has_permission(role, permission) for permission in permissions):
                return access_denied()

            return view(*args, **kwargs)

        return wrapped

    return decorator


def role_required(*roles):

    def decorator(view):

        @wraps(view)
        def wrapped(*args, **kwargs):

            if not validate_session_account():
                return redirect("/")

            if current_effective_role() not in roles:
                return access_denied()

            return view(*args, **kwargs)

        return wrapped

    return decorator


def super_admin_required(view):

    return role_required("super_admin")(view)


def static_asset(filename):

    normalized = filename.replace("\\", "/").lstrip("/")

    try:
        file_path = safe_abs_path("static", normalized)

        if os.path.isfile(file_path):
            return url_for("static", filename=normalized, v=int(os.path.getmtime(file_path)))

    except ValueError:
        pass

    return url_for("static", filename=normalized)


@app.context_processor
def inject_auth_context():

    profile = current_user_profile()

    return {
        "current_username": session.get("admin"),
        "current_admin_id": session.get("admin_id"),
        "current_role": session.get("role"),
        "current_effective_role": current_effective_role(),
        "current_role_label": role_label(session.get("role")),
        "current_profile": profile,
        "role_label": role_label,
        "effective_role": effective_role,
        "can": can,
        "static_asset": static_asset,
        "csrf_token": csrf_token,
        "full_employee_name": full_employee_name,
        "ROLE_LABELS": ROLE_LABELS,
        "ROLE_FORM_LABELS": ROLE_FORM_LABELS,
    }


def safe_abs_path(*parts):

    path = os.path.abspath(os.path.join(BASE_DIR, *parts))

    if not path.startswith(BASE_DIR + os.sep) and path != BASE_DIR:
        raise ValueError("Unsafe path")

    return path


def allowed_file(filename, allowed_extensions=ALLOWED_UPLOAD_EXTENSIONS):

    return (
        filename
        and "." in filename
        and filename.rsplit(".", 1)[1].lower() in allowed_extensions
    )


def allowed_extensions_for_category(category):

    category = LEGACY_UPLOAD_CATEGORIES.get(category, category)
    return CATEGORY_UPLOAD_EXTENSIONS.get(category, ALLOWED_DOCUMENT_EXTENSIONS)


def is_image_file(path_or_name):

    return allowed_file(path_or_name, ALLOWED_IMAGE_EXTENSIONS)


def save_legacy_upload(file_storage, folder):

    if not file_storage or file_storage.filename == "":
        return "", ""

    if not allowed_file(file_storage.filename, allowed_extensions_for_category(folder)):
        raise ValueError("Unsupported file type")

    folder_path = safe_abs_path("uploads", folder)
    os.makedirs(folder_path, exist_ok=True)

    filename = str(uuid.uuid4()) + "_" + secure_filename(file_storage.filename)
    file_path = os.path.join(folder_path, filename)
    file_storage.save(file_path)

    return filename, file_path


def save_project_upload(file_storage, project_id, employee_id, category):

    if not file_storage or file_storage.filename == "":
        return "", ""

    if category not in UPLOAD_CATEGORIES:
        raise ValueError("Invalid upload category")

    if not allowed_file(file_storage.filename, allowed_extensions_for_category(category)):
        raise ValueError("Unsupported file type")

    safe_project = str(project_id or "general")
    safe_employee = str(employee_id or "general")
    folder_path = safe_abs_path(
        "static", "uploads", "projects", safe_project, safe_employee, category
    )
    os.makedirs(folder_path, exist_ok=True)

    filename = str(uuid.uuid4()) + "_" + secure_filename(file_storage.filename)
    file_path = os.path.join(folder_path, filename)
    file_storage.save(file_path)

    rel_path = os.path.relpath(file_path, BASE_DIR).replace("\\", "/")
    return rel_path, file_path


def copy_to_project_upload(source_path, project_id, employee_id, category):

    if not source_path or not os.path.exists(source_path):
        return ""

    if category not in UPLOAD_CATEGORIES:
        raise ValueError("Invalid upload category")

    if not allowed_file(os.path.basename(source_path), allowed_extensions_for_category(category)):
        raise ValueError("Unsupported file type")

    folder_path = safe_abs_path(
        "static",
        "uploads",
        "projects",
        str(project_id or "general"),
        str(employee_id or "general"),
        category,
    )
    os.makedirs(folder_path, exist_ok=True)

    filename = str(uuid.uuid4()) + "_" + secure_filename(os.path.basename(source_path))
    target_path = os.path.join(folder_path, filename)
    shutil.copy(source_path, target_path)

    return os.path.relpath(target_path, BASE_DIR).replace("\\", "/")


def clear_folder_contents(folder_path):

    folder_path = os.path.abspath(folder_path)

    if not folder_path.startswith(BASE_DIR + os.sep):
        raise ValueError("Unsafe folder")

    if not os.path.exists(folder_path):
        return

    for name in os.listdir(folder_path):
        item_path = os.path.join(folder_path, name)

        if os.path.isfile(item_path) or os.path.islink(item_path):
            os.remove(item_path)
        elif os.path.isdir(item_path):
            shutil.rmtree(item_path)


def clean_text(value):

    if value is None:
        return ""

    return str(value).strip()


EXCEL_FORMULA_TRIGGER_PREFIXES = ("=", "+", "-", "@")


def excel_safe_text(value):

    if value is None:
        return None

    if not isinstance(value, str):
        return value

    visible_value = value.lstrip(" \t\r\n")

    if visible_value.startswith(EXCEL_FORMULA_TRIGGER_PREFIXES):
        return "'" + value

    return value


def excel_safe_row(values, text_columns=None):

    if text_columns is None:
        return [excel_safe_text(value) if isinstance(value, str) else value for value in values]

    text_columns = set(text_columns)
    return [
        excel_safe_text(value) if index in text_columns else value
        for index, value in enumerate(values, start=1)
    ]


def append_excel_row(ws, values, text_columns=None):

    ws.append(excel_safe_row(values, text_columns))


def write_excel_row(ws, row, values, text_columns=None):

    safe_values = excel_safe_row(values, text_columns)

    for col, value in enumerate(safe_values, start=1):
        ws.cell(row=row, column=col).value = value


def write_excel_text_cell(ws, row, column, value):

    ws.cell(row=row, column=column).value = excel_safe_text(value)


def clean_date(value):

    value = clean_text(value)

    if value == "":
        return None

    return value


def parse_date_value(value):

    if not value:
        return None

    if isinstance(value, date):
        return value

    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def format_display_date(value):

    parsed_date = parse_date_value(value)

    if parsed_date:
        return parsed_date.strftime("%d/%m/%Y")

    return clean_text(value)


def validate_duid_value(du_id):

    du_id = clean_text(du_id)

    if not du_id:
        raise ValueError("DUID is required")

    if len(du_id) > 100:
        raise ValueError("DUID is too long")

    if any(char in du_id for char in ("\r", "\n", "\t")):
        raise ValueError("Invalid DUID")

    return du_id


def normalize_choice(value, allowed_values, default_value=""):

    value = clean_text(value)

    if not value:
        return default_value

    lookup = {str(item).upper(): item for item in allowed_values}
    return lookup.get(value.upper())


def validate_progress(value):

    value = clean_text(value)

    if value == "":
        return 0

    try:
        progress = int(value)
    except ValueError as exc:
        raise ValueError("Progress must be a number") from exc

    if progress < 0 or progress > 100:
        raise ValueError("Progress must be between 0 and 100")

    return progress


def validate_daily_progress(value, field_label):

    try:
        return validate_progress(value)
    except ValueError as exc:
        raise ValueError(f"{field_label} must be a number between 0 and 100.") from exc


def safe_return_path(value, fallback):

    value = clean_text(value)

    if value.startswith("/") and not value.startswith("//") and "\n" not in value:
        return value

    return fallback


def calculate_safety_status(expiry_date):

    expiry = parse_date_value(expiry_date)

    if not expiry:
        return "MISSING"

    today = date.today()

    if expiry < today:
        return "EXPIRED"

    if expiry <= today + timedelta(days=EXPIRING_SOON_DAYS):
        return "EXPIRING SOON"

    return "VALID"


def document_present(*values):

    return any(clean_text(value) for value in values)


def calculate_document_status(expiry_date, has_document=True):

    if not has_document:
        return "MISSING"

    return calculate_safety_status(expiry_date)


def build_safety_summary_from_statuses(nbi_status, wah_status, first_aid_status):

    statuses = {
        "nbi_status": nbi_status,
        "wah_status": wah_status,
        "first_aid_status": first_aid_status,
    }

    if any(status == "MISSING" for status in statuses.values()):
        statuses["overall_safety_status"] = "MISSING"
    elif any(status == "EXPIRED" for status in statuses.values()):
        statuses["overall_safety_status"] = "EXPIRED"
    elif any(status == "EXPIRING SOON" for status in statuses.values()):
        statuses["overall_safety_status"] = "EXPIRING SOON"
    else:
        statuses["overall_safety_status"] = "VALID"

    statuses["safety_badge"] = (
        "SAFETY CLEARED"
        if statuses["overall_safety_status"] in ("VALID", "EXPIRING SOON")
        else "SAFETY ACTION NEEDED"
    )

    return statuses


def safety_summary_from_document_records(documents):

    by_type = {document["document_type"]: document for document in documents}
    return build_safety_summary_from_statuses(
        by_type["NBI"]["current_status"],
        by_type["WAH"]["current_status"],
        by_type["FIRST_AID"]["current_status"],
    )


def calculate_access_validity(expiry_date, manual_status="", tracker_status=""):

    if parse_date_value(expiry_date):
        return calculate_safety_status(expiry_date)

    manual_status = normalize_choice(manual_status, ACCESS_STATUSES, "")

    if manual_status:
        return manual_status

    tracker_status = clean_text(tracker_status)

    if tracker_status:
        return tracker_status

    return "MISSING"


def safety_summary_from_employee(emp):

    nbi_has_file_info = any(key in emp for key in ("nbi", "nbi_file", "nbi_file_path"))
    wah_has_file_info = any(key in emp for key in ("wah_file", "wah_file_path"))
    first_aid_has_file_info = any(
        key in emp for key in ("first_aid_file", "first_aid_file_path")
    )

    nbi_present = (
        document_present(emp.get("nbi"), emp.get("nbi_file"), emp.get("nbi_file_path"))
        if nbi_has_file_info
        else True
    )
    wah_present = (
        document_present(emp.get("wah_file"), emp.get("wah_file_path"))
        if wah_has_file_info
        else True
    )
    first_aid_present = (
        document_present(emp.get("first_aid_file"), emp.get("first_aid_file_path"))
        if first_aid_has_file_info
        else True
    )

    statuses = {
        "nbi_status": calculate_document_status(
            emp.get("nbi_expiry_date"), nbi_present
        ),
        "wah_status": calculate_document_status(
            emp.get("wah_expiry_date"), wah_present
        ),
        "first_aid_status": calculate_document_status(
            emp.get("first_aid_expiry_date"), first_aid_present
        ),
    }

    return build_safety_summary_from_statuses(
        statuses["nbi_status"],
        statuses["wah_status"],
        statuses["first_aid_status"],
    )


def rows_to_dicts(cursor):

    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def row_to_dict(cursor):

    row = cursor.fetchone()

    if not row:
        return None

    columns = [desc[0] for desc in cursor.description]
    return dict(zip(columns, row))


def duid_exists(cursor, du_id):

    du_id = clean_text(du_id)

    if not du_id:
        return True

    cursor.execute(
        """
        SELECT 1
        FROM (
            SELECT du_id FROM globe_nlz WHERE du_id=%s
            UNION ALL
            SELECT du_id FROM planning_reference WHERE du_id=%s
            UNION ALL
            SELECT du_id FROM telecom_sites WHERE du_id=%s
        ) known_duids
        LIMIT 1
        """,
        (du_id, du_id, du_id),
    )
    return cursor.fetchone() is not None


import psycopg2


def connect_db():

    database_config = get_database_config()

    if database_config.get("dsn"):
        return psycopg2.connect(database_config["dsn"])

    return psycopg2.connect(**database_config)


ACCESS_INFO_HEADERS = [
    "NAME",
    "COMPANY",
    "DESIGNATION",
    "AREA ASSIGNED",
    "MOBILE NO.",
    "EMAIL",
    "ANDROID OR IPHONE",
    "FTAP IMEI",
    "FTAP EMAIL USED",
    "PHILTOWER IMEI",
    "PHILTOWER EMAIL USED",
    "TELECOM ROLE",
    "ASSIGNED DUID",
    "NBI EXPIRY",
    "WAH EXPIRY",
    "FIRST AID EXPIRY",
    "SAFETY STATUS",
]


def get_projects_for_select():

    conn = connect_db()
    cursor = conn.cursor()

    if is_team_leader_role():
        cursor.execute(
            """
            SELECT DISTINCT p.id, p.project_name, p.region, p.company, p.project_code, p.date_created
            FROM projects p
            JOIN teams t ON t.project_id = p.id
            WHERE t.active IS TRUE
              AND t.team_leader_admin_id=%s
            ORDER BY p.date_created DESC, p.id DESC
            """,
            (session.get("admin_id"),),
        )
    else:
        cursor.execute(
            """
            SELECT id, project_name, region, company, project_code
            FROM projects
            ORDER BY date_created DESC, id DESC
            """
        )

    projects = rows_to_dicts(cursor)
    cursor.close()
    conn.close()
    return projects


def get_duids_for_select():

    conn = connect_db()
    cursor = conn.cursor()

    if is_team_leader_role():
        duids = get_team_leader_duids(cursor)
    else:
        cursor.execute(
            """
            SELECT DISTINCT du_id
            FROM (
                SELECT du_id FROM globe_nlz
                UNION
                SELECT du_id FROM planning_reference
                UNION
                SELECT du_id FROM telecom_sites
            ) all_duids
            WHERE du_id IS NOT NULL
              AND TRIM(du_id) <> ''
            ORDER BY du_id
            """
        )
        duids = [row[0] for row in cursor.fetchall()]

    cursor.close()
    conn.close()
    return duids


def get_towercos_for_select():

    conn = connect_db()
    cursor = conn.cursor()

    if is_team_leader_role():
        duids = get_team_leader_duids(cursor)

        if duids:
            cursor.execute(
                """
                SELECT DISTINCT towerco
                FROM globe_nlz
                WHERE du_id = ANY(%s)
                  AND towerco IS NOT NULL
                  AND TRIM(towerco) <> ''
                ORDER BY towerco
                """,
                (duids,),
            )
        else:
            cursor.execute("SELECT NULL WHERE FALSE")
    else:
        cursor.execute(
            """
            SELECT DISTINCT towerco
            FROM globe_nlz
            WHERE towerco IS NOT NULL
              AND TRIM(towerco) <> ''
            ORDER BY towerco
            """
        )

    towercos = [row[0] for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return towercos


def get_employees_for_select():

    conn = connect_db()
    cursor = conn.cursor()
    params = []
    where_sql = ""

    if is_team_leader_role():
        employee_ids = get_team_leader_employee_ids(cursor)
        if employee_ids:
            where_sql = "WHERE employees.id = ANY(%s)"
            params.append(employee_ids)
        else:
            where_sql = "WHERE FALSE"

    cursor.execute(
        f"""
        SELECT id,
               first_name,
               middle_name,
               last_name,
               position,
               telecom_role,
               assigned_du_id
        FROM employees
        {where_sql}
        ORDER BY first_name, middle_name, last_name, id
        """,
        params,
    )
    employees = rows_to_dicts(cursor)
    cursor.close()
    conn.close()
    return employees


def full_employee_name(emp):

    if not emp:
        return ""

    return compose_person_name(
        emp.get("first_name"),
        emp.get("middle_name"),
        emp.get("last_name"),
    )


def get_team_leader_linked_employee_id(cursor, admin_id=None):

    admin_id = admin_id or session.get("admin_id")

    if not admin_id:
        return None

    cursor.execute(
        """
        SELECT employee_id
        FROM admins
        WHERE id=%s
          AND active IS TRUE
          AND role='team_leader'
        """,
        (admin_id,),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def stored_file_display_name(path_or_name):

    filename = os.path.basename(clean_text(path_or_name).replace("\\", "/"))

    if "_" in filename:
        possible_uuid, original = filename.split("_", 1)
        try:
            uuid.UUID(possible_uuid)
            return original
        except ValueError:
            pass

    return filename


def stored_file_path_variants(rel_path):

    cleaned = clean_text(rel_path).replace("\\", "/").lstrip("/")
    normalized = os.path.normpath(cleaned).replace("\\", "/")

    if normalized == ".":
        normalized = ""

    variants = [normalized]

    if normalized.startswith("static/uploads/"):
        variants.append(normalized[len("static/") :])

    if normalized.startswith("uploads/"):
        variants.append("static/" + normalized)

    filename = os.path.basename(normalized)

    if filename:
        variants.append(filename)

    return list(dict.fromkeys([variant for variant in variants if variant]))


def employee_id_for_uploaded_path(cursor, rel_path):

    variants = stored_file_path_variants(rel_path)

    if not variants:
        return None

    cursor.execute(
        """
        SELECT id
        FROM employees
        WHERE photo = ANY(%s)
           OR nbi = ANY(%s)
           OR certificate = ANY(%s)
           OR signature = ANY(%s)
           OR wah_file = ANY(%s)
           OR first_aid_file = ANY(%s)
        ORDER BY id
        LIMIT 1
        """,
        (variants, variants, variants, variants, variants, variants),
    )
    row = cursor.fetchone()

    if row:
        return row[0]

    like_patterns = ["%/" + variant for variant in variants if "/" not in variant]

    if like_patterns:
        cursor.execute(
            """
            SELECT id
            FROM employees
            WHERE wah_file LIKE ANY(%s)
               OR first_aid_file LIKE ANY(%s)
            ORDER BY id
            LIMIT 1
            """,
            (like_patterns, like_patterns),
        )
        row = cursor.fetchone()

        if row:
            return row[0]

    cursor.execute(
        """
        SELECT employee_id
        FROM safety_documents
        WHERE file_path = ANY(%s)
           OR original_filename = ANY(%s)
        ORDER BY is_current DESC, updated_at DESC, id DESC
        LIMIT 1
        """,
        (variants, variants),
    )
    row = cursor.fetchone()

    if row:
        return row[0]

    if like_patterns:
        cursor.execute(
            """
            SELECT employee_id
            FROM safety_documents
            WHERE file_path LIKE ANY(%s)
            ORDER BY is_current DESC, updated_at DESC, id DESC
            LIMIT 1
            """,
            (like_patterns,),
        )
        row = cursor.fetchone()

        if row:
            return row[0]

    normalized = variants[0]
    parts = normalized.split("/")

    if (
        len(parts) >= 5
        and parts[0] == "static"
        and parts[1] == "uploads"
        and parts[2] == "projects"
        and parts[4].isdigit()
    ):
        return int(parts[4])

    return None


def duid_for_uploaded_path(cursor, rel_path):

    variants = stored_file_path_variants(rel_path)

    if not variants:
        return None

    cursor.execute(
        """
        SELECT dsl.duid
        FROM daily_log_files dlf
        JOIN daily_site_logs dsl ON dsl.id = dlf.daily_log_id
        WHERE dlf.file_path = ANY(%s)
           OR dlf.original_filename = ANY(%s)
        ORDER BY dlf.id DESC
        LIMIT 1
        """,
        (variants, variants),
    )
    row = cursor.fetchone()

    if row:
        return row[0]

    cursor.execute(
        """
        SELECT pi.duid
        FROM punchlist_files pf
        JOIN punchlist_items pi ON pi.id = pf.punchlist_item_id
        WHERE pf.file_path = ANY(%s)
           OR pf.filename = ANY(%s)
        ORDER BY pf.id DESC
        LIMIT 1
        """,
        (variants, variants),
    )
    row = cursor.fetchone()

    if row:
        return row[0]

    cursor.execute(
        """
        SELECT duid
        FROM pat_records
        WHERE document_path = ANY(%s)
           OR document_filename = ANY(%s)
        ORDER BY id DESC
        LIMIT 1
        """,
        (variants, variants),
    )
    row = cursor.fetchone()

    if row:
        return row[0]

    cursor.execute(
        """
        SELECT ir.du_id
        FROM incident_attachments ia
        JOIN incident_reports ir ON ir.id = ia.incident_report_id
        WHERE ia.file_path = ANY(%s)
           OR ia.original_filename = ANY(%s)
        ORDER BY ia.id DESC
        LIMIT 1
        """,
        (variants, variants),
    )
    row = cursor.fetchone()

    if row:
        return row[0]

    cursor.execute(
        """
        SELECT du_id
        FROM permit_to_work
        WHERE file_path = ANY(%s)
        ORDER BY id DESC
        LIMIT 1
        """,
        (variants,),
    )
    row = cursor.fetchone()

    if row:
        return row[0]

    normalized = variants[0]
    parts = normalized.split("/")

    if (
        len(parts) >= 6
        and parts[0] == "static"
        and parts[1] == "uploads"
        and parts[2] == "projects"
        and parts[4] == "sites"
    ):
        return parts[5]

    return None


def site_file_context_for_uploaded_path(cursor, rel_path):

    variants = stored_file_path_variants(rel_path)

    if not variants:
        return None

    cursor.execute(
        """
        SELECT 'daily_operations' AS module, dsl.duid
        FROM daily_log_files dlf
        JOIN daily_site_logs dsl ON dsl.id = dlf.daily_log_id
        WHERE dlf.file_path = ANY(%s)
           OR dlf.original_filename = ANY(%s)
        ORDER BY dlf.id DESC
        LIMIT 1
        """,
        (variants, variants),
    )
    row = cursor.fetchone()

    if row:
        return {"module": row[0], "duid": row[1]}

    cursor.execute(
        """
        SELECT 'punchlist' AS module, pi.duid
        FROM punchlist_files pf
        JOIN punchlist_items pi ON pi.id = pf.punchlist_item_id
        WHERE pf.file_path = ANY(%s)
           OR pf.filename = ANY(%s)
        ORDER BY pf.id DESC
        LIMIT 1
        """,
        (variants, variants),
    )
    row = cursor.fetchone()

    if row:
        return {"module": row[0], "duid": row[1]}

    cursor.execute(
        """
        SELECT 'pat' AS module, duid
        FROM pat_records
        WHERE document_path = ANY(%s)
           OR document_filename = ANY(%s)
        ORDER BY id DESC
        LIMIT 1
        """,
        (variants, variants),
    )
    row = cursor.fetchone()

    if row:
        return {"module": row[0], "duid": row[1]}

    cursor.execute(
        """
        SELECT 'incident' AS module, ir.du_id
        FROM incident_attachments ia
        JOIN incident_reports ir ON ir.id = ia.incident_report_id
        WHERE ia.file_path = ANY(%s)
           OR ia.original_filename = ANY(%s)
        ORDER BY ia.id DESC
        LIMIT 1
        """,
        (variants, variants),
    )
    row = cursor.fetchone()

    if row:
        return {"module": row[0], "duid": row[1]}

    cursor.execute(
        """
        SELECT 'permit' AS module, du_id
        FROM permit_to_work
        WHERE file_path = ANY(%s)
        ORDER BY id DESC
        LIMIT 1
        """,
        (variants,),
    )
    row = cursor.fetchone()

    if row:
        return {"module": row[0], "duid": row[1]}

    normalized = variants[0]
    parts = normalized.split("/")

    if (
        len(parts) >= 7
        and parts[0] == "static"
        and parts[1] == "uploads"
        and parts[2] == "projects"
        and parts[4] == "sites"
    ):
        module_map = {
            "daily_logs": "daily_operations",
            "punchlist": "punchlist",
            "pat": "pat",
            "incidents": "incident",
            "permits": "permit",
        }
        return {"module": module_map.get(parts[6], "site"), "duid": parts[5]}

    return None


def authorize_stored_file_access(rel_path):

    rel_path = clean_text(rel_path).replace("\\", "/").lstrip("/")
    rel_path = os.path.normpath(rel_path).replace("\\", "/")

    if not rel_path or rel_path == "." or rel_path.startswith("../") or "/../" in rel_path:
        return "Invalid file path"

    if is_super_admin_role():
        return None

    conn = connect_db()
    cursor = conn.cursor()

    try:
        employee_id = employee_id_for_uploaded_path(cursor, rel_path)
        site_file_context = site_file_context_for_uploaded_path(cursor, rel_path)

        if is_hr_role():
            if employee_id and not site_file_context:
                return None

            audit_scope_denied(
                conn,
                "file",
                rel_path,
                "Denied HR access to a non-personnel upload.",
            )
            return access_denied("This file is outside your HR personnel and safety access.")

        if not is_team_leader_role():
            return access_denied("This file is outside your authorized access.")

        if employee_id:
            denied = enforce_team_leader_employee_scope(cursor, conn, employee_id)
            return denied

        if site_file_context:
            module = site_file_context.get("module")
            module_permissions = {
                "daily_operations": ("manage_operations",),
                "punchlist": ("manage_punchlist",),
                "pat": ("view_pat", "manage_pat"),
                "incident": ("manage_incidents",),
                "permit": ("manage_permits",),
                "site": ("team_leader_portal",),
            }
            required_permissions = module_permissions.get(module, ("team_leader_portal",))

            if not any(can(permission) for permission in required_permissions):
                audit_scope_denied(
                    conn,
                    "file",
                    rel_path,
                    f"Denied Team Leader access to {module} file without module permission.",
                )
                return access_denied("This file is outside your authorized module access.")

            denied = enforce_team_leader_site_scope(cursor, conn, site_file_context.get("duid"))
            return denied

        duid = duid_for_uploaded_path(cursor, rel_path)

        if duid:
            denied = enforce_team_leader_site_scope(cursor, conn, duid)
            return denied

        audit_scope_denied(
            conn,
            "file",
            rel_path,
            "Denied Team Leader access to an upload outside current team scope.",
        )
        return access_denied("This file is outside your current team scope.")

    finally:
        cursor.close()
        conn.close()


def send_stored_file(rel_path):

    rel_path = clean_text(rel_path).replace("\\", "/").lstrip("/")
    rel_path = os.path.normpath(rel_path).replace("\\", "/")

    if not rel_path:
        return "File not found"

    if rel_path == "." or rel_path.startswith("../") or "/../" in rel_path:
        return "Invalid file path"

    denied = authorize_stored_file_access(rel_path)

    if denied:
        return denied

    if rel_path.startswith("static/uploads/"):
        folder, filename = os.path.split(rel_path)
        return send_from_directory(safe_abs_path(folder), filename)

    if rel_path.startswith("uploads/"):
        parts = rel_path.split("/", 2)

        if len(parts) != 3:
            return "Invalid file path"

        _, folder, filename = parts

        if folder not in LEGACY_UPLOAD_CATEGORIES:
            return "Invalid upload folder"

        return send_from_directory(safe_abs_path("uploads", folder), filename)

    return "Invalid file path"


def employee_file_rel_path(emp, file_kind):

    if file_kind == "photo" and emp.get("photo"):
        return "uploads/photos/" + emp["photo"]

    if file_kind == "nbi" and emp.get("nbi"):
        return "uploads/nbi/" + emp["nbi"]

    if file_kind == "certificate" and emp.get("certificate"):
        return "uploads/certificates/" + emp["certificate"]

    if file_kind == "signature" and emp.get("signature"):
        return "uploads/signatures/" + emp["signature"]

    if file_kind == "wah" and emp.get("wah_file"):
        return emp["wah_file"]

    if file_kind == "first_aid" and emp.get("first_aid_file"):
        return emp["first_aid_file"]

    return ""


def get_employee_detail(cursor, employee_id):

    cursor.execute(
        """
        SELECT e.id,
               e.project_id,
               e.first_name,
               e.middle_name,
               e.last_name,
               e.position,
               e.email,
               e.mobile,
               e.phone_type,
               e.ftap_imei,
               e.ftap_email,
               e.philtower_imei,
               e.philtower_email,
               e.photo,
               e.nbi,
               e.certificate,
               e.signature,
               e.telecom_role,
               e.assigned_du_id,
               e.nbi_reference,
               e.nbi_issue_date,
               e.nbi_expiry_date,
               e.wah_reference,
               e.wah_issue_date,
               e.wah_expiry_date,
               e.wah_file,
               e.first_aid_reference,
               e.first_aid_issue_date,
               e.first_aid_expiry_date,
               e.first_aid_file,
               e.dossier_folder_path,
               e.updated_at,
               p.project_code,
               p.project_name,
               p.region,
               p.company
        FROM employees e
        LEFT JOIN projects p ON e.project_id = p.id
        WHERE e.id=%s
        """,
        (employee_id,),
    )
    emp = row_to_dict(cursor)

    if emp:
        emp.update(safety_summary_from_employee(emp))
        emp["full_name"] = full_employee_name(emp)

    return emp


def get_team_leader_team_ids(cursor, admin_id=None):

    admin_id = admin_id or session.get("admin_id")

    if not admin_id:
        return []

    cursor.execute(
        """
        SELECT id
        FROM teams
        WHERE active IS TRUE
          AND team_leader_admin_id=%s
        ORDER BY id
        """,
        (admin_id,),
    )
    return [row[0] for row in cursor.fetchall()]


def get_team_leader_duids(cursor, admin_id=None):

    admin_id = admin_id or session.get("admin_id")

    if not admin_id:
        return []

    cursor.execute(
        """
        SELECT DISTINCT du_id
        FROM teams
        WHERE active IS TRUE
          AND team_leader_admin_id=%s
          AND du_id IS NOT NULL
          AND TRIM(du_id) <> ''
        ORDER BY du_id
        """,
        (admin_id,),
    )
    return [row[0] for row in cursor.fetchall()]


def get_team_leader_employee_ids(cursor, admin_id=None):

    team_ids = get_team_leader_team_ids(cursor, admin_id=admin_id)
    linked_employee_id = get_team_leader_linked_employee_id(cursor, admin_id=admin_id)

    if not team_ids:
        return [linked_employee_id] if linked_employee_id else []

    cursor.execute(
        """
        SELECT DISTINCT employee_id
        FROM team_memberships
        WHERE active IS TRUE
          AND team_id = ANY(%s)
        ORDER BY employee_id
        """,
        (team_ids,),
    )
    employee_ids = [row[0] for row in cursor.fetchall()]

    if linked_employee_id and linked_employee_id not in employee_ids:
        employee_ids.append(linked_employee_id)

    return employee_ids


def team_leader_has_site(cursor, du_id, admin_id=None):

    if not du_id:
        return False

    cursor.execute(
        """
        SELECT 1
        FROM teams
        WHERE active IS TRUE
          AND team_leader_admin_id=%s
          AND du_id=%s
        LIMIT 1
        """,
        (admin_id or session.get("admin_id"), du_id),
    )
    return cursor.fetchone() is not None


def team_leader_has_employee(cursor, employee_id, admin_id=None):

    linked_employee_id = get_team_leader_linked_employee_id(cursor, admin_id=admin_id)

    if linked_employee_id and str(linked_employee_id) == str(employee_id):
        return True

    cursor.execute(
        """
        SELECT 1
        FROM team_memberships tm
        JOIN teams t ON t.id = tm.team_id
        WHERE tm.active IS TRUE
          AND t.active IS TRUE
          AND t.team_leader_admin_id=%s
          AND tm.employee_id=%s
        LIMIT 1
        """,
        (admin_id or session.get("admin_id"), employee_id),
    )
    return cursor.fetchone() is not None


def team_leader_has_employee_at_site(cursor, employee_id, duid, admin_id=None):

    cursor.execute(
        """
        SELECT 1
        FROM team_memberships tm
        JOIN teams t ON t.id = tm.team_id
        WHERE tm.active IS TRUE
          AND t.active IS TRUE
          AND t.team_leader_admin_id=%s
          AND tm.employee_id=%s
          AND t.du_id=%s
        LIMIT 1
        """,
        (admin_id or session.get("admin_id"), employee_id, duid),
    )
    return cursor.fetchone() is not None


def audit_scope_denied(conn, entity_type, entity_id, description):

    audit_event(
        "TEAM_SCOPE_ACCESS_DENIED",
        entity_type,
        entity_id,
        description,
        conn=conn,
    )
    conn.commit()


def enforce_team_leader_site_scope(cursor, conn, du_id):

    if is_team_leader_role() and not team_leader_has_site(cursor, du_id):
        audit_scope_denied(
            conn,
            "site",
            du_id,
            f"Denied Team Leader access to unassigned site {du_id}.",
        )
        return access_denied("This site is outside your assigned team scope.")

    return None


def enforce_team_leader_employee_scope(cursor, conn, employee_id):

    if is_team_leader_role() and not team_leader_has_employee(cursor, employee_id):
        audit_scope_denied(
            conn,
            "employee",
            employee_id,
            f"Denied Team Leader access to employee {employee_id} outside current team.",
        )
        return access_denied("This employee is outside your current team scope.")

    return None


def add_team_leader_duid_scope(cursor, conditions, params, column_name):

    if not is_team_leader_role():
        return

    duids = get_team_leader_duids(cursor)

    if duids:
        conditions.append(f"{column_name} = ANY(%s)")
        params.append(duids)
    else:
        conditions.append("FALSE")


def add_team_leader_employee_scope(cursor, conditions, params, column_name):

    if not is_team_leader_role():
        return

    employee_ids = get_team_leader_employee_ids(cursor)

    if employee_ids:
        conditions.append(f"{column_name} = ANY(%s)")
        params.append(employee_ids)
    else:
        conditions.append("FALSE")


def build_document_record(document_type, employee, document=None):

    mapping = {
        "NBI": {
            "label": "NBI Clearance",
            "reference": "nbi_reference",
            "issue": "nbi_issue_date",
            "expiry": "nbi_expiry_date",
            "file_kind": "nbi",
            "file_field": "nbi",
        },
        "WAH": {
            "label": "Work At Heights",
            "reference": "wah_reference",
            "issue": "wah_issue_date",
            "expiry": "wah_expiry_date",
            "file_kind": "wah",
            "file_field": "wah_file",
        },
        "FIRST_AID": {
            "label": "First Aid",
            "reference": "first_aid_reference",
            "issue": "first_aid_issue_date",
            "expiry": "first_aid_expiry_date",
            "file_kind": "first_aid",
            "file_field": "first_aid_file",
        },
    }
    info = mapping[document_type]

    if document:
        file_path = document.get("file_path") or ""
        status = calculate_document_status(document.get("expiry_date"), bool(file_path))
        return {
            "id": document.get("id"),
            "document_type": document_type,
            "label": info["label"],
            "reference_number": document.get("reference_number") or "",
            "issue_date": document.get("issue_date"),
            "expiry_date": document.get("expiry_date"),
            "file_path": file_path,
            "file_kind": info["file_kind"],
            "filename": document.get("original_filename")
            or stored_file_display_name(file_path),
            "current_status": status,
            "is_current": document.get("is_current"),
            "created_at": document.get("created_at"),
            "updated_at": document.get("updated_at"),
        }

    file_path = employee_file_rel_path(employee, info["file_kind"])
    return {
        "id": None,
        "document_type": document_type,
        "label": info["label"],
        "reference_number": employee.get(info["reference"]) or "",
        "issue_date": employee.get(info["issue"]),
        "expiry_date": employee.get(info["expiry"]),
        "file_path": file_path,
        "file_kind": info["file_kind"],
        "filename": stored_file_display_name(file_path),
        "current_status": calculate_document_status(
            employee.get(info["expiry"]), bool(file_path)
        ),
        "is_current": None,
        "created_at": None,
        "updated_at": None,
    }


def get_employee_safety_documents(cursor, employee):

    cursor.execute(
        """
        SELECT id,
               document_type,
               reference_number,
               issue_date,
               expiry_date,
               file_path,
               is_current,
               original_filename,
               created_at,
               updated_at
        FROM safety_documents
        WHERE employee_id=%s
          AND document_type = ANY(%s)
        ORDER BY is_current DESC, expiry_date DESC NULLS LAST, created_at DESC, id DESC
        """,
        (employee["id"], DOCUMENT_TYPES),
    )
    documents = rows_to_dicts(cursor)
    current_by_type = {}
    history = []

    for document in documents:
        document_type = document["document_type"]
        built = build_document_record(document_type, employee, document)

        if document.get("is_current") and document_type not in current_by_type:
            current_by_type[document_type] = built
        else:
            history.append(built)

    current_documents = []
    for document_type in DOCUMENT_TYPES:
        current_documents.append(
            current_by_type.get(document_type)
            or build_document_record(document_type, employee)
        )

    return current_documents, history


SITE_REFERENCE_CTE = """
WITH all_duids AS (
    SELECT DISTINCT du_id
    FROM globe_nlz
    WHERE du_id IS NOT NULL
      AND TRIM(du_id) <> ''
    UNION
    SELECT DISTINCT du_id
    FROM planning_reference
    WHERE du_id IS NOT NULL
      AND TRIM(du_id) <> ''
    UNION
    SELECT DISTINCT du_id
    FROM telecom_sites
    WHERE du_id IS NOT NULL
      AND TRIM(du_id) <> ''
),
globe_sites AS (
    SELECT du_id,
           MAX(customer_site_id) AS customer_site_id,
           MAX(du_name) AS globe_du_name,
           MAX(sitename) AS sitename,
           MAX(site_name) AS site_name,
           MAX(towerco) AS towerco,
           MAX(province) AS province,
           MAX(site_address) AS site_address,
           MAX(town) AS town,
           MAX(team_leader) AS tracker_team_leader,
           MAX(lat) AS lat,
           MAX(long) AS long,
           MAX(raawa_status) AS raawa_status,
           MAX(towerco_access_status) AS towerco_access_status,
           MAX(confirmation_status) AS confirmation_status,
           MAX(po) AS po,
           MAX(mos_date) AS mos_date,
           MAX(installation_date) AS installation_date,
           MAX(date_finished) AS date_finished,
           MAX(rpa_tat_date) AS rpa_tat_date,
           MAX(remarks) AS tracker_remarks
    FROM globe_nlz
    WHERE du_id IS NOT NULL
      AND TRIM(du_id) <> ''
    GROUP BY du_id
),
planning_sites AS (
    SELECT du_id,
           MAX(territory) AS territory,
           MAX(plaid) AS plaid,
           MAX(du_name) AS planning_du_name,
           MAX(mdb_province) AS mdb_province,
           MAX(onair_plan_v2) AS onair_plan_v2,
           MAX(municipality) AS municipality
    FROM planning_reference
    WHERE du_id IS NOT NULL
      AND TRIM(du_id) <> ''
    GROUP BY du_id
)
"""


SITE_SELECT_COLUMNS = """
SELECT d.du_id,
       ts.id AS operational_site_id,
       ts.project_id,
       p.project_name,
       p.project_code,
       ts.vendor,
       COALESCE(ts.current_stage, 'Planning') AS current_stage,
       COALESCE(ts.overall_progress, 0) AS overall_progress,
       COALESCE(ts.overall_status, 'Not Started') AS overall_status,
       ts.access_valid_from,
       ts.access_valid_until,
       ts.access_status,
       COALESCE(ts.pat_status, 'MISSING') AS pat_status,
       ts.operational_team_leader,
       ts.remarks AS operational_remarks,
       ts.created_at,
       ts.updated_at,
       g.customer_site_id,
       COALESCE(g.globe_du_name, pr.planning_du_name) AS du_name,
       g.sitename,
       g.site_name,
       COALESCE(g.site_name, g.sitename, g.globe_du_name, pr.planning_du_name) AS display_site_name,
       g.towerco,
       COALESCE(g.province, pr.mdb_province, pr.territory) AS region_province,
       COALESCE(g.town, pr.municipality) AS municipality,
       g.site_address,
       g.tracker_team_leader,
       g.lat,
       g.long,
       g.raawa_status,
       g.towerco_access_status,
       g.confirmation_status,
       g.po,
       g.mos_date,
       g.installation_date,
       g.date_finished,
       g.rpa_tat_date,
       g.tracker_remarks,
       pr.territory,
       pr.plaid,
       pr.mdb_province,
       pr.onair_plan_v2
"""


SITE_FROM_JOINS = """
FROM all_duids d
LEFT JOIN globe_sites g ON g.du_id = d.du_id
LEFT JOIN planning_sites pr ON pr.du_id = d.du_id
LEFT JOIN telecom_sites ts ON ts.du_id = d.du_id
LEFT JOIN projects p ON ts.project_id = p.id
"""


def decorate_site_row(site):

    if not site:
        return site

    site["current_stage"] = site.get("current_stage") or "Planning"
    site["overall_progress"] = site.get("overall_progress") or 0
    site["overall_status"] = site.get("overall_status") or "Not Started"
    site["pat_status"] = site.get("pat_status") or "MISSING"
    site["team_leader"] = (
        site.get("operational_team_leader")
        or site.get("tracker_team_leader")
        or ""
    )
    site["remarks"] = site.get("operational_remarks") or site.get("tracker_remarks") or ""
    site["access_validity"] = calculate_access_validity(
        site.get("access_valid_until"),
        site.get("access_status"),
        site.get("towerco_access_status"),
    )
    site["has_operational_record"] = site.get("operational_site_id") is not None
    return site


def get_site_by_duid(cursor, du_id):

    cursor.execute(
        f"""
        {SITE_REFERENCE_CTE}
        {SITE_SELECT_COLUMNS}
        {SITE_FROM_JOINS}
        WHERE d.du_id=%s
        """,
        (du_id,),
    )
    return decorate_site_row(row_to_dict(cursor))


def validate_date_field(value, field_name):

    value = clean_date(value)

    if value and not parse_date_value(value):
        raise ValueError(f"{field_name} must use YYYY-MM-DD format")

    return value


def validate_time_field(value, field_name):

    value = clean_text(value)

    if not value:
        return None

    for time_format in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value, time_format).time()
        except ValueError:
            pass

    raise ValueError(f"{field_name} must use HH:MM format")


def save_daily_log_upload(file_storage, project_key, duid, report_date, file_type):

    if not file_storage or file_storage.filename == "":
        return "", "", ""

    file_type = normalize_choice(file_type, DAILY_FILE_TYPES, "PHOTO")

    if not file_type:
        raise ValueError("Invalid daily log file type")

    allowed_extensions = (
        ALLOWED_IMAGE_EXTENSIONS
        if file_type == "PHOTO"
        else ALLOWED_DOCUMENT_EXTENSIONS
    )

    if not allowed_file(file_storage.filename, allowed_extensions):
        raise ValueError("Unsupported daily evidence file type")

    date_value = parse_date_value(report_date)

    if not date_value:
        raise ValueError("Report date is required before saving evidence")

    safe_project = secure_filename(str(project_key or "general")) or "general"
    safe_duid = secure_filename(validate_duid_value(duid)) or "site"
    folder_name = "photos" if file_type == "PHOTO" else "documents"
    folder_path = safe_abs_path(
        "static",
        "uploads",
        "projects",
        safe_project,
        "sites",
        safe_duid,
        "daily_logs",
        date_value.isoformat(),
        folder_name,
    )
    os.makedirs(folder_path, exist_ok=True)

    original_filename = secure_filename(file_storage.filename)
    filename = str(uuid.uuid4()) + "_" + original_filename
    file_path = os.path.join(folder_path, filename)
    file_storage.save(file_path)

    return os.path.relpath(file_path, BASE_DIR).replace("\\", "/"), file_path, original_filename


def get_project_code_for_daily_path(cursor, project_id):

    if not project_id:
        return "general"

    cursor.execute(
        "SELECT project_code FROM projects WHERE id=%s",
        (project_id,),
    )
    project = row_to_dict(cursor)
    return project.get("project_code") if project and project.get("project_code") else project_id


def collect_daily_log_form_data(cursor, duid, existing_log=None):

    duid = validate_duid_value(duid)
    project_id = clean_text(request.form.get("project_id"))
    current_stage = normalize_choice(
        request.form.get("current_stage"), SITE_STAGES, ""
    )
    report_date = validate_date_field(request.form.get("report_date"), "Report date")
    progress_before = validate_daily_progress(
        request.form.get("progress_before"), "Starting Progress (%)"
    )
    progress_after = validate_daily_progress(
        request.form.get("progress_after"), "Ending Progress (%)"
    )
    blocker_category = normalize_choice(
        request.form.get("blocker_category"), DAILY_BLOCKER_CATEGORIES, ""
    )
    blockers = clean_text(request.form.get("blockers"))
    general_notes = clean_text(request.form.get("general_notes"))

    if not report_date:
        raise ValueError("Report date is required")

    if clean_text(request.form.get("current_stage")) and not current_stage:
        raise ValueError("Invalid site stage")

    if not current_stage:
        current_stage = (existing_log or {}).get("current_stage") or "Planning"

    if clean_text(request.form.get("blocker_category")) and blocker_category is None:
        raise ValueError("Invalid blocker category")

    if progress_after < progress_before and not (blockers or general_notes):
        raise ValueError(
            "Ending Progress (%) cannot be lower than Starting Progress (%) unless you describe the blocker or note why progress moved backward."
        )

    if project_id:
        cursor.execute("SELECT 1 FROM projects WHERE id=%s", (project_id,))

        if not cursor.fetchone():
            raise ValueError("Invalid project")

    if not duid_exists(cursor, duid):
        raise ValueError("Invalid DUID")

    return {
        "project_id": project_id or None,
        "duid": duid,
        "report_date": report_date,
        "current_stage": current_stage,
        "progress_before": progress_before,
        "progress_after": progress_after,
        "work_completed": clean_text(request.form.get("work_completed")),
        "blocker_category": blocker_category or None,
        "blockers": blockers,
        "next_day_plan": clean_text(request.form.get("next_day_plan")),
        "general_notes": general_notes,
        "weather_notes": clean_text(request.form.get("weather_notes")),
        "submitted_by": session.get("admin", ""),
    }


def apply_daily_site_progress(cursor, log_data):

    overall_status = (
        "Completed"
        if log_data["progress_after"] == 100 or log_data["current_stage"] == "Completed"
        else "Active"
    )

    cursor.execute(
        """
        INSERT INTO telecom_sites(
            du_id,
            project_id,
            current_stage,
            overall_progress,
            overall_status,
            updated_at
        )
        VALUES(%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
        ON CONFLICT(du_id)
        DO UPDATE SET
            project_id=COALESCE(EXCLUDED.project_id, telecom_sites.project_id),
            current_stage=EXCLUDED.current_stage,
            overall_progress=EXCLUDED.overall_progress,
            overall_status=CASE
                WHEN EXCLUDED.overall_status='Completed' THEN 'Completed'
                WHEN telecom_sites.overall_status IS NULL THEN 'Active'
                WHEN telecom_sites.overall_status='' THEN 'Active'
                WHEN telecom_sites.overall_status='Not Started' THEN 'Active'
                ELSE telecom_sites.overall_status
            END,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            log_data["duid"],
            log_data["project_id"],
            log_data["current_stage"],
            log_data["progress_after"],
            overall_status,
        ),
    )


def get_daily_attendance_people(cursor, duid, daily_log_id=None):

    if is_team_leader_role():
        if daily_log_id:
            cursor.execute(
                """
                SELECT DISTINCT ON (e.id)
                       e.id,
                       e.first_name,
                                              e.middle_name,
                       e.last_name,
                       e.position,
                       e.telecom_role,
                       e.nbi,
                       e.wah_file,
                       e.first_aid_file,
                       e.nbi_expiry_date,
                       e.wah_expiry_date,
                       e.first_aid_expiry_date,
                       COALESCE(tm_role.role, e.telecom_role, e.position) AS assignment_role,
                       da.attendance_status,
                       da.time_in,
                       da.time_out,
                       da.role_at_site,
                       da.safety_status_snapshot,
                       da.remarks
                FROM team_memberships tm
                JOIN teams t ON t.id = tm.team_id
                JOIN employees e ON e.id = tm.employee_id
                LEFT JOIN site_assignments tm_role
                  ON tm_role.employee_id = e.id
                 AND tm_role.du_id = t.du_id
                 AND tm_role.assignment_status='ACTIVE'
                LEFT JOIN daily_attendance da
                  ON da.employee_id = e.id
                 AND da.daily_log_id = %s
                WHERE tm.active IS TRUE
                  AND t.active IS TRUE
                  AND t.team_leader_admin_id=%s
                  AND t.du_id=%s
                ORDER BY e.id, da.id NULLS LAST, tm.id DESC
                """,
                (daily_log_id, session.get("admin_id"), duid),
            )
        else:
            cursor.execute(
                """
                SELECT DISTINCT ON (e.id)
                       e.id,
                       e.first_name,
                                              e.middle_name,
                       e.last_name,
                       e.position,
                       e.telecom_role,
                       e.nbi,
                       e.wah_file,
                       e.first_aid_file,
                       e.nbi_expiry_date,
                       e.wah_expiry_date,
                       e.first_aid_expiry_date,
                       COALESCE(sa.role, e.telecom_role, e.position) AS assignment_role,
                       'Present' AS attendance_status,
                       NULL::time AS time_in,
                       NULL::time AS time_out,
                       COALESCE(sa.role, e.telecom_role, e.position) AS role_at_site,
                       NULL AS safety_status_snapshot,
                       NULL AS remarks
                FROM team_memberships tm
                JOIN teams t ON t.id = tm.team_id
                JOIN employees e ON e.id = tm.employee_id
                LEFT JOIN site_assignments sa
                  ON sa.employee_id = e.id
                 AND sa.du_id = t.du_id
                 AND sa.assignment_status='ACTIVE'
                WHERE tm.active IS TRUE
                  AND t.active IS TRUE
                  AND t.team_leader_admin_id=%s
                  AND t.du_id=%s
                ORDER BY e.id, tm.id DESC
                """,
                (session.get("admin_id"), duid),
            )
    elif daily_log_id:
        cursor.execute(
            """
            SELECT DISTINCT ON (e.id)
                   e.id,
                   e.first_name,
                                      e.middle_name,
                   e.last_name,
                   e.position,
                   e.telecom_role,
                   e.nbi,
                   e.wah_file,
                   e.first_aid_file,
                   e.nbi_expiry_date,
                   e.wah_expiry_date,
                   e.first_aid_expiry_date,
                   sa.role AS assignment_role,
                   da.attendance_status,
                   da.time_in,
                   da.time_out,
                   da.role_at_site,
                   da.safety_status_snapshot,
                   da.remarks
            FROM employees e
            LEFT JOIN site_assignments sa
              ON sa.employee_id = e.id
             AND sa.du_id = %s
             AND sa.assignment_status='ACTIVE'
            LEFT JOIN daily_attendance da
              ON da.employee_id = e.id
             AND da.daily_log_id = %s
            WHERE sa.id IS NOT NULL
               OR da.id IS NOT NULL
            ORDER BY e.id, da.id NULLS LAST, sa.id DESC
            """,
            (duid, daily_log_id),
        )
    else:
        cursor.execute(
            """
            SELECT e.id,
                   e.first_name,
                                      e.middle_name,
                   e.last_name,
                   e.position,
                   e.telecom_role,
                   e.nbi,
                   e.wah_file,
                   e.first_aid_file,
                   e.nbi_expiry_date,
                   e.wah_expiry_date,
                   e.first_aid_expiry_date,
                   sa.role AS assignment_role,
                   'Present' AS attendance_status,
                   NULL::time AS time_in,
                   NULL::time AS time_out,
                   COALESCE(sa.role, e.telecom_role, e.position) AS role_at_site,
                   NULL AS safety_status_snapshot,
                   NULL AS remarks
            FROM site_assignments sa
            JOIN employees e ON sa.employee_id = e.id
            WHERE sa.du_id=%s
              AND sa.assignment_status='ACTIVE'
            ORDER BY e.first_name, e.last_name, e.id
            """,
            (duid,),
        )

    people = rows_to_dicts(cursor)

    for person in people:
        person["full_name"] = full_employee_name(person)
        person.update(safety_summary_from_employee(person))
        person["role_at_site"] = (
            person.get("role_at_site")
            or person.get("assignment_role")
            or person.get("telecom_role")
            or person.get("position")
            or ""
        )
        person["attendance_status"] = (
            person.get("attendance_status") or "Present"
        )

    return people


def sync_daily_attendance(cursor, daily_log_id, duid):

    employee_ids = request.form.getlist("attendance_employee_id")
    cleaned_employee_ids = [
        clean_text(raw_employee_id)
        for raw_employee_id in employee_ids
        if clean_text(raw_employee_id)
    ]

    if is_team_leader_role():
        unauthorized_employee_ids = [
            employee_id
            for employee_id in sorted(set(cleaned_employee_ids))
            if not team_leader_has_employee_at_site(cursor, employee_id, duid)
        ]

        if unauthorized_employee_ids:
            audit_event(
                "TEAM_SCOPE_ACCESS_DENIED",
                "attendance",
                duid,
                "Denied Team Leader attendance submission for employee(s) outside current team scope: "
                + ", ".join(unauthorized_employee_ids),
            )
            raise ValueError("Attendance includes an employee outside your current team scope.")

    for employee_id in cleaned_employee_ids:

        cursor.execute(
            """
            SELECT id,
                   first_name,
                                      middle_name,
                   last_name,
                   position,
                   telecom_role,
                   nbi,
                   wah_file,
                   first_aid_file,
                   nbi_expiry_date,
                   wah_expiry_date,
                   first_aid_expiry_date
            FROM employees
            WHERE id=%s
            """,
            (employee_id,),
        )
        employee = row_to_dict(cursor)

        if not employee:
            raise ValueError("Invalid attendance employee")

        attendance_status = normalize_choice(
            request.form.get(f"attendance_status_{employee_id}"),
            DAILY_ATTENDANCE_STATUSES,
            "Present",
        )

        if not attendance_status:
            raise ValueError("Invalid attendance status")

        time_in = validate_time_field(
            request.form.get(f"time_in_{employee_id}"),
            f"Time in for {full_employee_name(employee)}",
        )
        time_out = validate_time_field(
            request.form.get(f"time_out_{employee_id}"),
            f"Time out for {full_employee_name(employee)}",
        )

        if time_in and time_out and time_out < time_in:
            raise ValueError("Time out cannot be before time in")

        cursor.execute(
            """
            SELECT role
            FROM site_assignments
            WHERE employee_id=%s
              AND du_id=%s
              AND assignment_status='ACTIVE'
            ORDER BY id DESC
            LIMIT 1
            """,
            (employee_id, duid),
        )
        assignment = row_to_dict(cursor) or {}
        summary = safety_summary_from_employee(employee)
        role_at_site = clean_text(request.form.get(f"role_at_site_{employee_id}")) or (
            assignment.get("role")
            or employee.get("telecom_role")
            or employee.get("position")
            or ""
        )
        remarks = clean_text(request.form.get(f"attendance_remarks_{employee_id}"))

        cursor.execute(
            """
            INSERT INTO daily_attendance(
                daily_log_id,
                employee_id,
                attendance_status,
                time_in,
                time_out,
                role_at_site,
                safety_status_snapshot,
                remarks,
                updated_at
            )
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
            ON CONFLICT(daily_log_id, employee_id)
            DO UPDATE SET
                attendance_status=EXCLUDED.attendance_status,
                time_in=EXCLUDED.time_in,
                time_out=EXCLUDED.time_out,
                role_at_site=EXCLUDED.role_at_site,
                safety_status_snapshot=EXCLUDED.safety_status_snapshot,
                remarks=EXCLUDED.remarks,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                daily_log_id,
                employee_id,
                attendance_status,
                time_in,
                time_out,
                role_at_site,
                summary["overall_safety_status"],
                remarks,
            ),
        )


def save_daily_log_files(cursor, daily_log_id, project_key, duid, report_date):

    uploads = request.files.getlist("daily_files")
    captions = request.form.getlist("file_captions")
    file_type = clean_text(request.form.get("daily_file_type")) or "PHOTO"

    for index, upload in enumerate(uploads):
        if not upload or upload.filename == "":
            continue

        original_file_type = file_type
        if file_type == "PHOTO" and not allowed_file(upload.filename, ALLOWED_IMAGE_EXTENSIONS):
            original_file_type = "DOCUMENT"

        rel_path, _, original_filename = save_daily_log_upload(
            upload, project_key, duid, report_date, original_file_type
        )
        caption = clean_text(captions[index]) if index < len(captions) else ""

        cursor.execute(
            """
            INSERT INTO daily_log_files(
                daily_log_id,
                file_type,
                file_path,
                original_filename,
                caption
            )
            VALUES(%s,%s,%s,%s,%s)
            """,
            (
                daily_log_id,
                original_file_type,
                rel_path,
                original_filename,
                caption,
            ),
        )


def ensure_daily_workbook_sheets(wb):

    if "DAILY LOGS" not in wb.sheetnames:
        ws = wb.create_sheet("DAILY LOGS")
        ws.append(
            [
                "LOG ID",
                "DATE",
                "DUID",
                "STAGE",
                "PROGRESS BEFORE",
                "PROGRESS AFTER",
                "WORK COMPLETED",
                "BLOCKER CATEGORY",
                "BLOCKERS",
                "NEXT DAY PLAN",
                "SUBMITTED BY",
                "UPDATED AT",
            ]
        )

    if "ATTENDANCE" not in wb.sheetnames:
        ws = wb.create_sheet("ATTENDANCE")
        ws.append(
            [
                "LOG ID",
                "DATE",
                "DUID",
                "EMPLOYEE ID",
                "EMPLOYEE NAME",
                "ROLE",
                "ATTENDANCE STATUS",
                "TIME IN",
                "TIME OUT",
                "SAFETY STATUS",
                "REMARKS",
            ]
        )


def sync_daily_log_to_project_workbook(cursor, daily_log_id):

    cursor.execute(
        """
        SELECT dsl.id,
               dsl.project_id,
               dsl.duid,
               dsl.report_date,
               dsl.current_stage,
               dsl.progress_before,
               dsl.progress_after,
               dsl.work_completed,
               dsl.blocker_category,
               dsl.blockers,
               dsl.next_day_plan,
               dsl.submitted_by,
               dsl.updated_at,
               p.project_code
        FROM daily_site_logs dsl
        LEFT JOIN projects p ON dsl.project_id = p.id
        WHERE dsl.id=%s
        """,
        (daily_log_id,),
    )
    log = row_to_dict(cursor)

    if not log or not log.get("project_code"):
        return

    try:
        excel_path = project_excel_path(log["project_code"])
    except ValueError:
        return

    if not os.path.exists(excel_path):
        return

    cursor.execute(
        """
        SELECT da.employee_id,
               da.attendance_status,
               da.time_in,
               da.time_out,
               da.role_at_site,
               da.safety_status_snapshot,
               da.remarks,
               e.first_name,
                              e.middle_name,
               e.last_name
        FROM daily_attendance da
        LEFT JOIN employees e ON da.employee_id = e.id
        WHERE da.daily_log_id=%s
        ORDER BY e.first_name, e.last_name, da.employee_id
        """,
        (daily_log_id,),
    )
    attendance_rows = rows_to_dicts(cursor)

    def apply_daily_log_workbook_update(wb):

        ensure_daily_workbook_sheets(wb)

        ws = wb["DAILY LOGS"]
        row = None

        for current_row in range(2, ws.max_row + 1):
            if ws.cell(row=current_row, column=1).value == daily_log_id:
                row = current_row
                break

        if row is None:
            row = ws.max_row + 1

        values = [
            log["id"],
            log["report_date"],
            log["duid"],
            log["current_stage"],
            log["progress_before"],
            log["progress_after"],
            log["work_completed"],
            log["blocker_category"],
            log["blockers"],
            log["next_day_plan"],
            log["submitted_by"],
            log["updated_at"],
        ]

        write_excel_row(ws, row, values, text_columns={3, 4, 7, 8, 9, 10, 11})

        attendance_ws = wb["ATTENDANCE"]
        rows_to_delete = []

        for current_row in range(2, attendance_ws.max_row + 1):
            if attendance_ws.cell(row=current_row, column=1).value == daily_log_id:
                rows_to_delete.append(current_row)

        for current_row in reversed(rows_to_delete):
            attendance_ws.delete_rows(current_row, 1)

        for attendance in attendance_rows:
            append_excel_row(
                attendance_ws,
                [
                    log["id"],
                    log["report_date"],
                    log["duid"],
                    attendance["employee_id"],
                    full_employee_name(attendance),
                    attendance.get("role_at_site"),
                    attendance.get("attendance_status"),
                    str(attendance.get("time_in") or ""),
                    str(attendance.get("time_out") or ""),
                    attendance.get("safety_status_snapshot"),
                    attendance.get("remarks"),
                ],
                text_columns={3, 5, 6, 7, 8, 9, 10, 11},
            )

        apply_project_workbook_formatting(wb)

    return update_persistent_workbook(
        excel_path,
        apply_daily_log_workbook_update,
        expected_sheets=PROJECT_WORKBOOK_REQUIRED_SHEETS + ("DAILY LOGS", "ATTENDANCE"),
        backup_folder=safe_abs_path("backups", "excel"),
        operation="daily log sync",
    )


def ensure_phase5_workbook_sheets(wb):

    if "PUNCHLIST" not in wb.sheetnames:
        ws = wb.create_sheet("PUNCHLIST")
        ws.append(
            [
                "ITEM ID",
                "DUID",
                "ITEM NUMBER",
                "CATEGORY",
                "TITLE",
                "PRIORITY",
                "STATUS",
                "ASSIGNED TO",
                "RAISED DATE",
                "TARGET DATE",
                "RECTIFIED DATE",
                "VERIFIED DATE",
                "CLOSURE NOTES",
                "UPDATED AT",
            ]
        )

    if "PAT" not in wb.sheetnames:
        ws = wb.create_sheet("PAT")
        ws.append(
            [
                "PAT ID",
                "DUID",
                "PAT REFERENCE",
                "PAT DATE",
                "INSPECTOR",
                "VENDOR",
                "TOWERCO/CUSTOMER",
                "RESULT",
                "REMARKS",
                "DOCUMENT",
                "UPDATED AT",
            ]
        )


def get_project_code_value(cursor, project_id):

    if not project_id:
        return ""

    cursor.execute(
        "SELECT project_code FROM projects WHERE id=%s",
        (project_id,),
    )
    project = row_to_dict(cursor)

    if not project:
        return ""

    return project.get("project_code") or ""


def project_key_for_site_files(cursor, project_id):

    project_code = get_project_code_value(cursor, project_id)
    return project_code or project_id or "general"


def save_site_scoped_upload(
    file_storage,
    project_key,
    duid,
    section,
    allowed_extensions,
    extra_folder="",
):

    if not file_storage or file_storage.filename == "":
        return "", "", ""

    if not allowed_file(file_storage.filename, allowed_extensions):
        raise ValueError("Unsupported file type")

    safe_project = secure_filename(str(project_key or "general")) or "general"
    safe_duid = secure_filename(validate_duid_value(duid)) or "site"
    safe_section = secure_filename(section) or "files"
    folder_parts = ["static", "uploads", "projects", safe_project, "sites", safe_duid, safe_section]

    if extra_folder:
        folder_parts.append(secure_filename(str(extra_folder)) or "item")

    folder_path = safe_abs_path(*folder_parts)
    os.makedirs(folder_path, exist_ok=True)

    original_filename = secure_filename(file_storage.filename)
    filename = str(uuid.uuid4()) + "_" + original_filename
    file_path = os.path.join(folder_path, filename)
    file_storage.save(file_path)

    rel_path = os.path.relpath(file_path, BASE_DIR).replace("\\", "/")
    return rel_path, file_path, original_filename


def get_punchlist_item(cursor, item_id):

    cursor.execute(
        """
        SELECT pi.id,
               pi.duid,
               pi.telecom_site_id,
               pi.project_id,
               pi.item_number,
               pi.category,
               pi.title,
               pi.description,
               pi.priority,
               pi.status,
               pi.assigned_employee_id,
               pi.raised_by,
               pi.raised_date,
               pi.target_date,
               pi.rectified_date,
               pi.verified_date,
               pi.verified_by,
               pi.closure_notes,
               pi.created_at,
               pi.updated_at,
               p.project_name,
               p.project_code,
               e.first_name,
                              e.middle_name,
               e.last_name
        FROM punchlist_items pi
        LEFT JOIN projects p ON pi.project_id = p.id
        LEFT JOIN employees e ON pi.assigned_employee_id = e.id
        WHERE pi.id=%s
        """,
        (item_id,),
    )
    item = row_to_dict(cursor)

    if item:
        item["assigned_name"] = full_employee_name(item)

    return item


def get_punchlist_people(cursor, duid, project_id=None):

    cursor.execute(
        """
        SELECT DISTINCT ON (e.id)
               e.id,
               e.first_name,
                              e.middle_name,
               e.last_name,
               e.position,
               e.telecom_role,
               e.project_id
        FROM employees e
        LEFT JOIN site_assignments sa
          ON sa.employee_id = e.id
         AND sa.du_id = %s
         AND sa.assignment_status='ACTIVE'
        WHERE sa.id IS NOT NULL
           OR e.assigned_du_id=%s
           OR (%s IS NOT NULL AND e.project_id=%s)
        ORDER BY e.id, sa.id DESC
        """,
        (duid, duid, project_id, project_id),
    )
    employees = rows_to_dicts(cursor)

    for employee in employees:
        employee["full_name"] = full_employee_name(employee)

    return employees


def collect_punchlist_form_data(cursor, duid, site, existing_item=None):

    duid = validate_duid_value(duid)
    project_id = clean_text(request.form.get("project_id")) or site.get("project_id")
    item_number = clean_text(request.form.get("item_number"))
    category = clean_text(request.form.get("category"))
    title = clean_text(request.form.get("title"))
    description = clean_text(request.form.get("description"))
    priority = normalize_choice(
        request.form.get("priority"), PUNCHLIST_PRIORITIES, "MEDIUM"
    )
    status = normalize_choice(
        request.form.get("status"),
        PUNCHLIST_STATUSES,
        (existing_item or {}).get("status") or "OPEN",
    )
    assigned_employee_id = clean_text(request.form.get("assigned_employee_id"))
    closure_notes = clean_text(request.form.get("closure_notes"))

    if not title:
        raise ValueError("Punchlist title is required")

    if not priority:
        raise ValueError("Invalid punchlist priority")

    if not status:
        raise ValueError("Invalid punchlist status")

    if not existing_item and status not in ("OPEN", "IN PROGRESS"):
        raise ValueError("New punchlist items must start as OPEN or IN PROGRESS")

    if project_id:
        cursor.execute("SELECT 1 FROM projects WHERE id=%s", (project_id,))

        if not cursor.fetchone():
            raise ValueError("Invalid project")

    if assigned_employee_id:
        cursor.execute("SELECT 1 FROM employees WHERE id=%s", (assigned_employee_id,))

        if not cursor.fetchone():
            raise ValueError("Invalid assigned employee")

    raised_date = validate_date_field(request.form.get("raised_date"), "Raised date")
    target_date = validate_date_field(request.form.get("target_date"), "Target date")

    if raised_date and target_date:
        raised_value = parse_date_value(raised_date)
        target_value = parse_date_value(target_date)

        if raised_value and target_value and target_value < raised_value:
            raise ValueError("Target date cannot be before raised date")

    if item_number:
        cursor.execute(
            """
            SELECT id
            FROM punchlist_items
            WHERE duid=%s
              AND item_number=%s
              AND (%s IS NULL OR id<>%s)
            LIMIT 1
            """,
            (
                duid,
                item_number,
                (existing_item or {}).get("id"),
                (existing_item or {}).get("id"),
            ),
        )

        if cursor.fetchone():
            raise ValueError("Punchlist item number already exists for this DUID")

    return {
        "duid": duid,
        "telecom_site_id": site.get("operational_site_id"),
        "project_id": project_id or None,
        "item_number": item_number or None,
        "category": category,
        "title": title,
        "description": description,
        "priority": priority,
        "status": status,
        "assigned_employee_id": assigned_employee_id or None,
        "raised_by": clean_text(request.form.get("raised_by")) or session.get("admin", ""),
        "raised_date": raised_date or date.today(),
        "target_date": target_date,
        "closure_notes": closure_notes,
    }


def validate_punchlist_transition(current_status, new_status):

    current_status = clean_text(current_status) or "OPEN"
    new_status = clean_text(new_status) or current_status

    if current_status == new_status:
        return

    current_index = PUNCHLIST_STATUSES.index(current_status)
    new_index = PUNCHLIST_STATUSES.index(new_status)

    if new_index == current_index + 1:
        return

    raise ValueError(
        "Punchlist status must follow OPEN -> IN PROGRESS -> RECTIFIED -> VERIFIED -> CLOSED"
    )


def sync_punchlist_files(cursor, item, project_key):

    uploads = request.files.getlist("punchlist_files")
    captions = request.form.getlist("punchlist_file_captions")
    file_type = normalize_choice(
        request.form.get("punchlist_file_type"), PUNCHLIST_FILE_TYPES, "GENERAL"
    )

    if not file_type:
        raise ValueError("Invalid punchlist file type")

    allowed_extensions = (
        ALLOWED_IMAGE_EXTENSIONS
        if file_type in ("BEFORE", "AFTER")
        else ALLOWED_DOCUMENT_EXTENSIONS
    )

    for upload in uploads:
        if upload and upload.filename and not allowed_file(upload.filename, allowed_extensions):
            raise ValueError("Unsupported punchlist evidence file type")

    for index, upload in enumerate(uploads):
        if not upload or upload.filename == "":
            continue

        rel_path, _, original_filename = save_site_scoped_upload(
            upload,
            project_key,
            item["duid"],
            "punchlist",
            allowed_extensions,
            str(item["id"]),
        )
        caption = clean_text(captions[index]) if index < len(captions) else ""

        cursor.execute(
            """
            INSERT INTO punchlist_files(
                punchlist_item_id,
                file_type,
                filename,
                file_path,
                caption,
                uploaded_by
            )
            VALUES(%s,%s,%s,%s,%s,%s)
            """,
            (
                item["id"],
                file_type,
                original_filename,
                rel_path,
                caption,
                session.get("admin", ""),
            ),
        )


def sync_punchlist_item_to_project_workbook(cursor, item_id):

    item = get_punchlist_item(cursor, item_id)

    if not item or not item.get("project_code"):
        return

    try:
        excel_path = project_excel_path(item["project_code"])
    except ValueError:
        return

    if not os.path.exists(excel_path):
        return

    def apply_punchlist_workbook_update(wb):

        ensure_phase5_workbook_sheets(wb)
        ws = wb["PUNCHLIST"]
        row = None

        for current_row in range(2, ws.max_row + 1):
            if ws.cell(row=current_row, column=1).value == item_id:
                row = current_row
                break

        if row is None:
            row = ws.max_row + 1

        values = [
            item["id"],
            item["duid"],
            item.get("item_number"),
            item.get("category"),
            item.get("title"),
            item.get("priority"),
            item.get("status"),
            item.get("assigned_name"),
            item.get("raised_date"),
            item.get("target_date"),
            item.get("rectified_date"),
            item.get("verified_date"),
            item.get("closure_notes"),
            item.get("updated_at"),
        ]

        write_excel_row(ws, row, values, text_columns={2, 3, 4, 5, 6, 7, 8, 13})

        apply_project_workbook_formatting(wb)

    return update_persistent_workbook(
        excel_path,
        apply_punchlist_workbook_update,
        expected_sheets=PROJECT_WORKBOOK_REQUIRED_SHEETS + ("PUNCHLIST", "PAT"),
        backup_folder=safe_abs_path("backups", "excel"),
        operation="punchlist sync",
    )


def get_latest_pat_record(cursor, duid):

    cursor.execute(
        """
        SELECT pr.id,
               pr.duid,
               pr.telecom_site_id,
               pr.project_id,
               pr.pat_reference,
               pr.pat_date,
               pr.inspector_name,
               pr.vendor_name,
               pr.towerco_customer,
               pr.result,
               pr.remarks,
               pr.document_filename,
               pr.document_path,
               pr.created_by,
               pr.created_at,
               pr.updated_at,
               p.project_name,
               p.project_code
        FROM pat_records pr
        LEFT JOIN projects p ON pr.project_id = p.id
        WHERE pr.duid=%s
        ORDER BY pr.pat_date DESC, pr.created_at DESC, pr.id DESC
        LIMIT 1
        """,
        (duid,),
    )
    return row_to_dict(cursor)


def get_pat_record(cursor, pat_id):

    cursor.execute(
        """
        SELECT pr.id,
               pr.duid,
               pr.telecom_site_id,
               pr.project_id,
               pr.pat_reference,
               pr.pat_date,
               pr.inspector_name,
               pr.vendor_name,
               pr.towerco_customer,
               pr.result,
               pr.remarks,
               pr.document_filename,
               pr.document_path,
               pr.created_by,
               pr.created_at,
               pr.updated_at,
               p.project_name,
               p.project_code
        FROM pat_records pr
        LEFT JOIN projects p ON pr.project_id = p.id
        WHERE pr.id=%s
        """,
        (pat_id,),
    )
    return row_to_dict(cursor)


def collect_pat_form_data(cursor, duid, site, existing_record=None):

    duid = validate_duid_value(duid)
    project_id = clean_text(request.form.get("project_id")) or site.get("project_id")
    pat_reference = clean_text(request.form.get("pat_reference"))
    pat_date = validate_date_field(request.form.get("pat_date"), "PAT date")
    result = normalize_choice(request.form.get("result"), PAT_RESULTS, "PENDING")

    if not result:
        raise ValueError("Invalid PAT result")

    if not pat_date:
        raise ValueError("PAT date is required")

    if project_id:
        cursor.execute("SELECT 1 FROM projects WHERE id=%s", (project_id,))

        if not cursor.fetchone():
            raise ValueError("Invalid project")

    cursor.execute(
        """
        SELECT id
        FROM pat_records
        WHERE duid=%s
          AND pat_date=%s
          AND COALESCE(pat_reference, '') = COALESCE(%s, '')
          AND (%s IS NULL OR id<>%s)
        LIMIT 1
        """,
        (
            duid,
            pat_date,
            pat_reference or None,
            (existing_record or {}).get("id"),
            (existing_record or {}).get("id"),
        ),
    )

    if cursor.fetchone():
        raise ValueError("PAT record already exists for this DUID, date, and reference")

    return {
        "duid": duid,
        "telecom_site_id": site.get("operational_site_id"),
        "project_id": project_id or None,
        "pat_reference": pat_reference or None,
        "pat_date": pat_date,
        "inspector_name": clean_text(request.form.get("inspector_name")),
        "vendor_name": clean_text(request.form.get("vendor_name")),
        "towerco_customer": clean_text(request.form.get("towerco_customer")),
        "result": result,
        "remarks": clean_text(request.form.get("remarks")),
        "created_by": session.get("admin", ""),
    }


def save_pat_document(cursor, pat_record, project_key):

    upload = request.files.get("pat_document")

    if not upload or upload.filename == "":
        return pat_record.get("document_filename") or "", pat_record.get("document_path") or ""

    if not allowed_file(upload.filename, ALLOWED_DOCUMENT_EXTENSIONS):
        raise ValueError("Unsupported PAT document file type")

    rel_path, _, original_filename = save_site_scoped_upload(
        upload,
        project_key,
        pat_record["duid"],
        "pat",
        ALLOWED_DOCUMENT_EXTENSIONS,
    )
    return original_filename, rel_path


def sync_pat_record_to_project_workbook(cursor, pat_id):

    record = get_pat_record(cursor, pat_id)

    if not record or not record.get("project_code"):
        return

    try:
        excel_path = project_excel_path(record["project_code"])
    except ValueError:
        return

    if not os.path.exists(excel_path):
        return

    def apply_pat_workbook_update(wb):

        ensure_phase5_workbook_sheets(wb)
        ws = wb["PAT"]
        row = None

        for current_row in range(2, ws.max_row + 1):
            if ws.cell(row=current_row, column=1).value == pat_id:
                row = current_row
                break

        if row is None:
            row = ws.max_row + 1

        values = [
            record["id"],
            record["duid"],
            record.get("pat_reference"),
            record.get("pat_date"),
            record.get("inspector_name"),
            record.get("vendor_name"),
            record.get("towerco_customer"),
            record.get("result"),
            record.get("remarks"),
            record.get("document_filename") or stored_file_display_name(record.get("document_path")),
            record.get("updated_at"),
        ]

        write_excel_row(ws, row, values, text_columns={2, 3, 5, 6, 7, 8, 9, 10})

        apply_project_workbook_formatting(wb)

    return update_persistent_workbook(
        excel_path,
        apply_pat_workbook_update,
        expected_sheets=PROJECT_WORKBOOK_REQUIRED_SHEETS + ("PUNCHLIST", "PAT"),
        backup_folder=safe_abs_path("backups", "excel"),
        operation="PAT sync",
    )


def apply_latest_pat_to_site(cursor, duid):

    latest_pat = get_latest_pat_record(cursor, duid)

    if not latest_pat:
        return

    cursor.execute(
        """
        INSERT INTO telecom_sites(
            du_id,
            project_id,
            pat_status,
            updated_at
        )
        VALUES(%s,%s,%s,CURRENT_TIMESTAMP)
        ON CONFLICT(du_id)
        DO UPDATE SET
            project_id=COALESCE(EXCLUDED.project_id, telecom_sites.project_id),
            pat_status=EXCLUDED.pat_status,
            current_stage=CASE
                WHEN telecom_sites.current_stage='Completed' THEN telecom_sites.current_stage
                WHEN EXCLUDED.pat_status IN ('PASSED','PASSED WITH PUNCHLIST') THEN 'PAT'
                ELSE telecom_sites.current_stage
            END,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            latest_pat["duid"],
            latest_pat.get("project_id"),
            latest_pat["result"],
        ),
    )


def get_punchlist_summary(cursor, duid):

    cursor.execute(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE status='OPEN') AS open_count,
               COUNT(*) FILTER (WHERE status='IN PROGRESS') AS in_progress_count,
               COUNT(*) FILTER (WHERE status='RECTIFIED') AS rectified_count,
               COUNT(*) FILTER (WHERE status='VERIFIED') AS verified_count,
               COUNT(*) FILTER (WHERE status='CLOSED') AS closed_count,
               COUNT(*) FILTER (
                   WHERE priority='CRITICAL'
                     AND status = ANY(%s)
               ) AS critical_open,
               COUNT(*) FILTER (
                   WHERE priority IN ('CRITICAL','HIGH')
                     AND status = ANY(%s)
               ) AS critical_high_unresolved,
               COUNT(*) FILTER (WHERE status = ANY(%s)) AS unresolved_count
        FROM punchlist_items
        WHERE duid=%s
        """,
        (
            PUNCHLIST_UNRESOLVED_STATUSES,
            PUNCHLIST_UNRESOLVED_STATUSES,
            PUNCHLIST_UNRESOLVED_STATUSES,
            duid,
        ),
    )
    summary = row_to_dict(cursor) or {}

    for key in (
        "total",
        "open_count",
        "in_progress_count",
        "rectified_count",
        "verified_count",
        "closed_count",
        "critical_open",
        "critical_high_unresolved",
        "unresolved_count",
    ):
        summary[key] = summary.get(key) or 0

    return summary


def calculate_acceptance_readiness(latest_pat, punchlist_summary):

    reasons = []
    warnings = []
    pat_result = latest_pat.get("result") if latest_pat else ""

    if not latest_pat:
        reasons.append("No PAT record has been created.")
    elif pat_result == "PENDING":
        reasons.append("PAT is pending.")
    elif pat_result == "FAILED":
        reasons.append("PAT has failed.")
    elif pat_result not in ("PASSED", "PASSED WITH PUNCHLIST"):
        reasons.append("PAT has not passed.")

    if punchlist_summary["critical_high_unresolved"]:
        reasons.append(
            f"{punchlist_summary['critical_high_unresolved']} HIGH/CRITICAL punchlist item remains unresolved."
        )

    if punchlist_summary["unresolved_count"]:
        if pat_result == "PASSED WITH PUNCHLIST" and not punchlist_summary["critical_high_unresolved"]:
            warnings.append(
                f"{punchlist_summary['unresolved_count']} non-critical punchlist item remains under PASSED WITH PUNCHLIST."
            )
        elif not punchlist_summary["critical_high_unresolved"]:
            reasons.append(
                f"{punchlist_summary['unresolved_count']} unresolved punchlist item remains."
            )

    calculated_status = "READY" if not reasons else "NOT READY"

    return {
        "calculated_status": calculated_status,
        "reasons": reasons,
        "warnings": warnings,
        "pat_result": pat_result or "PENDING",
    }


def get_site_acceptance_info(cursor, duid):

    latest_pat = get_latest_pat_record(cursor, duid)
    punchlist_summary = get_punchlist_summary(cursor, duid)
    readiness = calculate_acceptance_readiness(latest_pat, punchlist_summary)

    cursor.execute(
        """
        SELECT id,
               duid,
               telecom_site_id,
               project_id,
               acceptance_status,
               accepted_by,
               accepted_at,
               acceptance_reference,
               remarks,
               override_used,
               override_reason,
               created_at,
               updated_at
        FROM site_acceptance
        WHERE duid=%s
        """,
        (duid,),
    )
    record = row_to_dict(cursor) or {}
    display_status = readiness["calculated_status"]

    if record.get("acceptance_status") in ("ACCEPTED", "REJECTED"):
        display_status = record["acceptance_status"]

    return {
        "record": record,
        "latest_pat": latest_pat,
        "punchlist_summary": punchlist_summary,
        "readiness": readiness,
        "display_status": display_status,
    }


def build_daily_log_filter_conditions(filters):

    conditions = ["TRUE"]
    params = []
    blocker_filter = clean_text(filters.get("has_blocker"))

    if filters.get("report_date"):
        report_date = validate_date_field(filters["report_date"], "Report date")
        conditions.append("dsl.report_date=%s")
        params.append(report_date)

    if filters.get("duid"):
        conditions.append("dsl.duid ILIKE %s")
        params.append("%" + clean_text(filters["duid"]) + "%")

    if filters.get("site"):
        conditions.append(
            """
            COALESCE(g.site_name, g.sitename, g.globe_du_name, pr.planning_du_name, '')
                ILIKE %s
            """
        )
        params.append("%" + clean_text(filters["site"]) + "%")

    if filters.get("stage"):
        stage = normalize_choice(filters["stage"], SITE_STAGES, "")

        if not stage:
            raise ValueError("Invalid site stage")

        conditions.append("dsl.current_stage=%s")
        params.append(stage)

    if filters.get("project_id"):
        conditions.append("dsl.project_id=%s")
        params.append(clean_text(filters["project_id"]))

    if filters.get("towerco"):
        conditions.append("g.towerco=%s")
        params.append(clean_text(filters["towerco"]))

    if blocker_filter:
        blocker_condition = """
            (
                COALESCE(TRIM(dsl.blockers), '') <> ''
                OR COALESCE(TRIM(dsl.blocker_category), '') <> ''
            )
        """

        if blocker_filter == "yes":
            conditions.append(blocker_condition)
        elif blocker_filter == "no":
            conditions.append("NOT " + blocker_condition)
        else:
            raise ValueError("Invalid blocker filter")

    if filters.get("submitted_by"):
        conditions.append("dsl.submitted_by ILIKE %s")
        params.append("%" + clean_text(filters["submitted_by"]) + "%")

    return conditions, params


def collect_site_form_data(cursor, existing_du_id=None):

    du_id = validate_duid_value(existing_du_id or request.form.get("du_id"))
    project_id = clean_text(request.form.get("project_id"))
    vendor = clean_text(request.form.get("vendor"))
    current_stage = normalize_choice(
        request.form.get("current_stage"), SITE_STAGES, "Planning"
    )
    overall_status = normalize_choice(
        request.form.get("overall_status"), SITE_STATUSES, "Not Started"
    )
    access_status = normalize_choice(request.form.get("access_status"), ACCESS_STATUSES, "")
    pat_status = normalize_choice(request.form.get("pat_status"), PAT_STATUSES, "MISSING")
    overall_progress = validate_progress(request.form.get("overall_progress"))
    access_valid_from = validate_date_field(
        request.form.get("access_valid_from"), "Access valid from"
    )
    access_valid_until = validate_date_field(
        request.form.get("access_valid_until"), "Access valid until"
    )

    if not current_stage:
        raise ValueError("Invalid site stage")

    if not overall_status:
        raise ValueError("Invalid site status")

    if clean_text(request.form.get("access_status")) and not access_status:
        raise ValueError("Invalid access status")

    if not pat_status:
        raise ValueError("Invalid PAT status")

    if project_id:
        cursor.execute("SELECT 1 FROM projects WHERE id=%s", (project_id,))

        if not cursor.fetchone():
            raise ValueError("Invalid project")

    return {
        "du_id": du_id,
        "project_id": project_id or None,
        "vendor": vendor,
        "current_stage": current_stage,
        "overall_progress": overall_progress,
        "overall_status": overall_status,
        "access_valid_from": access_valid_from,
        "access_valid_until": access_valid_until,
        "access_status": access_status or None,
        "pat_status": pat_status,
        "operational_team_leader": clean_text(
            request.form.get("operational_team_leader")
        ),
        "remarks": clean_text(request.form.get("remarks")),
    }


def save_site_operational_record(cursor, site_data):

    cursor.execute(
        """
        INSERT INTO telecom_sites(
            du_id,
            project_id,
            vendor,
            current_stage,
            overall_progress,
            overall_status,
            access_valid_from,
            access_valid_until,
            access_status,
            pat_status,
            operational_team_leader,
            remarks,
            updated_at
        )
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
        ON CONFLICT(du_id)
        DO UPDATE SET
            project_id=EXCLUDED.project_id,
            vendor=EXCLUDED.vendor,
            current_stage=EXCLUDED.current_stage,
            overall_progress=EXCLUDED.overall_progress,
            overall_status=EXCLUDED.overall_status,
            access_valid_from=EXCLUDED.access_valid_from,
            access_valid_until=EXCLUDED.access_valid_until,
            access_status=EXCLUDED.access_status,
            pat_status=EXCLUDED.pat_status,
            operational_team_leader=EXCLUDED.operational_team_leader,
            remarks=EXCLUDED.remarks,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            site_data["du_id"],
            site_data["project_id"],
            site_data["vendor"],
            site_data["current_stage"],
            site_data["overall_progress"],
            site_data["overall_status"],
            site_data["access_valid_from"],
            site_data["access_valid_until"],
            site_data["access_status"],
            site_data["pat_status"],
            site_data["operational_team_leader"],
            site_data["remarks"],
        ),
    )


def project_excel_path(project_code):

    safe_code = secure_filename(str(project_code))

    if safe_code != str(project_code):
        raise ValueError("Invalid project code")

    path = os.path.abspath(os.path.join(EXCEL_DIR, safe_code + ".xlsx"))

    if not path.startswith(EXCEL_DIR + os.sep):
        raise ValueError("Invalid project Excel path")

    return path


def build_project_workbook_template():

    wb = Workbook()

    ws1 = wb.active
    ws1.title = "ACCESS INFO"
    ws1.append(ACCESS_INFO_HEADERS)
    ensure_access_info_headers(ws1)

    sheets = [
        "2X2",
        "NBI",
        "CERTIFICATES",
        "eSignature",
        "SEC ID",
        "WAH CERT",
        "ID",
    ]

    for sheet_name in sheets:

        ws = wb.create_sheet(sheet_name)

        if sheet_name == "SEC ID":
            ws.append(["NAME", "SEC NUMBER", "EXPIRY", "IMAGE"])
        elif sheet_name == "ID":
            ws.append(["NAME", "ID NUMBER", "EXPIRY", "IMAGE"])
        else:
            ws.append(["NAME", "IMAGE"])

    ensure_phase5_workbook_sheets(wb)
    apply_project_workbook_formatting(wb)

    return wb


def project_workbook_missing_message(project):

    return (
        f"Project workbook for {project.get('project_name') or 'this project'} / "
        f"{project.get('project_code') or 'unknown code'} is missing. "
        "Please rebuild or restore the project workbook before using this Excel workflow."
    )


def get_project_by_code(cursor, code):

    cursor.execute(
        """
        SELECT id, project_name, region, company, project_code, date_created
        FROM projects
        WHERE project_code=%s
        """,
        (code,),
    )
    return row_to_dict(cursor)


def style_project_header(ws):

    fill = PatternFill(start_color="FFE599", end_color="FFE599", fill_type="solid")
    font = Font(bold=True)
    border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    for cell in ws[1]:
        cell.fill = fill
        cell.font = font
        cell.alignment = Alignment(
            horizontal="center",
            vertical="center",
            wrap_text=True,
        )
        cell.border = border

    ws.row_dimensions[1].height = 34
    ws.freeze_panes = "A2"


def apply_project_sheet_formatting(ws, fixed_widths=None, wrapped_columns=None):

    fixed_widths = fixed_widths or {}
    wrapped_columns = set(wrapped_columns or [])
    style_project_header(ws)

    for column, width in fixed_widths.items():
        ws.column_dimensions[column].width = width

    for row in ws.iter_rows(min_row=2):
        max_text_length = 0

        for cell in row:
            column = cell.column_letter
            wrap = column in wrapped_columns
            cell.alignment = Alignment(
                horizontal="left",
                vertical="top",
                wrap_text=wrap,
            )

            if isinstance(cell.value, datetime):
                cell.number_format = "yyyy-mm-dd hh:mm"
            elif isinstance(cell.value, date):
                cell.number_format = "yyyy-mm-dd"
            elif isinstance(cell.value, time):
                cell.number_format = "hh:mm"

            if wrap and cell.value not in (None, ""):
                max_text_length = max(max_text_length, len(str(cell.value)))

        if max_text_length > 90:
            ws.row_dimensions[row[0].row].height = 60
        elif max_text_length > 45:
            ws.row_dimensions[row[0].row].height = 42


def apply_project_workbook_formatting(wb):

    sheet_widths = {
        "ACCESS INFO": {
            "A": 26,
            "B": 10,
            "C": 18,
            "D": 18,
            "E": 18,
            "F": 30,
            "G": 18,
            "H": 20,
            "I": 30,
            "J": 20,
            "K": 32,
            "L": 20,
            "M": 18,
            "N": 14,
            "O": 14,
            "P": 16,
            "Q": 16,
        },
        "DAILY LOGS": {
            "A": 10,
            "B": 20,
            "C": 16,
            "D": 18,
            "E": 16,
            "F": 16,
            "G": 42,
            "H": 22,
            "I": 36,
            "J": 42,
            "K": 18,
            "L": 21,
        },
        "ATTENDANCE": {
            "A": 10,
            "B": 20,
            "C": 16,
            "D": 13,
            "E": 26,
            "F": 22,
            "G": 18,
            "H": 12,
            "I": 12,
            "J": 16,
            "K": 35,
        },
        "PUNCHLIST": {
            "A": 10,
            "B": 16,
            "C": 24,
            "D": 18,
            "E": 36,
            "F": 14,
            "G": 16,
            "H": 24,
            "I": 14,
            "J": 14,
            "K": 16,
            "L": 16,
            "M": 42,
            "N": 21,
        },
        "PAT": {
            "A": 10,
            "B": 16,
            "C": 24,
            "D": 14,
            "E": 22,
            "F": 22,
            "G": 24,
            "H": 20,
            "I": 42,
            "J": 28,
            "K": 21,
        },
        "SEC ID": {"A": 28, "B": 20, "C": 14, "D": 45},
        "ID": {"A": 28, "B": 20, "C": 14, "D": 52, "E": 14, "F": 22, "G": 18, "H": 18},
    }
    wrapped_columns = {
        "ACCESS INFO": {"F", "I", "K"},
        "DAILY LOGS": {"G", "H", "I", "J"},
        "ATTENDANCE": {"E", "F", "K"},
        "PUNCHLIST": {"E", "M"},
        "PAT": {"I", "J"},
    }

    image_sheet_widths = {"A": 28, "B": 45}

    for ws in wb.worksheets:
        widths = sheet_widths.get(ws.title)

        if widths is None and ws.title in ("2X2", "NBI", "CERTIFICATES", "eSignature", "WAH CERT"):
            widths = image_sheet_widths

        apply_project_sheet_formatting(ws, widths, wrapped_columns.get(ws.title))

        if ws.title in ("2X2", "NBI", "CERTIFICATES", "eSignature", "WAH CERT"):
            for row in range(2, ws.max_row + 1):
                if ws.cell(row=row, column=2).value:
                    ws.row_dimensions[row].height = max(
                        ws.row_dimensions[row].height or 0,
                        25,
                    )

        if ws.title in ("SEC ID", "ID"):
            for row in range(2, ws.max_row + 1):
                if any(ws.cell(row=row, column=col).value for col in range(1, ws.max_column + 1)):
                    ws.row_dimensions[row].height = max(
                        ws.row_dimensions[row].height or 0,
                        32,
                    )


def apply_daily_operations_export_formatting(ws):

    widths = {
        "A": 16,
        "B": 14,
        "C": 34,
        "D": 20,
        "E": 20,
        "F": 20,
        "G": 46,
        "H": 24,
        "I": 46,
        "J": 46,
        "K": 24,
        "L": 18,
        "M": 22,
    }
    wrapped_columns = {"C", "G", "H", "I", "J", "K"}

    apply_project_sheet_formatting(
        ws,
        fixed_widths=widths,
        wrapped_columns=wrapped_columns,
    )

    ws.auto_filter.ref = ws.dimensions

    for row in ws.iter_rows(min_row=2):
        if row[0].value not in (None, ""):
            row[0].number_format = "yyyy-mm-dd"
            row[0].alignment = Alignment(horizontal="left", vertical="top")

        for cell in (row[4], row[5], row[11], row[12]):
            cell.alignment = Alignment(horizontal="center", vertical="top")


def ensure_access_info_headers(ws):

    yellow = PatternFill(start_color="FFFF00", fill_type="solid")
    bold = Font(bold=True)
    center = Alignment(horizontal="center")
    border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    for col, header in enumerate(ACCESS_INFO_HEADERS, start=1):
        cell = ws.cell(row=1, column=col)
        if cell.value in (None, ""):
            cell.value = header
        elif str(cell.value).strip().upper() != header:
            found = find_column(ws, header)
            if not found:
                cell.value = header

        cell.fill = yellow
        cell.font = bold
        cell.alignment = center
        cell.border = border


def write_access_info_row(wb, project, employee, old_name=None):

    ws = wb["ACCESS INFO"]
    ensure_access_info_headers(ws)

    full_name = full_employee_name(employee)
    target_name = clean_text(old_name or full_name)
    row = None

    for current_row in range(2, ws.max_row + 1):
        value = clean_text(ws.cell(row=current_row, column=1).value)
        if value == target_name or value == full_name:
            row = current_row
            break

    if row is None:
        row = ws.max_row + 1

    values = [
        full_name,
        "RUC",
        employee.get("position", ""),
        project.get("region", ""),
        employee.get("mobile", ""),
        employee.get("email", ""),
        employee.get("phone_type", ""),
        employee.get("ftap_imei", ""),
        employee.get("ftap_email", ""),
        employee.get("philtower_imei", ""),
        employee.get("philtower_email", ""),
        employee.get("telecom_role", ""),
        employee.get("assigned_du_id", ""),
        employee.get("nbi_expiry_date", ""),
        employee.get("wah_expiry_date", ""),
        employee.get("first_aid_expiry_date", ""),
        employee.get("overall_safety_status", ""),
    ]

    write_excel_row(
        ws,
        row,
        values,
        text_columns={1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 17},
    )

    widths = [28, 10, 18, 18, 18, 32, 18, 25, 32, 25, 32, 18, 18, 16, 16, 18, 18]
    for col, width in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = width


def update_master_tracker_safety(employee):

    du_id = clean_text(employee.get("assigned_du_id"))

    if not du_id or not os.path.exists(MASTER_TRACKER_PATH):
        return

    def apply_master_safety_update(wb):

        if "RUC SAFETY" in wb.sheetnames:
            ws = wb["RUC SAFETY"]
        else:
            ws = wb.create_sheet("RUC SAFETY")
            ws.append(
                [
                    "DUID",
                    "EMPLOYEE ID",
                    "EMPLOYEE NAME",
                    "ROLE",
                    "PROJECT ID",
                    "NBI EXPIRY",
                    "WAH EXPIRY",
                    "FIRST AID EXPIRY",
                    "SAFETY STATUS",
                    "UPDATED AT",
                ]
            )

        employee_id = employee.get("id")
        row = None

        for current_row in range(2, ws.max_row + 1):
            if ws.cell(row=current_row, column=2).value == employee_id:
                row = current_row
                break

        if row is None:
            row = ws.max_row + 1

        write_excel_text_cell(ws, row, 1, du_id)
        ws.cell(row=row, column=2).value = employee_id
        write_excel_text_cell(ws, row, 3, full_employee_name(employee))
        write_excel_text_cell(ws, row, 4, employee.get("telecom_role", ""))
        ws.cell(row=row, column=5).value = employee.get("project_id", "")
        ws.cell(row=row, column=6).value = employee.get("nbi_expiry_date", "")
        ws.cell(row=row, column=7).value = employee.get("wah_expiry_date", "")
        ws.cell(row=row, column=8).value = employee.get("first_aid_expiry_date", "")
        write_excel_text_cell(ws, row, 9, employee.get("overall_safety_status", ""))
        write_excel_text_cell(ws, row, 10, datetime.now().strftime("%Y-%m-%d %H:%M"))

    return update_persistent_workbook(
        MASTER_TRACKER_PATH,
        apply_master_safety_update,
        expected_sheets=MASTER_TRACKER_REQUIRED_SHEETS + ("RUC SAFETY",),
        backup_folder=safe_abs_path("backups", "master"),
        operation="master tracker safety sync",
    )


def sync_safety_document(
    cursor,
    employee_id,
    project_id,
    document_type,
    reference_number,
    issue_date,
    expiry_date,
    file_path,
    original_filename="",
    is_replacement=False,
):

    if document_type not in DOCUMENT_TYPES:
        raise ValueError("Invalid document type")

    status = None
    has_document_data = any(
        [
            clean_text(reference_number),
            clean_text(issue_date),
            clean_text(expiry_date),
            clean_text(file_path),
        ]
    )

    cursor.execute(
        """
        SELECT id
        FROM safety_documents
        WHERE employee_id=%s
          AND document_type=%s
          AND is_current=TRUE
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (employee_id, document_type),
    )
    current_document = row_to_dict(cursor)

    if not current_document and not file_path:
        cursor.execute(
            """
            SELECT nbi, wah_file, first_aid_file
            FROM employees
            WHERE id=%s
            """,
            (employee_id,),
        )
        employee_files = row_to_dict(cursor) or {}

        if document_type == "NBI" and employee_files.get("nbi"):
            file_path = "uploads/nbi/" + employee_files["nbi"]
        elif document_type == "WAH" and employee_files.get("wah_file"):
            file_path = employee_files["wah_file"]
        elif document_type == "FIRST_AID" and employee_files.get("first_aid_file"):
            file_path = employee_files["first_aid_file"]

        if file_path and not original_filename:
            original_filename = stored_file_display_name(file_path)

    if current_document and file_path and is_replacement:
        cursor.execute(
            """
            UPDATE safety_documents
            SET is_current=FALSE,
                updated_at=CURRENT_TIMESTAMP
            WHERE employee_id=%s
              AND document_type=%s
              AND is_current=TRUE
            """,
            (employee_id, document_type),
        )
    elif current_document:
        cursor.execute(
            """
            UPDATE safety_documents
            SET project_id=%s,
                reference_number=%s,
                issue_date=%s,
                expiry_date=%s,
                file_path=COALESCE(NULLIF(%s, ''), file_path),
                original_filename=COALESCE(NULLIF(%s, ''), original_filename),
                status=%s,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=%s
            """,
            (
                project_id,
                reference_number,
                issue_date,
                expiry_date,
                file_path if is_replacement else "",
                original_filename,
                status,
                current_document["id"],
            ),
        )
        return

    if not has_document_data:
        return

    cursor.execute(
        """
        INSERT INTO safety_documents(
            employee_id,
            project_id,
            document_type,
            reference_number,
            issue_date,
            expiry_date,
            file_path,
            status,
            is_current,
            original_filename,
            updated_at
        )
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s,CURRENT_TIMESTAMP)
        """,
        (
            employee_id,
            project_id,
            document_type,
            reference_number,
            issue_date,
            expiry_date,
            file_path,
            status,
            original_filename,
        ),
    )


def sync_site_assignment(cursor, project_id, employee_id, du_id, role):

    du_id = clean_text(du_id)
    role = clean_text(role)

    if not du_id:
        cursor.execute(
            """
            UPDATE site_assignments
            SET assignment_status='INACTIVE',
                end_date=COALESCE(end_date, CURRENT_DATE)
            WHERE employee_id=%s
              AND project_id=%s
              AND assignment_status='ACTIVE'
            """,
            (employee_id, project_id),
        )
        return

    cursor.execute(
        """
        UPDATE site_assignments
        SET du_id=%s,
            role=%s,
            assignment_status='ACTIVE',
            end_date=NULL
        WHERE employee_id=%s
          AND project_id=%s
          AND assignment_status='ACTIVE'
        """,
        (du_id, role, employee_id, project_id),
    )

    if cursor.rowcount == 0:
        cursor.execute(
            """
            INSERT INTO site_assignments(
                project_id,
                du_id,
                employee_id,
                role,
                assignment_status,
                start_date
            )
            VALUES(%s,%s,%s,%s,'ACTIVE',CURRENT_DATE)
            """,
            (project_id, du_id, employee_id, role),
        )


#############################################
# MASTER TRACKER
#############################################


@app.route("/master_tracker")
@permission_required("view_master_tracker")
def master_tracker():

    if not os.path.exists(MASTER_TRACKER_PATH):
        flash("Master Tracker workbook is not available.")
        return redirect(url_for("reports_center"))

    if request.method == "GET":
        tracker_status = get_master_tracker_status()

        try:
            tracker_status = record_master_tracker_download_for_edit(
                admin_id=session.get("admin_id"),
                username=session.get("admin"),
            )
        except Exception:
            app.logger.warning("Unable to record Master Tracker download-for-edit state.")

        audit_event(
            "MASTER_TRACKER_DOWNLOADED_FOR_EDIT",
            "master_tracker",
            None,
            f"Downloaded Master Tracker editing copy for {tracker_status['version_label']}.",
        )

    return send_file(MASTER_TRACKER_PATH, as_attachment=True)


#############################################
# UPLOAD MASTER TRACKER
#############################################


@app.route("/upload_master_tracker", methods=["GET", "POST"])
@permission_required("manage_master_tracker")
def upload_master_tracker():

    master_path = MASTER_TRACKER_PATH
    tracker_status = get_master_tracker_status()

    if request.method == "POST":

        file = request.files.get("tracker")

        if not file or file.filename == "":
            flash("No file selected.")
            audit_event(
                "MASTER_TRACKER_UPLOAD_REJECTED",
                "master_tracker",
                None,
                "Rejected Master Tracker upload: no file selected.",
            )
            return redirect(url_for("upload_master_tracker"))

        if not allowed_file(file.filename, {"xlsx"}):
            flash("Only .xlsx files are allowed.")
            audit_event(
                "MASTER_TRACKER_UPLOAD_REJECTED",
                "master_tracker",
                None,
                "Rejected Master Tracker upload: invalid file type.",
            )
            return redirect(url_for("upload_master_tracker"))

        upload_path = safe_abs_path(
            "excel_files",
            "master",
            f"{WORKBOOK_TEMP_PREFIX}upload_{uuid.uuid4().hex}.xlsx",
        )

        try:
            file.save(upload_path)
            replace_persistent_workbook_file(
                master_path,
                upload_path,
                validator=validate_tracker,
                expected_sheets=MASTER_TRACKER_REQUIRED_SHEETS,
                backup_folder=safe_abs_path("backups", "master"),
                operation="master tracker upload",
            )
        except WorkbookSafetyError as exc:
            rejection_reason = workbook_error_message(exc)

            try:
                tracker_status = record_master_tracker_upload_rejected(
                    rejection_reason,
                    admin_id=session.get("admin_id"),
                    username=session.get("admin"),
                )
            except Exception:
                app.logger.warning("Unable to record rejected Master Tracker upload.")

            audit_event(
                "MASTER_TRACKER_UPLOAD_REJECTED",
                "master_tracker",
                None,
                f"Rejected Master Tracker upload: {rejection_reason}",
            )
            flash("Master Tracker was not updated. " + rejection_reason)
            return redirect(url_for("upload_master_tracker"))
        finally:
            if os.path.exists(upload_path):
                os.remove(upload_path)

        try:
            tracker_status = record_master_tracker_upload_accepted(
                admin_id=session.get("admin_id"),
                username=session.get("admin"),
            )
        except Exception:
            tracker_status = get_master_tracker_status()
            app.logger.warning("Unable to record accepted Master Tracker upload.")

        audit_event(
            "MASTER_TRACKER_UPLOAD_ACCEPTED",
            "master_tracker",
            None,
            f"Accepted Master Tracker upload as {tracker_status['version_label']}.",
        )
        flash("Master Tracker updated successfully.")
        return redirect(url_for("upload_master_tracker"))

    return render_template(
        "upload_master_tracker.html",
        tracker_status=tracker_status,
    )


#############################################
# LOGIN
#############################################


@app.route("/", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        limited, retry_after = login_rate_limit_status()

        if limited:
            audit_security_event_once(
                "AUTH_LOGIN_RATE_LIMITED",
                "Login temporarily blocked after repeated failed attempts.",
            )
            return rate_limited_response(retry_after)

        username = clean_text(request.form.get("username"))
        password = request.form.get("password", "")

        conn = connect_db()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, username, password, role, active, employee_id
            FROM admins
            WHERE username=%s
            """,
            (username,),
        )

        admin = cursor.fetchone()

        if admin and admin[4] and check_password_hash(admin[2], password):

            clear_login_failures()
            session.clear()
            session.permanent = True
            session["admin_id"] = admin[0]
            session["admin"] = admin[1]
            session["role"] = admin[3]
            session["employee_id"] = admin[5]
            ensure_csrf_token()

            cursor.execute(
                """
                UPDATE admins
                SET last_login=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=%s
                """,
                (admin[0],),
            )
            audit_event(
                "AUTH_LOGIN_SUCCESS",
                "admin",
                admin[0],
                "User logged in successfully.",
                conn=conn,
                admin_id=admin[0],
                username_snapshot=admin[1],
                role_snapshot=admin[3],
            )
            conn.commit()
            cursor.close()
            conn.close()

            if is_team_leader_role(admin[3]):
                return redirect(url_for("team_leader_dashboard"))

            return redirect("/dashboard")

        audit_event(
            "AUTH_LOGIN_FAILED",
            "admin",
            None,
            "Failed login attempt.",
            conn=conn,
            admin_id=None,
            username_snapshot=username,
            role_snapshot=None,
        )
        conn.commit()
        cursor.close()
        conn.close()

        blocked, retry_after = record_login_failure()

        if blocked:
            audit_security_event_once(
                "AUTH_LOGIN_RATE_LIMITED",
                "Login temporarily blocked after repeated failed attempts.",
            )
            return rate_limited_response(retry_after)

        flash("Invalid username or password.")

    return render_template("login.html")


#############################################
# DASHBOARD WITH STATISTICS
#############################################


def dashboard_tone(label):

    label = clean_text(label).upper()

    if label in {
        "BLOCKED",
        "FAILED",
        "EXPIRED",
        "MISSING",
        "CRITICAL",
        "HIGH",
        "NOT READY",
        "OPEN",
    }:
        return "danger"

    if label in {
        "PENDING",
        "IN PROGRESS",
        "ON HOLD",
        "EXPIRING SOON",
        "PASSED WITH PUNCHLIST",
        "RECTIFIED",
    }:
        return "warning"

    if label in {"VALID", "COMPLETED", "PASSED", "READY", "ACCEPTED", "CLOSED"}:
        return "success"

    return "primary"


def dashboard_add_percentages(rows, count_key="count"):

    total = sum(int(row.get(count_key) or 0) for row in rows)

    for row in rows:
        count = int(row.get(count_key) or 0)
        row["percent"] = round((count / total) * 100, 1) if total else 0

    return rows


def dashboard_distribution_from_counts(counts):

    rows = [
        {"label": label, "count": count, "tone": dashboard_tone(label)}
        for label, count in counts.items()
        if count
    ]
    rows.sort(key=lambda row: (-row["count"], row["label"]))
    return dashboard_add_percentages(rows)


def dashboard_distribution_from_rows(rows, key, fallback="Unspecified"):

    counts = {}

    for row in rows:
        label = clean_text(row.get(key)) or fallback
        counts[label] = counts.get(label, 0) + 1

    return dashboard_distribution_from_counts(counts)


def dashboard_number(value):

    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0


def dashboard_count_label(count, singular, plural=None):

    count = int(count or 0)
    label = singular if count == 1 else (plural or singular + "s")
    return f"{count} {label}"


@app.route("/dashboard")
@login_required
def dashboard():

    if is_team_leader_role():
        return redirect(url_for("team_leader_dashboard"))

    conn = connect_db()
    cursor = conn.cursor()

    filters = {
        "project_id": clean_text(request.args.get("project_id")),
        "towerco": clean_text(request.args.get("towerco")),
        "region": clean_text(request.args.get("region")),
    }

    if filters["project_id"] and not filters["project_id"].isdigit():
        filters["project_id"] = ""

    cursor.execute(
        """
        SELECT id, project_name, region, company, project_code, date_created
        FROM projects
        ORDER BY date_created DESC, id DESC
        """
    )
    projects = rows_to_dicts(cursor)
    total_projects = len(projects)

    cursor.execute(
        """
        SELECT id,
               first_name,
                              middle_name,
               last_name,
               project_id,
               position,
               telecom_role,
               assigned_du_id,
               nbi,
               wah_file,
               first_aid_file,
               nbi_expiry_date,
               wah_expiry_date,
               first_aid_expiry_date
        FROM employees
        ORDER BY first_name, last_name, id
        """
    )
    employees = rows_to_dicts(cursor)
    total_employees = len(employees)

    expiring_certificates = 0
    missing_certificates = 0

    for emp in employees:
        emp.update(safety_summary_from_employee(emp))
        emp["full_name"] = full_employee_name(emp)

        for key in ("nbi_status", "wah_status", "first_aid_status"):
            if emp[key] == "EXPIRING SOON":
                expiring_certificates += 1
            elif emp[key] == "MISSING":
                missing_certificates += 1

    attention_rank = {"MISSING": 0, "EXPIRED": 1, "EXPIRING SOON": 2, "VALID": 3}
    attention_employees = [
        emp for emp in employees if emp["overall_safety_status"] != "VALID"
    ]
    attention_employees.sort(
        key=lambda emp: (
            attention_rank.get(emp["overall_safety_status"], 9),
            emp.get("nbi_expiry_date") or date.max,
            emp.get("wah_expiry_date") or date.max,
            emp.get("first_aid_expiry_date") or date.max,
        )
    )

    cursor.execute(
        """
        SELECT DISTINCT towerco
        FROM globe_nlz
        WHERE towerco IS NOT NULL
          AND TRIM(towerco) <> ''
        ORDER BY towerco
        """
    )
    towercos = [row[0] for row in cursor.fetchall()]

    cursor.execute(
        """
        SELECT DISTINCT cleaned_region AS region_name
        FROM (
            SELECT NULLIF(TRIM(REPLACE(region_name, CHR(160), '')), '') AS cleaned_region
            FROM (
                SELECT province AS region_name FROM globe_nlz
                UNION
                SELECT mdb_province AS region_name FROM planning_reference
                UNION
                SELECT territory AS region_name FROM planning_reference
                UNION
                SELECT region AS region_name FROM projects
            ) raw_regions
        ) regions
        WHERE cleaned_region IS NOT NULL
        ORDER BY cleaned_region
        """
    )
    regions = [row[0] for row in cursor.fetchall()]

    dashboard_site_conditions = ["TRUE"]
    dashboard_site_params = []

    if filters["project_id"]:
        dashboard_site_conditions.append(
            """
            (
                ts.project_id = %s
                OR EXISTS (
                    SELECT 1
                    FROM site_assignments sx
                    WHERE sx.du_id = d.du_id
                      AND sx.project_id = %s
                )
            )
            """
        )
        dashboard_site_params.extend([filters["project_id"], filters["project_id"]])

    if filters["towerco"]:
        dashboard_site_conditions.append("g.towerco=%s")
        dashboard_site_params.append(filters["towerco"])

    if filters["region"]:
        dashboard_site_conditions.append(
            "COALESCE(g.province, pr.mdb_province, pr.territory, '') ILIKE %s"
        )
        dashboard_site_params.append("%" + filters["region"] + "%")

    cursor.execute(
        f"""
        {SITE_REFERENCE_CTE}
        {SITE_SELECT_COLUMNS},
               COALESCE(task_counts.open_tasks, 0) AS open_tasks,
               COALESCE(task_counts.high_priority_open_tasks, 0) AS high_priority_open_tasks,
               COALESCE(permit_counts.active_permits, 0) AS active_permits,
               COALESCE(incident_counts.open_incidents, 0) AS open_incidents,
               COALESCE(punchlist_counts.open_punchlists, 0) AS open_punchlists,
               COALESCE(punchlist_counts.critical_open, 0) AS critical_punchlists,
               COALESCE(latest_pat.result, ts.pat_status, 'PENDING') AS latest_pat_result,
               COALESCE(acceptance.acceptance_status, 'NOT READY') AS acceptance_status,
               latest_daily.report_date AS latest_report_date,
               latest_daily.blocker_category AS latest_blocker_category,
               latest_daily.blockers AS latest_blockers
        {SITE_FROM_JOINS}
        LEFT JOIN (
            SELECT du_id,
                   COUNT(*) FILTER (WHERE status NOT IN ('COMPLETED','CLOSED','CANCELLED')) AS open_tasks,
                   COUNT(*) FILTER (
                       WHERE status NOT IN ('COMPLETED','CLOSED','CANCELLED')
                         AND priority IN ('HIGH','URGENT')
                   ) AS high_priority_open_tasks
            FROM telecom_tasks
            GROUP BY du_id
        ) task_counts ON task_counts.du_id = d.du_id
        LEFT JOIN (
            SELECT du_id, COUNT(*) AS active_permits
            FROM permit_to_work
            WHERE status='ACTIVE'
              AND (valid_until IS NULL OR valid_until >= CURRENT_DATE)
            GROUP BY du_id
        ) permit_counts ON permit_counts.du_id = d.du_id
        LEFT JOIN (
            SELECT du_id, COUNT(*) AS open_incidents
            FROM incident_reports
            WHERE status NOT IN ('CLOSED','RESOLVED','CANCELLED')
            GROUP BY du_id
        ) incident_counts ON incident_counts.du_id = d.du_id
        LEFT JOIN (
            SELECT duid AS du_id,
                   COUNT(*) FILTER (WHERE status <> 'CLOSED') AS open_punchlists,
                   COUNT(*) FILTER (
                       WHERE priority='CRITICAL'
                         AND status = ANY(%s)
                   ) AS critical_open
            FROM punchlist_items
            GROUP BY duid
        ) punchlist_counts ON punchlist_counts.du_id = d.du_id
        LEFT JOIN (
            SELECT DISTINCT ON (duid)
                   duid AS du_id,
                   result
            FROM pat_records
            ORDER BY duid, pat_date DESC, created_at DESC, id DESC
        ) latest_pat ON latest_pat.du_id = d.du_id
        LEFT JOIN site_acceptance acceptance ON acceptance.duid = d.du_id
        LEFT JOIN (
            SELECT DISTINCT ON (duid)
                   duid AS du_id,
                   report_date,
                   blocker_category,
                   blockers
            FROM daily_site_logs
            ORDER BY duid, report_date DESC, created_at DESC, id DESC
        ) latest_daily ON latest_daily.du_id = d.du_id
        WHERE {' AND '.join(dashboard_site_conditions)}
        ORDER BY d.du_id
        """,
        [PUNCHLIST_UNRESOLVED_STATUSES] + dashboard_site_params,
    )
    dashboard_site_rows = rows_to_dicts(cursor)

    for site in dashboard_site_rows:
        decorate_site_row(site)

    dashboard_site_duids = [
        site["du_id"] for site in dashboard_site_rows if site.get("du_id")
    ]
    dashboard_site_filter_active = bool(
        filters["project_id"] or filters["towerco"] or filters["region"]
    )
    dashboard_project_ids = {
        site.get("project_id") for site in dashboard_site_rows if site.get("project_id")
    }
    dashboard_project_scope_count = total_projects

    if dashboard_site_filter_active:
        if filters["project_id"]:
            dashboard_project_scope_count = 1 if any(
                str(project.get("id")) == filters["project_id"] for project in projects
            ) else 0
        else:
            dashboard_project_scope_count = len(dashboard_project_ids)

    dashboard_total_telecom_sites = len(dashboard_site_rows)
    dashboard_active_sites = len(
        [
            site
            for site in dashboard_site_rows
            if clean_text(site.get("overall_status")) == "Active"
        ]
    )
    dashboard_completed_sites = len(
        [
            site
            for site in dashboard_site_rows
            if clean_text(site.get("overall_status")) == "Completed"
            or clean_text(site.get("current_stage")) == "Completed"
        ]
    )
    dashboard_sites_by_stage = dashboard_distribution_from_rows(
        dashboard_site_rows, "current_stage", "Planning"
    )
    active_permits = sum(int(site.get("active_permits") or 0) for site in dashboard_site_rows)
    open_incidents = sum(int(site.get("open_incidents") or 0) for site in dashboard_site_rows)

    dashboard_safety_employees = employees

    if filters["project_id"]:
        dashboard_safety_employees = [
            emp
            for emp in dashboard_safety_employees
            if str(emp.get("project_id") or "") == filters["project_id"]
            or clean_text(emp.get("assigned_du_id")) in dashboard_site_duids
        ]
    elif filters["towerco"] or filters["region"]:
        dashboard_safety_employees = [
            emp
            for emp in dashboard_safety_employees
            if clean_text(emp.get("assigned_du_id")) in dashboard_site_duids
        ]

    dashboard_employee_count = len(dashboard_safety_employees)
    dashboard_safety_counts = {
        "VALID": 0,
        "EXPIRING SOON": 0,
        "EXPIRED": 0,
        "MISSING": 0,
    }

    for emp in dashboard_safety_employees:
        dashboard_safety_counts[emp["overall_safety_status"]] = (
            dashboard_safety_counts.get(emp["overall_safety_status"], 0) + 1
        )

    dashboard_safety_distribution = dashboard_distribution_from_counts(
        dashboard_safety_counts
    )
    dashboard_safety_compliant_workers = dashboard_safety_counts["VALID"]
    dashboard_safety_attention_workers = (
        dashboard_safety_counts["EXPIRING SOON"]
        + dashboard_safety_counts["EXPIRED"]
        + dashboard_safety_counts["MISSING"]
    )

    def dashboard_scope_conditions(alias, project_column=None, duid_column=None):

        conditions = ["TRUE"]
        params = []
        duid_column = duid_column or f"{alias}.du_id"

        if filters["towerco"] or filters["region"]:
            if dashboard_site_duids:
                conditions.append(f"{duid_column} = ANY(%s)")
                params.append(dashboard_site_duids)
            else:
                conditions.append("FALSE")
        elif filters["project_id"] and project_column:
            if dashboard_site_duids:
                conditions.append(f"({project_column}=%s OR {duid_column} = ANY(%s))")
                params.extend([filters["project_id"], dashboard_site_duids])
            else:
                conditions.append(f"{project_column}=%s")
                params.append(filters["project_id"])
        elif filters["project_id"]:
            if dashboard_site_duids:
                conditions.append(f"{duid_column} = ANY(%s)")
                params.append(dashboard_site_duids)
            else:
                conditions.append("FALSE")

        return conditions, params

    dashboard_task_conditions, dashboard_task_params = dashboard_scope_conditions(
        "t", project_column="t.project_id", duid_column="t.du_id"
    )
    cursor.execute(
        f"""
        SELECT COALESCE(NULLIF(TRIM(t.status), ''), 'Unspecified') AS label,
               COUNT(*) AS count,
               COUNT(*) FILTER (
                   WHERE t.status NOT IN ('COMPLETED','CLOSED','CANCELLED')
               ) AS open_count,
               COUNT(*) FILTER (
                   WHERE t.status NOT IN ('COMPLETED','CLOSED','CANCELLED')
                     AND t.priority IN ('HIGH','URGENT')
               ) AS high_priority_open_count
        FROM telecom_tasks t
        WHERE {' AND '.join(dashboard_task_conditions)}
        GROUP BY COALESCE(NULLIF(TRIM(t.status), ''), 'Unspecified')
        ORDER BY count DESC, label
        """,
        dashboard_task_params,
    )
    dashboard_task_status_rows = rows_to_dicts(cursor)
    dashboard_open_telecom_tasks = sum(
        int(row.get("open_count") or 0) for row in dashboard_task_status_rows
    )
    dashboard_high_priority_open_tasks = sum(
        int(row.get("high_priority_open_count") or 0)
        for row in dashboard_task_status_rows
    )
    dashboard_task_status_distribution = dashboard_add_percentages(
        [
            {
                "label": row["label"],
                "count": row["count"],
                "tone": dashboard_tone(row["label"]),
            }
            for row in dashboard_task_status_rows
        ]
    )

    cursor.execute(
        f"""
        SELECT t.id,
               t.du_id,
               t.task_type,
               t.priority,
               t.status,
               t.planned_date,
               e.first_name,
                              e.middle_name,
               e.last_name
        FROM telecom_tasks t
        LEFT JOIN employees e ON t.assigned_employee_id = e.id
        WHERE {' AND '.join(dashboard_task_conditions)}
          AND t.status NOT IN ('COMPLETED','CLOSED','CANCELLED')
        ORDER BY
            CASE t.priority
                WHEN 'URGENT' THEN 0
                WHEN 'HIGH' THEN 1
                WHEN 'MEDIUM' THEN 2
                ELSE 3
            END,
            t.planned_date ASC NULLS LAST,
            t.created_at DESC,
            t.id DESC
        LIMIT 5
        """,
        dashboard_task_params,
    )
    dashboard_priority_tasks = rows_to_dicts(cursor)

    dashboard_team_conditions, dashboard_team_params = dashboard_scope_conditions(
        "t", project_column="t.project_id", duid_column="t.du_id"
    )
    cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM teams t
        WHERE {' AND '.join(dashboard_team_conditions)}
          AND t.active IS TRUE
        """,
        dashboard_team_params,
    )
    dashboard_active_teams = cursor.fetchone()[0]

    dashboard_daily_conditions, dashboard_daily_params = dashboard_scope_conditions(
        "dsl", project_column="dsl.project_id", duid_column="dsl.duid"
    )
    cursor.execute(
        f"""
        SELECT COUNT(DISTINCT dsl.id) FILTER (
                   WHERE dsl.report_date=CURRENT_DATE
               ) AS reports_today,
               COUNT(DISTINCT dsl.duid) FILTER (
                   WHERE dsl.report_date=CURRENT_DATE
               ) AS active_sites_today,
               COUNT(da.id) FILTER (
                   WHERE dsl.report_date=CURRENT_DATE
                     AND da.attendance_status IN ('Present','Late')
               ) AS personnel_present_today,
               COUNT(DISTINCT dsl.duid) FILTER (
                   WHERE dsl.report_date=CURRENT_DATE
                     AND (
                         COALESCE(TRIM(dsl.blockers), '') <> ''
                         OR COALESCE(TRIM(dsl.blocker_category), '') <> ''
                     )
               ) AS sites_with_blockers
        FROM daily_site_logs dsl
        LEFT JOIN daily_attendance da ON da.daily_log_id = dsl.id
        WHERE {' AND '.join(dashboard_daily_conditions)}
        """,
        dashboard_daily_params,
    )
    dashboard_daily_pulse = row_to_dict(cursor)
    reports_today = dashboard_daily_pulse["reports_today"] or 0
    active_sites_today = dashboard_daily_pulse["active_sites_today"] or 0
    personnel_present_today = dashboard_daily_pulse["personnel_present_today"] or 0
    sites_with_blockers = dashboard_daily_pulse["sites_with_blockers"] or 0

    cursor.execute(
        f"""
        {SITE_REFERENCE_CTE}
        SELECT dsl.id,
               dsl.duid,
               dsl.report_date,
               dsl.current_stage,
               dsl.progress_before,
               dsl.progress_after,
               dsl.work_completed,
               dsl.blocker_category,
               dsl.blockers,
               dsl.submitted_by,
               p.project_name,
               COALESCE(g.site_name, g.sitename, g.globe_du_name, pr.planning_du_name) AS display_site_name,
               COALESCE(att.present_count, 0) AS present_count,
               COALESCE(att.total_count, 0) AS attendance_count
        FROM daily_site_logs dsl
        LEFT JOIN globe_sites g ON g.du_id = dsl.duid
        LEFT JOIN planning_sites pr ON pr.du_id = dsl.duid
        LEFT JOIN projects p ON dsl.project_id = p.id
        LEFT JOIN (
            SELECT daily_log_id,
                   COUNT(*) AS total_count,
                   COUNT(*) FILTER (WHERE attendance_status IN ('Present','Late')) AS present_count
            FROM daily_attendance
            GROUP BY daily_log_id
        ) att ON att.daily_log_id = dsl.id
        WHERE {' AND '.join(dashboard_daily_conditions)}
        ORDER BY dsl.report_date DESC, dsl.created_at DESC, dsl.id DESC
        LIMIT 6
        """,
        dashboard_daily_params,
    )
    dashboard_recent_daily_activity = rows_to_dicts(cursor)

    dashboard_sites_with_open_punchlists = len(
        [
            site
            for site in dashboard_site_rows
            if int(site.get("open_punchlists") or 0) > 0
        ]
    )
    dashboard_critical_punchlist_items = sum(
        int(site.get("critical_punchlists") or 0) for site in dashboard_site_rows
    )
    dashboard_sites_awaiting_pat = len(
        [
            site
            for site in dashboard_site_rows
            if clean_text(site.get("latest_pat_result")).upper()
            in ("", "MISSING", "PENDING")
        ]
    )
    dashboard_pat_failed = len(
        [
            site
            for site in dashboard_site_rows
            if clean_text(site.get("latest_pat_result")).upper() == "FAILED"
        ]
    )

    def dashboard_site_ready_for_acceptance(site):

        pat_result = clean_text(site.get("latest_pat_result")).upper()
        acceptance_status = clean_text(site.get("acceptance_status")).upper()
        unresolved_count = int(site.get("open_punchlists") or 0)
        critical_count = int(site.get("critical_punchlists") or 0)

        return (
            pat_result in ("PASSED", "PASSED WITH PUNCHLIST")
            and critical_count == 0
            and (unresolved_count == 0 or pat_result == "PASSED WITH PUNCHLIST")
            and acceptance_status != "ACCEPTED"
        )

    dashboard_sites_ready_for_acceptance = len(
        [
            site
            for site in dashboard_site_rows
            if dashboard_site_ready_for_acceptance(site)
        ]
    )
    dashboard_pat_distribution = dashboard_distribution_from_rows(
        dashboard_site_rows, "latest_pat_result", "PENDING"
    )
    dashboard_acceptance_distribution = dashboard_distribution_from_rows(
        dashboard_site_rows, "acceptance_status", "NOT READY"
    )

    dashboard_site_attention_items = []

    for site in dashboard_site_rows:
        reasons = []
        score = 0

        if clean_text(site.get("overall_status")) in ("Blocked", "On Hold"):
            reasons.append(f"Status: {site.get('overall_status')}")
            score = max(score, 90)

        if clean_text(site.get("latest_pat_result")).upper() == "FAILED":
            reasons.append("Latest PAT failed")
            score = max(score, 95)

        if int(site.get("critical_punchlists") or 0) > 0:
            reasons.append(f"{site.get('critical_punchlists')} critical punchlist")
            score = max(score, 100)
        elif int(site.get("open_punchlists") or 0) > 0:
            reasons.append(f"{site.get('open_punchlists')} open punchlist")
            score = max(score, 55)

        if int(site.get("open_incidents") or 0) > 0:
            reasons.append(f"{site.get('open_incidents')} open incident")
            score = max(score, 85)

        if clean_text(site.get("latest_blocker_category")) or clean_text(
            site.get("latest_blockers")
        ):
            reasons.append(site.get("latest_blocker_category") or "Latest report blocker")
            score = max(score, 80)

        pat_result = clean_text(site.get("latest_pat_result")).upper()

        if (
            pat_result in ("", "MISSING", "PENDING")
            and (
                clean_text(site.get("current_stage")) == "PAT"
                or dashboard_number(site.get("overall_progress")) >= 80
            )
        ):
            reasons.append("Awaiting PAT")
            score = max(score, 60)

        if site.get("access_validity") == "EXPIRED":
            reasons.append("Access expired")
            score = max(score, 50)

        if reasons:
            latest_report_date = site.get("latest_report_date") or date.min
            dashboard_site_attention_items.append(
                {
                    "du_id": site.get("du_id"),
                    "display_site_name": site.get("display_site_name"),
                    "project_name": site.get("project_name"),
                    "current_stage": site.get("current_stage"),
                    "overall_progress": site.get("overall_progress"),
                    "overall_status": site.get("overall_status"),
                    "reasons": reasons[:3],
                    "score": score,
                    "latest_report_date": site.get("latest_report_date"),
                    "sort_date": latest_report_date.toordinal()
                    if hasattr(latest_report_date, "toordinal")
                    else 0,
                }
            )

    dashboard_attention_site_count = len(dashboard_site_attention_items)
    dashboard_site_attention_items.sort(
        key=lambda item: (-item["score"], -item["sort_date"], item.get("du_id") or "")
    )
    dashboard_site_attention_items = dashboard_site_attention_items[:8]

    dashboard_project_progress = {}

    for site in dashboard_site_rows:
        project_id = site.get("project_id")

        if not project_id:
            continue

        entry = dashboard_project_progress.setdefault(
            project_id,
            {
                "project_id": project_id,
                "project_name": site.get("project_name") or "Unnamed project",
                "project_code": site.get("project_code"),
                "site_count": 0,
                "active_sites": 0,
                "completed_sites": 0,
                "progress_total": 0,
            },
        )
        entry["site_count"] += 1
        entry["progress_total"] += dashboard_number(site.get("overall_progress"))

        if clean_text(site.get("overall_status")) == "Active":
            entry["active_sites"] += 1

        if (
            clean_text(site.get("overall_status")) == "Completed"
            or clean_text(site.get("current_stage")) == "Completed"
        ):
            entry["completed_sites"] += 1

    dashboard_project_progress_rows = []

    for entry in dashboard_project_progress.values():
        entry["average_progress"] = (
            round(entry["progress_total"] / entry["site_count"], 1)
            if entry["site_count"]
            else 0
        )
        entry["percent"] = entry["average_progress"]
        dashboard_project_progress_rows.append(entry)

    dashboard_project_progress_rows.sort(
        key=lambda row: (-row["site_count"], row["project_name"])
    )
    dashboard_project_progress_rows = dashboard_project_progress_rows[:6]

    cursor.close()
    conn.close()

    dashboard_scope_text = "current scope" if dashboard_site_filter_active else "portfolio"
    dashboard_projects_context = (
        "Projects represented in current scope"
        if dashboard_site_filter_active
        else "Current project portfolio"
    )
    dashboard_sites_context = (
        f"{dashboard_count_label(dashboard_active_sites, 'active site')}, "
        f"{dashboard_count_label(dashboard_completed_sites, 'completed site')}"
    )
    dashboard_employees_context = (
        "Registered personnel in current scope"
        if dashboard_site_filter_active
        else "Registered personnel"
    )
    dashboard_tasks_context = (
        "No open telecom tasks"
        if not dashboard_open_telecom_tasks
        else (
            f"{dashboard_high_priority_open_tasks} high priority or urgent"
            if dashboard_high_priority_open_tasks
            else f"Open telecom work in {dashboard_scope_text}"
        )
    )
    dashboard_pat_context = (
        "No pending PAT"
        if not dashboard_sites_awaiting_pat
        else (
            f"{dashboard_pat_failed} latest PAT failed"
            if dashboard_pat_failed
            else "Awaiting PAT status"
        )
    )

    dashboard_kpi_cards = [
        {
            "title": "Projects",
            "value": dashboard_project_scope_count,
            "context": dashboard_projects_context,
            "icon": "PR",
            "url": url_for("projects_workspace") if can("manage_projects") else None,
            "tone": "primary",
        },
        {
            "title": "Telecom Sites",
            "value": dashboard_total_telecom_sites,
            "context": dashboard_sites_context,
            "icon": "ST",
            "url": url_for("sites"),
            "tone": "primary",
        },
        {
            "title": "Employees",
            "value": dashboard_employee_count,
            "context": dashboard_employees_context,
            "icon": "EM",
            "url": url_for("search"),
            "tone": "primary",
        },
        {
            "title": "Active Teams",
            "value": dashboard_active_teams,
            "context": "No active field teams"
            if not dashboard_active_teams
            else f"Field teams in {dashboard_scope_text}",
            "icon": "TM",
            "url": url_for("teams") if can("manage_teams") else None,
            "tone": "success",
        },
        {
            "title": "Open Tasks",
            "value": dashboard_open_telecom_tasks,
            "context": dashboard_tasks_context,
            "icon": "TK",
            "url": url_for("telecom_tasks") if can("manage_tasks") else None,
            "tone": "warning" if dashboard_open_telecom_tasks else "success",
        },
        {
            "title": "Safety Alerts",
            "value": dashboard_safety_attention_workers,
            "context": "No safety alerts"
            if not dashboard_safety_attention_workers
            else "Workers need compliance attention",
            "icon": "SF",
            "url": url_for("safety_compliance"),
            "tone": "danger" if dashboard_safety_attention_workers else "success",
        },
        {
            "title": "Pending PAT",
            "value": dashboard_sites_awaiting_pat,
            "context": dashboard_pat_context,
            "icon": "PA",
            "url": url_for("pat_history") if can("view_pat") or can("manage_pat") else None,
            "tone": "warning" if dashboard_sites_awaiting_pat else "success",
        },
        {
            "title": "Ready Acceptance",
            "value": dashboard_sites_ready_for_acceptance,
            "context": "No sites ready"
            if not dashboard_sites_ready_for_acceptance
            else "Ready for handover review",
            "icon": "HO",
            "url": url_for("pat_acceptance_report") if can("export_reports") else None,
            "tone": "success",
        },
    ]

    dashboard_hr_kpi_cards = [
        {
            "title": "Employees",
            "value": total_employees,
            "context": "Registered personnel",
            "icon": "EM",
            "url": url_for("search"),
            "tone": "primary",
        },
        {
            "title": "Safety Cleared",
            "value": dashboard_safety_compliant_workers,
            "context": "Fully valid safety records",
            "icon": "OK",
            "url": url_for("safety_compliance"),
            "tone": "success",
        },
        {
            "title": "Safety Alerts",
            "value": dashboard_safety_attention_workers,
            "context": "No HR safety alerts"
            if not dashboard_safety_attention_workers
            else "Workers requiring HR attention",
            "icon": "SF",
            "url": url_for("safety_compliance"),
            "tone": "danger" if dashboard_safety_attention_workers else "success",
        },
        {
            "title": "Expiring Certs",
            "value": expiring_certificates,
            "context": f"Within {EXPIRING_SOON_DAYS} days",
            "icon": "EX",
            "url": url_for("safety_documents") if can("manage_safety") else None,
            "tone": "warning" if expiring_certificates else "success",
        },
        {
            "title": "Missing Certs",
            "value": missing_certificates,
            "context": "Required safety documents missing",
            "icon": "MS",
            "url": url_for("safety_documents") if can("manage_safety") else None,
            "tone": "danger" if missing_certificates else "success",
        },
    ]

    dashboard_operational_pulse = [
        {
            "label": "Reports Today",
            "value": reports_today,
            "detail": f"{active_sites_today} sites updated today",
        },
        {
            "label": "Present Today",
            "value": personnel_present_today,
            "detail": "Present or late attendance entries",
        },
        {
            "label": "Blockers Today",
            "value": sites_with_blockers,
            "detail": "Daily reports with blockers",
        },
        {
            "label": "Open Incidents",
            "value": open_incidents,
            "detail": "Not closed or resolved",
        },
        {
            "label": "Active Permits",
            "value": active_permits,
            "detail": "Currently valid permit records",
        },
        {
            "label": "Open Punchlists",
            "value": dashboard_sites_with_open_punchlists,
            "detail": f"{dashboard_critical_punchlist_items} critical items",
        },
    ]

    return render_template(
        "dashboard.html",
        dashboard_view="super_admin" if is_super_admin_role() else "hr",
        master_tracker_status=get_master_tracker_status() if is_super_admin_role() else None,
        towercos=towercos,
        regions=regions,
        dashboard_site_filter_active=dashboard_site_filter_active,
        dashboard_kpi_cards=dashboard_kpi_cards,
        dashboard_hr_kpi_cards=dashboard_hr_kpi_cards,
        dashboard_operational_pulse=dashboard_operational_pulse,
        dashboard_total_telecom_sites=dashboard_total_telecom_sites,
        dashboard_attention_site_count=dashboard_attention_site_count,
        dashboard_sites_by_stage=dashboard_sites_by_stage,
        dashboard_safety_distribution=dashboard_safety_distribution,
        dashboard_safety_attention_workers=dashboard_safety_attention_workers,
        dashboard_open_telecom_tasks=dashboard_open_telecom_tasks,
        dashboard_high_priority_open_tasks=dashboard_high_priority_open_tasks,
        dashboard_task_status_distribution=dashboard_task_status_distribution,
        dashboard_priority_tasks=dashboard_priority_tasks,
        dashboard_pat_distribution=dashboard_pat_distribution,
        dashboard_acceptance_distribution=dashboard_acceptance_distribution,
        dashboard_sites_ready_for_acceptance=dashboard_sites_ready_for_acceptance,
        dashboard_site_attention_items=dashboard_site_attention_items,
        dashboard_recent_daily_activity=dashboard_recent_daily_activity,
        dashboard_project_progress_rows=dashboard_project_progress_rows,
        projects=projects,
        filters=filters,
        attention_employees=attention_employees[:10],
    )


@app.route("/projects")
@permission_required("manage_projects")
def projects_workspace():

    filters = {"search": clean_text(request.args.get("search"))}
    conditions = ["TRUE"]
    params = []

    if filters["search"]:
        conditions.append(
            """
            (
                p.project_code ILIKE %s
                OR p.project_name ILIKE %s
                OR p.company ILIKE %s
                OR p.region ILIKE %s
            )
            """
        )
        search_term = f"%{filters['search']}%"
        params.extend([search_term, search_term, search_term, search_term])

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT p.id,
               p.project_code,
               p.project_name,
               p.company,
               p.region,
               p.date_created,
               COUNT(DISTINCT e.id) AS employee_count,
               COUNT(DISTINCT ts.id) AS site_count,
               COALESCE(ROUND(AVG(ts.overall_progress))::int, 0) AS average_progress
        FROM projects p
        LEFT JOIN employees e ON e.project_id = p.id
        LEFT JOIN telecom_sites ts ON ts.project_id = p.id
        WHERE {' AND '.join(conditions)}
        GROUP BY p.id, p.project_code, p.project_name, p.company, p.region, p.date_created
        ORDER BY p.date_created DESC, p.id DESC
        """,
        params,
    )
    project_rows = rows_to_dicts(cursor)

    cursor.execute("SELECT COUNT(*) FROM projects")
    total_projects = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM employees")
    total_employees = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM telecom_sites")
    total_sites = cursor.fetchone()[0]

    cursor.close()
    conn.close()

    return render_template(
        "projects.html",
        projects=project_rows,
        filters=filters,
        summary={
            "total_projects": total_projects,
            "total_employees": total_employees,
            "total_sites": total_sites,
        },
    )


#############################################
# PHASE 7 USER MANAGEMENT AND AUDIT LOGS
#############################################


def count_active_super_admins(cursor, exclude_admin_id=None):

    if exclude_admin_id:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM admins
            WHERE role='super_admin'
              AND active IS TRUE
              AND id<>%s
            """,
            (exclude_admin_id,),
        )
    else:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM admins
            WHERE role='super_admin'
              AND active IS TRUE
            """
        )

    return cursor.fetchone()[0]


def is_last_active_super_admin(cursor, user):

    return (
        bool(user)
        and user.get("role") == "super_admin"
        and bool(user.get("active"))
        and count_active_super_admins(cursor, exclude_admin_id=user.get("id")) == 0
    )


def audit_last_super_admin_denied(cursor, conn, user, attempted_action):

    audit_event(
        "USER_SUPER_ADMIN_PROTECTION_DENIED",
        "admin",
        user.get("id") if user else None,
        f"Blocked {attempted_action} for the last active Super Admin.",
        conn=conn,
    )
    conn.commit()


def get_admin_account(cursor, admin_id):

    cursor.execute(
        """
        SELECT a.id,
               a.username,
               a.role,
               a.active,
               a.created_at,
               a.updated_at,
               a.last_login,
               a.employee_id,
               a.created_by_admin_id,
               a.activated_by_admin_id,
               a.deactivated_by_admin_id,
               a.status_changed_at,
               e.first_name,
               e.middle_name,
               e.last_name
        FROM admins a
        LEFT JOIN employees e ON a.employee_id = e.id
        WHERE a.id=%s
        """,
        (admin_id,),
    )
    return row_to_dict(cursor)


def account_display_name(account):

    employee_name = full_employee_name(account) if account else ""
    return employee_name or clean_text(account.get("username") if account else "")


def get_employees_for_account_select(exclude_admin_id=None):

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT e.id,
               e.first_name,
               e.middle_name,
               e.last_name,
               e.position,
               e.telecom_role,
               e.assigned_du_id,
               p.project_name,
               p.project_code,
               active_account.id AS active_account_id,
               active_account.username AS active_account_username
        FROM employees e
        LEFT JOIN projects p ON e.project_id = p.id
        LEFT JOIN admins active_account
          ON active_account.employee_id = e.id
         AND active_account.active IS TRUE
         AND (%s IS NULL OR active_account.id <> %s)
        ORDER BY e.first_name, e.middle_name, e.last_name, e.id
        """,
        (exclude_admin_id, exclude_admin_id),
    )
    employees = rows_to_dicts(cursor)
    cursor.close()
    conn.close()
    return employees


def active_account_for_employee(cursor, employee_id, exclude_admin_id=None):

    if not employee_id:
        return None

    conditions = ["employee_id=%s", "active IS TRUE"]
    params = [employee_id]

    if exclude_admin_id:
        conditions.append("id<>%s")
        params.append(exclude_admin_id)

    cursor.execute(
        f"""
        SELECT id, username, role, active, employee_id
        FROM admins
        WHERE {' AND '.join(conditions)}
        ORDER BY id
        LIMIT 1
        """,
        params,
    )
    return row_to_dict(cursor)


def validate_admin_employee_link(cursor, role, active, employee_id, exclude_admin_id=None):

    normalized_role = effective_role(role)

    if normalized_role == "team_leader" and not employee_id:
        return "Team Leader login accounts must be linked to an existing registered employee."

    if not employee_id:
        return None

    if not str(employee_id).isdigit():
        return "Linked employee not found."

    cursor.execute(
        """
        SELECT id
        FROM employees
        WHERE id=%s
        """,
        (employee_id,),
    )

    if not cursor.fetchone():
        return "Linked employee not found."

    if active:
        linked_account = active_account_for_employee(
            cursor, employee_id, exclude_admin_id=exclude_admin_id
        )
        if linked_account:
            return (
                "That employee is already linked to active account "
                f"{linked_account['username']}. Deactivate or unlink that account first."
            )

    return None


def audit_user_employee_link_denied(cursor, conn, target_admin_id, description):

    audit_event(
        "USER_EMPLOYEE_LINK_DENIED",
        "admin",
        target_admin_id,
        description,
        conn=conn,
    )
    conn.commit()


def can_hr_manage_team_leader_account(account):

    return (
        is_hr_role()
        and account
        and effective_role(account.get("role")) == "team_leader"
        and (
            account.get("created_by_admin_id") == session.get("admin_id")
            or account.get("activated_by_admin_id") == session.get("admin_id")
        )
    )


def can_current_user_manage_account(account):

    if is_super_admin_role():
        return True

    return can_hr_manage_team_leader_account(account)


def audit_hr_account_action_denied(cursor, conn, account, action):

    audit_event(
        "USER_HR_ACCOUNT_ACTION_DENIED",
        "admin",
        account.get("id") if account else None,
        f"Blocked HR {action}; target account is outside HR account authority.",
        conn=conn,
    )
    conn.commit()


def available_user_roles():

    if is_super_admin_role():
        return ROLE_FORM_LABELS

    if has_permission(session.get("role"), "manage_team_leader_accounts"):
        return {"team_leader": ROLE_FORM_LABELS["team_leader"]}

    return {}


@app.route("/profile")
@login_required
def profile():

    conn = connect_db()
    cursor = conn.cursor()
    user = get_admin_account(cursor, session.get("admin_id"))
    cursor.close()
    conn.close()

    if not user:
        flash("Profile not found.")
        return redirect(url_for("dashboard"))

    return render_template(
        "profile.html",
        user=user,
        display_name=account_display_name(user),
    )


@app.route("/users")
@any_permission_required("manage_users", "manage_team_leader_accounts")
def users():

    conn = connect_db()
    cursor = conn.cursor()

    if is_super_admin_role():
        cursor.execute(
            """
            SELECT a.id,
                   a.username,
                   a.role,
                   a.active,
                   a.created_at,
                   a.updated_at,
                   a.last_login,
                   a.employee_id,
                   a.created_by_admin_id,
                   a.activated_by_admin_id,
                   e.first_name,
                   e.middle_name,
                   e.last_name
            FROM admins a
            LEFT JOIN employees e ON a.employee_id = e.id
            ORDER BY a.created_at DESC, a.id DESC
            """
        )
    else:
        cursor.execute(
            """
            SELECT a.id,
                   a.username,
                   a.role,
                   a.active,
                   a.created_at,
                   a.updated_at,
                   a.last_login,
                   a.employee_id,
                   a.created_by_admin_id,
                   a.activated_by_admin_id,
                   e.first_name,
                   e.middle_name,
                   e.last_name
            FROM admins a
            LEFT JOIN employees e ON a.employee_id = e.id
            WHERE a.role='team_leader'
              AND (
                  a.created_by_admin_id=%s
                  OR a.activated_by_admin_id=%s
              )
            ORDER BY a.created_at DESC, a.id DESC
            """,
            (session.get("admin_id"), session.get("admin_id")),
        )
    admin_users = rows_to_dicts(cursor)
    active_super_admin_count = count_active_super_admins(cursor)

    for admin_user in admin_users:
        admin_user["display_name"] = account_display_name(admin_user)
        admin_user["linked_employee_name"] = (
            full_employee_name(admin_user) if admin_user.get("employee_id") else ""
        )
        admin_user["effective_role"] = effective_role(admin_user.get("role"))
        admin_user["can_manage"] = can_current_user_manage_account(admin_user)
        admin_user["is_last_active_super_admin"] = (
            admin_user.get("role") == "super_admin"
            and bool(admin_user.get("active"))
            and active_super_admin_count == 1
        )

    cursor.close()
    conn.close()

    return render_template("users.html", users=admin_users, can_create_user=bool(available_user_roles()))


@app.route("/users/new", methods=["GET", "POST"])
@any_permission_required("manage_users", "manage_team_leader_accounts")
def new_user():

    roles = available_user_roles()

    if request.method == "POST":
        username = clean_text(request.form.get("username"))
        password = request.form.get("password", "")
        role = clean_text(request.form.get("role"))
        active = request.form.get("active") == "on"
        employee_id = clean_text(request.form.get("employee_id"))

        if not username or not password or role not in roles:
            flash("Username, password, and a valid role are required.")
            return render_template(
                "user_form.html",
                user=None,
                roles=roles,
                employees=get_employees_for_account_select(),
            )

        conn = connect_db()
        cursor = conn.cursor()

        link_error = validate_admin_employee_link(cursor, role, active, employee_id)
        if link_error:
            audit_user_employee_link_denied(cursor, conn, None, link_error)
            cursor.close()
            conn.close()
            flash(link_error)
            return render_template(
                "user_form.html",
                user=None,
                roles=roles,
                employees=get_employees_for_account_select(),
            )

        try:
            cursor.execute(
                """
                INSERT INTO admins (
                    username,
                    password,
                    role,
                    active,
                    employee_id,
                    created_by_admin_id,
                    activated_by_admin_id,
                    status_changed_at,
                    created_at,
                    updated_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                RETURNING id
                """,
                (
                    username,
                    generate_password_hash(password),
                    role,
                    active,
                    employee_id or None,
                    session.get("admin_id"),
                    session.get("admin_id") if active else None,
                    datetime.now() if active else None,
                ),
            )
            new_admin_id = cursor.fetchone()[0]
            audit_event(
                "TEAM_LEADER_ACCOUNT_CREATED" if role == "team_leader" else "USER_CREATED",
                "admin",
                new_admin_id,
                (
                    f"Created user {username} with role {role_label(role)}; "
                    f"employee_id={employee_id or 'none'}."
                ),
                conn=conn,
            )
            conn.commit()
            flash("User account created.")
            return redirect(url_for("users"))

        except psycopg2.IntegrityError:
            conn.rollback()
            flash("Username already exists.")

        finally:
            cursor.close()
            conn.close()

    return render_template(
        "user_form.html",
        user=None,
        roles=roles,
        employees=get_employees_for_account_select(),
    )


@app.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@any_permission_required("manage_users", "manage_team_leader_accounts")
def edit_user(user_id):

    conn = connect_db()
    cursor = conn.cursor()
    user = get_admin_account(cursor, user_id)

    if not user:
        cursor.close()
        conn.close()
        flash("User account not found.")
        return redirect(url_for("users"))

    if not can_current_user_manage_account(user):
        audit_hr_account_action_denied(cursor, conn, user, "edit")
        cursor.close()
        conn.close()
        flash("You can only manage Team Leader accounts you created or activated.")
        return redirect(url_for("users"))

    roles = available_user_roles()
    if user.get("role") not in roles:
        roles = {user.get("role"): role_label(user.get("role")), **roles}

    if request.method == "POST":
        role = (
            clean_text(request.form.get("role"))
            if is_super_admin_role()
            else user.get("role")
        )
        active = request.form.get("active") == "on"
        employee_id = clean_text(request.form.get("employee_id"))
        last_active_super_admin = is_last_active_super_admin(cursor, user)

        if role not in roles and role != user.get("role"):
            flash("Invalid role selected.")
            cursor.close()
            conn.close()
            return render_template(
                "user_form.html",
                user=user,
                roles=roles,
                employees=get_employees_for_account_select(exclude_admin_id=user_id),
                last_active_super_admin=last_active_super_admin,
            )

        if last_active_super_admin and (role != "super_admin" or not active):
            audit_last_super_admin_denied(cursor, conn, user, "role/status change")
            flash(LAST_SUPER_ADMIN_MESSAGE)
            cursor.close()
            conn.close()
            return render_template(
                "user_form.html",
                user=user,
                roles=roles,
                employees=get_employees_for_account_select(exclude_admin_id=user_id),
                last_active_super_admin=True,
            )

        link_error = validate_admin_employee_link(
            cursor,
            role,
            active,
            employee_id,
            exclude_admin_id=user_id,
        )
        if link_error:
            audit_user_employee_link_denied(cursor, conn, user_id, link_error)
            cursor.close()
            conn.close()
            flash(link_error)
            return render_template(
                "user_form.html",
                user=user,
                roles=roles,
                employees=get_employees_for_account_select(exclude_admin_id=user_id),
                last_active_super_admin=last_active_super_admin,
            )

        activated_by_admin_id = user.get("activated_by_admin_id")
        deactivated_by_admin_id = user.get("deactivated_by_admin_id")
        status_changed_at = user.get("status_changed_at")

        if bool(active) != bool(user.get("active")):
            status_changed_at = datetime.now()
            if active:
                activated_by_admin_id = session.get("admin_id")
                deactivated_by_admin_id = None
            else:
                deactivated_by_admin_id = session.get("admin_id")

        cursor.execute(
            """
            UPDATE admins
            SET role=%s,
                active=%s,
                employee_id=%s,
                activated_by_admin_id=%s,
                deactivated_by_admin_id=%s,
                status_changed_at=%s,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=%s
            """,
            (
                role,
                active,
                employee_id or None,
                activated_by_admin_id,
                deactivated_by_admin_id,
                status_changed_at,
                user_id,
            ),
        )
        audit_event(
            "USER_UPDATED",
            "admin",
            user_id,
            (
                f"Updated user {user['username']} to role {role_label(role)}; "
                f"active={active}; employee_id {user.get('employee_id') or 'none'} "
                f"-> {employee_id or 'none'}."
            ),
            conn=conn,
        )
        conn.commit()
        cursor.close()
        conn.close()
        flash("User account updated.")
        return redirect(url_for("users"))

    last_active_super_admin = is_last_active_super_admin(cursor, user)
    cursor.close()
    conn.close()
    return render_template(
        "user_form.html",
        user=user,
        roles=roles,
        employees=get_employees_for_account_select(exclude_admin_id=user_id),
        last_active_super_admin=last_active_super_admin,
    )


@app.route("/users/<int:user_id>/password", methods=["GET", "POST"])
@any_permission_required("manage_users", "manage_team_leader_accounts")
def reset_user_password(user_id):

    conn = connect_db()
    cursor = conn.cursor()
    user = get_admin_account(cursor, user_id)

    if not user:
        cursor.close()
        conn.close()
        flash("User account not found.")
        return redirect(url_for("users"))

    if not can_current_user_manage_account(user):
        audit_hr_account_action_denied(cursor, conn, user, "password reset")
        cursor.close()
        conn.close()
        flash("You can only manage Team Leader accounts you created or activated.")
        return redirect(url_for("users"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not password or password != confirm_password:
            flash("Password and confirmation must match.")
            cursor.close()
            conn.close()
            return render_template("user_password.html", user=user)

        cursor.execute(
            """
            UPDATE admins
            SET password=%s,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=%s
            """,
            (generate_password_hash(password), user_id),
        )
        audit_event(
            "USER_PASSWORD_RESET",
            "admin",
            user_id,
            f"Password reset for user {user['username']}.",
            conn=conn,
        )
        conn.commit()
        cursor.close()
        conn.close()
        flash("Password updated.")
        return redirect(url_for("users"))

    cursor.close()
    conn.close()
    return render_template("user_password.html", user=user)


@app.route("/users/<int:user_id>/toggle", methods=["POST"])
@any_permission_required("manage_users", "manage_team_leader_accounts")
def toggle_user(user_id):

    conn = connect_db()
    cursor = conn.cursor()
    user = get_admin_account(cursor, user_id)

    if not user:
        cursor.close()
        conn.close()
        flash("User account not found.")
        return redirect(url_for("users"))

    if not can_current_user_manage_account(user):
        audit_hr_account_action_denied(cursor, conn, user, "status change")
        cursor.close()
        conn.close()
        flash("You can only activate or deactivate Team Leader accounts you created or activated.")
        return redirect(url_for("users"))

    new_active = not user["active"]

    if user["role"] == "super_admin" and user["active"] and not new_active:
        if is_last_active_super_admin(cursor, user):
            audit_last_super_admin_denied(cursor, conn, user, "deactivation")
            cursor.close()
            conn.close()
            flash(LAST_SUPER_ADMIN_MESSAGE)
            return redirect(url_for("users"))

    link_error = validate_admin_employee_link(
        cursor,
        user.get("role"),
        new_active,
        user.get("employee_id"),
        exclude_admin_id=user_id,
    )
    if link_error:
        audit_user_employee_link_denied(cursor, conn, user_id, link_error)
        cursor.close()
        conn.close()
        flash(link_error)
        return redirect(url_for("users"))

    cursor.execute(
        """
        UPDATE admins
        SET active=%s,
            activated_by_admin_id=CASE WHEN %s THEN %s ELSE activated_by_admin_id END,
            deactivated_by_admin_id=CASE WHEN %s THEN NULL ELSE %s END,
            status_changed_at=CURRENT_TIMESTAMP,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=%s
        """,
        (
            new_active,
            new_active,
            session.get("admin_id"),
            new_active,
            session.get("admin_id"),
            user_id,
        ),
    )
    audit_event(
        "USER_ACTIVATED" if new_active else "USER_DEACTIVATED",
        "admin",
        user_id,
        f"Set user {user['username']} active={new_active}.",
        conn=conn,
    )
    conn.commit()
    cursor.close()
    conn.close()

    flash("User account status updated.")
    return redirect(url_for("users"))


#############################################
# PHASE 7 TEAM MANAGEMENT
#############################################


def get_team_leader_accounts_for_select():

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT a.id,
               a.username,
               a.employee_id,
               e.first_name,
                              e.middle_name,
               e.last_name
        FROM admins a
        LEFT JOIN employees e ON a.employee_id = e.id
        WHERE a.role='team_leader'
          AND a.active IS TRUE
        ORDER BY e.first_name NULLS LAST, e.last_name NULLS LAST, a.username
        """
    )
    accounts = rows_to_dicts(cursor)
    cursor.close()
    conn.close()

    for account in accounts:
        account["display_name"] = account_display_name(account)

    return accounts


def get_team_detail(cursor, team_id):

    cursor.execute(
        """
        SELECT t.id,
               t.name,
               t.project_id,
               t.du_id,
               t.team_leader_admin_id,
               t.team_leader_employee_id,
               t.active,
               t.created_by_admin_id,
               t.created_at,
               t.updated_at,
               p.project_name,
               p.project_code,
               a.username AS team_leader_username,
               ae.first_name AS leader_first_name,
               ae.middle_name AS leader_middle_name,
               ae.last_name AS leader_last_name
        FROM teams t
        LEFT JOIN projects p ON t.project_id = p.id
        LEFT JOIN admins a ON t.team_leader_admin_id = a.id
        LEFT JOIN employees ae ON COALESCE(t.team_leader_employee_id, a.employee_id) = ae.id
        WHERE t.id=%s
        """,
        (team_id,),
    )
    team = row_to_dict(cursor)

    if team:
        team["leader_name"] = full_employee_name(
            {
                "first_name": team.get("leader_first_name"),
                "middle_name": team.get("leader_middle_name"),
                "last_name": team.get("leader_last_name"),
            }
        ) or team.get("team_leader_username")

    return team


def collect_team_form_data(cursor):

    name = clean_text(request.form.get("name"))
    project_id = clean_text(request.form.get("project_id"))
    du_id = clean_text(request.form.get("du_id"))
    team_leader_admin_id = clean_text(request.form.get("team_leader_admin_id"))
    team_leader_employee_id = clean_text(request.form.get("team_leader_employee_id"))
    active = request.form.get("active") == "on"

    if not name:
        raise ValueError("Team name is required.")

    if project_id:
        cursor.execute("SELECT 1 FROM projects WHERE id=%s", (project_id,))
        if not cursor.fetchone():
            raise ValueError("Invalid project.")

    if du_id and not duid_exists(cursor, du_id):
        raise ValueError("Invalid DUID.")

    if team_leader_admin_id:
        cursor.execute(
            """
            SELECT employee_id
            FROM admins
            WHERE id=%s
              AND role='team_leader'
              AND active IS TRUE
            """,
            (team_leader_admin_id,),
        )
        account = row_to_dict(cursor)

        if not account:
            raise ValueError("Invalid Team Leader account.")

        team_leader_employee_id = team_leader_employee_id or account.get("employee_id")

    if team_leader_employee_id:
        cursor.execute("SELECT 1 FROM employees WHERE id=%s", (team_leader_employee_id,))
        if not cursor.fetchone():
            raise ValueError("Invalid Team Leader employee record.")

    return {
        "name": name,
        "project_id": project_id or None,
        "du_id": du_id or None,
        "team_leader_admin_id": team_leader_admin_id or None,
        "team_leader_employee_id": team_leader_employee_id or None,
        "active": active,
    }


@app.route("/teams")
@permission_required("manage_teams")
def teams():

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT t.id,
               t.name,
               t.project_id,
               t.du_id,
               t.active,
               t.created_at,
               t.updated_at,
               p.project_name,
               p.project_code,
               a.username AS team_leader_username,
               ae.first_name AS leader_first_name,
               ae.middle_name AS leader_middle_name,
               ae.last_name AS leader_last_name,
               COUNT(tm.id) FILTER (WHERE tm.active IS TRUE) AS active_members,
               COUNT(tm.id) AS historical_memberships
        FROM teams t
        LEFT JOIN projects p ON t.project_id = p.id
        LEFT JOIN admins a ON t.team_leader_admin_id = a.id
        LEFT JOIN employees ae ON COALESCE(t.team_leader_employee_id, a.employee_id) = ae.id
        LEFT JOIN team_memberships tm ON tm.team_id = t.id
        GROUP BY t.id, p.project_name, p.project_code, a.username, ae.first_name, ae.middle_name, ae.last_name
        ORDER BY t.active DESC, t.updated_at DESC, t.id DESC
        """
    )
    team_rows = rows_to_dicts(cursor)
    cursor.close()
    conn.close()

    for team in team_rows:
        team["leader_name"] = full_employee_name(
            {
                "first_name": team.get("leader_first_name"),
                "middle_name": team.get("leader_middle_name"),
                "last_name": team.get("leader_last_name"),
            }
        ) or team.get("team_leader_username")

    return render_template("teams.html", teams=team_rows)


@app.route("/teams/new", methods=["GET", "POST"])
@permission_required("manage_teams")
def new_team():

    if request.method == "POST":
        conn = connect_db()
        cursor = conn.cursor()

        try:
            team_data = collect_team_form_data(cursor)
            cursor.execute(
                """
                INSERT INTO teams (
                    name,
                    project_id,
                    du_id,
                    team_leader_admin_id,
                    team_leader_employee_id,
                    active,
                    created_by_admin_id,
                    updated_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
                RETURNING id
                """,
                (
                    team_data["name"],
                    team_data["project_id"],
                    team_data["du_id"],
                    team_data["team_leader_admin_id"],
                    team_data["team_leader_employee_id"],
                    team_data["active"],
                    session.get("admin_id"),
                ),
            )
            team_id = cursor.fetchone()[0]
            audit_event(
                "TEAM_CREATED",
                "team",
                team_id,
                f"Created team {team_data['name']} for DUID {team_data['du_id'] or '-'}."
                ,
                conn=conn,
            )
            conn.commit()
            cursor.close()
            conn.close()
            flash("Team created.")
            return redirect(url_for("team_detail", team_id=team_id))
        except (ValueError, psycopg2.Error) as exc:
            conn.rollback()
            cursor.close()
            conn.close()
            flash(str(exc))

    return render_template(
        "team_form.html",
        team=None,
        projects=get_projects_for_select(),
        duids=get_duids_for_select(),
        employees=get_employees_for_select(),
        team_leaders=get_team_leader_accounts_for_select(),
    )


@app.route("/teams/<int:team_id>")
@permission_required("manage_teams")
def team_detail(team_id):

    conn = connect_db()
    cursor = conn.cursor()
    team = get_team_detail(cursor, team_id)

    if not team:
        cursor.close()
        conn.close()
        return "Team not found"

    cursor.execute(
        """
        SELECT tm.id,
               tm.employee_id,
               tm.start_at,
               tm.end_at,
               tm.active,
               tm.assigned_by_admin_id,
               e.first_name,
                              e.middle_name,
               e.last_name,
               e.position,
               e.telecom_role,
               e.assigned_du_id,
               p.project_name,
               p.project_code
        FROM team_memberships tm
        JOIN employees e ON e.id = tm.employee_id
        LEFT JOIN projects p ON e.project_id = p.id
        WHERE tm.team_id=%s
        ORDER BY tm.active DESC, tm.start_at DESC, tm.id DESC
        """,
        (team_id,),
    )
    memberships = rows_to_dicts(cursor)

    for membership in memberships:
        membership["full_name"] = full_employee_name(membership)

    cursor.close()
    conn.close()

    return render_template(
        "team_detail.html",
        team=team,
        memberships=memberships,
        employees=get_employees_for_select(),
    )


@app.route("/teams/<int:team_id>/edit", methods=["GET", "POST"])
@permission_required("manage_teams")
def edit_team(team_id):

    conn = connect_db()
    cursor = conn.cursor()
    team = get_team_detail(cursor, team_id)

    if not team:
        cursor.close()
        conn.close()
        return "Team not found"

    if request.method == "POST":
        try:
            team_data = collect_team_form_data(cursor)
            cursor.execute(
                """
                UPDATE teams
                SET name=%s,
                    project_id=%s,
                    du_id=%s,
                    team_leader_admin_id=%s,
                    team_leader_employee_id=%s,
                    active=%s,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=%s
                """,
                (
                    team_data["name"],
                    team_data["project_id"],
                    team_data["du_id"],
                    team_data["team_leader_admin_id"],
                    team_data["team_leader_employee_id"],
                    team_data["active"],
                    team_id,
                ),
            )
            audit_event(
                "TEAM_UPDATED",
                "team",
                team_id,
                f"Updated team {team_data['name']} for DUID {team_data['du_id'] or '-'}."
                ,
                conn=conn,
            )
            conn.commit()
            cursor.close()
            conn.close()
            flash("Team updated.")
            return redirect(url_for("team_detail", team_id=team_id))
        except (ValueError, psycopg2.Error) as exc:
            conn.rollback()
            cursor.close()
            conn.close()
            flash(str(exc))
            return redirect(url_for("edit_team", team_id=team_id))

    cursor.close()
    conn.close()

    return render_template(
        "team_form.html",
        team=team,
        projects=get_projects_for_select(),
        duids=get_duids_for_select(),
        employees=get_employees_for_select(),
        team_leaders=get_team_leader_accounts_for_select(),
    )


@app.route("/teams/<int:team_id>/members/add", methods=["POST"])
@permission_required("transfer_team_members")
def add_team_member(team_id):

    employee_id = clean_text(request.form.get("employee_id"))

    conn = connect_db()
    cursor = conn.cursor()
    team = get_team_detail(cursor, team_id)

    if not team:
        cursor.close()
        conn.close()
        return "Team not found"

    employee = get_employee_detail(cursor, employee_id)

    if not employee:
        cursor.close()
        conn.close()
        return "Employee not found"

    cursor.execute(
        """
        SELECT tm.id,
               tm.team_id,
               t.name
        FROM team_memberships tm
        JOIN teams t ON t.id = tm.team_id
        WHERE tm.employee_id=%s
          AND tm.active IS TRUE
        LIMIT 1
        """,
        (employee_id,),
    )
    existing_membership = row_to_dict(cursor)
    was_transfer = bool(existing_membership and existing_membership["team_id"] != team_id)

    if existing_membership and existing_membership["team_id"] == team_id:
        cursor.close()
        conn.close()
        flash("This employee is already an active member of this team.")
        return redirect(url_for("team_detail", team_id=team_id))

    try:
        cursor.execute(
            """
            UPDATE team_memberships
            SET active=FALSE,
                end_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            WHERE employee_id=%s
              AND active IS TRUE
            """,
            (employee_id,),
        )
        cursor.execute(
            """
            INSERT INTO team_memberships (
                team_id,
                employee_id,
                start_at,
                active,
                assigned_by_admin_id,
                updated_at
            )
            VALUES (%s,%s,CURRENT_TIMESTAMP,TRUE,%s,CURRENT_TIMESTAMP)
            """,
            (team_id, employee_id, session.get("admin_id")),
        )

        if team.get("du_id"):
            cursor.execute(
                """
                UPDATE employees
                SET assigned_du_id=%s
                WHERE id=%s
                """,
                (team["du_id"], employee_id),
            )
            sync_site_assignment(
                cursor,
                team.get("project_id") or employee.get("project_id"),
                employee_id,
                team["du_id"],
                employee.get("telecom_role") or employee.get("position"),
            )

        audit_event(
            "TEAM_MEMBER_TRANSFERRED" if was_transfer else "TEAM_MEMBER_ADDED",
            "employee",
            employee_id,
            (
                f"Moved employee {employee_id} from team {existing_membership['team_id']} "
                f"to team {team_id}."
                if was_transfer
                else f"Added employee {employee_id} to team {team_id}."
            ),
            conn=conn,
        )
        conn.commit()
    except psycopg2.Error as exc:
        conn.rollback()
        cursor.close()
        conn.close()
        flash(str(exc))
        return redirect(url_for("team_detail", team_id=team_id))

    cursor.close()
    conn.close()
    flash("Team membership updated.")
    return redirect(url_for("team_detail", team_id=team_id))


@app.route("/team_memberships/<int:membership_id>/end", methods=["POST"])
@permission_required("transfer_team_members")
def end_team_membership(membership_id):

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT team_id,
               employee_id,
               active
        FROM team_memberships
        WHERE id=%s
        """,
        (membership_id,),
    )
    membership = row_to_dict(cursor)

    if not membership:
        cursor.close()
        conn.close()
        return "Membership not found"

    cursor.execute(
        """
        UPDATE team_memberships
        SET active=FALSE,
            end_at=COALESCE(end_at, CURRENT_TIMESTAMP),
            updated_at=CURRENT_TIMESTAMP
        WHERE id=%s
        """,
        (membership_id,),
    )
    audit_event(
        "TEAM_MEMBER_ENDED",
        "team_membership",
        membership_id,
        f"Ended team membership for employee {membership['employee_id']}.",
        conn=conn,
    )
    conn.commit()
    cursor.close()
    conn.close()
    flash("Team membership ended.")
    return redirect(url_for("team_detail", team_id=membership["team_id"]))


def current_id_card_duid_for_employee(employee_id):

    if not employee_id:
        return ""

    conn = connect_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, assigned_du_id
        FROM employees
        WHERE id=%s
        """,
        (employee_id,),
    )
    employee = row_to_dict(cursor)

    if employee:
        current_duid = authoritative_employee_duid(cursor, employee)
    else:
        current_duid = ""

    cursor.close()
    conn.close()
    return current_duid


def build_id_card_metadata(
    id_number,
    expiry,
    employee_id,
    telecom_role,
    duid,
    safety_status,
    workbook_name,
    row_index,
    current_duid=None,
):

    safe_id = secure_filename(id_number)
    front_filename = safe_id + "_front.png"
    back_filename = safe_id + "_back.png"
    front_path = os.path.join(ID_CARD_DIR, front_filename)
    back_path = os.path.join(ID_CARD_DIR, back_filename)
    files_exist = os.path.exists(front_path) and os.path.exists(back_path)
    generated_at = None

    if files_exist:
        generated_at = datetime.fromtimestamp(
            max(os.path.getmtime(front_path), os.path.getmtime(back_path))
        )

    current_duid = clean_text(current_duid)
    card_duid = clean_text(duid)
    is_current = bool(current_duid and card_duid == current_duid)

    return {
        "id_number": id_number,
        "safe_id": safe_id,
        "expiry": expiry,
        "employee_id": employee_id,
        "telecom_role": telecom_role,
        "duid": card_duid,
        "current_duid": current_duid,
        "safety_status": safety_status,
        "workbook_name": workbook_name,
        "row_index": row_index,
        "front_filename": front_filename,
        "back_filename": back_filename,
        "front_path": front_path,
        "back_path": back_path,
        "files_exist": files_exist,
        "generated_at": generated_at,
        "generated_at_display": generated_at.strftime("%Y-%m-%d %H:%M") if generated_at else "",
        "is_current_data": is_current,
        "status_label": "Current ID Card" if is_current else "Historical ID Card",
    }


def generated_id_card_metadata(id_number):

    clean_id_number = clean_text(id_number)

    if not clean_id_number or not os.path.isdir(EXCEL_DIR):
        return None

    for filename in os.listdir(EXCEL_DIR):
        if not filename.lower().endswith(".xlsx"):
            continue

        workbook_path = os.path.join(EXCEL_DIR, filename)

        try:
            wb = load_workbook(workbook_path, data_only=True, read_only=True)
        except Exception:
            continue

        try:
            if "ID" not in wb.sheetnames:
                continue

            ws = wb["ID"]

            for row_index, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
                row_id_number = clean_text(row[1] if len(row) > 1 else "")
                row_employee_id = row[4] if len(row) > 4 else None

                if row_id_number == clean_id_number and row_employee_id:
                    current_duid = current_id_card_duid_for_employee(row_employee_id)
                    return build_id_card_metadata(
                        row_id_number,
                        row[2] if len(row) > 2 else "",
                        row_employee_id,
                        row[5] if len(row) > 5 else "",
                        row[6] if len(row) > 6 else "",
                        row[7] if len(row) > 7 else "",
                        filename,
                        row_index,
                        current_duid=current_duid,
                    )
        finally:
            wb.close()

    return None


def find_generated_id_cards_for_employee(employee_id):

    cards = []

    if not os.path.isdir(EXCEL_DIR):
        return cards

    current_duid = current_id_card_duid_for_employee(employee_id)

    for filename in os.listdir(EXCEL_DIR):
        if not filename.lower().endswith(".xlsx"):
            continue

        workbook_path = os.path.join(EXCEL_DIR, filename)

        try:
            wb = load_workbook(workbook_path, data_only=True, read_only=True)
        except Exception:
            continue

        try:
            if "ID" not in wb.sheetnames:
                continue

            ws = wb["ID"]

            for row_index, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
                id_number = clean_text(row[1] if len(row) > 1 else "")
                row_employee_id = row[4] if len(row) > 4 else None

                if not id_number or clean_text(row_employee_id) != clean_text(employee_id):
                    continue

                card = build_id_card_metadata(
                    id_number,
                    row[2] if len(row) > 2 else "",
                    row_employee_id,
                    row[5] if len(row) > 5 else "",
                    row[6] if len(row) > 6 else "",
                    row[7] if len(row) > 7 else "",
                    filename,
                    row_index,
                    current_duid=current_duid,
                )

                if card["files_exist"]:
                    card["print_url"] = url_for("print_id", id_number=card["safe_id"])
                    card["front_url"] = url_for("id_cards", filename=card["front_filename"])
                    card["back_url"] = url_for("id_cards", filename=card["back_filename"])
                    cards.append(card)
        finally:
            wb.close()

    cards.sort(
        key=lambda card: (
            not card["is_current_data"],
            -(card["generated_at"].timestamp() if card["generated_at"] else 0),
            card["workbook_name"],
            -card["row_index"],
        )
    )
    return cards


def employee_id_for_id_card_number(id_number):

    card = generated_id_card_metadata(id_number)
    return card["employee_id"] if card else None


def enforce_team_leader_id_card_scope(id_number):

    if not is_team_leader_role():
        return None

    employee_id = employee_id_for_id_card_number(id_number)

    if not employee_id:
        audit_event(
            "TEAM_SCOPE_ACCESS_DENIED",
            "id_card",
            id_number,
            "Denied Team Leader access to unknown ID card.",
        )
        return access_denied("This ID card is outside your current team scope.")

    conn = connect_db()
    cursor = conn.cursor()
    denied = enforce_team_leader_employee_scope(cursor, conn, employee_id)
    cursor.close()
    conn.close()
    return denied


@app.route("/team_leader")
@permission_required("team_leader_portal")
def team_leader_dashboard():

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        {SITE_REFERENCE_CTE}
        SELECT t.id,
               t.name,
               t.project_id,
               t.du_id,
               t.active,
               p.project_name,
               p.project_code,
               COALESCE(g.site_name, g.sitename, g.globe_du_name, pr.planning_du_name) AS display_site_name,
               ts.current_stage,
               ts.overall_progress,
               ts.overall_status,
               ts.pat_status
        FROM teams t
        LEFT JOIN projects p ON t.project_id = p.id
        LEFT JOIN globe_sites g ON g.du_id = t.du_id
        LEFT JOIN planning_sites pr ON pr.du_id = t.du_id
        LEFT JOIN telecom_sites ts ON ts.du_id = t.du_id
        WHERE t.active IS TRUE
          AND t.team_leader_admin_id=%s
        ORDER BY t.name, t.id
        """,
        (session.get("admin_id"),),
    )
    teams_data = rows_to_dicts(cursor)
    team_ids = [team["id"] for team in teams_data]
    duids = [team["du_id"] for team in teams_data if team.get("du_id")]

    if team_ids:
        cursor.execute(
            """
            SELECT e.id,
                   e.project_id,
                   e.first_name,
                                      e.middle_name,
                   e.last_name,
                   e.position,
                   e.telecom_role,
                   e.assigned_du_id,
                   e.photo,
                   e.nbi,
                   e.wah_file,
                   e.first_aid_file,
                   e.nbi_expiry_date,
                   e.wah_expiry_date,
                   e.first_aid_expiry_date,
                   tm.team_id,
                   p.project_name,
                   p.project_code
            FROM team_memberships tm
            JOIN employees e ON e.id = tm.employee_id
            LEFT JOIN projects p ON e.project_id = p.id
            WHERE tm.active IS TRUE
              AND tm.team_id = ANY(%s)
            ORDER BY e.first_name, e.last_name, e.id
            """,
            (team_ids,),
        )
        members = rows_to_dicts(cursor)
    else:
        members = []

    for member in members:
        member["full_name"] = full_employee_name(member)
        member.update(safety_summary_from_employee(member))
        member["id_cards"] = find_generated_id_cards_for_employee(member["id"])

    today = date.today()

    if duids:
        cursor.execute(
            """
            SELECT COUNT(*) AS open_tasks
            FROM telecom_tasks
            WHERE du_id = ANY(%s)
              AND status NOT IN ('COMPLETED','CLOSED','CANCELLED')
            """,
            (duids,),
        )
        open_tasks = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT COUNT(*) AS open_incidents
            FROM incident_reports
            WHERE du_id = ANY(%s)
              AND status NOT IN ('CLOSED','RESOLVED','CANCELLED')
            """,
            (duids,),
        )
        open_incidents = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT COUNT(*) AS open_punchlists
            FROM punchlist_items
            WHERE duid = ANY(%s)
              AND status <> 'CLOSED'
            """,
            (duids,),
        )
        open_punchlists = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT COUNT(*) AS reports_today
            FROM daily_site_logs
            WHERE duid = ANY(%s)
              AND report_date=%s
            """,
            (duids, today),
        )
        reports_today = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT COUNT(*) FILTER (WHERE da.attendance_status IN ('Present','Late')) AS present_today
            FROM daily_attendance da
            JOIN daily_site_logs dsl ON dsl.id = da.daily_log_id
            WHERE dsl.duid = ANY(%s)
              AND dsl.report_date=%s
            """,
            (duids, today),
        )
        present_today = cursor.fetchone()[0] or 0
    else:
        open_tasks = 0
        open_incidents = 0
        open_punchlists = 0
        reports_today = 0
        present_today = 0

    cursor.close()
    conn.close()

    return render_template(
        "team_leader_dashboard.html",
        teams=teams_data,
        members=members,
        summary={
            "team_members": len(members),
            "present_today": present_today,
            "reports_today": reports_today,
            "open_tasks": open_tasks,
            "open_incidents": open_incidents,
            "open_punchlists": open_punchlists,
        },
    )


@app.route("/team_leader/members/<int:employee_id>")
@permission_required("view_team")
def team_member_detail(employee_id):

    conn = connect_db()
    cursor = conn.cursor()
    denied = enforce_team_leader_employee_scope(cursor, conn, employee_id)

    if denied:
        cursor.close()
        conn.close()
        return denied

    employee = get_employee_detail(cursor, employee_id)

    if not employee:
        cursor.close()
        conn.close()
        return "Employee not found"

    current_documents, document_history = get_employee_safety_documents(cursor, employee)
    employee.update(safety_summary_from_document_records(current_documents))

    cursor.execute(
        """
        SELECT dsl.id AS daily_log_id,
               dsl.duid,
               dsl.report_date,
               dsl.current_stage,
               da.attendance_status,
               da.time_in,
               da.time_out,
               da.role_at_site,
               da.safety_status_snapshot
        FROM daily_attendance da
        JOIN daily_site_logs dsl ON da.daily_log_id = dsl.id
        WHERE da.employee_id=%s
        ORDER BY dsl.report_date DESC, dsl.created_at DESC, dsl.id DESC
        LIMIT 10
        """,
        (employee_id,),
    )
    recent_attendance = rows_to_dicts(cursor)
    id_cards_data = find_generated_id_cards_for_employee(employee_id)
    cursor.close()
    conn.close()

    return render_template(
        "team_member_detail.html",
        emp=employee,
        current_documents=current_documents,
        recent_attendance=recent_attendance,
        id_cards=id_cards_data,
    )


@app.route("/audit_logs")
@permission_required("view_audit_logs")
def audit_logs():

    filters = {
        "username": clean_text(request.args.get("username")),
        "action": clean_text(request.args.get("action")),
        "entity_type": clean_text(request.args.get("entity_type")),
        "date_from": clean_text(request.args.get("date_from")),
        "date_to": clean_text(request.args.get("date_to")),
    }
    clauses = []
    values = []

    if filters["username"]:
        clauses.append("username_snapshot ILIKE %s")
        values.append(f"%{filters['username']}%")

    if filters["action"]:
        clauses.append("action ILIKE %s")
        values.append(f"%{filters['action']}%")

    if filters["entity_type"]:
        clauses.append("entity_type ILIKE %s")
        values.append(f"%{filters['entity_type']}%")

    if filters["date_from"]:
        clauses.append("created_at::date >= %s")
        values.append(filters["date_from"])

    if filters["date_to"]:
        clauses.append("created_at::date <= %s")
        values.append(filters["date_to"])

    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT id, username_snapshot, role_snapshot, action, entity_type,
               entity_id, description, ip_address, http_method, route, created_at
        FROM audit_logs
        {where_sql}
        ORDER BY created_at DESC, id DESC
        LIMIT 300
        """,
        values,
    )
    logs = rows_to_dicts(cursor)
    cursor.close()
    conn.close()

    return render_template("audit_logs.html", logs=logs, filters=filters)


@app.route("/search", methods=["GET", "POST"])
@login_required
def search():

    conn = connect_db()
    cursor = conn.cursor()

    filters = {
        "search": clean_text(request.values.get("search")),
        "employee_id": clean_text(request.values.get("employee_id")),
        "project_id": clean_text(request.values.get("project_id")),
        "du_id": clean_text(request.values.get("du_id")),
        "role": clean_text(request.values.get("role")),
        "nbi_status": clean_text(request.values.get("nbi_status")),
        "wah_status": clean_text(request.values.get("wah_status")),
        "first_aid_status": clean_text(request.values.get("first_aid_status")),
        "overall_safety_status": clean_text(
            request.values.get("overall_safety_status")
        ),
    }

    conditions = ["TRUE"]
    params = []

    if filters["search"]:
        conditions.append(
            """
            (
                e.first_name ILIKE %s
                OR e.middle_name ILIKE %s
                OR e.last_name ILIKE %s
                OR e.email ILIKE %s
                OR e.mobile ILIKE %s
            )
            """
        )
        keyword = "%" + filters["search"] + "%"
        params.extend([keyword, keyword, keyword, keyword, keyword])

    if filters["employee_id"]:
        conditions.append("CAST(e.id AS TEXT)=%s")
        params.append(filters["employee_id"])

    if filters["project_id"]:
        conditions.append("e.project_id=%s")
        params.append(filters["project_id"])

    if filters["du_id"]:
        conditions.append("e.assigned_du_id=%s")
        params.append(filters["du_id"])

    if filters["role"]:
        conditions.append("(e.telecom_role ILIKE %s OR e.position ILIKE %s)")
        params.extend(["%" + filters["role"] + "%", "%" + filters["role"] + "%"])

    add_team_leader_employee_scope(cursor, conditions, params, "e.id")

    cursor.execute(
        f"""
        SELECT e.id,
               e.project_id,
               e.first_name,
               e.middle_name,
               e.last_name,
               e.position,
               e.telecom_role,
               e.assigned_du_id,
               e.email,
               e.mobile,
               e.photo,
               e.nbi,
               e.wah_file,
               e.first_aid_file,
               e.nbi_expiry_date,
               e.wah_expiry_date,
               e.first_aid_expiry_date,
               p.project_name,
               p.project_code
        FROM employees e
        LEFT JOIN projects p ON e.project_id = p.id
        WHERE {' AND '.join(conditions)}
        ORDER BY e.first_name, e.middle_name, e.last_name, e.id
        """,
        params,
    )

    employees = rows_to_dicts(cursor)

    for emp in employees:
        emp.update(safety_summary_from_employee(emp))

    if filters["nbi_status"]:
        employees = [emp for emp in employees if emp["nbi_status"] == filters["nbi_status"]]

    if filters["wah_status"]:
        employees = [emp for emp in employees if emp["wah_status"] == filters["wah_status"]]

    if filters["first_aid_status"]:
        employees = [
            emp
            for emp in employees
            if emp["first_aid_status"] == filters["first_aid_status"]
        ]

    if filters["overall_safety_status"]:
        employees = [
            emp
            for emp in employees
            if emp["overall_safety_status"] == filters["overall_safety_status"]
        ]

    cursor.execute(
        """
        SELECT id, project_name, project_code
        FROM projects
        ORDER BY project_name, project_code
        """
    )
    projects = rows_to_dicts(cursor)

    cursor.execute(
        """
        SELECT DISTINCT du_id
        FROM (
            SELECT du_id FROM globe_nlz
            UNION
            SELECT du_id FROM planning_reference
            UNION
            SELECT du_id FROM telecom_sites
        ) all_duids
        WHERE du_id IS NOT NULL
          AND TRIM(du_id) <> ''
        ORDER BY du_id
        """
    )
    duids = [row[0] for row in cursor.fetchall()]

    cursor.close()
    conn.close()

    return render_template(
        "search.html",
        employees=employees,
        projects=projects,
        duids=duids,
        filters=filters,
    )


#############################################
# EDIT EMPLOYEE
#############################################


@app.route("/edit_employee/<emp_id>", methods=["GET", "POST"])
@permission_required("manage_personnel")
def edit_employee(emp_id):

    conn = connect_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT e.id,
               e.project_id,
               e.first_name,
               e.middle_name,
               e.last_name,
               e.position,
               e.email,
               e.mobile,
               e.phone_type,
               e.ftap_imei,
               e.ftap_email,
               e.philtower_imei,
               e.philtower_email,
               e.photo,
               e.nbi,
               e.certificate,
               e.signature,
               e.telecom_role,
               e.assigned_du_id,
               e.nbi_reference,
               e.nbi_issue_date,
               e.nbi_expiry_date,
               e.wah_reference,
               e.wah_issue_date,
               e.wah_expiry_date,
               e.wah_file,
               e.first_aid_reference,
               e.first_aid_issue_date,
               e.first_aid_expiry_date,
               e.first_aid_file,
               e.dossier_folder_path,
               p.project_code,
               p.project_name,
               p.region,
               p.company
        FROM employees e
        LEFT JOIN projects p ON e.project_id = p.id
        WHERE e.id=%s
        """,
        (emp_id,),
    )

    emp = row_to_dict(cursor)

    if not emp:
        cursor.close()
        conn.close()
        return "Employee not found"

    if request.method == "POST":

        old_name = full_employee_name(emp)

        first_name = clean_text(request.form.get("first_name"))
        middle_name = clean_text(request.form.get("middle_name")) or None
        last_name = clean_text(request.form.get("last_name"))
        position = clean_text(request.form.get("position"))
        email = clean_text(request.form.get("email"))
        mobile = clean_text(request.form.get("mobile"))
        phone_type = clean_text(request.form.get("phone_type"))
        ftap_imei = clean_text(request.form.get("ftap_imei"))
        ftap_email = clean_text(request.form.get("ftap_email"))
        philtower_imei = clean_text(request.form.get("philtower_imei"))
        philtower_email = clean_text(request.form.get("philtower_email"))
        telecom_role = clean_text(request.form.get("telecom_role")) or position
        assigned_du_id = clean_text(request.form.get("assigned_du_id"))

        nbi_reference = clean_text(request.form.get("nbi_reference"))
        try:
            nbi_issue_date = validate_date_field(request.form.get("nbi_issue_date"), "NBI issue date")
            nbi_expiry_date = validate_date_field(request.form.get("nbi_expiry_date"), "NBI expiry date")
            wah_issue_date = validate_date_field(request.form.get("wah_issue_date"), "WAH issue date")
            wah_expiry_date = validate_date_field(request.form.get("wah_expiry_date"), "WAH expiry date")
            first_aid_issue_date = validate_date_field(
                request.form.get("first_aid_issue_date"), "First Aid issue date"
            )
            first_aid_expiry_date = validate_date_field(
                request.form.get("first_aid_expiry_date"), "First Aid expiry date"
            )
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        wah_reference = clean_text(request.form.get("wah_reference"))
        first_aid_reference = clean_text(request.form.get("first_aid_reference"))

        if not duid_exists(cursor, assigned_du_id):
            cursor.close()
            conn.close()
            return "Invalid DUID"

        photo_filename = emp.get("photo") or ""
        nbi_filename = emp.get("nbi") or ""
        certificate_filename = emp.get("certificate") or ""
        signature_filename = emp.get("signature") or ""
        wah_file_path = emp.get("wah_file") or ""
        first_aid_file_path = emp.get("first_aid_file") or ""
        nbi_original_filename = ""
        wah_original_filename = ""
        first_aid_original_filename = ""

        try:
            photo_file = request.files.get("photo")
            nbi_file = request.files.get("nbi")
            certificate_file = request.files.get("certificate")
            signature_file = request.files.get("signature")
            wah_file = request.files.get("wah_file")
            first_aid_file = request.files.get("first_aid_file")

            if photo_file and photo_file.filename:
                photo_filename, photo_path = save_legacy_upload(photo_file, "photos")
                copy_to_project_upload(photo_path, emp["project_id"], emp_id, "photo")

            if nbi_file and nbi_file.filename:
                nbi_original_filename = secure_filename(nbi_file.filename)
                nbi_filename, _ = save_legacy_upload(nbi_file, "nbi")
                nbi_file.seek(0)
                nbi_file_path, _ = save_project_upload(
                    nbi_file, emp["project_id"], emp_id, "nbi"
                )
            else:
                nbi_file_path = "uploads/nbi/" + nbi_filename if nbi_filename else ""

            if certificate_file and certificate_file.filename:
                certificate_filename, certificate_path = save_legacy_upload(
                    certificate_file, "certificates"
                )
                copy_to_project_upload(
                    certificate_path, emp["project_id"], emp_id, "certificates"
                )

            if signature_file and signature_file.filename:
                signature_filename, signature_path = save_legacy_upload(
                    signature_file, "signatures"
                )
                copy_to_project_upload(
                    signature_path, emp["project_id"], emp_id, "signatures"
                )

            if wah_file and wah_file.filename:
                wah_original_filename = secure_filename(wah_file.filename)
                wah_file_path, _ = save_project_upload(
                    wah_file, emp["project_id"], emp_id, "wah"
                )

            if first_aid_file and first_aid_file.filename:
                first_aid_original_filename = secure_filename(first_aid_file.filename)
                first_aid_file_path, _ = save_project_upload(
                    first_aid_file, emp["project_id"], emp_id, "first_aid"
                )
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        dossier_folder_path = os.path.join(
            "static",
            "uploads",
            "projects",
            str(emp["project_id"]),
            str(emp_id),
        ).replace("\\", "/")

        cursor.execute(
            """
            UPDATE employees
            SET first_name=%s,
                middle_name=%s,
                last_name=%s,
                position=%s,
                email=%s,
                mobile=%s,
                phone_type=%s,
                ftap_imei=%s,
                ftap_email=%s,
                philtower_imei=%s,
                philtower_email=%s,
                photo=%s,
                nbi=%s,
                certificate=%s,
                signature=%s,
                telecom_role=%s,
                assigned_du_id=%s,
                nbi_reference=%s,
                nbi_issue_date=%s,
                nbi_expiry_date=%s,
                wah_reference=%s,
                wah_issue_date=%s,
                wah_expiry_date=%s,
                wah_file=%s,
                first_aid_reference=%s,
                first_aid_issue_date=%s,
                first_aid_expiry_date=%s,
                first_aid_file=%s,
                dossier_folder_path=%s
            WHERE id=%s
            """,
            (
                first_name,
                middle_name,
                last_name,
                position,
                email,
                mobile,
                phone_type,
                ftap_imei,
                ftap_email,
                philtower_imei,
                philtower_email,
                photo_filename,
                nbi_filename,
                certificate_filename,
                signature_filename,
                telecom_role,
                assigned_du_id,
                nbi_reference,
                nbi_issue_date,
                nbi_expiry_date,
                wah_reference,
                wah_issue_date,
                wah_expiry_date,
                wah_file_path,
                first_aid_reference,
                first_aid_issue_date,
                first_aid_expiry_date,
                first_aid_file_path,
                dossier_folder_path,
                emp_id,
            ),
        )

        sync_safety_document(
            cursor,
            emp_id,
            emp["project_id"],
            "NBI",
            nbi_reference,
            nbi_issue_date,
            nbi_expiry_date,
            nbi_file_path,
            nbi_original_filename,
            is_replacement=bool(nbi_original_filename),
        )
        sync_safety_document(
            cursor,
            emp_id,
            emp["project_id"],
            "WAH",
            wah_reference,
            wah_issue_date,
            wah_expiry_date,
            wah_file_path,
            wah_original_filename,
            is_replacement=bool(wah_original_filename),
        )
        sync_safety_document(
            cursor,
            emp_id,
            emp["project_id"],
            "FIRST_AID",
            first_aid_reference,
            first_aid_issue_date,
            first_aid_expiry_date,
            first_aid_file_path,
            first_aid_original_filename,
            is_replacement=bool(first_aid_original_filename),
        )
        sync_site_assignment(
            cursor, emp["project_id"], emp_id, assigned_du_id, telecom_role
        )
        audit_event(
            "EMPLOYEE_UPDATED",
            "employee",
            emp_id,
            f"Updated employee {compose_person_name(first_name, middle_name, last_name)}.",
            conn=conn,
        )

        updated_employee = {
            "id": int(emp_id),
            "project_id": emp["project_id"],
            "first_name": first_name,
            "middle_name": middle_name,
            "last_name": last_name,
            "position": position,
            "email": email,
            "mobile": mobile,
            "phone_type": phone_type,
            "ftap_imei": ftap_imei,
            "ftap_email": ftap_email,
            "philtower_imei": philtower_imei,
            "philtower_email": philtower_email,
            "telecom_role": telecom_role,
            "assigned_du_id": assigned_du_id,
            "nbi_expiry_date": nbi_expiry_date,
            "wah_expiry_date": wah_expiry_date,
            "first_aid_expiry_date": first_aid_expiry_date,
        }
        updated_employee.update(safety_summary_from_employee(updated_employee))

        workbook_results = []

        try:
            if emp.get("project_code"):
                excel_path = project_excel_path(emp["project_code"])

                if os.path.exists(excel_path):

                    def apply_employee_workbook_update(wb):

                        write_access_info_row(wb, emp, updated_employee, old_name=old_name)
                        apply_project_workbook_formatting(wb)

                    workbook_results.append(update_persistent_workbook(
                        excel_path,
                        apply_employee_workbook_update,
                        expected_sheets=PROJECT_WORKBOOK_REQUIRED_SHEETS,
                        backup_folder=safe_abs_path("backups", "excel"),
                        operation="employee edit workbook sync",
                    ))

            workbook_results.append(update_master_tracker_safety(updated_employee))
            conn.commit()
        except (ValueError, psycopg2.Error, WorkbookSafetyError) as exc:
            conn.rollback()
            restore_workbook_results(*workbook_results)
            cursor.close()
            conn.close()
            flash(workbook_error_message(exc) if isinstance(exc, WorkbookSafetyError) else str(exc))
            return redirect(url_for("edit_employee", emp_id=emp_id))

        cursor.close()
        conn.close()

        return redirect("/search")

    emp.update(safety_summary_from_employee(emp))
    duids = get_duids_for_select()

    cursor.close()
    conn.close()

    return render_template("edit_employee.html", emp=emp, duids=duids)


#############################################
# CREATE PROJECT + EXCEL TEMPLATE
#############################################


@app.route("/create_project", methods=["GET", "POST"])
@permission_required("manage_projects")
def create_project():

    if request.method == "POST":

        project_name = request.form["project_name"]
        region = request.form["region"]
        company = request.form["company"]

        conn = connect_db()
        cursor = conn.cursor()

        while True:
            project_code = str(random.randint(10000, 99999))
            cursor.execute(
                "SELECT 1 FROM projects WHERE project_code=%s",
                (project_code,),
            )
            if not cursor.fetchone():
                break

        cursor.execute(
            """
            INSERT INTO projects(project_name,region,company,project_code)
            VALUES(%s,%s,%s,%s)
            """,
            (project_name, region, company, project_code),
        )
        audit_event(
            "PROJECT_CREATED",
            "project",
            project_code,
            f"Created project {project_name}.",
            conn=conn,
        )

        #################################
        # CREATE EXCEL
        #################################

        file_path = project_excel_path(project_code)
        workbook_created = False

        try:
            create_persistent_workbook(
                file_path,
                build_project_workbook_template,
                expected_sheets=PROJECT_WORKBOOK_REQUIRED_SHEETS + ("PUNCHLIST", "PAT"),
                operation="project workbook create",
            )
            workbook_created = True
            conn.commit()
        except (WorkbookSafetyError, psycopg2.Error) as exc:
            conn.rollback()

            if workbook_created and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass

            cursor.close()
            conn.close()
            flash(workbook_error_message(exc) if isinstance(exc, WorkbookSafetyError) else str(exc))
            return redirect(url_for("create_project"))

        cursor.close()
        conn.close()

        return redirect("/dashboard")

    return render_template("create_project.html")


#############################################
# EMPLOYEE FORM
#############################################


@app.route("/form/<code>", methods=["GET", "POST"])
def form(code):

    conn = connect_db()
    cursor = conn.cursor()

    project = get_project_by_code(cursor, code)

    if not project:
        cursor.close()
        conn.close()
        return "Invalid Project Link"

    try:
        workbook_path = project_excel_path(project["project_code"])
    except ValueError:
        cursor.close()
        conn.close()
        return "Invalid Project Excel Path"

    if request.method == "POST" and not os.path.exists(workbook_path):
        flash(project_workbook_missing_message(project))
        cursor.close()
        conn.close()
        return redirect(url_for("form", code=project["project_code"]))

    #################################
    # FORM SUBMIT
    #################################

    if request.method == "POST":

        # TEXT DATA
        first_name = clean_text(request.form.get("first_name"))
        middle_name = clean_text(request.form.get("middle_name")) or None
        last_name = clean_text(request.form.get("last_name"))
        position = clean_text(request.form.get("position"))
        email = clean_text(request.form.get("email"))
        mobile = clean_text(request.form.get("mobile"))
        phone_type = clean_text(request.form.get("phone_type"))
        ftap_imei = clean_text(request.form.get("ftap_imei"))
        ftap_email = clean_text(request.form.get("ftap_email"))
        philtower_imei = clean_text(request.form.get("philtower_imei"))
        philtower_email = clean_text(request.form.get("philtower_email"))
        telecom_role = clean_text(request.form.get("telecom_role")) or position
        assigned_du_id = clean_text(request.form.get("assigned_du_id"))

        sec_number = clean_text(request.form.get("sec_number"))
        nbi_reference = clean_text(request.form.get("nbi_reference"))
        wah_reference = clean_text(request.form.get("wah_reference"))
        first_aid_reference = clean_text(request.form.get("first_aid_reference"))

        try:
            sec_expiry = validate_date_field(request.form.get("sec_expiry"), "SEC expiry")
            nbi_issue_date = validate_date_field(request.form.get("nbi_issue_date"), "NBI issue date")
            nbi_expiry_date = validate_date_field(request.form.get("nbi_expiry_date"), "NBI expiry date")
            wah_issue_date = validate_date_field(request.form.get("wah_issue_date"), "WAH issue date")
            wah_expiry_date = validate_date_field(request.form.get("wah_expiry_date"), "WAH expiry date")
            first_aid_issue_date = validate_date_field(
                request.form.get("first_aid_issue_date"), "First Aid issue date"
            )
            first_aid_expiry_date = validate_date_field(
                request.form.get("first_aid_expiry_date"), "First Aid expiry date"
            )
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        full_name = compose_person_name(first_name, middle_name, last_name)

        if not duid_exists(cursor, assigned_du_id):
            cursor.close()
            conn.close()
            return "Invalid DUID"

        #################################
        # FILES
        #################################

        photo = request.files.get("photo")
        nbi = request.files.get("nbi")
        certificate = request.files.get("certificate")
        signature = request.files.get("signature")

        sec_id = request.files.get("sec_id")
        wah_cert = request.files.get("wah_cert")
        first_aid_file = request.files.get("first_aid_file")

        required_uploads = [photo, nbi, certificate, signature]

        if any(not upload or upload.filename == "" for upload in required_uploads):
            cursor.close()
            conn.close()
            return "Photo, NBI, certificate, and signature files are required"
        nbi_original_filename = secure_filename(nbi.filename) if nbi and nbi.filename else ""
        wah_original_filename = (
            secure_filename(wah_cert.filename)
            if wah_cert and wah_cert.filename
            else ""
        )
        first_aid_original_filename = (
            secure_filename(first_aid_file.filename)
            if first_aid_file and first_aid_file.filename
            else ""
        )

        #################################
        # SAVE FILES
        #################################

        try:
            photo_filename, photo_path = save_legacy_upload(photo, "photos")
            nbi_filename, nbi_path = save_legacy_upload(nbi, "nbi")
            cert_filename, cert_path = save_legacy_upload(certificate, "certificates")
            sign_filename, sign_path = save_legacy_upload(signature, "signatures")

            sec_path = ""
            wah_path = ""

            if sec_id and sec_id.filename != "":
                _, sec_path = save_legacy_upload(sec_id, "secid")

            if wah_cert and wah_cert.filename != "":
                _, wah_path = save_legacy_upload(wah_cert, "wah")

        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        #################################
        # SAVE EMPLOYEE
        #################################

        cursor.execute(
            """
        INSERT INTO employees(
        project_id,
        first_name,
        middle_name,
        last_name,
        position,
        email,
        mobile,
        phone_type,
        ftap_imei,
        ftap_email,
        philtower_imei,
        philtower_email,
        photo,
        nbi,
        certificate,
        signature,
        telecom_role,
        assigned_du_id,
        nbi_reference,
        nbi_issue_date,
        nbi_expiry_date,
        wah_reference,
        wah_issue_date,
        wah_expiry_date,
        first_aid_reference,
        first_aid_issue_date,
        first_aid_expiry_date
        )
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
        """,
            (
                project["id"],
                first_name,
                middle_name,
                last_name,
                position,
                email,
                mobile,
                phone_type,
                ftap_imei,
                ftap_email,
                philtower_imei,
                philtower_email,
                photo_filename,
                nbi_filename,
                cert_filename,
                sign_filename,
                telecom_role,
                assigned_du_id,
                nbi_reference,
                nbi_issue_date,
                nbi_expiry_date,
                wah_reference,
                wah_issue_date,
                wah_expiry_date,
                first_aid_reference,
                first_aid_issue_date,
                first_aid_expiry_date,
            ),
        )

        employee_id = cursor.fetchone()[0]

        try:
            copy_to_project_upload(photo_path, project["id"], employee_id, "photo")
            copy_to_project_upload(cert_path, project["id"], employee_id, "certificates")
            copy_to_project_upload(sign_path, project["id"], employee_id, "signatures")

            nbi.seek(0)
            nbi_file_path, _ = save_project_upload(nbi, project["id"], employee_id, "nbi")

            wah_file_path = ""
            if wah_cert and wah_cert.filename != "":
                wah_cert.seek(0)
                wah_file_path, _ = save_project_upload(
                    wah_cert, project["id"], employee_id, "wah"
                )

            first_aid_file_path = ""
            if first_aid_file and first_aid_file.filename != "":
                first_aid_file_path, _ = save_project_upload(
                    first_aid_file, project["id"], employee_id, "first_aid"
                )
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        dossier_folder_path = os.path.join(
            "static",
            "uploads",
            "projects",
            str(project["id"]),
            str(employee_id),
        ).replace("\\", "/")

        cursor.execute(
            """
            UPDATE employees
            SET wah_file=%s,
                first_aid_file=%s,
                dossier_folder_path=%s
            WHERE id=%s
            """,
            (wah_file_path, first_aid_file_path, dossier_folder_path, employee_id),
        )

        sync_safety_document(
            cursor,
            employee_id,
            project["id"],
            "NBI",
            nbi_reference,
            nbi_issue_date,
            nbi_expiry_date,
            nbi_file_path,
            nbi_original_filename,
        )
        sync_safety_document(
            cursor,
            employee_id,
            project["id"],
            "WAH",
            wah_reference,
            wah_issue_date,
            wah_expiry_date,
            wah_file_path,
            wah_original_filename,
        )
        sync_safety_document(
            cursor,
            employee_id,
            project["id"],
            "FIRST_AID",
            first_aid_reference,
            first_aid_issue_date,
            first_aid_expiry_date,
            first_aid_file_path,
            first_aid_original_filename,
        )
        sync_site_assignment(cursor, project["id"], employee_id, assigned_du_id, telecom_role)

        #################################
        # OPEN EXCEL
        #################################

        file_path = workbook_path

        #################################
        # ACCESS INFO SHEET
        #################################

        employee_record = {
            "id": employee_id,
            "project_id": project["id"],
            "first_name": first_name,
            "middle_name": middle_name,
            "last_name": last_name,
            "position": position,
            "email": email,
            "mobile": mobile,
            "phone_type": phone_type,
            "ftap_imei": ftap_imei,
            "ftap_email": ftap_email,
            "philtower_imei": philtower_imei,
            "philtower_email": philtower_email,
            "telecom_role": telecom_role,
            "assigned_du_id": assigned_du_id,
            "nbi_expiry_date": nbi_expiry_date,
            "wah_expiry_date": wah_expiry_date,
            "first_aid_expiry_date": first_aid_expiry_date,
        }
        employee_record.update(safety_summary_from_employee(employee_record))

        def apply_registration_workbook_update(wb):

            write_access_info_row(wb, project, employee_record)

            #################################
            # CLIENT IMAGE LAYOUT ENGINE
            #################################

            def insert_image(sheet, image_path):

                if image_path == "":
                    return

                if not is_image_file(image_path):
                    return

                ws = wb[sheet]

                row = ws.max_row + 3

                img = ExcelImage(image_path)

                img.width = 250
                img.height = 250

                ws.row_dimensions[row].height = 210
                ws.row_dimensions[row + 1].height = 25

                ws.column_dimensions["B"].width = 45

                ws.add_image(img, "B" + str(row))

                write_excel_text_cell(ws, row + 1, 2, full_name)

            #################################
            # INSERT CLIENT STYLE IMAGES
            #################################

            insert_image("2X2", photo_path)
            insert_image("NBI", nbi_path)
            insert_image("CERTIFICATES", cert_path)
            insert_image("eSignature", sign_path)
            insert_image("WAH CERT", wah_path)

            #################################
            # SEC ID SHEET (PROPER FORMAT)
            #################################

            ws6 = wb["SEC ID"]

            row = ws6.max_row + 1

            write_excel_text_cell(ws6, row, 1, full_name)
            write_excel_text_cell(ws6, row, 2, sec_number)
            ws6.cell(row=row, column=3).value = sec_expiry

            ws6.column_dimensions["A"].width = 30
            ws6.column_dimensions["B"].width = 20
            ws6.column_dimensions["C"].width = 18
            ws6.column_dimensions["D"].width = 40

            if sec_path != "" and is_image_file(sec_path):

                img = ExcelImage(sec_path)

                img.width = 200
                img.height = 140

                ws6.row_dimensions[row].height = 110

                ws6.add_image(img, "D" + str(row))

            apply_project_workbook_formatting(wb)

        workbook_results = []

        try:
            workbook_results.append(update_persistent_workbook(
                file_path,
                apply_registration_workbook_update,
                expected_sheets=PROJECT_WORKBOOK_REQUIRED_SHEETS,
                backup_folder=safe_abs_path("backups", "excel"),
                operation="employee registration workbook sync",
            ))
            workbook_results.append(update_master_tracker_safety(employee_record))
            conn.commit()
        except (psycopg2.Error, WorkbookSafetyError) as exc:
            conn.rollback()
            restore_workbook_results(*workbook_results)
            cursor.close()
            conn.close()
            flash(workbook_error_message(exc) if isinstance(exc, WorkbookSafetyError) else str(exc))
            return redirect(url_for("form", code=project["project_code"]))

        cursor.close()
        conn.close()

        return "Form Submitted Successfully"

    cursor.close()
    conn.close()

    duids = get_duids_for_select()

    return render_template("form.html", project=project, duids=duids)


#############################################
# OPEN EXCEL
#############################################


@app.route("/open_excel/<code>")
@permission_required("view_project_workbooks")
def open_excel(code):

    conn = connect_db()
    cursor = conn.cursor()
    project = get_project_by_code(cursor, code)
    cursor.close()
    conn.close()

    if not project:
        flash("Project not found.")
        return redirect(url_for("dashboard"))

    try:
        excel_path = project_excel_path(project["project_code"])
    except ValueError:
        flash("Invalid project Excel path.")
        return redirect(url_for("dashboard"))

    if os.path.exists(excel_path):
        return send_file(excel_path)

    flash(project_workbook_missing_message(project))
    return redirect(url_for("dashboard"))


#############################################
# DELETE PROJECT
#############################################


@app.route("/delete_project/<code>", methods=["POST"])
@permission_required("delete_projects")
def delete_project(code):

    conn = connect_db()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM projects WHERE project_code=%s", (code,))
    audit_event(
        "PROJECT_DELETED",
        "project",
        code,
        f"Deleted project {code}.",
        conn=conn,
    )

    conn.commit()

    file_path = project_excel_path(code)

    if os.path.exists(file_path):
        os.remove(file_path)

    cursor.close()
    conn.close()

    return redirect("/dashboard")


@app.route("/uploads/photos/<filename>")
@login_required
def photos(filename):

    safe_filename = secure_filename(filename)

    if safe_filename != filename:
        return "Invalid upload filename"

    denied = authorize_stored_file_access("uploads/photos/" + filename)

    if denied:
        return denied

    return send_from_directory(safe_abs_path("uploads", "photos"), filename)


#############################################
# ID GENERATOR PAGE
#############################################


@app.route("/id_generator")
@login_required
def id_generator():

    if not can("generate_ids"):
        return access_denied("Team Leaders can view existing team ID cards, but cannot generate new ID cards.")

    conn = connect_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT e.id,
               e.first_name,
                              e.middle_name,
               e.last_name,
               e.position,
               e.photo,
               e.project_id,
               p.project_code,
               e.telecom_role,
               e.assigned_du_id,
               e.nbi,
               e.wah_file,
               e.first_aid_file,
               e.nbi_expiry_date,
               e.wah_expiry_date,
               e.first_aid_expiry_date
        FROM employees e
        LEFT JOIN projects p ON e.project_id = p.id
        ORDER BY e.first_name, e.last_name, e.id
        """
    )

    employees = rows_to_dicts(cursor)

    for emp in employees:
        emp.update(safety_summary_from_employee(emp))

    cursor.close()
    conn.close()

    return render_template("id_generator.html", employees=employees)


@app.route("/uploads/<folder>/<filename>")
@login_required
def uploaded_file(folder, filename):

    if folder not in {"photos", "nbi", "certificates", "signatures", "secid", "wah"}:
        return "Invalid upload folder"

    safe_filename = secure_filename(filename)

    if safe_filename != filename:
        return "Invalid upload filename"

    denied = authorize_stored_file_access("uploads/" + folder + "/" + filename)

    if denied:
        return denied

    return send_from_directory(safe_abs_path("uploads", folder), filename)


@app.route("/employees/<int:employee_id>")
@login_required
def employee_dossier(employee_id):

    conn = connect_db()
    cursor = conn.cursor()
    employee = get_employee_detail(cursor, employee_id)

    if not employee:
        cursor.close()
        conn.close()
        return "Employee not found"

    denied = enforce_team_leader_employee_scope(cursor, conn, employee_id)

    if denied:
        cursor.close()
        conn.close()
        return denied

    site = None
    if employee.get("assigned_du_id"):
        site = get_site_by_duid(cursor, employee["assigned_du_id"])

    current_documents, document_history = get_employee_safety_documents(cursor, employee)
    employee.update(safety_summary_from_document_records(current_documents))

    cursor.execute(
        """
        SELECT sa.id,
               sa.du_id,
               sa.role,
               sa.assignment_status,
               sa.start_date,
               sa.end_date,
               p.project_name,
               p.project_code
        FROM site_assignments sa
        LEFT JOIN projects p ON sa.project_id = p.id
        WHERE sa.employee_id=%s
        ORDER BY
            CASE WHEN sa.assignment_status='ACTIVE' THEN 0 ELSE 1 END,
            sa.start_date DESC NULLS LAST,
            sa.id DESC
        """,
        (employee_id,),
    )
    assignments = rows_to_dicts(cursor)

    cursor.execute(
        """
        SELECT dsl.id AS daily_log_id,
               dsl.duid,
               dsl.report_date,
               dsl.current_stage,
               da.attendance_status,
               da.time_in,
               da.time_out,
               da.role_at_site,
               da.safety_status_snapshot
        FROM daily_attendance da
        JOIN daily_site_logs dsl ON da.daily_log_id = dsl.id
        WHERE da.employee_id=%s
        ORDER BY dsl.report_date DESC, dsl.created_at DESC, dsl.id DESC
        LIMIT 10
        """,
        (employee_id,),
    )
    recent_attendance = rows_to_dicts(cursor)

    cursor.close()
    conn.close()

    legacy_files = [
        {"label": "Employee Photo", "kind": "photo", "path": employee_file_rel_path(employee, "photo")},
        {
            "label": "General Safety Certificate",
            "kind": "certificate",
            "path": employee_file_rel_path(employee, "certificate"),
        },
        {"label": "Signature", "kind": "signature", "path": employee_file_rel_path(employee, "signature")},
    ]

    return render_template(
        "employee_dossier.html",
        emp=employee,
        site=site,
        current_documents=current_documents,
        document_history=document_history,
        assignments=assignments,
        recent_attendance=recent_attendance,
        legacy_files=legacy_files,
        document_types=DOCUMENT_TYPES,
    )


@app.route("/employees/<int:employee_id>/files/<file_kind>")
@login_required
def employee_file(employee_id, file_kind):

    if file_kind not in {"photo", "nbi", "certificate", "signature", "wah", "first_aid"}:
        return "Invalid file type"

    conn = connect_db()
    cursor = conn.cursor()
    employee = get_employee_detail(cursor, employee_id)
    cursor.close()
    conn.close()

    if not employee:
        return "Employee not found"

    if is_team_leader_role():
        conn = connect_db()
        cursor = conn.cursor()
        denied = enforce_team_leader_employee_scope(cursor, conn, employee_id)
        cursor.close()
        conn.close()

        if denied:
            return denied

    rel_path = employee_file_rel_path(employee, file_kind)

    if not rel_path:
        return "File not found"

    return send_stored_file(rel_path)


@app.route("/safety_documents/<int:document_id>/file")
@login_required
def safety_document_file(document_id):

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT employee_id,
               file_path
        FROM safety_documents
        WHERE id=%s
        """,
        (document_id,),
    )
    document = row_to_dict(cursor)

    if document:
        denied = enforce_team_leader_employee_scope(cursor, conn, document["employee_id"])

        if denied:
            cursor.close()
            conn.close()
            return denied

    cursor.close()
    conn.close()

    if not document or not document.get("file_path"):
        return "File not found"

    return send_stored_file(document["file_path"])


@app.route("/safety")
@login_required
def safety_compliance():

    conn = connect_db()
    cursor = conn.cursor()

    filters = {
        "project_id": clean_text(request.args.get("project_id")),
        "du_id": clean_text(request.args.get("du_id")),
        "status": clean_text(request.args.get("status")),
    }
    conditions = ["TRUE"]
    params = []

    if filters["project_id"]:
        conditions.append("e.project_id=%s")
        params.append(filters["project_id"])

    if filters["du_id"]:
        conditions.append("e.assigned_du_id=%s")
        params.append(filters["du_id"])

    add_team_leader_employee_scope(cursor, conditions, params, "e.id")

    cursor.execute(
        f"""
        SELECT e.id,
               e.project_id,
               e.first_name,
                              e.middle_name,
               e.last_name,
               e.position,
               e.telecom_role,
               e.assigned_du_id,
               e.photo,
               e.nbi,
               e.wah_file,
               e.first_aid_file,
               e.nbi_expiry_date,
               e.wah_expiry_date,
               e.first_aid_expiry_date,
               p.project_name,
               p.project_code
        FROM employees e
        LEFT JOIN projects p ON e.project_id = p.id
        WHERE {' AND '.join(conditions)}
        ORDER BY e.first_name, e.last_name, e.id
        """,
        params,
    )
    employees = rows_to_dicts(cursor)

    for employee in employees:
        employee.update(safety_summary_from_employee(employee))
        employee["full_name"] = full_employee_name(employee)

    if filters["status"]:
        employees = [
            employee
            for employee in employees
            if employee["overall_safety_status"] == filters["status"]
        ]

    cursor.close()
    conn.close()

    return render_template(
        "safety_compliance.html",
        employees=employees,
        projects=get_projects_for_select(),
        duids=get_duids_for_select(),
        filters=filters,
        statuses=["VALID", "EXPIRING SOON", "EXPIRED", "MISSING"],
    )


#############################################
# SAFETY DOCUMENTS
#############################################


@app.route("/safety_documents", methods=["GET", "POST"])
@permission_required("manage_safety")
def safety_documents():

    conn = connect_db()
    cursor = conn.cursor()

    if request.method == "POST":
        if not can("manage_safety"):
            cursor.close()
            conn.close()
            return access_denied()

        employee_id = clean_text(request.form.get("employee_id"))
        project_id = clean_text(request.form.get("project_id"))
        document_type = clean_text(request.form.get("document_type"))
        reference_number = clean_text(request.form.get("reference_number"))
        upload = request.files.get("document_file")

        try:
            issue_date = validate_date_field(request.form.get("issue_date"), "Issue date")
            expiry_date = validate_date_field(request.form.get("expiry_date"), "Expiry date")
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        if document_type not in DOCUMENT_TYPES:
            cursor.close()
            conn.close()
            return "Invalid document type"

        cursor.execute(
            """
            SELECT id, project_id
            FROM employees
            WHERE id=%s
            """,
            (employee_id,),
        )
        employee = row_to_dict(cursor)

        if not employee:
            cursor.close()
            conn.close()
            return "Employee not found"

        project_id = project_id or employee["project_id"]
        category = document_type.lower()

        if document_type == "FIRST_AID":
            category = "first_aid"

        file_path = ""
        original_filename = secure_filename(upload.filename) if upload and upload.filename else ""
        legacy_nbi_filename = ""

        try:
            if upload and upload.filename != "":
                if document_type == "NBI":
                    legacy_nbi_filename, _ = save_legacy_upload(upload, "nbi")
                    upload.seek(0)

                file_path, _ = save_project_upload(
                    upload, project_id, employee_id, category
                )
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        sync_safety_document(
            cursor,
            employee_id,
            project_id,
            document_type,
            reference_number,
            issue_date,
            expiry_date,
            file_path,
            original_filename,
            is_replacement=bool(file_path),
        )

        if document_type == "NBI":
            cursor.execute(
                """
                UPDATE employees
                SET nbi_reference=%s,
                    nbi_issue_date=%s,
                    nbi_expiry_date=%s,
                    nbi=COALESCE(NULLIF(%s, ''), nbi)
                WHERE id=%s
                """,
                (
                    reference_number,
                    issue_date,
                    expiry_date,
                    legacy_nbi_filename,
                    employee_id,
                ),
            )
        elif document_type == "WAH":
            cursor.execute(
                """
                UPDATE employees
                SET wah_reference=%s,
                    wah_issue_date=%s,
                    wah_expiry_date=%s,
                    wah_file=COALESCE(NULLIF(%s, ''), wah_file)
                WHERE id=%s
                """,
                (reference_number, issue_date, expiry_date, file_path, employee_id),
            )
        elif document_type == "FIRST_AID":
            cursor.execute(
                """
                UPDATE employees
                SET first_aid_reference=%s,
                    first_aid_issue_date=%s,
                    first_aid_expiry_date=%s,
                    first_aid_file=COALESCE(NULLIF(%s, ''), first_aid_file)
                WHERE id=%s
                """,
                (reference_number, issue_date, expiry_date, file_path, employee_id),
            )

        updated_employee = get_employee_detail(cursor, employee_id)
        workbook_results = []

        try:
            if updated_employee and updated_employee.get("project_code"):
                excel_path = project_excel_path(updated_employee["project_code"])

                if os.path.exists(excel_path):

                    def apply_safety_workbook_update(wb):

                        write_access_info_row(wb, updated_employee, updated_employee)
                        apply_project_workbook_formatting(wb)

                    workbook_results.append(update_persistent_workbook(
                        excel_path,
                        apply_safety_workbook_update,
                        expected_sheets=PROJECT_WORKBOOK_REQUIRED_SHEETS,
                        backup_folder=safe_abs_path("backups", "excel"),
                        operation="safety document workbook sync",
                    ))

            if updated_employee:
                workbook_results.append(update_master_tracker_safety(updated_employee))

            audit_event(
                "SAFETY_DOCUMENT_CHANGED",
                "employee",
                employee_id,
                f"Updated {document_type} safety document.",
                conn=conn,
            )
            conn.commit()
        except (ValueError, psycopg2.Error, WorkbookSafetyError) as exc:
            conn.rollback()
            restore_workbook_results(*workbook_results)
            cursor.close()
            conn.close()
            flash(workbook_error_message(exc) if isinstance(exc, WorkbookSafetyError) else str(exc))
            return redirect("/safety_documents")

        cursor.close()
        conn.close()
        return redirect("/safety_documents")

    filters = {
        "project_id": clean_text(request.args.get("project_id")),
        "employee_id": clean_text(request.args.get("employee_id")),
        "document_type": clean_text(request.args.get("document_type")),
        "status": clean_text(request.args.get("status")),
    }
    conditions = ["TRUE"]
    params = []

    if filters["project_id"]:
        conditions.append("sd.project_id=%s")
        params.append(filters["project_id"])
    if filters["employee_id"]:
        conditions.append("sd.employee_id=%s")
        params.append(filters["employee_id"])
    if filters["document_type"]:
        conditions.append("sd.document_type=%s")
        params.append(filters["document_type"])

    add_team_leader_employee_scope(cursor, conditions, params, "sd.employee_id")

    cursor.execute(
        f"""
        SELECT sd.id,
               sd.employee_id,
               sd.project_id,
               sd.document_type,
               sd.reference_number,
               sd.issue_date,
               sd.expiry_date,
               sd.file_path,
               sd.is_current,
               sd.original_filename,
               sd.created_at,
               sd.updated_at,
               e.first_name,
                              e.middle_name,
               e.last_name,
               p.project_name,
               p.project_code
        FROM safety_documents sd
        LEFT JOIN employees e ON sd.employee_id = e.id
        LEFT JOIN projects p ON sd.project_id = p.id
        WHERE {' AND '.join(conditions)}
        ORDER BY sd.is_current DESC,
                 sd.expiry_date NULLS FIRST,
                 sd.created_at DESC,
                 sd.id DESC
        LIMIT 200
        """,
        params,
    )
    documents = rows_to_dicts(cursor)

    for document in documents:
        document["current_status"] = calculate_document_status(
            document.get("expiry_date"),
            bool(document.get("file_path")),
        )
        document["filename"] = document.get("original_filename") or stored_file_display_name(
            document.get("file_path")
        )

    if filters["status"]:
        documents = [
            document
            for document in documents
            if document["current_status"] == filters["status"]
        ]

    cursor.close()
    conn.close()

    return render_template(
        "safety_documents.html",
        documents=documents,
        projects=get_projects_for_select(),
        employees=get_employees_for_select(),
        document_types=DOCUMENT_TYPES,
        filters=filters,
    )


#############################################
# TELECOM SITES
#############################################


@app.route("/sites")
@any_permission_required("manage_sites", "team_leader_portal")
def sites():

    conn = connect_db()
    cursor = conn.cursor()

    filters = {
        "du_id": clean_text(request.args.get("du_id")),
        "site_name": clean_text(request.args.get("site_name")),
        "towerco": clean_text(request.args.get("towerco")),
        "region": clean_text(request.args.get("region")),
        "municipality": clean_text(request.args.get("municipality")),
        "stage": clean_text(request.args.get("stage")),
        "site_status": clean_text(request.args.get("site_status")),
        "project_id": clean_text(request.args.get("project_id")),
    }

    conditions = ["TRUE"]
    params = []

    if filters["du_id"]:
        conditions.append("d.du_id ILIKE %s")
        params.append("%" + filters["du_id"] + "%")

    if filters["site_name"]:
        conditions.append(
            """
            COALESCE(g.site_name, g.sitename, g.globe_du_name, pr.planning_du_name, '')
                ILIKE %s
            """
        )
        params.append("%" + filters["site_name"] + "%")

    if filters["towerco"]:
        conditions.append("g.towerco=%s")
        params.append(filters["towerco"])

    if filters["region"]:
        conditions.append("COALESCE(g.province, pr.mdb_province, pr.territory, '') ILIKE %s")
        params.append("%" + filters["region"] + "%")

    if filters["municipality"]:
        conditions.append("COALESCE(g.town, pr.municipality, '') ILIKE %s")
        params.append("%" + filters["municipality"] + "%")

    if filters["stage"] in SITE_STAGES:
        conditions.append("COALESCE(ts.current_stage, 'Planning')=%s")
        params.append(filters["stage"])

    if filters["site_status"] in SITE_STATUSES:
        conditions.append("COALESCE(ts.overall_status, 'Not Started')=%s")
        params.append(filters["site_status"])

    if filters["project_id"]:
        conditions.append(
            """
            (
                ts.project_id = %s
                OR EXISTS (
                SELECT 1
                FROM site_assignments sx
                WHERE sx.du_id = d.du_id
                  AND sx.project_id = %s
                )
            )
            """
        )
        params.extend([filters["project_id"], filters["project_id"]])

    add_team_leader_duid_scope(cursor, conditions, params, "d.du_id")

    cursor.execute(
        f"""
        {SITE_REFERENCE_CTE}
        {SITE_SELECT_COLUMNS},
               COALESCE(sa_count.assigned_workers, 0) AS assigned_workers,
               COALESCE(tt_count.open_tasks, 0) AS open_tasks,
               COALESCE(ptw_count.active_permits, 0) AS active_permits,
               COALESCE(ir_count.open_incidents, 0) AS open_incidents,
               COALESCE(daily_count.daily_reports, 0) AS daily_reports,
               COALESCE(pl_count.open_punchlists, 0) AS open_punchlists,
               COALESCE(pl_count.critical_open, 0) AS critical_punchlists,
               COALESCE(latest_pat.result, ts.pat_status, 'PENDING') AS latest_pat_result,
               COALESCE(sa.acceptance_status, 'NOT READY') AS acceptance_status
        {SITE_FROM_JOINS}
        LEFT JOIN (
            SELECT du_id, COUNT(DISTINCT employee_id) AS assigned_workers
            FROM site_assignments
            WHERE assignment_status='ACTIVE'
            GROUP BY du_id
        ) sa_count ON sa_count.du_id = d.du_id
        LEFT JOIN (
            SELECT du_id, COUNT(*) AS open_tasks
            FROM telecom_tasks
            WHERE status NOT IN ('COMPLETED','CLOSED','CANCELLED')
            GROUP BY du_id
        ) tt_count ON tt_count.du_id = d.du_id
        LEFT JOIN (
            SELECT du_id, COUNT(*) AS active_permits
            FROM permit_to_work
            WHERE status='ACTIVE'
              AND (valid_until IS NULL OR valid_until >= CURRENT_DATE)
            GROUP BY du_id
        ) ptw_count ON ptw_count.du_id = d.du_id
        LEFT JOIN (
            SELECT du_id, COUNT(*) AS open_incidents
            FROM incident_reports
            WHERE status NOT IN ('CLOSED','RESOLVED','CANCELLED')
            GROUP BY du_id
        ) ir_count ON ir_count.du_id = d.du_id
        LEFT JOIN (
            SELECT duid AS du_id, COUNT(*) AS daily_reports
            FROM daily_site_logs
            GROUP BY duid
        ) daily_count ON daily_count.du_id = d.du_id
        LEFT JOIN (
            SELECT duid AS du_id,
                   COUNT(*) FILTER (WHERE status <> 'CLOSED') AS open_punchlists,
                   COUNT(*) FILTER (
                       WHERE priority='CRITICAL'
                         AND status = ANY(%s)
                   ) AS critical_open
            FROM punchlist_items
            GROUP BY duid
        ) pl_count ON pl_count.du_id = d.du_id
        LEFT JOIN (
            SELECT DISTINCT ON (duid)
                   duid AS du_id,
                   result
            FROM pat_records
            ORDER BY duid, pat_date DESC, created_at DESC, id DESC
        ) latest_pat ON latest_pat.du_id = d.du_id
        LEFT JOIN site_acceptance sa ON sa.duid = d.du_id
        WHERE {' AND '.join(conditions)}
        ORDER BY d.du_id
        LIMIT 300
        """,
        [PUNCHLIST_UNRESOLVED_STATUSES] + params,
    )
    sites_data = rows_to_dicts(cursor)

    for site in sites_data:
        decorate_site_row(site)

    projects = get_projects_for_select()
    towercos = get_towercos_for_select()

    cursor.close()
    conn.close()

    return render_template(
        "sites.html",
        sites=sites_data,
        projects=projects,
        towercos=towercos,
        stages=SITE_STAGES,
        site_statuses=SITE_STATUSES,
        filters=filters,
    )


@app.route("/sites/new", methods=["GET", "POST"])
@permission_required("manage_sites")
def new_site():

    conn = connect_db()
    cursor = conn.cursor()
    selected_duid = clean_text(request.args.get("du_id"))
    reference_site = None

    if selected_duid:
        reference_site = get_site_by_duid(cursor, selected_duid)

    if request.method == "POST":
        try:
            site_data = collect_site_form_data(cursor)
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        cursor.execute(
            "SELECT 1 FROM telecom_sites WHERE du_id=%s LIMIT 1",
            (site_data["du_id"],),
        )

        if cursor.fetchone():
            cursor.close()
            conn.close()
            return "Operational site already exists for this DUID"

        save_site_operational_record(cursor, site_data)
        audit_event(
            "SITE_CREATED",
            "telecom_site",
            site_data["du_id"],
            f"Created operational site {site_data['du_id']}.",
            conn=conn,
        )
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for("site_detail", du_id=site_data["du_id"]))

    cursor.close()
    conn.close()

    return render_template(
        "site_form.html",
        mode="new",
        site=None,
        reference_site=reference_site,
        projects=get_projects_for_select(),
        duids=get_duids_for_select(),
        stages=SITE_STAGES,
        site_statuses=SITE_STATUSES,
        access_statuses=ACCESS_STATUSES,
        pat_statuses=PAT_STATUSES,
        selected_duid=selected_duid,
    )


@app.route("/sites/<path:du_id>")
@any_permission_required("manage_sites", "team_leader_portal")
def site_detail(du_id):

    du_id = validate_duid_value(du_id)
    conn = connect_db()
    cursor = conn.cursor()
    site = get_site_by_duid(cursor, du_id)

    if not site:
        cursor.close()
        conn.close()
        return "Site not found"

    denied = enforce_team_leader_site_scope(cursor, conn, du_id)

    if denied:
        cursor.close()
        conn.close()
        return denied

    cursor.execute(
        """
        SELECT sa.id,
               sa.project_id,
               sa.employee_id,
               sa.role,
               sa.assignment_status,
               sa.start_date,
               sa.end_date,
               p.project_name,
               p.project_code,
               e.first_name,
                              e.middle_name,
               e.last_name,
               e.position,
               e.telecom_role,
               e.nbi,
               e.wah_file,
               e.first_aid_file,
               e.nbi_expiry_date,
               e.wah_expiry_date,
               e.first_aid_expiry_date
        FROM site_assignments sa
        LEFT JOIN projects p ON sa.project_id = p.id
        LEFT JOIN employees e ON sa.employee_id = e.id
        WHERE sa.du_id=%s
        ORDER BY
            CASE WHEN sa.assignment_status='ACTIVE' THEN 0 ELSE 1 END,
            sa.start_date DESC NULLS LAST,
            sa.id DESC
        """,
        (du_id,),
    )
    assignments = rows_to_dicts(cursor)

    for assignment in assignments:
        assignment.update(safety_summary_from_employee(assignment))

    cursor.execute(
        """
        SELECT t.id,
               t.project_id,
               t.task_type,
               t.description,
               t.priority,
               t.assigned_employee_id,
               t.planned_date,
               t.completed_date,
               t.status,
               t.created_at,
               t.updated_at,
               p.project_name,
               e.first_name,
                              e.middle_name,
               e.last_name
        FROM telecom_tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        LEFT JOIN employees e ON t.assigned_employee_id = e.id
        WHERE t.du_id=%s
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT 100
        """,
        (du_id,),
    )
    tasks = rows_to_dicts(cursor)

    cursor.execute(
        """
        SELECT permit_number,
               permit_type,
               status,
               valid_until,
               created_at
        FROM permit_to_work
        WHERE du_id=%s
        ORDER BY created_at DESC, id DESC
        LIMIT 5
        """,
        (du_id,),
    )
    recent_permits = rows_to_dicts(cursor)

    cursor.execute(
        """
        SELECT incident_date,
               severity,
               category,
               status,
               created_at
        FROM incident_reports
        WHERE du_id=%s
        ORDER BY incident_date DESC, id DESC
        LIMIT 5
        """,
        (du_id,),
    )
    recent_incidents = rows_to_dicts(cursor)

    cursor.execute(
        """
        SELECT dsl.id,
               dsl.report_date,
               dsl.current_stage,
               dsl.progress_before,
               dsl.progress_after,
               dsl.work_completed,
               dsl.blocker_category,
               dsl.blockers,
               dsl.submitted_by,
               COALESCE(att.total_count, 0) AS attendance_count,
               COALESCE(att.present_count, 0) AS present_count
        FROM daily_site_logs dsl
        LEFT JOIN (
            SELECT daily_log_id,
                   COUNT(*) AS total_count,
                   COUNT(*) FILTER (WHERE attendance_status IN ('Present','Late')) AS present_count
            FROM daily_attendance
            GROUP BY daily_log_id
        ) att ON att.daily_log_id = dsl.id
        WHERE dsl.duid=%s
        ORDER BY dsl.report_date DESC, dsl.created_at DESC, dsl.id DESC
        LIMIT 10
        """,
        (du_id,),
    )
    recent_daily_logs = rows_to_dicts(cursor)

    cursor.execute(
        """
        SELECT id,
               report_date
        FROM daily_site_logs
        WHERE duid=%s
          AND report_date=%s
        ORDER BY id DESC
        LIMIT 1
        """,
        (du_id, date.today()),
    )
    today_daily_log = row_to_dict(cursor)

    acceptance_info = get_site_acceptance_info(cursor, du_id)
    punchlist_summary = acceptance_info["punchlist_summary"]
    latest_pat = acceptance_info["latest_pat"]

    cursor.execute(
        """
        SELECT pi.id,
               pi.item_number,
               pi.category,
               pi.title,
               pi.priority,
               pi.status,
               pi.assigned_employee_id,
               pi.raised_date,
               pi.target_date,
               pi.rectified_date,
               pi.verified_date,
               pi.verified_by,
               e.first_name,
                              e.middle_name,
               e.last_name
        FROM punchlist_items pi
        LEFT JOIN employees e ON pi.assigned_employee_id = e.id
        WHERE pi.duid=%s
        ORDER BY
            CASE pi.priority
                WHEN 'CRITICAL' THEN 0
                WHEN 'HIGH' THEN 1
                WHEN 'MEDIUM' THEN 2
                ELSE 3
            END,
            pi.updated_at DESC,
            pi.id DESC
        LIMIT 10
        """,
        (du_id,),
    )
    recent_punchlist_items = rows_to_dicts(cursor)

    for item in recent_punchlist_items:
        item["assigned_name"] = full_employee_name(item)

    cursor.execute(
        """
        SELECT id,
               pat_reference,
               pat_date,
               inspector_name,
               result,
               document_filename,
               document_path,
               updated_at
        FROM pat_records
        WHERE duid=%s
        ORDER BY pat_date DESC, created_at DESC, id DESC
        LIMIT 5
        """,
        (du_id,),
    )
    recent_pat_records = rows_to_dicts(cursor)

    cursor.close()
    conn.close()

    return render_template(
        "site_detail.html",
        site=site,
        assignments=assignments,
        tasks=tasks,
        recent_permits=recent_permits,
        recent_incidents=recent_incidents,
        recent_daily_logs=recent_daily_logs,
        today_daily_log=today_daily_log,
        punchlist_summary=punchlist_summary,
        recent_punchlist_items=recent_punchlist_items,
        latest_pat=latest_pat,
        recent_pat_records=recent_pat_records,
        acceptance_info=acceptance_info,
        projects=get_projects_for_select(),
        employees=get_employees_for_select(),
        task_types=TELECOM_TASK_TYPES,
        priorities=TASK_PRIORITIES,
        task_statuses=TASK_STATUSES,
        assignment_statuses=["ACTIVE", "INACTIVE", "COMPLETED"],
    )


@app.route("/sites/<path:du_id>/edit", methods=["GET", "POST"])
@permission_required("manage_sites")
def edit_site(du_id):

    du_id = validate_duid_value(du_id)
    conn = connect_db()
    cursor = conn.cursor()
    site = get_site_by_duid(cursor, du_id)

    if not site:
        cursor.close()
        conn.close()
        return "Site not found"

    denied = enforce_team_leader_site_scope(cursor, conn, du_id)

    if denied:
        cursor.close()
        conn.close()
        return denied

    if request.method == "POST":
        try:
            site_data = collect_site_form_data(cursor, du_id)
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        save_site_operational_record(cursor, site_data)
        audit_event(
            "SITE_UPDATED",
            "telecom_site",
            du_id,
            f"Updated operational site {du_id}.",
            conn=conn,
        )
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for("site_detail", du_id=du_id))

    cursor.close()
    conn.close()

    return render_template(
        "site_form.html",
        mode="edit",
        site=site,
        reference_site=site,
        projects=get_projects_for_select(),
        duids=get_duids_for_select(),
        stages=SITE_STAGES,
        site_statuses=SITE_STATUSES,
        access_statuses=ACCESS_STATUSES,
        pat_statuses=PAT_STATUSES,
        selected_duid=du_id,
    )


#############################################
# DAILY SITE OPERATIONS
#############################################


@app.route("/daily_operations")
@permission_required("manage_operations")
def daily_operations():

    filters = {
        "report_date": clean_text(request.args.get("report_date")),
        "duid": clean_text(request.args.get("duid")),
        "site": clean_text(request.args.get("site")),
        "stage": clean_text(request.args.get("stage")),
        "project_id": clean_text(request.args.get("project_id")),
        "towerco": clean_text(request.args.get("towerco")),
        "has_blocker": clean_text(request.args.get("has_blocker")),
        "submitted_by": clean_text(request.args.get("submitted_by")),
    }

    try:
        conditions, params = build_daily_log_filter_conditions(filters)
    except ValueError as exc:
        return str(exc)

    conn = connect_db()
    cursor = conn.cursor()
    add_team_leader_duid_scope(cursor, conditions, params, "dsl.duid")
    cursor.execute(
        f"""
        {SITE_REFERENCE_CTE}
        SELECT dsl.id,
               dsl.project_id,
               dsl.duid,
               dsl.report_date,
               dsl.current_stage,
               dsl.progress_before,
               dsl.progress_after,
               dsl.work_completed,
               dsl.blocker_category,
               dsl.blockers,
               dsl.next_day_plan,
               dsl.submitted_by,
               dsl.created_at,
               dsl.updated_at,
               p.project_name,
               p.project_code,
               COALESCE(g.site_name, g.sitename, g.globe_du_name, pr.planning_du_name) AS display_site_name,
               g.towerco,
               COALESCE(g.province, pr.mdb_province, pr.territory) AS region_province,
               COALESCE(att.total_count, 0) AS attendance_count,
               COALESCE(att.present_count, 0) AS present_count,
               COALESCE(files.file_count, 0) AS file_count
        FROM daily_site_logs dsl
        LEFT JOIN globe_sites g ON g.du_id = dsl.duid
        LEFT JOIN planning_sites pr ON pr.du_id = dsl.duid
        LEFT JOIN projects p ON dsl.project_id = p.id
        LEFT JOIN (
            SELECT daily_log_id,
                   COUNT(*) AS total_count,
                   COUNT(*) FILTER (WHERE attendance_status IN ('Present','Late')) AS present_count
            FROM daily_attendance
            GROUP BY daily_log_id
        ) att ON att.daily_log_id = dsl.id
        LEFT JOIN (
            SELECT daily_log_id, COUNT(*) AS file_count
            FROM daily_log_files
            GROUP BY daily_log_id
        ) files ON files.daily_log_id = dsl.id
        WHERE {' AND '.join(conditions)}
        ORDER BY dsl.report_date DESC, dsl.created_at DESC, dsl.id DESC
        LIMIT 300
        """,
        params,
    )
    logs = rows_to_dicts(cursor)
    cursor.close()
    conn.close()

    return render_template(
        "daily_operations.html",
        logs=logs,
        filters=filters,
        projects=get_projects_for_select(),
        duids=get_duids_for_select(),
        towercos=get_towercos_for_select(),
        stages=SITE_STAGES,
    )


@app.route("/daily_operations/export")
@permission_required("manage_operations")
def daily_operations_export():

    filters = {
        "report_date": clean_text(request.args.get("report_date")),
        "duid": clean_text(request.args.get("duid")),
        "site": clean_text(request.args.get("site")),
        "stage": clean_text(request.args.get("stage")),
        "project_id": clean_text(request.args.get("project_id")),
        "towerco": clean_text(request.args.get("towerco")),
        "has_blocker": clean_text(request.args.get("has_blocker")),
        "submitted_by": clean_text(request.args.get("submitted_by")),
    }

    try:
        conditions, params = build_daily_log_filter_conditions(filters)
    except ValueError as exc:
        return str(exc)

    conn = connect_db()
    cursor = conn.cursor()
    add_team_leader_duid_scope(cursor, conditions, params, "dsl.duid")
    cursor.execute(
        f"""
        {SITE_REFERENCE_CTE}
        SELECT dsl.report_date,
               dsl.duid,
               COALESCE(g.site_name, g.sitename, g.globe_du_name, pr.planning_du_name) AS display_site_name,
               dsl.current_stage,
               dsl.progress_before,
               dsl.progress_after,
               dsl.work_completed,
               dsl.blocker_category,
               dsl.blockers,
               dsl.next_day_plan,
               dsl.submitted_by,
               COALESCE(att.total_count, 0) AS attendance_count,
               COALESCE(att.present_count, 0) AS present_count
        FROM daily_site_logs dsl
        LEFT JOIN globe_sites g ON g.du_id = dsl.duid
        LEFT JOIN planning_sites pr ON pr.du_id = dsl.duid
        LEFT JOIN (
            SELECT daily_log_id,
                   COUNT(*) AS total_count,
                   COUNT(*) FILTER (WHERE attendance_status IN ('Present','Late')) AS present_count
            FROM daily_attendance
            GROUP BY daily_log_id
        ) att ON att.daily_log_id = dsl.id
        WHERE {' AND '.join(conditions)}
        ORDER BY dsl.report_date DESC, dsl.created_at DESC, dsl.id DESC
        LIMIT 1000
        """,
        params,
    )
    logs = rows_to_dicts(cursor)
    cursor.close()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "DAILY OPERATIONS"
    ws.append(
        [
            "DATE",
            "DUID",
            "SITE NAME",
            "STAGE",
            "PROGRESS BEFORE",
            "PROGRESS AFTER",
            "WORK COMPLETED",
            "BLOCKER CATEGORY",
            "BLOCKERS",
            "NEXT DAY PLAN",
            "SUBMITTED BY",
            "ATTENDANCE COUNT",
            "PRESENT/LATE COUNT",
        ]
    )

    for log in logs:
        append_excel_row(
            ws,
            [
                log.get("report_date"),
                log.get("duid"),
                log.get("display_site_name"),
                log.get("current_stage"),
                log.get("progress_before"),
                log.get("progress_after"),
                log.get("work_completed"),
                log.get("blocker_category"),
                log.get("blockers"),
                log.get("next_day_plan"),
                log.get("submitted_by"),
                log.get("attendance_count"),
                log.get("present_count"),
            ],
            text_columns={2, 3, 4, 7, 8, 9, 10, 11},
        )

    apply_daily_operations_export_formatting(ws)

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name="daily_operations.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/sites/<path:du_id>/daily_logs/new", methods=["GET", "POST"])
@permission_required("manage_operations")
def new_daily_log(du_id):

    du_id = validate_duid_value(du_id)
    conn = connect_db()
    cursor = conn.cursor()
    site = get_site_by_duid(cursor, du_id)

    if not site:
        cursor.close()
        conn.close()
        return "Site not found"

    denied = enforce_team_leader_site_scope(cursor, conn, du_id)

    if denied:
        cursor.close()
        conn.close()
        return denied

    if request.method == "POST":
        try:
            log_data = collect_daily_log_form_data(cursor, du_id)
        except ValueError as exc:
            cursor.close()
            conn.close()
            flash(str(exc))
            return redirect(url_for("new_daily_log", du_id=du_id))

        cursor.execute(
            """
            SELECT id
            FROM daily_site_logs
            WHERE duid=%s
              AND report_date=%s
            """,
            (du_id, log_data["report_date"]),
        )

        existing_daily_log = cursor.fetchone()

        if existing_daily_log:
            flash(
                f"A daily report already exists for {du_id} on {format_display_date(log_data['report_date'])}. You can edit the existing report below."
            )
            existing_daily_log_id = existing_daily_log[0]
            cursor.close()
            conn.close()
            return redirect(url_for("edit_daily_log", log_id=existing_daily_log_id))

        workbook_results = []

        try:
            cursor.execute(
                """
                INSERT INTO daily_site_logs(
                    project_id,
                    duid,
                    report_date,
                    current_stage,
                    progress_before,
                    progress_after,
                    work_completed,
                    blocker_category,
                    blockers,
                    next_day_plan,
                    general_notes,
                    weather_notes,
                    submitted_by,
                    updated_at
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
                RETURNING id
                """,
                (
                    log_data["project_id"],
                    log_data["duid"],
                    log_data["report_date"],
                    log_data["current_stage"],
                    log_data["progress_before"],
                    log_data["progress_after"],
                    log_data["work_completed"],
                    log_data["blocker_category"],
                    log_data["blockers"],
                    log_data["next_day_plan"],
                    log_data["general_notes"],
                    log_data["weather_notes"],
                    log_data["submitted_by"],
                ),
            )
            daily_log_id = cursor.fetchone()[0]
            sync_daily_attendance(cursor, daily_log_id, du_id)
            project_key = get_project_code_for_daily_path(cursor, log_data["project_id"])
            save_daily_log_files(
                cursor,
                daily_log_id,
                project_key,
                du_id,
                log_data["report_date"],
            )
            apply_daily_site_progress(cursor, log_data)
            workbook_results.append(sync_daily_log_to_project_workbook(cursor, daily_log_id))
            audit_event(
                "DAILY_REPORT_CREATED",
                "daily_site_log",
                daily_log_id,
                f"Created daily report for {du_id} on {log_data['report_date']}.",
                conn=conn,
            )
            conn.commit()
        except (ValueError, psycopg2.Error, WorkbookSafetyError) as exc:
            conn.rollback()
            restore_workbook_results(*workbook_results)
            cursor.close()
            conn.close()
            flash(workbook_error_message(exc) if isinstance(exc, WorkbookSafetyError) else str(exc))
            return redirect(url_for("new_daily_log", du_id=du_id))

        cursor.close()
        conn.close()
        return redirect(url_for("daily_log_detail", log_id=daily_log_id))

    default_log = {
        "project_id": site.get("project_id"),
        "report_date": date.today(),
        "current_stage": site.get("current_stage") or "Planning",
        "progress_before": site.get("overall_progress") or 0,
        "progress_after": site.get("overall_progress") or 0,
    }
    attendance_people = get_daily_attendance_people(cursor, du_id)
    cursor.close()
    conn.close()

    return render_template(
        "daily_log_form.html",
        mode="new",
        site=site,
        log=default_log,
        attendance_people=attendance_people,
        existing_files=[],
        projects=get_projects_for_select(),
        stages=SITE_STAGES,
        attendance_statuses=DAILY_ATTENDANCE_STATUSES,
        blocker_categories=DAILY_BLOCKER_CATEGORIES,
    )


@app.route("/daily_logs/<int:log_id>")
@permission_required("manage_operations")
def daily_log_detail(log_id):

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT dsl.id,
               dsl.project_id,
               dsl.duid,
               dsl.report_date,
               dsl.current_stage,
               dsl.progress_before,
               dsl.progress_after,
               dsl.work_completed,
               dsl.blocker_category,
               dsl.blockers,
               dsl.next_day_plan,
               dsl.general_notes,
               dsl.weather_notes,
               dsl.submitted_by,
               dsl.created_at,
               dsl.updated_at,
               p.project_name,
               p.project_code
        FROM daily_site_logs dsl
        LEFT JOIN projects p ON dsl.project_id = p.id
        WHERE dsl.id=%s
        """,
        (log_id,),
    )
    log = row_to_dict(cursor)

    if not log:
        cursor.close()
        conn.close()
        return "Daily report not found"

    denied = enforce_team_leader_site_scope(cursor, conn, log["duid"])

    if denied:
        cursor.close()
        conn.close()
        return denied

    site = get_site_by_duid(cursor, log["duid"])

    cursor.execute(
        """
        SELECT da.id,
               da.employee_id,
               da.attendance_status,
               da.time_in,
               da.time_out,
               da.role_at_site,
               da.safety_status_snapshot,
               da.remarks,
               e.first_name,
                              e.middle_name,
               e.last_name,
               e.position,
               e.telecom_role
        FROM daily_attendance da
        LEFT JOIN employees e ON da.employee_id = e.id
        WHERE da.daily_log_id=%s
        ORDER BY e.first_name, e.last_name, da.employee_id
        """,
        (log_id,),
    )
    attendance = rows_to_dicts(cursor)

    for row in attendance:
        row["full_name"] = full_employee_name(row)

    cursor.execute(
        """
        SELECT id,
               file_type,
               file_path,
               original_filename,
               caption,
               uploaded_at
        FROM daily_log_files
        WHERE daily_log_id=%s
        ORDER BY uploaded_at DESC, id DESC
        """,
        (log_id,),
    )
    files = rows_to_dicts(cursor)

    for file_row in files:
        file_row["display_name"] = (
            file_row.get("original_filename")
            or stored_file_display_name(file_row.get("file_path"))
        )

    cursor.close()
    conn.close()

    return render_template(
        "daily_log_detail.html",
        log=log,
        site=site,
        attendance=attendance,
        files=files,
    )


@app.route("/daily_logs/<int:log_id>/edit", methods=["GET", "POST"])
@permission_required("manage_operations")
def edit_daily_log(log_id):

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT *
        FROM daily_site_logs
        WHERE id=%s
        """,
        (log_id,),
    )
    log = row_to_dict(cursor)

    if not log:
        cursor.close()
        conn.close()
        return "Daily report not found"

    denied = enforce_team_leader_site_scope(cursor, conn, log["duid"])

    if denied:
        cursor.close()
        conn.close()
        return denied

    site = get_site_by_duid(cursor, log["duid"])

    if request.method == "POST":
        try:
            log_data = collect_daily_log_form_data(cursor, log["duid"], log)
        except ValueError as exc:
            cursor.close()
            conn.close()
            flash(str(exc))
            return redirect(url_for("edit_daily_log", log_id=log_id))

        cursor.execute(
            """
            SELECT id
            FROM daily_site_logs
            WHERE duid=%s
              AND report_date=%s
              AND id<>%s
            """,
            (log["duid"], log_data["report_date"], log_id),
        )

        existing_daily_log = cursor.fetchone()

        if existing_daily_log:
            flash(
                f"A daily report already exists for {log['duid']} on {format_display_date(log_data['report_date'])}. You can edit the existing report below."
            )
            existing_daily_log_id = existing_daily_log[0]
            cursor.close()
            conn.close()
            return redirect(url_for("edit_daily_log", log_id=existing_daily_log_id))

        workbook_results = []

        try:
            cursor.execute(
                """
                UPDATE daily_site_logs
                SET project_id=%s,
                    report_date=%s,
                    current_stage=%s,
                    progress_before=%s,
                    progress_after=%s,
                    work_completed=%s,
                    blocker_category=%s,
                    blockers=%s,
                    next_day_plan=%s,
                    general_notes=%s,
                    weather_notes=%s,
                    submitted_by=%s,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=%s
                """,
                (
                    log_data["project_id"],
                    log_data["report_date"],
                    log_data["current_stage"],
                    log_data["progress_before"],
                    log_data["progress_after"],
                    log_data["work_completed"],
                    log_data["blocker_category"],
                    log_data["blockers"],
                    log_data["next_day_plan"],
                    log_data["general_notes"],
                    log_data["weather_notes"],
                    log_data["submitted_by"],
                    log_id,
                ),
            )
            sync_daily_attendance(cursor, log_id, log["duid"])
            project_key = get_project_code_for_daily_path(cursor, log_data["project_id"])
            save_daily_log_files(
                cursor,
                log_id,
                project_key,
                log["duid"],
                log_data["report_date"],
            )
            apply_daily_site_progress(cursor, log_data)
            workbook_results.append(sync_daily_log_to_project_workbook(cursor, log_id))
            audit_event(
                "DAILY_REPORT_UPDATED",
                "daily_site_log",
                log_id,
                f"Updated daily report for {log['duid']} on {log_data['report_date']}.",
                conn=conn,
            )
            conn.commit()
        except (ValueError, psycopg2.Error, WorkbookSafetyError) as exc:
            conn.rollback()
            restore_workbook_results(*workbook_results)
            cursor.close()
            conn.close()
            flash(workbook_error_message(exc) if isinstance(exc, WorkbookSafetyError) else str(exc))
            return redirect(url_for("edit_daily_log", log_id=log_id))

        cursor.close()
        conn.close()
        return redirect(url_for("daily_log_detail", log_id=log_id))

    attendance_people = get_daily_attendance_people(cursor, log["duid"], log_id)
    cursor.execute(
        """
        SELECT id,
               file_type,
               file_path,
               original_filename,
               caption,
               uploaded_at
        FROM daily_log_files
        WHERE daily_log_id=%s
        ORDER BY uploaded_at DESC, id DESC
        """,
        (log_id,),
    )
    existing_files = rows_to_dicts(cursor)

    for file_row in existing_files:
        file_row["display_name"] = (
            file_row.get("original_filename")
            or stored_file_display_name(file_row.get("file_path"))
        )

    cursor.close()
    conn.close()

    return render_template(
        "daily_log_form.html",
        mode="edit",
        site=site,
        log=log,
        attendance_people=attendance_people,
        existing_files=existing_files,
        projects=get_projects_for_select(),
        stages=SITE_STAGES,
        attendance_statuses=DAILY_ATTENDANCE_STATUSES,
        blocker_categories=DAILY_BLOCKER_CATEGORIES,
    )


@app.route("/daily_log_files/<int:file_id>")
@permission_required("manage_operations")
def daily_log_file(file_id):

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT dlf.file_path,
               dsl.duid
        FROM daily_log_files dlf
        JOIN daily_site_logs dsl ON dsl.id = dlf.daily_log_id
        WHERE dlf.id=%s
        """,
        (file_id,),
    )
    file_record = row_to_dict(cursor)

    if file_record:
        denied = enforce_team_leader_site_scope(cursor, conn, file_record["duid"])

        if denied:
            cursor.close()
            conn.close()
            return denied

    cursor.close()
    conn.close()

    if not file_record or not file_record.get("file_path"):
        return "File not found"

    return send_stored_file(file_record["file_path"])


#############################################
# SITE ASSIGNMENTS
#############################################


@app.route("/site_assignments", methods=["GET", "POST"])
@permission_required("manage_assignments")
def site_assignments():

    if not can("manage_assignments"):
        return access_denied()

    conn = connect_db()
    cursor = conn.cursor()

    if request.method == "POST":
        if not can("manage_assignments"):
            cursor.close()
            conn.close()
            return access_denied()

        project_id = clean_text(request.form.get("project_id"))
        employee_id = clean_text(request.form.get("employee_id"))
        return_to = safe_return_path(request.form.get("return_to"), "/site_assignments")

        try:
            du_id = validate_duid_value(request.form.get("du_id"))
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        role = clean_text(request.form.get("role"))
        assignment_status = clean_text(request.form.get("assignment_status")) or "ACTIVE"

        try:
            start_date = validate_date_field(request.form.get("start_date"), "Start date")
            end_date = validate_date_field(request.form.get("end_date"), "End date")
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        if assignment_status not in ("ACTIVE", "INACTIVE", "COMPLETED"):
            cursor.close()
            conn.close()
            return "Invalid assignment status"

        if not duid_exists(cursor, du_id):
            cursor.close()
            conn.close()
            return "Invalid DUID"

        if not employee_id:
            cursor.close()
            conn.close()
            return "Employee is required"

        employee = get_employee_detail(cursor, employee_id)

        if not employee:
            cursor.close()
            conn.close()
            return "Employee not found"

        if (
            employee["overall_safety_status"] in ("MISSING", "EXPIRED")
            and request.form.get("confirm_safety_warning") != "yes"
        ):
            site = get_site_by_duid(cursor, du_id)
            form_values = dict(request.form)
            cursor.close()
            conn.close()
            return render_template(
                "assignment_warning.html",
                employee=employee,
                site=site,
                form_values=form_values,
            )

        if assignment_status == "ACTIVE":
            cursor.execute(
                """
                UPDATE site_assignments
                SET assignment_status='INACTIVE',
                    end_date=COALESCE(end_date, CURRENT_DATE)
                WHERE employee_id=%s
                  AND project_id IS NOT DISTINCT FROM %s
                  AND assignment_status='ACTIVE'
                """,
                (employee_id, project_id or None),
            )

        cursor.execute(
            """
            INSERT INTO site_assignments(
                project_id,
                du_id,
                employee_id,
                role,
                assignment_status,
                start_date,
                end_date
            )
            VALUES(%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                project_id or None,
                du_id,
                employee_id or None,
                role,
                assignment_status,
                start_date,
                end_date,
            ),
        )

        if employee_id and assignment_status == "ACTIVE":
            cursor.execute(
                """
                UPDATE employees
                SET assigned_du_id=%s,
                    telecom_role=COALESCE(NULLIF(%s, ''), telecom_role)
                WHERE id=%s
                """,
                (du_id, role, employee_id),
            )

        audit_event(
            "SITE_ASSIGNMENT_CHANGED",
            "employee",
            employee_id,
            f"Assigned employee {employee_id} to {du_id}.",
            conn=conn,
        )
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(return_to)

    cursor.execute(
        """
        SELECT sa.id,
               sa.du_id,
               sa.role,
               sa.assignment_status,
               sa.start_date,
               sa.end_date,
               p.project_name,
               p.project_code,
               e.first_name,
                              e.middle_name,
               e.last_name
        FROM site_assignments sa
        LEFT JOIN projects p ON sa.project_id = p.id
        LEFT JOIN employees e ON sa.employee_id = e.id
        ORDER BY sa.created_at DESC, sa.id DESC
        LIMIT 200
        """
    )
    assignments = rows_to_dicts(cursor)

    cursor.close()
    conn.close()

    return render_template(
        "site_assignments.html",
        assignments=assignments,
        projects=get_projects_for_select(),
        employees=get_employees_for_select(),
        duids=get_duids_for_select(),
    )


#############################################
# TELECOM TASK TRACKING
#############################################


@app.route("/telecom_tasks", methods=["GET", "POST"])
@permission_required("manage_tasks")
def telecom_tasks():

    conn = connect_db()
    cursor = conn.cursor()

    if request.method == "POST":
        if not can("manage_tasks"):
            cursor.close()
            conn.close()
            return access_denied()

        project_id = clean_text(request.form.get("project_id"))
        return_to = safe_return_path(request.form.get("return_to"), "/telecom_tasks")

        try:
            du_id = validate_duid_value(request.form.get("du_id"))
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        denied = enforce_team_leader_site_scope(cursor, conn, du_id)

        if denied:
            cursor.close()
            conn.close()
            return denied

        task_type = clean_text(request.form.get("task_type"))
        description = clean_text(request.form.get("description"))
        priority = clean_text(request.form.get("priority")) or "MEDIUM"
        assigned_employee_id = clean_text(request.form.get("assigned_employee_id"))
        status = clean_text(request.form.get("status")) or "PENDING"

        if assigned_employee_id:
            denied = enforce_team_leader_employee_scope(cursor, conn, assigned_employee_id)

            if denied:
                cursor.close()
                conn.close()
                return denied

        try:
            planned_date = validate_date_field(request.form.get("planned_date"), "Planned date")
            completed_date = validate_date_field(
                request.form.get("completed_date"), "Completed date"
            )
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        if task_type not in TELECOM_TASK_TYPES:
            cursor.close()
            conn.close()
            return "Invalid task type"

        if priority not in TASK_PRIORITIES:
            cursor.close()
            conn.close()
            return "Invalid priority"

        if status not in TASK_STATUSES:
            cursor.close()
            conn.close()
            return "Invalid task status"

        if status == "COMPLETED" and not completed_date:
            completed_date = date.today()

        if not duid_exists(cursor, du_id):
            cursor.close()
            conn.close()
            return "Invalid DUID"

        cursor.execute(
            """
            INSERT INTO telecom_tasks(
                project_id,
                du_id,
                task_type,
                description,
                priority,
                assigned_employee_id,
                planned_date,
                completed_date,
                status
            )
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                project_id or None,
                du_id,
                task_type,
                description,
                priority,
                assigned_employee_id or None,
                planned_date,
                completed_date,
                status,
            ),
        )
        audit_event(
            "TELECOM_TASK_CREATED",
            "telecom_task",
            du_id,
            f"Created {task_type} task for {du_id}.",
            conn=conn,
        )
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(return_to)

    filters = {
        "project_id": clean_text(request.args.get("project_id")),
        "du_id": clean_text(request.args.get("du_id")),
        "status": clean_text(request.args.get("status")),
        "employee_id": clean_text(request.args.get("employee_id")),
    }
    conditions = ["TRUE"]
    params = []

    if filters["project_id"]:
        conditions.append("t.project_id=%s")
        params.append(filters["project_id"])
    if filters["du_id"]:
        conditions.append("t.du_id=%s")
        params.append(filters["du_id"])
    if filters["status"]:
        conditions.append("t.status=%s")
        params.append(filters["status"])
    if filters["employee_id"]:
        conditions.append("t.assigned_employee_id=%s")
        params.append(filters["employee_id"])

    add_team_leader_duid_scope(cursor, conditions, params, "t.du_id")

    cursor.execute(
        f"""
        SELECT t.id,
               t.project_id,
               t.du_id,
               t.task_type,
               t.description,
               t.priority,
               t.assigned_employee_id,
               t.planned_date,
               t.completed_date,
               t.status,
               t.created_at,
               p.project_name,
               e.first_name,
                              e.middle_name,
               e.last_name
        FROM telecom_tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        LEFT JOIN employees e ON t.assigned_employee_id = e.id
        WHERE {' AND '.join(conditions)}
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT 200
        """,
        params,
    )
    tasks = rows_to_dicts(cursor)

    cursor.close()
    conn.close()

    return render_template(
        "telecom_tasks.html",
        tasks=tasks,
        projects=get_projects_for_select(),
        employees=get_employees_for_select(),
        duids=get_duids_for_select(),
        task_types=TELECOM_TASK_TYPES,
        priorities=TASK_PRIORITIES,
        statuses=TASK_STATUSES,
        filters=filters,
    )


@app.route("/telecom_tasks/<task_id>/status", methods=["POST"])
@permission_required("manage_tasks")
def update_telecom_task_status(task_id):

    if not can("manage_tasks"):
        return access_denied()

    status = clean_text(request.form.get("status"))
    return_to = safe_return_path(request.form.get("return_to"), "/telecom_tasks")

    try:
        completed_date = validate_date_field(request.form.get("completed_date"), "Completed date")
    except ValueError as exc:
        return str(exc)

    if status not in TASK_STATUSES:
        return "Invalid task status"

    if status == "COMPLETED" and not completed_date:
        completed_date = date.today()

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT du_id, assigned_employee_id FROM telecom_tasks WHERE id=%s", (task_id,))
    task = row_to_dict(cursor)

    if not task:
        cursor.close()
        conn.close()
        return "Telecom task not found"

    denied = enforce_team_leader_site_scope(cursor, conn, task["du_id"])

    if denied:
        cursor.close()
        conn.close()
        return denied

    if task.get("assigned_employee_id"):
        denied = enforce_team_leader_employee_scope(cursor, conn, task["assigned_employee_id"])

        if denied:
            cursor.close()
            conn.close()
            return denied

    cursor.execute(
        """
        UPDATE telecom_tasks
        SET status=%s,
            completed_date=%s,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=%s
        """,
        (status, completed_date, task_id),
    )
    audit_event(
        "TELECOM_TASK_STATUS_UPDATED",
        "telecom_task",
        task_id,
        f"Updated telecom task status to {status}.",
        conn=conn,
    )
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(return_to)


#############################################
# PERMIT TO WORK
#############################################


@app.route("/permits", methods=["GET", "POST"])
@permission_required("manage_permits")
def permits():

    conn = connect_db()
    cursor = conn.cursor()

    if request.method == "POST":
        if not can("manage_permits"):
            cursor.close()
            conn.close()
            return access_denied()

        permit_number = clean_text(request.form.get("permit_number"))
        project_id = clean_text(request.form.get("project_id"))
        try:
            du_id = validate_duid_value(request.form.get("du_id"))
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        permit_type = clean_text(request.form.get("permit_type"))
        issued_to = clean_text(request.form.get("issued_to"))
        issued_by = clean_text(request.form.get("issued_by"))
        valid_from = clean_date(request.form.get("valid_from"))
        valid_until = clean_date(request.form.get("valid_until"))
        status = clean_text(request.form.get("status")) or "PENDING"
        notes = clean_text(request.form.get("notes"))
        permit_file = request.files.get("permit_file")
        file_path = ""

        if status not in PERMIT_STATUSES:
            cursor.close()
            conn.close()
            return "Invalid permit status"

        if not duid_exists(cursor, du_id):
            cursor.close()
            conn.close()
            return "Invalid DUID"

        try:
            if permit_file and permit_file.filename != "":
                file_path, _ = save_project_upload(
                    permit_file, project_id or "general", "project", "permits"
                )
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        cursor.execute(
            """
            INSERT INTO permit_to_work(
                permit_number,
                project_id,
                du_id,
                permit_type,
                issued_to,
                issued_by,
                valid_from,
                valid_until,
                status,
                file_path,
                notes
            )
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                permit_number,
                project_id or None,
                du_id,
                permit_type,
                issued_to,
                issued_by,
                valid_from,
                valid_until,
                status,
                file_path,
                notes,
            ),
        )
        audit_event(
            "PERMIT_CREATED",
            "permit_to_work",
            permit_number,
            f"Created permit {permit_number} for {du_id}.",
            conn=conn,
        )
        conn.commit()
        cursor.close()
        conn.close()
        return redirect("/permits")

    filters = {
        "project_id": clean_text(request.args.get("project_id")),
        "du_id": clean_text(request.args.get("du_id")),
        "status": clean_text(request.args.get("status")),
    }
    conditions = ["TRUE"]
    params = []

    if filters["project_id"]:
        conditions.append("ptw.project_id=%s")
        params.append(filters["project_id"])
    if filters["du_id"]:
        conditions.append("ptw.du_id=%s")
        params.append(filters["du_id"])
    if filters["status"]:
        conditions.append("ptw.status=%s")
        params.append(filters["status"])

    add_team_leader_duid_scope(cursor, conditions, params, "ptw.du_id")

    cursor.execute(
        f"""
        SELECT ptw.id,
               ptw.permit_number,
               ptw.project_id,
               ptw.du_id,
               ptw.permit_type,
               ptw.issued_to,
               ptw.issued_by,
               ptw.valid_from,
               ptw.valid_until,
               ptw.status,
               ptw.file_path,
               ptw.notes,
               p.project_name
        FROM permit_to_work ptw
        LEFT JOIN projects p ON ptw.project_id = p.id
        WHERE {' AND '.join(conditions)}
        ORDER BY ptw.created_at DESC, ptw.id DESC
        LIMIT 200
        """,
        params,
    )
    permits_data = rows_to_dicts(cursor)

    cursor.close()
    conn.close()

    return render_template(
        "permits.html",
        permits=permits_data,
        projects=get_projects_for_select(),
        duids=get_duids_for_select(),
        statuses=PERMIT_STATUSES,
        filters=filters,
    )


@app.route("/permits/<permit_id>/status", methods=["POST"])
@permission_required("manage_permits")
def update_permit_status(permit_id):

    if not can("manage_permits"):
        return access_denied()

    status = clean_text(request.form.get("status"))

    if status not in PERMIT_STATUSES:
        return "Invalid permit status"

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE permit_to_work
        SET status=%s
        WHERE id=%s
        """,
        (status, permit_id),
    )
    audit_event(
        "PERMIT_STATUS_UPDATED",
        "permit_to_work",
        permit_id,
        f"Updated permit status to {status}.",
        conn=conn,
    )
    conn.commit()
    cursor.close()
    conn.close()
    return redirect("/permits")


#############################################
# TOOLBOX TALKS
#############################################


@app.route("/toolbox_talks", methods=["GET", "POST"])
@permission_required("manage_toolbox")
def toolbox_talks():

    conn = connect_db()
    cursor = conn.cursor()

    if request.method == "POST":
        if not can("manage_toolbox"):
            cursor.close()
            conn.close()
            return access_denied()

        project_id = clean_text(request.form.get("project_id"))
        try:
            du_id = validate_duid_value(request.form.get("du_id"))
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        denied = enforce_team_leader_site_scope(cursor, conn, du_id)

        if denied:
            cursor.close()
            conn.close()
            return denied

        topic = clean_text(request.form.get("topic"))
        talk_date = clean_date(request.form.get("date"))
        conducted_by = clean_text(request.form.get("conducted_by"))
        notes = clean_text(request.form.get("notes"))
        employee_ids = request.form.getlist("employee_ids")

        if not duid_exists(cursor, du_id):
            cursor.close()
            conn.close()
            return "Invalid DUID"

        for emp_id in employee_ids:
            denied = enforce_team_leader_employee_scope(cursor, conn, emp_id)

            if denied:
                cursor.close()
                conn.close()
                return denied

        cursor.execute(
            """
            INSERT INTO toolbox_talks(
                project_id,
                du_id,
                topic,
                date,
                conducted_by,
                notes
            )
            VALUES(%s,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (
                project_id or None,
                du_id,
                topic,
                talk_date,
                conducted_by or None,
                notes,
            ),
        )
        toolbox_talk_id = cursor.fetchone()[0]

        for emp_id in employee_ids:
            cursor.execute(
                """
                INSERT INTO toolbox_attendance(
                    toolbox_talk_id,
                    employee_id,
                    attended
                )
                VALUES(%s,%s,TRUE)
                ON CONFLICT(toolbox_talk_id, employee_id)
                DO UPDATE SET attended=TRUE
                """,
                (toolbox_talk_id, emp_id),
            )

        audit_event(
            "TOOLBOX_TALK_CREATED",
            "toolbox_talk",
            toolbox_talk_id,
            f"Created toolbox talk for {du_id}.",
            conn=conn,
        )
        conn.commit()
        cursor.close()
        conn.close()
        return redirect("/toolbox_talks")

    conditions = ["TRUE"]
    params = []
    add_team_leader_duid_scope(cursor, conditions, params, "tt.du_id")

    cursor.execute(
        f"""
        SELECT tt.id,
               tt.project_id,
               tt.du_id,
               tt.topic,
               tt.date,
               tt.notes,
               p.project_name,
               e.first_name AS conducted_first_name,
                              e.middle_name AS conducted_middle_name,
               e.last_name AS conducted_last_name,
               COUNT(ta.employee_id) AS attendees
        FROM toolbox_talks tt
        LEFT JOIN projects p ON tt.project_id = p.id
        LEFT JOIN employees e ON tt.conducted_by = e.id
        LEFT JOIN toolbox_attendance ta ON ta.toolbox_talk_id = tt.id AND ta.attended=TRUE
        WHERE {' AND '.join(conditions)}
        GROUP BY tt.id, p.project_name, e.first_name, e.middle_name, e.last_name
        ORDER BY tt.date DESC, tt.id DESC
        LIMIT 200
        """,
        params,
    )
    talks = rows_to_dicts(cursor)

    cursor.close()
    conn.close()

    return render_template(
        "toolbox_talks.html",
        talks=talks,
        projects=get_projects_for_select(),
        employees=get_employees_for_select(),
        duids=get_duids_for_select(),
    )


#############################################
# INCIDENT REPORTING
#############################################


@app.route("/incidents", methods=["GET", "POST"])
@permission_required("manage_incidents")
def incidents():

    conn = connect_db()
    cursor = conn.cursor()

    if request.method == "POST":
        if not can("manage_incidents"):
            cursor.close()
            conn.close()
            return access_denied()

        project_id = clean_text(request.form.get("project_id"))
        try:
            du_id = validate_duid_value(request.form.get("du_id"))
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        denied = enforce_team_leader_site_scope(cursor, conn, du_id)

        if denied:
            cursor.close()
            conn.close()
            return denied

        reported_by = clean_text(request.form.get("reported_by"))
        incident_date = clean_date(request.form.get("incident_date"))
        severity = clean_text(request.form.get("severity"))
        category = clean_text(request.form.get("category"))
        description = clean_text(request.form.get("description"))
        action_taken = clean_text(request.form.get("action_taken"))
        status = clean_text(request.form.get("status")) or "OPEN"

        if status not in INCIDENT_STATUSES:
            cursor.close()
            conn.close()
            return "Invalid incident status"

        if not duid_exists(cursor, du_id):
            cursor.close()
            conn.close()
            return "Invalid DUID"

        if reported_by:
            denied = enforce_team_leader_employee_scope(cursor, conn, reported_by)

            if denied:
                cursor.close()
                conn.close()
                return denied

        cursor.execute(
            """
            INSERT INTO incident_reports(
                project_id,
                du_id,
                reported_by,
                incident_date,
                severity,
                category,
                description,
                action_taken,
                status
            )
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (
                project_id or None,
                du_id,
                reported_by or None,
                incident_date,
                severity,
                category,
                description,
                action_taken,
                status,
            ),
        )
        incident_id = cursor.fetchone()[0]

        attachments = request.files.getlist("attachments")
        for attachment in attachments:
            if not attachment or attachment.filename == "":
                continue
            try:
                file_path, _ = save_project_upload(
                    attachment, project_id or "general", "incidents", "incidents"
                )
            except ValueError as exc:
                cursor.close()
                conn.close()
                return str(exc)

            cursor.execute(
                """
                INSERT INTO incident_attachments(
                    incident_report_id,
                    file_path,
                    original_filename
                )
                VALUES(%s,%s,%s)
                """,
                (incident_id, file_path, secure_filename(attachment.filename)),
            )

        audit_event(
            "INCIDENT_CREATED",
            "incident_report",
            incident_id,
            f"Created incident report for {du_id}.",
            conn=conn,
        )
        conn.commit()
        cursor.close()
        conn.close()
        return redirect("/incidents")

    filters = {
        "project_id": clean_text(request.args.get("project_id")),
        "du_id": clean_text(request.args.get("du_id")),
        "status": clean_text(request.args.get("status")),
    }
    conditions = ["TRUE"]
    params = []

    if filters["project_id"]:
        conditions.append("ir.project_id=%s")
        params.append(filters["project_id"])
    if filters["du_id"]:
        conditions.append("ir.du_id=%s")
        params.append(filters["du_id"])
    if filters["status"]:
        conditions.append("ir.status=%s")
        params.append(filters["status"])

    add_team_leader_duid_scope(cursor, conditions, params, "ir.du_id")

    cursor.execute(
        f"""
        SELECT ir.id,
               ir.project_id,
               ir.du_id,
               ir.reported_by,
               ir.incident_date,
               ir.severity,
               ir.category,
               ir.description,
               ir.action_taken,
               ir.status,
               p.project_name,
               e.first_name,
                              e.middle_name,
               e.last_name,
               COUNT(ia.id) AS attachments
        FROM incident_reports ir
        LEFT JOIN projects p ON ir.project_id = p.id
        LEFT JOIN employees e ON ir.reported_by = e.id
        LEFT JOIN incident_attachments ia ON ia.incident_report_id = ir.id
        WHERE {' AND '.join(conditions)}
        GROUP BY ir.id, p.project_name, e.first_name, e.middle_name, e.last_name
        ORDER BY ir.created_at DESC, ir.id DESC
        LIMIT 200
        """,
        params,
    )
    incidents_data = rows_to_dicts(cursor)

    cursor.close()
    conn.close()

    return render_template(
        "incidents.html",
        incidents=incidents_data,
        projects=get_projects_for_select(),
        employees=get_employees_for_select(),
        duids=get_duids_for_select(),
        statuses=INCIDENT_STATUSES,
        severities=INCIDENT_SEVERITIES,
        filters=filters,
    )


@app.route("/incidents/<incident_id>/status", methods=["POST"])
@permission_required("manage_incidents")
def update_incident_status(incident_id):

    if not can("manage_incidents"):
        return access_denied()

    status = clean_text(request.form.get("status"))

    if status not in INCIDENT_STATUSES:
        return "Invalid incident status"

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id,
               du_id,
               status
        FROM incident_reports
        WHERE id=%s
        """,
        (incident_id,),
    )
    incident = row_to_dict(cursor)

    if not incident:
        cursor.close()
        conn.close()
        return "Incident not found"

    denied = enforce_team_leader_site_scope(cursor, conn, incident["du_id"])

    if denied:
        cursor.close()
        conn.close()
        return denied

    cursor.execute(
        """
        UPDATE incident_reports
        SET status=%s
        WHERE id=%s
        """,
        (status, incident_id),
    )
    audit_event(
        "INCIDENT_STATUS_UPDATED",
        "incident_report",
        incident_id,
        f"Updated incident status to {status}.",
        conn=conn,
    )
    conn.commit()
    cursor.close()
    conn.close()
    return redirect("/incidents")


#############################################
# GENERATE ID
#############################################


def load_id_font(size, bold=False):

    font_names = ["arialbd.ttf"] if bold else ["arial.ttf"]
    font_names.append("Arial.ttf")

    for font_name in font_names:
        try:
            return ImageFont.truetype(font_name, size)
        except OSError:
            continue

    return ImageFont.load_default()


def text_dimensions(draw, text, font):

    bbox = draw.textbbox((0, 0), clean_text(text), font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def text_fits(draw, text, font, width, height):

    text_width, text_height = text_dimensions(draw, text, font)
    return text_width <= width and text_height <= height


def fitted_font(draw, text, box, max_size, min_size, bold=False):

    width = box[2] - box[0]
    height = box[3] - box[1]

    for size in range(max_size, min_size - 1, -1):
        font = load_id_font(size, bold=bold)
        if text_fits(draw, text, font, width, height):
            return font

    return load_id_font(min_size, bold=bold)


def draw_fitted_text(draw, text, box, max_size, min_size, bold=False, fill=(0, 0, 0), align="center"):

    text = clean_text(text)

    if not text:
        return

    font = fitted_font(draw, text, box, max_size, min_size, bold=bold)
    text_width, text_height = text_dimensions(draw, text, font)

    if align == "left":
        x = box[0]
    elif align == "right":
        x = box[2] - text_width
    else:
        x = box[0] + ((box[2] - box[0] - text_width) // 2)

    y = box[1] + ((box[3] - box[1] - text_height) // 2)
    draw.text((x, y), text, fill, font=font)


def wrap_text_for_width(draw, text, font, max_width, max_lines):

    words = clean_text(text).split()
    lines = []
    current = ""

    for word in words:
        candidate = clean_text(current + " " + word)

        if not current or text_dimensions(draw, candidate, font)[0] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word

            if len(lines) == max_lines:
                break

    if current and len(lines) < max_lines:
        lines.append(current)

    if len(lines) == max_lines and len(words) > len(" ".join(lines).split()):
        ellipsis = "..."
        while lines[-1] and text_dimensions(draw, lines[-1] + ellipsis, font)[0] > max_width:
            lines[-1] = lines[-1][:-1].rstrip()
        lines[-1] = lines[-1] + ellipsis

    return lines


def draw_wrapped_text(draw, text, box, max_size, min_size, max_lines=2, bold=False, fill=(0, 0, 0)):

    text = clean_text(text)

    if not text:
        return

    width = box[2] - box[0]
    height = box[3] - box[1]

    for size in range(max_size, min_size - 1, -1):
        font = load_id_font(size, bold=bold)
        lines = wrap_text_for_width(draw, text, font, width, max_lines)
        line_height = text_dimensions(draw, "Ag", font)[1] + 4

        if lines and len(lines) * line_height <= height:
            y = box[1] + ((height - len(lines) * line_height) // 2)

            for line in lines:
                draw.text((box[0], y), line, fill, font=font)
                y += line_height

            return

    font = load_id_font(min_size, bold=bold)
    lines = wrap_text_for_width(draw, text, font, width, max_lines)
    line_height = text_dimensions(draw, "Ag", font)[1] + 4
    y = box[1]

    for line in lines:
        draw.text((box[0], y), line, fill, font=font)
        y += line_height


def fit_image_to_box(image, box):

    target_width = box[2] - box[0]
    target_height = box[3] - box[1]
    source_width, source_height = image.size
    source_ratio = source_width / source_height
    target_ratio = target_width / target_height

    if source_ratio > target_ratio:
        crop_width = int(source_height * target_ratio)
        left = (source_width - crop_width) // 2
        crop = (left, 0, left + crop_width, source_height)
    else:
        crop_height = int(source_width / target_ratio)
        top = max(0, int((source_height - crop_height) * 0.36))
        crop = (0, top, source_width, top + crop_height)

    resample = getattr(Image, "Resampling", Image).LANCZOS
    return image.crop(crop).resize((target_width, target_height), resample)


def authoritative_employee_duid(cursor, employee):

    assigned_du_id = clean_text(employee.get("assigned_du_id"))

    if assigned_du_id:
        return assigned_du_id

    cursor.execute(
        """
        SELECT du_id
        FROM site_assignments
        WHERE employee_id=%s
          AND assignment_status='ACTIVE'
          AND du_id IS NOT NULL
          AND TRIM(du_id) <> ''
        ORDER BY start_date DESC NULLS LAST,
                 created_at DESC NULLS LAST,
                 id DESC
        LIMIT 1
        """,
        (employee.get("id"),),
    )
    row = cursor.fetchone()

    if row and clean_text(row[0]):
        return clean_text(row[0])

    return "UNASSIGNED"


@app.route("/generate_id/<code>/<employee_id>", methods=["GET", "POST"])
@permission_required("generate_ids")
def generate_id(code, employee_id):

    conn = connect_db()
    cursor = conn.cursor()

    #################################
    # GET EMPLOYEE
    #################################

    cursor.execute(
        """
        SELECT e.id,
               e.project_id,
               e.first_name,
                              e.middle_name,
               e.last_name,
               e.position,
               e.telecom_role,
               e.assigned_du_id,
               e.photo,
               e.nbi,
               e.wah_file,
               e.first_aid_file,
               e.nbi_expiry_date,
               e.wah_expiry_date,
               e.first_aid_expiry_date,
               p.project_code
        FROM employees e
        LEFT JOIN projects p ON e.project_id = p.id
        WHERE e.id=%s
        """,
        (employee_id,),
    )

    emp = row_to_dict(cursor)

    if not emp:
        cursor.close()
        conn.close()
        return "Employee not found"

    emp.update(safety_summary_from_employee(emp))

    name = full_employee_name(emp)
    position = emp["position"]
    telecom_role = emp.get("telecom_role") or position
    assigned_du_id = authoritative_employee_duid(cursor, emp)
    emp["display_duid"] = assigned_du_id

    if not emp.get("project_code"):
        cursor.close()
        conn.close()
        return "Project not found"

    if not emp.get("photo"):
        cursor.close()
        conn.close()
        return "Employee photo missing"

    photo_path = safe_abs_path("uploads", "photos", emp["photo"])

    #################################
    # IF FORM SUBMITTED
    #################################

    if request.method == "POST":

        try:
            excel_path = project_excel_path(emp["project_code"])
        except ValueError:
            cursor.close()
            conn.close()
            return "Invalid Project Excel Path"

        if not os.path.exists(excel_path):
            flash(project_workbook_missing_message(emp))
            cursor.close()
            conn.close()
            return redirect(
                url_for(
                    "generate_id",
                    code=emp["project_code"],
                    employee_id=employee_id,
                )
            )

        id_number = clean_text(request.form.get("id_number"))
        expiry = clean_text(request.form.get("expiry"))
        address = clean_text(request.form.get("address"))
        contact_number = clean_text(request.form.get("contact_number"))
        safe_id_number = secure_filename(id_number)

        if not safe_id_number:
            cursor.close()
            conn.close()
            return "Invalid ID Number"

        #################################
        # OPEN EXCEL AND CHECK DUPLICATE
        #################################

        front_file = os.path.join(ID_CARD_DIR, safe_id_number + "_front.png")
        back_file = os.path.join(ID_CARD_DIR, safe_id_number + "_back.png")
        generated_files = []

        def render_id_card_files():

            #################################
            # LOAD ID TEMPLATES (PIXEL PERFECT)
            #################################

            front = Image.open(os.path.join(ID_TEMPLATE_DIR, "front.png")).convert("RGB")
            back = Image.open(os.path.join(ID_TEMPLATE_DIR, "back.png")).convert("RGB")

            draw_front = ImageDraw.Draw(front)
            draw_back = ImageDraw.Draw(back)

            #################################
            # FIT PHOTO INTO TEMPLATE FRAME
            #################################

            photo_box = (140, 195, 356, 411)
            photo = Image.open(photo_path).convert("RGB")
            front.paste(fit_image_to_box(photo, photo_box), (photo_box[0], photo_box[1]))

            #################################
            # FRONT TEXT
            #################################

            draw_fitted_text(draw_front, name.upper(), (52, 420, 443, 457), 30, 18, bold=True)
            draw_fitted_text(
                draw_front,
                "Employee ID: " + str(emp["id"]),
                (118, 462, 377, 486),
                16,
                11,
            )
            draw_fitted_text(
                draw_front,
                "ID No: " + id_number,
                (100, 492, 395, 518),
                18,
                12,
                bold=True,
            )
            draw_fitted_text(
                draw_front,
                "DUID: " + assigned_du_id,
                (114, 522, 381, 548),
                16,
                11,
            )
            draw_fitted_text(draw_front, telecom_role, (104, 559, 391, 599), 22, 12, bold=True)

            badge_text = emp["safety_badge"]
            badge_color = (
                (18, 120, 66)
                if emp["overall_safety_status"] in ("VALID", "EXPIRING SOON")
                else (150, 45, 45)
            )
            badge_box = (155, 616, 340, 646)
            draw_front.rounded_rectangle(badge_box, radius=8, fill=badge_color)
            draw_fitted_text(
                draw_front,
                badge_text,
                (badge_box[0] + 6, badge_box[1] + 2, badge_box[2] - 6, badge_box[3] - 2),
                16,
                10,
                bold=True,
            )

            #################################
            # BACK TEXT
            #################################

            draw_fitted_text(draw_back, name, (126, 189, 442, 214), 18, 11, bold=True, align="left")
            draw_wrapped_text(draw_back, address, (144, 217, 442, 260), 14, 8, max_lines=3)
            draw_fitted_text(
                draw_back,
                contact_number,
                (218, 263, 442, 284),
                16,
                10,
                align="left",
            )
            draw_fitted_text(draw_back, "DUID: " + assigned_du_id, (70, 287, 425, 306), 13, 9)
            draw_fitted_text(draw_back, "ID No: " + id_number, (70, 574, 425, 598), 15, 10)
            draw_fitted_text(draw_back, "EXPIRY: " + expiry, (70, 604, 425, 630), 17, 11, bold=True)

            #################################
            # SAVE ID CARDS
            #################################

            os.makedirs(ID_CARD_DIR, exist_ok=True)
            front.save(front_file, quality=100)
            generated_files.append(front_file)
            back.save(back_file, quality=100)
            generated_files.append(back_file)

        def apply_id_workbook_update(wb):

            if "ID" not in wb.sheetnames:

                ws = wb.create_sheet("ID")
                ws.append(
                    [
                        "NAME",
                        "ID NUMBER",
                        "EXPIRY",
                        "IMAGE",
                        "EMPLOYEE ID",
                        "TELECOM ROLE",
                        "DUID",
                        "SAFETY STATUS",
                    ]
                )

            else:
                ws = wb["ID"]

            extra_headers = ["EMPLOYEE ID", "TELECOM ROLE", "DUID", "SAFETY STATUS"]
            for offset, header in enumerate(extra_headers, start=5):
                if ws.cell(row=1, column=offset).value in (None, ""):
                    ws.cell(row=1, column=offset).value = header

            for r in ws.iter_rows(min_row=2):
                if len(r) > 1 and r[1].value == id_number:
                    raise ValueError("ID Number Exists")

            if os.path.exists(front_file) or os.path.exists(back_file):
                raise ValueError("ID Number Exists")

            render_id_card_files()

            #################################
            # SAVE TO EXCEL
            #################################

            row = ws.max_row + 2

            write_excel_text_cell(ws, row, 1, name)
            write_excel_text_cell(ws, row, 2, id_number)
            write_excel_text_cell(ws, row, 3, expiry)
            ws.cell(row=row, column=5).value = emp["id"]
            write_excel_text_cell(ws, row, 6, telecom_role)
            write_excel_text_cell(ws, row, 7, assigned_du_id)
            write_excel_text_cell(ws, row, 8, emp["overall_safety_status"])

            img = ExcelImage(front_file)
            img.width = 420
            img.height = 260

            ws.row_dimensions[row].height = 200

            ws.column_dimensions["A"].width = 25
            ws.column_dimensions["B"].width = 20
            ws.column_dimensions["C"].width = 15
            ws.column_dimensions["D"].width = 70
            ws.column_dimensions["E"].width = 15
            ws.column_dimensions["F"].width = 20
            ws.column_dimensions["G"].width = 18
            ws.column_dimensions["H"].width = 20

            ws.add_image(img, "D" + str(row))

            apply_project_workbook_formatting(wb)

        try:
            update_persistent_workbook(
                excel_path,
                apply_id_workbook_update,
                expected_sheets=PROJECT_WORKBOOK_REQUIRED_SHEETS,
                backup_folder=safe_abs_path("backups", "excel"),
                operation="ID card workbook sync",
            )
        except (ValueError, WorkbookSafetyError, OSError) as exc:
            for generated_file in generated_files:
                if os.path.exists(generated_file):
                    try:
                        os.remove(generated_file)
                    except OSError:
                        pass

            cursor.close()
            conn.close()

            if isinstance(exc, WorkbookSafetyError):
                flash(workbook_error_message(exc))
                return redirect(
                    url_for(
                        "generate_id",
                        code=emp["project_code"],
                        employee_id=employee_id,
                    )
                )

            return str(exc)
        audit_event(
            "ID_GENERATED",
            "employee",
            employee_id,
            f"Generated ID card {id_number} for {name}.",
        )

        #################################
        # REDIRECT
        #################################

        cursor.close()
        conn.close()

        return redirect("/print_id/" + safe_id_number)

    #################################
    # SHOW PAGE
    #################################

    cursor.close()
    conn.close()

    return render_template(
        "generate_id.html", emp=emp, code=code, employee_id=employee_id
    )


#############################################
# PRINT PAGE
#############################################


@app.route("/print_id/<id_number>")
@login_required
def print_id(id_number):

    safe_id_number = secure_filename(id_number)

    if safe_id_number != id_number:
        return "Invalid ID Number"

    card = generated_id_card_metadata(safe_id_number)

    if not card or not card["files_exist"]:
        return "ID card not found"

    denied = enforce_team_leader_id_card_scope(safe_id_number)

    if denied:
        return denied

    front_file = "id_cards/" + safe_id_number + "_front.png"
    back_file = "id_cards/" + safe_id_number + "_back.png"

    return render_template(
        "print_id.html",
        front=front_file,
        back=back_file,
        card=card,
    )


#############################################
# SERVE IMAGES
#############################################


@app.route("/id_cards/<filename>")
@login_required
def id_cards(filename):

    safe_filename = secure_filename(filename)

    if safe_filename != filename:
        return "Invalid ID card file"

    id_number = ""

    for suffix in ("_front.png", "_back.png"):
        if filename.endswith(suffix):
            id_number = filename[: -len(suffix)]
            break

    denied = enforce_team_leader_id_card_scope(id_number)

    if denied:
        return denied

    return send_from_directory(ID_CARD_DIR, filename)


#############################################
# PHASE 5: PUNCHLIST, PAT, ACCEPTANCE
#############################################


@app.route("/punchlist")
@permission_required("manage_punchlist")
def punchlist():

    filters = {
        "duid": clean_text(request.args.get("duid")),
        "project_id": clean_text(request.args.get("project_id")),
        "status": clean_text(request.args.get("status")),
        "priority": clean_text(request.args.get("priority")),
        "assigned_employee_id": clean_text(request.args.get("assigned_employee_id")),
    }
    conditions = ["TRUE"]
    params = []

    if filters["duid"]:
        conditions.append("pi.duid ILIKE %s")
        params.append("%" + filters["duid"] + "%")

    if filters["project_id"]:
        conditions.append("pi.project_id=%s")
        params.append(filters["project_id"])

    if filters["status"] in PUNCHLIST_STATUSES:
        conditions.append("pi.status=%s")
        params.append(filters["status"])

    if filters["priority"] in PUNCHLIST_PRIORITIES:
        conditions.append("pi.priority=%s")
        params.append(filters["priority"])

    if filters["assigned_employee_id"]:
        conditions.append("pi.assigned_employee_id=%s")
        params.append(filters["assigned_employee_id"])

    conn = connect_db()
    cursor = conn.cursor()
    add_team_leader_duid_scope(cursor, conditions, params, "pi.duid")
    cursor.execute(
        f"""
        {SITE_REFERENCE_CTE}
        SELECT pi.id,
               pi.duid,
               pi.project_id,
               pi.item_number,
               pi.category,
               pi.title,
               pi.priority,
               pi.status,
               pi.assigned_employee_id,
               pi.raised_date,
               pi.target_date,
               pi.rectified_date,
               pi.verified_date,
               pi.verified_by,
               pi.updated_at,
               p.project_name,
               p.project_code,
               e.first_name,
                              e.middle_name,
               e.last_name,
               COALESCE(g.site_name, g.sitename, g.globe_du_name, pr.planning_du_name) AS display_site_name
        FROM punchlist_items pi
        LEFT JOIN globe_sites g ON g.du_id = pi.duid
        LEFT JOIN planning_sites pr ON pr.du_id = pi.duid
        LEFT JOIN projects p ON pi.project_id = p.id
        LEFT JOIN employees e ON pi.assigned_employee_id = e.id
        WHERE {' AND '.join(conditions)}
        ORDER BY
            CASE pi.priority
                WHEN 'CRITICAL' THEN 0
                WHEN 'HIGH' THEN 1
                WHEN 'MEDIUM' THEN 2
                ELSE 3
            END,
            pi.updated_at DESC,
            pi.id DESC
        LIMIT 300
        """,
        params,
    )
    items = rows_to_dicts(cursor)

    for item in items:
        item["assigned_name"] = full_employee_name(item)

    cursor.close()
    conn.close()

    return render_template(
        "punchlist.html",
        items=items,
        filters=filters,
        projects=get_projects_for_select(),
        employees=get_employees_for_select(),
        duids=get_duids_for_select(),
        statuses=PUNCHLIST_STATUSES,
        priorities=PUNCHLIST_PRIORITIES,
    )


@app.route("/sites/<path:du_id>/punchlist/new", methods=["GET", "POST"])
@permission_required("manage_punchlist")
def new_punchlist_item(du_id):

    du_id = validate_duid_value(du_id)
    conn = connect_db()
    cursor = conn.cursor()
    site = get_site_by_duid(cursor, du_id)

    if not site:
        cursor.close()
        conn.close()
        return "Site not found"

    denied = enforce_team_leader_site_scope(cursor, conn, du_id)

    if denied:
        cursor.close()
        conn.close()
        return denied

    if request.method == "POST":
        workbook_results = []

        try:
            item_data = collect_punchlist_form_data(cursor, du_id, site)
            if item_data.get("assigned_employee_id"):
                denied = enforce_team_leader_employee_scope(
                    cursor,
                    conn,
                    item_data["assigned_employee_id"],
                )

                if denied:
                    cursor.close()
                    conn.close()
                    return denied

            cursor.execute(
                """
                INSERT INTO punchlist_items(
                    duid,
                    telecom_site_id,
                    project_id,
                    item_number,
                    category,
                    title,
                    description,
                    priority,
                    status,
                    assigned_employee_id,
                    raised_by,
                    raised_date,
                    target_date,
                    closure_notes,
                    updated_at
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
                RETURNING id
                """,
                (
                    item_data["duid"],
                    item_data["telecom_site_id"],
                    item_data["project_id"],
                    item_data["item_number"],
                    item_data["category"],
                    item_data["title"],
                    item_data["description"],
                    item_data["priority"],
                    item_data["status"],
                    item_data["assigned_employee_id"],
                    item_data["raised_by"],
                    item_data["raised_date"],
                    item_data["target_date"],
                    item_data["closure_notes"],
                ),
            )
            item_id = cursor.fetchone()[0]

            if not item_data["item_number"]:
                item_data["item_number"] = "PL-" + str(item_id).zfill(4)
                cursor.execute(
                    "UPDATE punchlist_items SET item_number=%s WHERE id=%s",
                    (item_data["item_number"], item_id),
                )

            item = get_punchlist_item(cursor, item_id)
            project_key = project_key_for_site_files(cursor, item["project_id"])
            sync_punchlist_files(cursor, item, project_key)
            workbook_results.append(sync_punchlist_item_to_project_workbook(cursor, item_id))
            audit_event(
                "PUNCHLIST_CREATED",
                "punchlist_item",
                item_id,
                f"Created punchlist item for {du_id}.",
                conn=conn,
            )
            conn.commit()
        except (ValueError, psycopg2.Error, WorkbookSafetyError) as exc:
            conn.rollback()
            restore_workbook_results(*workbook_results)
            cursor.close()
            conn.close()
            flash(workbook_error_message(exc) if isinstance(exc, WorkbookSafetyError) else str(exc))
            return redirect(url_for("new_punchlist_item", du_id=du_id))

        cursor.close()
        conn.close()
        return redirect(url_for("punchlist_detail", item_id=item_id))

    item = {
        "duid": du_id,
        "project_id": site.get("project_id"),
        "priority": "MEDIUM",
        "status": "OPEN",
        "raised_date": date.today(),
    }
    people = get_punchlist_people(cursor, du_id, site.get("project_id"))
    cursor.close()
    conn.close()

    return render_template(
        "punchlist_form.html",
        mode="new",
        site=site,
        item=item,
        people=people,
        files=[],
        projects=get_projects_for_select(),
        priorities=PUNCHLIST_PRIORITIES,
        statuses=PUNCHLIST_STATUSES,
        file_types=PUNCHLIST_FILE_TYPES,
    )


@app.route("/punchlist/<int:item_id>")
@permission_required("manage_punchlist")
def punchlist_detail(item_id):

    conn = connect_db()
    cursor = conn.cursor()
    item = get_punchlist_item(cursor, item_id)

    if not item:
        cursor.close()
        conn.close()
        return "Punchlist item not found"

    denied = enforce_team_leader_site_scope(cursor, conn, item["duid"])

    if denied:
        cursor.close()
        conn.close()
        return denied

    site = get_site_by_duid(cursor, item["duid"])
    cursor.execute(
        """
        SELECT id,
               file_type,
               filename,
               file_path,
               caption,
               uploaded_by,
               uploaded_at
        FROM punchlist_files
        WHERE punchlist_item_id=%s
        ORDER BY uploaded_at DESC, id DESC
        """,
        (item_id,),
    )
    files = rows_to_dicts(cursor)

    for file_row in files:
        file_row["display_name"] = (
            file_row.get("filename")
            or stored_file_display_name(file_row.get("file_path"))
        )

    cursor.close()
    conn.close()

    return render_template(
        "punchlist_detail.html",
        item=item,
        site=site,
        files=files,
    )


@app.route("/punchlist/<int:item_id>/edit", methods=["GET", "POST"])
@permission_required("manage_punchlist")
def edit_punchlist_item(item_id):

    conn = connect_db()
    cursor = conn.cursor()
    item = get_punchlist_item(cursor, item_id)

    if not item:
        cursor.close()
        conn.close()
        return "Punchlist item not found"

    denied = enforce_team_leader_site_scope(cursor, conn, item["duid"])

    if denied:
        cursor.close()
        conn.close()
        return denied

    site = get_site_by_duid(cursor, item["duid"])

    if request.method == "POST":
        workbook_results = []

        try:
            item_data = collect_punchlist_form_data(cursor, item["duid"], site, item)
            if item_data.get("assigned_employee_id"):
                denied = enforce_team_leader_employee_scope(
                    cursor,
                    conn,
                    item_data["assigned_employee_id"],
                )

                if denied:
                    cursor.close()
                    conn.close()
                    return denied

            validate_punchlist_transition(item["status"], item_data["status"])
            rectified_date = item.get("rectified_date")
            verified_date = item.get("verified_date")
            verified_by = item.get("verified_by")

            if item_data["status"] == "RECTIFIED" and item["status"] != "RECTIFIED":
                rectified_date = rectified_date or date.today()

            if item_data["status"] == "VERIFIED" and item["status"] != "VERIFIED":
                verified_date = date.today()
                verified_by = session.get("admin", "")

            cursor.execute(
                """
                UPDATE punchlist_items
                SET project_id=%s,
                    item_number=%s,
                    category=%s,
                    title=%s,
                    description=%s,
                    priority=%s,
                    status=%s,
                    assigned_employee_id=%s,
                    raised_by=%s,
                    raised_date=%s,
                    target_date=%s,
                    rectified_date=%s,
                    verified_date=%s,
                    verified_by=%s,
                    closure_notes=%s,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=%s
                """,
                (
                    item_data["project_id"],
                    item_data["item_number"],
                    item_data["category"],
                    item_data["title"],
                    item_data["description"],
                    item_data["priority"],
                    item_data["status"],
                    item_data["assigned_employee_id"],
                    item_data["raised_by"],
                    item_data["raised_date"],
                    item_data["target_date"],
                    rectified_date,
                    verified_date,
                    verified_by,
                    item_data["closure_notes"],
                    item_id,
                ),
            )
            updated_item = get_punchlist_item(cursor, item_id)
            project_key = project_key_for_site_files(cursor, updated_item["project_id"])
            sync_punchlist_files(cursor, updated_item, project_key)
            workbook_results.append(sync_punchlist_item_to_project_workbook(cursor, item_id))
            audit_event(
                "PUNCHLIST_UPDATED",
                "punchlist_item",
                item_id,
                "Updated punchlist item.",
                conn=conn,
            )
            conn.commit()
        except (ValueError, psycopg2.Error, WorkbookSafetyError) as exc:
            conn.rollback()
            restore_workbook_results(*workbook_results)
            cursor.close()
            conn.close()
            flash(workbook_error_message(exc) if isinstance(exc, WorkbookSafetyError) else str(exc))
            return redirect(url_for("edit_punchlist_item", item_id=item_id))

        cursor.close()
        conn.close()
        return redirect(url_for("punchlist_detail", item_id=item_id))

    cursor.execute(
        """
        SELECT id,
               file_type,
               filename,
               file_path,
               caption,
               uploaded_by,
               uploaded_at
        FROM punchlist_files
        WHERE punchlist_item_id=%s
        ORDER BY uploaded_at DESC, id DESC
        """,
        (item_id,),
    )
    files = rows_to_dicts(cursor)

    for file_row in files:
        file_row["display_name"] = (
            file_row.get("filename")
            or stored_file_display_name(file_row.get("file_path"))
        )

    people = get_punchlist_people(cursor, item["duid"], item.get("project_id"))
    cursor.close()
    conn.close()

    return render_template(
        "punchlist_form.html",
        mode="edit",
        site=site,
        item=item,
        people=people,
        files=files,
        projects=get_projects_for_select(),
        priorities=PUNCHLIST_PRIORITIES,
        statuses=PUNCHLIST_STATUSES,
        file_types=PUNCHLIST_FILE_TYPES,
    )


@app.route("/punchlist_files/<int:file_id>")
@permission_required("manage_punchlist")
def punchlist_file(file_id):

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT pf.file_path,
               pi.duid
        FROM punchlist_files pf
        JOIN punchlist_items pi ON pi.id = pf.punchlist_item_id
        WHERE pf.id=%s
        """,
        (file_id,),
    )
    file_record = row_to_dict(cursor)

    if file_record:
        denied = enforce_team_leader_site_scope(cursor, conn, file_record["duid"])

        if denied:
            cursor.close()
            conn.close()
            return denied

    cursor.close()
    conn.close()

    if not file_record or not file_record.get("file_path"):
        return "File not found"

    return send_stored_file(file_record["file_path"])


@app.route("/pat")
@any_permission_required("view_pat", "manage_pat")
def pat_history():

    filters = {
        "duid": clean_text(request.args.get("duid")),
        "project_id": clean_text(request.args.get("project_id")),
        "result": clean_text(request.args.get("result")),
        "pat_date": clean_text(request.args.get("pat_date")),
    }
    conditions = ["TRUE"]
    params = []

    if filters["duid"]:
        conditions.append("pr.duid ILIKE %s")
        params.append("%" + filters["duid"] + "%")

    if filters["project_id"]:
        conditions.append("pr.project_id=%s")
        params.append(filters["project_id"])

    if filters["result"] in PAT_RESULTS:
        conditions.append("pr.result=%s")
        params.append(filters["result"])

    if filters["pat_date"]:
        try:
            pat_date = validate_date_field(filters["pat_date"], "PAT date")
        except ValueError as exc:
            return str(exc)
        conditions.append("pr.pat_date=%s")
        params.append(pat_date)

    conn = connect_db()
    cursor = conn.cursor()
    add_team_leader_duid_scope(cursor, conditions, params, "pr.duid")
    cursor.execute(
        f"""
        {SITE_REFERENCE_CTE}
        SELECT pr.id,
               pr.duid,
               pr.project_id,
               pr.pat_reference,
               pr.pat_date,
               pr.inspector_name,
               pr.vendor_name,
               pr.towerco_customer,
               pr.result,
               pr.remarks,
               pr.document_filename,
               pr.document_path,
               pr.created_by,
               pr.updated_at,
               p.project_name,
               p.project_code,
               COALESCE(g.site_name, g.sitename, g.globe_du_name, prn.planning_du_name) AS display_site_name
        FROM pat_records pr
        LEFT JOIN globe_sites g ON g.du_id = pr.duid
        LEFT JOIN planning_sites prn ON prn.du_id = pr.duid
        LEFT JOIN projects p ON pr.project_id = p.id
        WHERE {' AND '.join(conditions)}
        ORDER BY pr.pat_date DESC, pr.created_at DESC, pr.id DESC
        LIMIT 300
        """,
        params,
    )
    records = rows_to_dicts(cursor)
    cursor.close()
    conn.close()

    return render_template(
        "pat.html",
        records=records,
        filters=filters,
        projects=get_projects_for_select(),
        duids=get_duids_for_select(),
        results=PAT_RESULTS,
    )


@app.route("/sites/<path:du_id>/pat/new", methods=["GET", "POST"])
@permission_required("manage_pat")
def new_pat_record(du_id):

    du_id = validate_duid_value(du_id)
    conn = connect_db()
    cursor = conn.cursor()
    site = get_site_by_duid(cursor, du_id)

    if not site:
        cursor.close()
        conn.close()
        return "Site not found"

    if request.method == "POST":
        workbook_results = []

        try:
            pat_data = collect_pat_form_data(cursor, du_id, site)
            cursor.execute(
                """
                INSERT INTO pat_records(
                    duid,
                    telecom_site_id,
                    project_id,
                    pat_reference,
                    pat_date,
                    inspector_name,
                    vendor_name,
                    towerco_customer,
                    result,
                    remarks,
                    created_by,
                    updated_at
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
                RETURNING id
                """,
                (
                    pat_data["duid"],
                    pat_data["telecom_site_id"],
                    pat_data["project_id"],
                    pat_data["pat_reference"],
                    pat_data["pat_date"],
                    pat_data["inspector_name"],
                    pat_data["vendor_name"],
                    pat_data["towerco_customer"],
                    pat_data["result"],
                    pat_data["remarks"],
                    pat_data["created_by"],
                ),
            )
            pat_id = cursor.fetchone()[0]
            project_key = project_key_for_site_files(cursor, pat_data["project_id"])
            pat_data["id"] = pat_id
            document_filename, document_path = save_pat_document(cursor, pat_data, project_key)
            cursor.execute(
                """
                UPDATE pat_records
                SET document_filename=%s,
                    document_path=%s
                WHERE id=%s
                """,
                (document_filename, document_path, pat_id),
            )
            apply_latest_pat_to_site(cursor, du_id)
            workbook_results.append(sync_pat_record_to_project_workbook(cursor, pat_id))
            audit_event(
                "PAT_CREATED",
                "pat_record",
                pat_id,
                f"Created PAT record for {du_id}.",
                conn=conn,
            )
            conn.commit()
        except (ValueError, psycopg2.Error, WorkbookSafetyError) as exc:
            conn.rollback()
            restore_workbook_results(*workbook_results)
            cursor.close()
            conn.close()
            flash(workbook_error_message(exc) if isinstance(exc, WorkbookSafetyError) else str(exc))
            return redirect(url_for("new_pat_record", du_id=du_id))

        cursor.close()
        conn.close()
        return redirect(url_for("pat_history", duid=du_id))

    record = {
        "duid": du_id,
        "project_id": site.get("project_id"),
        "pat_date": date.today(),
        "result": "PENDING",
        "vendor_name": site.get("vendor") or "",
        "towerco_customer": site.get("towerco") or "",
    }
    cursor.close()
    conn.close()

    return render_template(
        "pat_form.html",
        mode="new",
        site=site,
        record=record,
        projects=get_projects_for_select(),
        results=PAT_RESULTS,
    )


@app.route("/pat/<int:pat_id>/edit", methods=["GET", "POST"])
@permission_required("manage_pat")
def edit_pat_record(pat_id):

    conn = connect_db()
    cursor = conn.cursor()
    record = get_pat_record(cursor, pat_id)

    if not record:
        cursor.close()
        conn.close()
        return "PAT record not found"

    site = get_site_by_duid(cursor, record["duid"])

    if request.method == "POST":
        workbook_results = []

        try:
            pat_data = collect_pat_form_data(cursor, record["duid"], site, record)
            project_key = project_key_for_site_files(cursor, pat_data["project_id"])
            document_filename, document_path = save_pat_document(cursor, record, project_key)
            cursor.execute(
                """
                UPDATE pat_records
                SET project_id=%s,
                    pat_reference=%s,
                    pat_date=%s,
                    inspector_name=%s,
                    vendor_name=%s,
                    towerco_customer=%s,
                    result=%s,
                    remarks=%s,
                    document_filename=%s,
                    document_path=%s,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=%s
                """,
                (
                    pat_data["project_id"],
                    pat_data["pat_reference"],
                    pat_data["pat_date"],
                    pat_data["inspector_name"],
                    pat_data["vendor_name"],
                    pat_data["towerco_customer"],
                    pat_data["result"],
                    pat_data["remarks"],
                    document_filename,
                    document_path,
                    pat_id,
                ),
            )
            apply_latest_pat_to_site(cursor, record["duid"])
            workbook_results.append(sync_pat_record_to_project_workbook(cursor, pat_id))
            audit_event(
                "PAT_UPDATED",
                "pat_record",
                pat_id,
                "Updated PAT record.",
                conn=conn,
            )
            conn.commit()
        except (ValueError, psycopg2.Error, WorkbookSafetyError) as exc:
            conn.rollback()
            restore_workbook_results(*workbook_results)
            cursor.close()
            conn.close()
            flash(workbook_error_message(exc) if isinstance(exc, WorkbookSafetyError) else str(exc))
            return redirect(url_for("edit_pat_record", pat_id=pat_id))

        cursor.close()
        conn.close()
        return redirect(url_for("pat_history", duid=record["duid"]))

    cursor.close()
    conn.close()

    return render_template(
        "pat_form.html",
        mode="edit",
        site=site,
        record=record,
        projects=get_projects_for_select(),
        results=PAT_RESULTS,
    )


@app.route("/pat_records/<int:pat_id>/file")
@any_permission_required("view_pat", "manage_pat")
def pat_document_file(pat_id):

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT document_path,
               duid
        FROM pat_records
        WHERE id=%s
        """,
        (pat_id,),
    )
    record = row_to_dict(cursor)

    if record:
        denied = enforce_team_leader_site_scope(cursor, conn, record["duid"])

        if denied:
            cursor.close()
            conn.close()
            return denied

    cursor.close()
    conn.close()

    if not record or not record.get("document_path"):
        return "File not found"

    return send_stored_file(record["document_path"])


@app.route("/sites/<path:du_id>/acceptance", methods=["POST"])
@permission_required("manage_acceptance")
def site_acceptance_action(du_id):

    du_id = validate_duid_value(du_id)
    action = clean_text(request.form.get("action")) or "accept"
    acceptance_reference = clean_text(request.form.get("acceptance_reference"))
    remarks = clean_text(request.form.get("remarks"))
    override_used = request.form.get("override_used") == "on"
    override_reason = clean_text(request.form.get("override_reason"))

    conn = connect_db()
    cursor = conn.cursor()
    site = get_site_by_duid(cursor, du_id)

    if not site:
        cursor.close()
        conn.close()
        return "Site not found"

    project_id = clean_text(request.form.get("project_id")) or site.get("project_id")

    if project_id:
        cursor.execute("SELECT 1 FROM projects WHERE id=%s", (project_id,))

        if not cursor.fetchone():
            cursor.close()
            conn.close()
            return "Invalid project"

    acceptance_info = get_site_acceptance_info(cursor, du_id)
    readiness = acceptance_info["readiness"]

    try:
        if action == "reject":
            cursor.execute(
                """
                INSERT INTO site_acceptance(
                    duid,
                    telecom_site_id,
                    project_id,
                    acceptance_status,
                    accepted_by,
                    accepted_at,
                    acceptance_reference,
                    remarks,
                    override_used,
                    override_reason,
                    updated_at
                )
                VALUES(%s,%s,%s,'REJECTED',%s,CURRENT_TIMESTAMP,%s,%s,FALSE,NULL,CURRENT_TIMESTAMP)
                ON CONFLICT(duid)
                DO UPDATE SET
                    telecom_site_id=EXCLUDED.telecom_site_id,
                    project_id=EXCLUDED.project_id,
                    acceptance_status='REJECTED',
                    accepted_by=EXCLUDED.accepted_by,
                    accepted_at=CURRENT_TIMESTAMP,
                    acceptance_reference=EXCLUDED.acceptance_reference,
                    remarks=EXCLUDED.remarks,
                    override_used=FALSE,
                    override_reason=NULL,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    du_id,
                    site.get("operational_site_id"),
                    project_id or None,
                    session.get("admin", ""),
                    acceptance_reference,
                    remarks,
                ),
            )
            audit_event(
                "SITE_ACCEPTANCE_REJECTED",
                "telecom_site",
                du_id,
                "Rejected site acceptance.",
                conn=conn,
            )
            conn.commit()
            flash("Site acceptance was marked REJECTED.")
            cursor.close()
            conn.close()
            return redirect(url_for("site_detail", du_id=du_id))

        if action != "accept":
            raise ValueError("Invalid acceptance action")

        if override_used and not override_reason:
            raise ValueError("Override reason is required")

        if readiness["calculated_status"] != "READY" and not override_used:
            raise ValueError("Site is not ready for acceptance: " + " ".join(readiness["reasons"]))

        cursor.execute(
            """
            INSERT INTO telecom_sites(
                du_id,
                project_id,
                current_stage,
                overall_progress,
                overall_status,
                updated_at
            )
            VALUES(%s,%s,'Completed',100,'Completed',CURRENT_TIMESTAMP)
            ON CONFLICT(du_id)
            DO UPDATE SET
                project_id=COALESCE(EXCLUDED.project_id, telecom_sites.project_id),
                current_stage='Completed',
                overall_progress=100,
                overall_status='Completed',
                updated_at=CURRENT_TIMESTAMP
            RETURNING id
            """,
            (du_id, project_id or None),
        )
        telecom_site_id = cursor.fetchone()[0]

        cursor.execute(
            """
            INSERT INTO site_acceptance(
                duid,
                telecom_site_id,
                project_id,
                acceptance_status,
                accepted_by,
                accepted_at,
                acceptance_reference,
                remarks,
                override_used,
                override_reason,
                updated_at
            )
            VALUES(%s,%s,%s,'ACCEPTED',%s,CURRENT_TIMESTAMP,%s,%s,%s,%s,CURRENT_TIMESTAMP)
            ON CONFLICT(duid)
            DO UPDATE SET
                telecom_site_id=EXCLUDED.telecom_site_id,
                project_id=EXCLUDED.project_id,
                acceptance_status='ACCEPTED',
                accepted_by=EXCLUDED.accepted_by,
                accepted_at=CURRENT_TIMESTAMP,
                acceptance_reference=EXCLUDED.acceptance_reference,
                remarks=EXCLUDED.remarks,
                override_used=EXCLUDED.override_used,
                override_reason=EXCLUDED.override_reason,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                du_id,
                telecom_site_id,
                project_id or None,
                session.get("admin", ""),
                acceptance_reference,
                remarks,
                override_used,
                override_reason or None,
            ),
        )
        audit_event(
            "SITE_ACCEPTED",
            "telecom_site",
            du_id,
            "Accepted site and marked it Completed.",
            conn=conn,
        )
        conn.commit()
        flash("Site accepted and marked Completed.")
    except ValueError as exc:
        conn.rollback()
        flash(str(exc))

    cursor.close()
    conn.close()
    return redirect(url_for("site_detail", du_id=du_id))


#############################################
# PHASE 6: REPORTING, HANDOVER, MANAGEMENT
#############################################


REPORT_EXCEL_MIMETYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def report_text(value, fallback="-"):

    value = clean_text(value)
    return value if value else fallback


def report_date_stamp():

    return datetime.now().strftime("%Y%m%d")


def report_timestamp():

    return datetime.now().strftime("%Y%m%d_%H%M%S")


def clean_report_filename(value):

    return secure_filename(clean_text(value)) or "report"


def parse_report_date_filters(filters, from_key="date_from", to_key="date_to"):

    date_from = validate_date_field(filters.get(from_key), "Date From")
    date_to = validate_date_field(filters.get(to_key), "Date To")

    from_value = parse_date_value(date_from)
    to_value = parse_date_value(date_to)

    if from_value and to_value and from_value > to_value:
        raise ValueError("Date From cannot be after Date To.")

    return date_from, date_to


def workbook_response(wb, filename):

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype=REPORT_EXCEL_MIMETYPE,
    )


def style_report_sheet(ws):

    header_fill = PatternFill(start_color="0A2A66", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    thin = Side(style="thin", color="D9E1F2")

    if ws.max_row >= 1:
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = Border(bottom=thin)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    for column_cells in ws.columns:
        values = [clean_text(cell.value) for cell in column_cells]
        width = min(max([len(value) for value in values] + [10]) + 2, 45)
        ws.column_dimensions[column_cells[0].column_letter].width = width


def append_report_sheet(wb, title, headers, rows):

    ws = wb.create_sheet(title[:31])
    append_excel_row(ws, headers)

    for row in rows:
        append_excel_row(ws, row)

    style_report_sheet(ws)
    return ws


def build_report_workbook(title, sheet_specs):

    wb = Workbook()
    summary = wb.active
    summary.title = "SUMMARY"
    append_excel_row(summary, ["REPORT", title])
    append_excel_row(summary, ["GENERATED", datetime.now()])
    append_excel_row(summary, ["GENERATED BY", session.get("admin", "System")])
    summary.column_dimensions["A"].width = 22
    summary.column_dimensions["B"].width = 42
    summary["A1"].font = Font(bold=True)
    summary["A2"].font = Font(bold=True)
    summary["A3"].font = Font(bold=True)

    for sheet_title, headers, rows in sheet_specs:
        append_report_sheet(wb, sheet_title, headers, rows)

    return wb


def add_pdf_page_number(canvas, doc):

    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#555555"))
    canvas.drawRightString(A4[0] - 0.45 * inch, 0.35 * inch, f"Page {doc.page}")
    canvas.restoreState()


def pdf_paragraph(text, style):

    safe_text = report_text(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Paragraph(safe_text, style)


def build_pdf_bytes(title, sections):

    output = BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
        pageCompression=0,
    )
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="RucTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=18,
            textColor=colors.HexColor("#0A2A66"),
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="RucSection",
            parent=styles["Heading2"],
            fontSize=12,
            textColor=colors.HexColor("#0A2A66"),
            spaceBefore=10,
            spaceAfter=5,
        )
    )
    styles.add(
        ParagraphStyle(
            name="RucBodySmall",
            parent=styles["BodyText"],
            fontSize=8,
            leading=10,
        )
    )

    elements = [
        Paragraph("RUC SYSTEM", styles["RucTitle"]),
        Paragraph(title, styles["Heading1"]),
        Paragraph(
            f"Generated {datetime.now().strftime('%d/%m/%Y %H:%M')} by {report_text(session.get('admin'), 'System')}",
            styles["RucBodySmall"],
        ),
        Spacer(1, 10),
    ]

    for section_title, headers, rows in sections:
        elements.append(Paragraph(section_title, styles["RucSection"]))

        if not rows:
            elements.append(Paragraph("Not available", styles["RucBodySmall"]))
            elements.append(Spacer(1, 6))
            continue

        table_data = [[pdf_paragraph(header, styles["RucBodySmall"]) for header in headers]]
        for row in rows:
            table_data.append([pdf_paragraph(value, styles["RucBodySmall"]) for value in row])

        table = Table(table_data, repeatRows=1, hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0A2A66")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9E1F2")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F9FC")]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        elements.append(table)
        elements.append(Spacer(1, 8))

    doc.build(elements, onFirstPage=add_pdf_page_number, onLaterPages=add_pdf_page_number)
    output.seek(0)
    return output


def pdf_response(title, sections, filename):

    output = build_pdf_bytes(title, sections)
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf",
    )


def controlled_upload_abs_path(rel_path):

    rel_path = clean_text(rel_path).replace("\\", "/").lstrip("/")
    rel_path = os.path.normpath(rel_path).replace("\\", "/")

    if not rel_path or rel_path == "." or rel_path.startswith("../") or "/../" in rel_path:
        return None

    if not rel_path.startswith(("static/uploads/", "uploads/")):
        return None

    try:
        abs_path = safe_abs_path(*rel_path.split("/"))
    except ValueError:
        return None

    if not os.path.isfile(abs_path):
        return None

    return abs_path


def report_evidence_row(source, display_name, rel_path, status="AVAILABLE"):

    return {
        "source": source,
        "display_name": report_text(display_name, "Document"),
        "status": status,
        "rel_path": clean_text(rel_path),
    }


def fetch_site_completion_report(cursor, duid):

    duid = validate_duid_value(duid)
    site = get_site_by_duid(cursor, duid)

    if not site:
        return None

    cursor.execute(
        """
        SELECT sa.id,
               sa.project_id,
               sa.employee_id,
               sa.role,
               sa.assignment_status,
               sa.start_date,
               sa.end_date,
               p.project_name,
               p.project_code,
               e.first_name,
                              e.middle_name,
               e.last_name,
               e.position,
               e.telecom_role,
               e.nbi,
               e.wah_file,
               e.first_aid_file,
               e.nbi_expiry_date,
               e.wah_expiry_date,
               e.first_aid_expiry_date
        FROM site_assignments sa
        LEFT JOIN projects p ON sa.project_id = p.id
        LEFT JOIN employees e ON sa.employee_id = e.id
        WHERE sa.du_id=%s
        ORDER BY
            CASE WHEN sa.assignment_status='ACTIVE' THEN 0 ELSE 1 END,
            sa.id DESC
        """,
        (duid,),
    )
    assignments = rows_to_dicts(cursor)

    safety_counts = {"VALID": 0, "EXPIRING SOON": 0, "EXPIRED": 0, "MISSING": 0}

    for assignment in assignments:
        assignment["full_name"] = full_employee_name(assignment)
        assignment.update(safety_summary_from_employee(assignment))
        safety_counts[assignment["overall_safety_status"]] += 1

    cursor.execute(
        """
        SELECT dsl.id,
               dsl.project_id,
               dsl.report_date,
               dsl.current_stage,
               dsl.progress_before,
               dsl.progress_after,
               dsl.work_completed,
               dsl.blocker_category,
               dsl.blockers,
               dsl.next_day_plan,
               dsl.weather_notes,
               dsl.submitted_by,
               COALESCE(att.total_count, 0) AS attendance_count,
               COALESCE(att.present_count, 0) AS present_count,
               COALESCE(files.file_count, 0) AS file_count
        FROM daily_site_logs dsl
        LEFT JOIN (
            SELECT daily_log_id,
                   COUNT(*) AS total_count,
                   COUNT(*) FILTER (WHERE attendance_status IN ('Present','Late')) AS present_count
            FROM daily_attendance
            GROUP BY daily_log_id
        ) att ON att.daily_log_id=dsl.id
        LEFT JOIN (
            SELECT daily_log_id, COUNT(*) AS file_count
            FROM daily_log_files
            GROUP BY daily_log_id
        ) files ON files.daily_log_id=dsl.id
        WHERE dsl.duid=%s
        ORDER BY dsl.report_date DESC, dsl.created_at DESC, dsl.id DESC
        LIMIT 100
        """,
        (duid,),
    )
    daily_logs = rows_to_dicts(cursor)

    cursor.execute(
        """
        SELECT da.attendance_status,
               COUNT(*) AS status_count
        FROM daily_attendance da
        JOIN daily_site_logs dsl ON dsl.id=da.daily_log_id
        WHERE dsl.duid=%s
        GROUP BY da.attendance_status
        ORDER BY da.attendance_status
        """,
        (duid,),
    )
    attendance_summary = rows_to_dicts(cursor)

    punchlist_summary = get_punchlist_summary(cursor, duid)
    cursor.execute(
        """
        SELECT pi.id,
               pi.item_number,
               pi.category,
               pi.title,
               pi.priority,
               pi.status,
               pi.raised_date,
               pi.target_date,
               pi.rectified_date,
               pi.verified_date,
               pi.verified_by,
               e.first_name,
                              e.middle_name,
               e.last_name
        FROM punchlist_items pi
        LEFT JOIN employees e ON pi.assigned_employee_id=e.id
        WHERE pi.duid=%s
        ORDER BY pi.updated_at DESC, pi.id DESC
        LIMIT 100
        """,
        (duid,),
    )
    punchlist_items = rows_to_dicts(cursor)

    for item in punchlist_items:
        item["assigned_name"] = full_employee_name(item)

    cursor.execute(
        """
        SELECT id,
               pat_reference,
               pat_date,
               inspector_name,
               vendor_name,
               towerco_customer,
               result,
               remarks,
               document_filename,
               document_path,
               updated_at
        FROM pat_records
        WHERE duid=%s
        ORDER BY pat_date DESC, created_at DESC, id DESC
        LIMIT 20
        """,
        (duid,),
    )
    pat_records = rows_to_dicts(cursor)
    latest_pat = pat_records[0] if pat_records else None
    acceptance_info = get_site_acceptance_info(cursor, duid)

    evidence = []
    cursor.execute(
        """
        SELECT dlf.file_path,
               dlf.original_filename,
               dlf.file_type,
               dsl.report_date
        FROM daily_log_files dlf
        JOIN daily_site_logs dsl ON dsl.id=dlf.daily_log_id
        WHERE dsl.duid=%s
        ORDER BY dsl.report_date DESC, dlf.id DESC
        """,
        (duid,),
    )
    for file_row in rows_to_dicts(cursor):
        label = f"{file_row.get('report_date')} {file_row.get('original_filename') or stored_file_display_name(file_row.get('file_path'))}"
        evidence.append(report_evidence_row("Daily Evidence", label, file_row.get("file_path")))

    cursor.execute(
        """
        SELECT pf.file_path,
               pf.filename,
               pf.file_type,
               pi.item_number
        FROM punchlist_files pf
        JOIN punchlist_items pi ON pi.id=pf.punchlist_item_id
        WHERE pi.duid=%s
        ORDER BY pf.id DESC
        """,
        (duid,),
    )
    for file_row in rows_to_dicts(cursor):
        label = f"{file_row.get('item_number') or 'Punchlist'} {file_row.get('filename') or stored_file_display_name(file_row.get('file_path'))}"
        evidence.append(report_evidence_row("Punchlist Evidence", label, file_row.get("file_path")))

    for record in pat_records:
        if record.get("document_path"):
            evidence.append(
                report_evidence_row(
                    "PAT Document",
                    record.get("document_filename") or stored_file_display_name(record.get("document_path")),
                    record.get("document_path"),
                )
            )

    cursor.execute(
        """
        SELECT sd.file_path,
               sd.original_filename,
               sd.document_type,
               e.first_name,
                              e.middle_name,
               e.last_name
        FROM safety_documents sd
        JOIN employees e ON e.id=sd.employee_id
        WHERE sd.is_current=TRUE
          AND (
              e.assigned_du_id=%s
              OR EXISTS (
                  SELECT 1
                  FROM site_assignments sa
                  WHERE sa.employee_id=e.id
                    AND sa.du_id=%s
                    AND sa.assignment_status='ACTIVE'
              )
          )
        ORDER BY e.first_name, e.last_name, sd.document_type
        """,
        (duid, duid),
    )
    for doc in rows_to_dicts(cursor):
        if doc.get("file_path"):
            label = f"{full_employee_name(doc)} {doc.get('document_type')} {doc.get('original_filename') or stored_file_display_name(doc.get('file_path'))}"
            evidence.append(report_evidence_row("Safety Document", label, doc.get("file_path")))

    return {
        "site": site,
        "assignments": assignments,
        "safety_counts": safety_counts,
        "daily_logs": daily_logs,
        "attendance_summary": attendance_summary,
        "punchlist_summary": punchlist_summary,
        "punchlist_items": punchlist_items,
        "latest_pat": latest_pat,
        "pat_records": pat_records,
        "acceptance_info": acceptance_info,
        "evidence": evidence,
    }


def site_report_pdf_sections(data):

    site = data["site"]
    acceptance = data["acceptance_info"].get("record") or {}
    readiness = data["acceptance_info"].get("readiness") or {}
    summary_rows = [
        ["DUID", site.get("du_id")],
        ["Site Name", site.get("display_site_name")],
        ["Project", site.get("project_name") or site.get("project_code")],
        ["Region / Province", site.get("region_province")],
        ["TowerCo", site.get("towerco")],
        ["Vendor", site.get("vendor")],
        ["Stage", site.get("current_stage")],
        ["Progress", f"{site.get('overall_progress') or 0}%"],
        ["Status", site.get("overall_status")],
        ["PAT Status", site.get("pat_status")],
    ]
    personnel_rows = [
        [
            row.get("full_name"),
            row.get("role") or row.get("telecom_role") or row.get("position"),
            row.get("assignment_status"),
            row.get("overall_safety_status"),
        ]
        for row in data["assignments"][:20]
    ]
    daily_rows = [
        [
            row.get("report_date"),
            f"{row.get('progress_before')}% to {row.get('progress_after')}%",
            row.get("work_completed"),
            row.get("blocker_category") or row.get("blockers"),
            f"{row.get('present_count')}/{row.get('attendance_count')}",
        ]
        for row in data["daily_logs"][:15]
    ]
    punch_rows = [
        [
            "Total",
            data["punchlist_summary"].get("total"),
            "Open",
            data["punchlist_summary"].get("open_count"),
            "High/Critical Unresolved",
            data["punchlist_summary"].get("critical_high_unresolved"),
        ]
    ] + [
        [
            item.get("item_number") or item.get("id"),
            item.get("title"),
            item.get("priority"),
            item.get("status"),
            item.get("assigned_name"),
            item.get("target_date"),
        ]
        for item in data["punchlist_items"][:10]
    ]
    latest_pat = data["latest_pat"] or {}
    pat_rows = [
        ["Reference", latest_pat.get("pat_reference")],
        ["Date", latest_pat.get("pat_date")],
        ["Inspector", latest_pat.get("inspector_name")],
        ["Result", latest_pat.get("result")],
        ["Remarks", latest_pat.get("remarks")],
    ]
    acceptance_rows = [
        ["Display Status", data["acceptance_info"].get("display_status")],
        ["Calculated Readiness", readiness.get("calculated_status")],
        ["Accepted By", acceptance.get("accepted_by")],
        ["Accepted Date", acceptance.get("accepted_at")],
        ["Reference", acceptance.get("acceptance_reference")],
        ["Remarks", acceptance.get("remarks")],
    ]
    evidence_rows = [
        [row.get("source"), row.get("display_name"), row.get("status")]
        for row in data["evidence"][:25]
    ]
    return [
        ("Site Information", ["Field", "Value"], summary_rows),
        ("Personnel", ["Employee", "Role", "Assignment", "Safety"], personnel_rows),
        ("Daily Operations", ["Date", "Progress", "Work Completed", "Blocker", "Attendance"], daily_rows),
        ("Punchlist", ["A", "B", "C", "D", "E", "F"], punch_rows),
        ("PAT", ["Field", "Value"], pat_rows),
        ("Acceptance", ["Field", "Value"], acceptance_rows),
        ("Evidence Summary", ["Source", "Document", "Status"], evidence_rows),
    ]


def build_site_report_workbook(data):

    site = data["site"]
    safety_rows = [
        [status, count] for status, count in data["safety_counts"].items()
    ]
    return build_report_workbook(
        f"Site Completion Report {site.get('du_id')}",
        [
            (
                "SITE",
                ["FIELD", "VALUE"],
                [
                    ["DUID", site.get("du_id")],
                    ["SITE NAME", site.get("display_site_name")],
                    ["PROJECT", site.get("project_name") or site.get("project_code")],
                    ["REGION", site.get("region_province")],
                    ["TOWERCO", site.get("towerco")],
                    ["VENDOR", site.get("vendor")],
                    ["STAGE", site.get("current_stage")],
                    ["PROGRESS", site.get("overall_progress")],
                    ["STATUS", site.get("overall_status")],
                    ["PAT STATUS", site.get("pat_status")],
                ],
            ),
            (
                "PERSONNEL",
                ["EMPLOYEE", "ROLE", "ASSIGNMENT", "NBI", "WAH", "FIRST AID", "OVERALL"],
                [
                    [
                        row.get("full_name"),
                        row.get("role") or row.get("telecom_role") or row.get("position"),
                        row.get("assignment_status"),
                        row.get("nbi_status"),
                        row.get("wah_status"),
                        row.get("first_aid_status"),
                        row.get("overall_safety_status"),
                    ]
                    for row in data["assignments"]
                ],
            ),
            ("SAFETY", ["STATUS", "COUNT"], safety_rows),
            (
                "DAILY OPS",
                ["DATE", "STAGE", "PROGRESS BEFORE", "PROGRESS AFTER", "WORK COMPLETED", "BLOCKERS", "NEXT PLAN", "ATTENDANCE"],
                [
                    [
                        row.get("report_date"),
                        row.get("current_stage"),
                        row.get("progress_before"),
                        row.get("progress_after"),
                        row.get("work_completed"),
                        row.get("blocker_category") or row.get("blockers"),
                        row.get("next_day_plan"),
                        f"{row.get('present_count')}/{row.get('attendance_count')}",
                    ]
                    for row in data["daily_logs"]
                ],
            ),
            (
                "PUNCHLIST",
                ["ITEM", "TITLE", "PRIORITY", "STATUS", "ASSIGNED", "RAISED", "TARGET", "VERIFIED"],
                [
                    [
                        row.get("item_number") or row.get("id"),
                        row.get("title"),
                        row.get("priority"),
                        row.get("status"),
                        row.get("assigned_name"),
                        row.get("raised_date"),
                        row.get("target_date"),
                        row.get("verified_by"),
                    ]
                    for row in data["punchlist_items"]
                ],
            ),
            (
                "PAT",
                ["REFERENCE", "DATE", "INSPECTOR", "VENDOR", "RESULT", "REMARKS"],
                [
                    [
                        row.get("pat_reference"),
                        row.get("pat_date"),
                        row.get("inspector_name"),
                        row.get("vendor_name"),
                        row.get("result"),
                        row.get("remarks"),
                    ]
                    for row in data["pat_records"]
                ],
            ),
            (
                "EVIDENCE INDEX",
                ["SOURCE", "DOCUMENT", "STATUS"],
                [
                    [row.get("source"), row.get("display_name"), row.get("status")]
                    for row in data["evidence"]
                ],
            ),
        ],
    )


def fetch_project_management_report(cursor, project_id):

    cursor.execute(
        """
        SELECT id, project_name, region, company, project_code, date_created
        FROM projects
        WHERE id=%s
        """,
        (project_id,),
    )
    project = row_to_dict(cursor)

    if not project:
        return None

    cursor.execute(
        f"""
        {SITE_REFERENCE_CTE}
        SELECT d.du_id,
               COALESCE(g.site_name, g.sitename, g.globe_du_name, pr.planning_du_name) AS display_site_name,
               COALESCE(ts.current_stage, 'Planning') AS current_stage,
               COALESCE(ts.overall_progress, 0) AS overall_progress,
               COALESCE(ts.overall_status, 'Not Started') AS overall_status,
               COALESCE(ts.pat_status, 'MISSING') AS pat_status,
               COALESCE(pl.open_count, 0) AS open_punchlists,
               COALESCE(pl.critical_high, 0) AS critical_high_punchlists,
               COALESCE(latest_pat.result, ts.pat_status, 'MISSING') AS latest_pat_result,
               COALESCE(sa.acceptance_status, 'NOT READY') AS acceptance_status,
               last_log.report_date AS last_activity
        {SITE_FROM_JOINS}
        LEFT JOIN (
            SELECT duid,
                   COUNT(*) FILTER (WHERE status <> 'CLOSED') AS open_count,
                   COUNT(*) FILTER (
                       WHERE priority IN ('CRITICAL','HIGH')
                         AND status = ANY(%s)
                   ) AS critical_high
            FROM punchlist_items
            GROUP BY duid
        ) pl ON pl.duid=d.du_id
        LEFT JOIN (
            SELECT DISTINCT ON (duid)
                   duid,
                   result
            FROM pat_records
            ORDER BY duid, pat_date DESC, created_at DESC, id DESC
        ) latest_pat ON latest_pat.duid=d.du_id
        LEFT JOIN site_acceptance sa ON sa.duid=d.du_id
        LEFT JOIN (
            SELECT DISTINCT ON (duid)
                   duid,
                   report_date
            FROM daily_site_logs
            ORDER BY duid, report_date DESC, created_at DESC, id DESC
        ) last_log ON last_log.duid=d.du_id
        WHERE ts.project_id=%s
           OR EXISTS (
               SELECT 1
               FROM site_assignments sx
               WHERE sx.du_id=d.du_id
                 AND sx.project_id=%s
           )
        ORDER BY d.du_id
        """,
        (PUNCHLIST_UNRESOLVED_STATUSES, project_id, project_id),
    )
    sites = rows_to_dicts(cursor)

    total_sites = len(sites)
    active_sites = len([site for site in sites if site.get("overall_status") == "Active"])
    blocked_sites = len([site for site in sites if site.get("overall_status") in ("Blocked", "On Hold")])
    completed_sites = len([site for site in sites if site.get("overall_status") == "Completed"])
    average_progress = round(
        sum([site.get("overall_progress") or 0 for site in sites]) / total_sites,
        1,
    ) if total_sites else 0
    sites_awaiting_pat = len([site for site in sites if site.get("latest_pat_result") in ("MISSING", "PENDING", None, "")])
    pat_passed = len([site for site in sites if site.get("latest_pat_result") in ("PASSED", "PASSED WITH PUNCHLIST")])
    pat_failed = len([site for site in sites if site.get("latest_pat_result") == "FAILED"])
    open_punchlist_sites = len([site for site in sites if (site.get("open_punchlists") or 0) > 0])
    critical_high_punchlists = sum([site.get("critical_high_punchlists") or 0 for site in sites])
    accepted_sites = len([site for site in sites if site.get("acceptance_status") == "ACCEPTED"])

    cursor.execute(
        """
        SELECT e.id,
               e.first_name,
                              e.middle_name,
               e.last_name,
               e.position,
               e.telecom_role,
               e.assigned_du_id,
               e.nbi,
               e.wah_file,
               e.first_aid_file,
               e.nbi_expiry_date,
               e.wah_expiry_date,
               e.first_aid_expiry_date
        FROM employees e
        WHERE e.project_id=%s
           OR EXISTS (
               SELECT 1
               FROM site_assignments sa
               WHERE sa.employee_id=e.id
                 AND sa.project_id=%s
           )
        ORDER BY e.first_name, e.last_name, e.id
        """,
        (project_id, project_id),
    )
    personnel = rows_to_dicts(cursor)
    safety_counts = {"VALID": 0, "EXPIRING SOON": 0, "EXPIRED": 0, "MISSING": 0}

    for person in personnel:
        person["full_name"] = full_employee_name(person)
        person.update(safety_summary_from_employee(person))
        safety_counts[person["overall_safety_status"]] += 1

    cursor.execute(
        """
        SELECT COUNT(*) AS report_count,
               COUNT(DISTINCT duid) AS reporting_sites,
               COALESCE(SUM(progress_after - progress_before), 0) AS progress_movement,
               COUNT(*) FILTER (
                   WHERE COALESCE(TRIM(blockers), '') <> ''
                      OR COALESCE(TRIM(blocker_category), '') <> ''
               ) AS blocker_reports
        FROM daily_site_logs
        WHERE project_id=%s
        """,
        (project_id,),
    )
    daily_summary = row_to_dict(cursor) or {}

    cursor.execute(
        """
        SELECT da.attendance_status,
               COUNT(*) AS status_count
        FROM daily_attendance da
        JOIN daily_site_logs dsl ON dsl.id=da.daily_log_id
        WHERE dsl.project_id=%s
        GROUP BY da.attendance_status
        ORDER BY da.attendance_status
        """,
        (project_id,),
    )
    attendance_summary = rows_to_dicts(cursor)

    cursor.execute(
        """
        SELECT id,
               duid,
               report_date,
               current_stage,
               progress_before,
               progress_after,
               work_completed,
               blocker_category
        FROM daily_site_logs
        WHERE project_id=%s
        ORDER BY report_date DESC, created_at DESC, id DESC
        LIMIT 25
        """,
        (project_id,),
    )
    recent_activity = rows_to_dicts(cursor)

    return {
        "project": project,
        "sites": sites,
        "personnel": personnel,
        "safety_counts": safety_counts,
        "daily_summary": daily_summary,
        "attendance_summary": attendance_summary,
        "recent_activity": recent_activity,
        "summary": {
            "total_sites": total_sites,
            "active_sites": active_sites,
            "blocked_sites": blocked_sites,
            "completed_sites": completed_sites,
            "average_progress": average_progress,
            "sites_awaiting_pat": sites_awaiting_pat,
            "pat_passed": pat_passed,
            "pat_failed": pat_failed,
            "open_punchlist_sites": open_punchlist_sites,
            "critical_high_punchlists": critical_high_punchlists,
            "personnel_assigned": len(personnel),
            "accepted_sites": accepted_sites,
        },
    }


def project_report_pdf_sections(data):

    summary = data["summary"]
    summary_rows = [[key.replace("_", " ").title(), value] for key, value in summary.items()]
    site_rows = [
        [
            site.get("du_id"),
            site.get("display_site_name"),
            site.get("current_stage"),
            f"{site.get('overall_progress') or 0}%",
            site.get("overall_status"),
            site.get("open_punchlists"),
            site.get("latest_pat_result"),
            site.get("acceptance_status"),
            site.get("last_activity"),
        ]
        for site in data["sites"][:30]
    ]
    safety_rows = [[status, count] for status, count in data["safety_counts"].items()]
    activity_rows = [
        [
            row.get("report_date"),
            row.get("duid"),
            f"{row.get('progress_before')}% to {row.get('progress_after')}%",
            row.get("work_completed"),
            row.get("blocker_category"),
        ]
        for row in data["recent_activity"][:15]
    ]
    return [
        ("Project Summary", ["Metric", "Value"], summary_rows),
        ("Sites", ["DUID", "Site", "Stage", "Progress", "Status", "Punchlist", "PAT", "Acceptance", "Last Activity"], site_rows),
        ("Safety", ["Status", "Personnel"], safety_rows),
        ("Recent Daily Activity", ["Date", "DUID", "Progress", "Work Completed", "Blocker"], activity_rows),
    ]


def build_project_report_workbook(data):

    project = data["project"]
    return build_report_workbook(
        f"Project Management Report {project.get('project_code')}",
        [
            ("METRICS", ["METRIC", "VALUE"], [[key.replace("_", " ").upper(), value] for key, value in data["summary"].items()]),
            (
                "SITES",
                ["DUID", "SITE", "STAGE", "PROGRESS", "STATUS", "OPEN PUNCHLISTS", "PAT", "ACCEPTANCE", "LAST ACTIVITY"],
                [
                    [
                        site.get("du_id"),
                        site.get("display_site_name"),
                        site.get("current_stage"),
                        site.get("overall_progress"),
                        site.get("overall_status"),
                        site.get("open_punchlists"),
                        site.get("latest_pat_result"),
                        site.get("acceptance_status"),
                        site.get("last_activity"),
                    ]
                    for site in data["sites"]
                ],
            ),
            ("SAFETY", ["STATUS", "COUNT"], [[status, count] for status, count in data["safety_counts"].items()]),
            (
                "RECENT DAILY",
                ["DATE", "DUID", "STAGE", "PROGRESS BEFORE", "PROGRESS AFTER", "WORK COMPLETED", "BLOCKER"],
                [
                    [
                        row.get("report_date"),
                        row.get("duid"),
                        row.get("current_stage"),
                        row.get("progress_before"),
                        row.get("progress_after"),
                        row.get("work_completed"),
                        row.get("blocker_category"),
                    ]
                    for row in data["recent_activity"]
                ],
            ),
        ],
    )


def fetch_personnel_report(cursor, filters):

    conditions = ["TRUE"]
    params = []

    if filters.get("project_id"):
        conditions.append("(e.project_id=%s OR sa.project_id=%s)")
        params.extend([filters["project_id"], filters["project_id"]])

    if filters.get("duid"):
        conditions.append("(e.assigned_du_id=%s OR sa.du_id=%s)")
        params.extend([filters["duid"], filters["duid"]])

    if filters.get("role"):
        conditions.append("(e.telecom_role ILIKE %s OR e.position ILIKE %s OR sa.role ILIKE %s)")
        role_param = "%" + filters["role"] + "%"
        params.extend([role_param, role_param, role_param])

    add_team_leader_employee_scope(cursor, conditions, params, "e.id")

    cursor.execute(
        f"""
        SELECT DISTINCT ON (e.id)
               e.id,
               e.project_id,
               e.first_name,
                              e.middle_name,
               e.last_name,
               e.position,
               e.telecom_role,
               e.assigned_du_id,
               e.nbi,
               e.wah_file,
               e.first_aid_file,
               e.nbi_expiry_date,
               e.wah_expiry_date,
               e.first_aid_expiry_date,
               p.project_name,
               p.project_code,
               sa.du_id AS assignment_duid,
               sa.role AS assignment_role,
               sa.assignment_status
        FROM employees e
        LEFT JOIN projects p ON p.id=e.project_id
        LEFT JOIN site_assignments sa
          ON sa.employee_id=e.id
         AND sa.assignment_status='ACTIVE'
        WHERE {' AND '.join(conditions)}
        ORDER BY e.id, sa.id DESC NULLS LAST
        LIMIT 1000
        """,
        params,
    )
    rows = rows_to_dicts(cursor)
    filtered_rows = []
    summary = {
        "total_personnel": 0,
        "active_assignments": 0,
        "VALID": 0,
        "EXPIRING SOON": 0,
        "EXPIRED": 0,
        "MISSING": 0,
        "nbi_valid": 0,
        "wah_valid": 0,
        "first_aid_valid": 0,
    }

    for row in rows:
        row["full_name"] = full_employee_name(row)
        row["display_duid"] = row.get("assignment_duid") or row.get("assigned_du_id")
        row["display_role"] = row.get("assignment_role") or row.get("telecom_role") or row.get("position")
        row.update(safety_summary_from_employee(row))

        if filters.get("safety_status") and row["overall_safety_status"] != filters["safety_status"]:
            continue

        filtered_rows.append(row)
        summary["total_personnel"] += 1

        if row.get("assignment_status") == "ACTIVE":
            summary["active_assignments"] += 1

        summary[row["overall_safety_status"]] += 1

        if row["nbi_status"] == "VALID":
            summary["nbi_valid"] += 1
        if row["wah_status"] == "VALID":
            summary["wah_valid"] += 1
        if row["first_aid_status"] == "VALID":
            summary["first_aid_valid"] += 1

    return {"rows": filtered_rows, "summary": summary}


def build_personnel_workbook(data):

    return build_report_workbook(
        "Personnel Safety Report",
        [
            ("METRICS", ["METRIC", "VALUE"], [[key.replace("_", " ").upper(), value] for key, value in data["summary"].items()]),
            (
                "PERSONNEL",
                ["EMPLOYEE", "PROJECT", "DUID", "ROLE", "NBI", "WAH", "FIRST AID", "OVERALL"],
                [
                    [
                        row.get("full_name"),
                        row.get("project_name") or row.get("project_code"),
                        row.get("display_duid"),
                        row.get("display_role"),
                        row.get("nbi_status"),
                        row.get("wah_status"),
                        row.get("first_aid_status"),
                        row.get("overall_safety_status"),
                    ]
                    for row in data["rows"]
                ],
            ),
        ],
    )


def fetch_daily_report(cursor, filters):

    date_from, date_to = parse_report_date_filters(filters)
    conditions = ["TRUE"]
    params = []

    if filters.get("project_id"):
        conditions.append("dsl.project_id=%s")
        params.append(filters["project_id"])

    if filters.get("duid"):
        conditions.append("dsl.duid=%s")
        params.append(filters["duid"])

    if filters.get("stage"):
        stage = normalize_choice(filters["stage"], SITE_STAGES, "")

        if not stage:
            raise ValueError("Invalid site stage.")

        conditions.append("dsl.current_stage=%s")
        params.append(stage)

    if date_from:
        conditions.append("dsl.report_date >= %s")
        params.append(date_from)

    if date_to:
        conditions.append("dsl.report_date <= %s")
        params.append(date_to)

    add_team_leader_duid_scope(cursor, conditions, params, "dsl.duid")

    cursor.execute(
        f"""
        {SITE_REFERENCE_CTE}
        SELECT dsl.id,
               dsl.project_id,
               dsl.duid,
               dsl.report_date,
               dsl.current_stage,
               dsl.progress_before,
               dsl.progress_after,
               dsl.work_completed,
               dsl.blocker_category,
               dsl.blockers,
               dsl.next_day_plan,
               p.project_name,
               p.project_code,
               COALESCE(g.site_name, g.sitename, g.globe_du_name, pr.planning_du_name) AS display_site_name,
               COALESCE(att.total_count, 0) AS attendance_count,
               COALESCE(att.present_count, 0) AS present_count,
               COALESCE(att.absent_count, 0) AS absent_count,
               COALESCE(att.late_count, 0) AS late_count
        FROM daily_site_logs dsl
        LEFT JOIN globe_sites g ON g.du_id=dsl.duid
        LEFT JOIN planning_sites pr ON pr.du_id=dsl.duid
        LEFT JOIN projects p ON p.id=dsl.project_id
        LEFT JOIN (
            SELECT daily_log_id,
                   COUNT(*) AS total_count,
                   COUNT(*) FILTER (WHERE attendance_status IN ('Present','Late')) AS present_count,
                   COUNT(*) FILTER (WHERE attendance_status='Absent') AS absent_count,
                   COUNT(*) FILTER (WHERE attendance_status='Late') AS late_count
            FROM daily_attendance
            GROUP BY daily_log_id
        ) att ON att.daily_log_id=dsl.id
        WHERE {' AND '.join(conditions)}
        ORDER BY dsl.report_date DESC, dsl.created_at DESC, dsl.id DESC
        LIMIT 1000
        """,
        params,
    )
    rows = rows_to_dicts(cursor)
    summary = {
        "daily_reports": len(rows),
        "sites_reporting": len({row.get("duid") for row in rows}),
        "progress_movement": sum([(row.get("progress_after") or 0) - (row.get("progress_before") or 0) for row in rows]),
        "workers_present": sum([row.get("present_count") or 0 for row in rows]),
        "workers_absent": sum([row.get("absent_count") or 0 for row in rows]),
        "workers_late": sum([row.get("late_count") or 0 for row in rows]),
        "blockers_reported": len([row for row in rows if row.get("blocker_category") or row.get("blockers")]),
    }
    return {"rows": rows, "summary": summary}


def build_daily_report_workbook(data):

    return build_report_workbook(
        "Daily Operations Report",
        [
            ("METRICS", ["METRIC", "VALUE"], [[key.replace("_", " ").upper(), value] for key, value in data["summary"].items()]),
            (
                "DAILY OPS",
                ["DATE", "DUID", "SITE", "PROJECT", "STAGE", "PROGRESS BEFORE", "PROGRESS AFTER", "WORK COMPLETED", "BLOCKERS", "NEXT DAY PLAN", "PRESENT", "ABSENT", "LATE"],
                [
                    [
                        row.get("report_date"),
                        row.get("duid"),
                        row.get("display_site_name"),
                        row.get("project_name") or row.get("project_code"),
                        row.get("current_stage"),
                        row.get("progress_before"),
                        row.get("progress_after"),
                        row.get("work_completed"),
                        row.get("blocker_category") or row.get("blockers"),
                        row.get("next_day_plan"),
                        row.get("present_count"),
                        row.get("absent_count"),
                        row.get("late_count"),
                    ]
                    for row in data["rows"]
                ],
            ),
        ],
    )


def fetch_punchlist_report(cursor, filters):

    date_from, date_to = parse_report_date_filters(filters)
    conditions = ["TRUE"]
    params = []

    if filters.get("project_id"):
        conditions.append("pi.project_id=%s")
        params.append(filters["project_id"])

    if filters.get("duid"):
        conditions.append("pi.duid=%s")
        params.append(filters["duid"])

    if filters.get("priority"):
        priority = normalize_choice(filters["priority"], PUNCHLIST_PRIORITIES, "")

        if not priority:
            raise ValueError("Invalid punchlist priority.")

        conditions.append("pi.priority=%s")
        params.append(priority)

    if filters.get("status"):
        status = normalize_choice(filters["status"], PUNCHLIST_STATUSES, "")

        if not status:
            raise ValueError("Invalid punchlist status.")

        conditions.append("pi.status=%s")
        params.append(status)

    if filters.get("assigned_employee_id"):
        conditions.append("pi.assigned_employee_id=%s")
        params.append(filters["assigned_employee_id"])

    if date_from:
        conditions.append("pi.raised_date >= %s")
        params.append(date_from)

    if date_to:
        conditions.append("pi.raised_date <= %s")
        params.append(date_to)

    add_team_leader_duid_scope(cursor, conditions, params, "pi.duid")

    cursor.execute(
        f"""
        {SITE_REFERENCE_CTE}
        SELECT pi.id,
               pi.duid,
               pi.project_id,
               pi.item_number,
               pi.category,
               pi.title,
               pi.priority,
               pi.status,
               pi.raised_date,
               pi.target_date,
               pi.rectified_date,
               pi.verified_date,
               pi.verified_by,
               p.project_name,
               p.project_code,
               e.first_name,
                              e.middle_name,
               e.last_name,
               COALESCE(g.site_name, g.sitename, g.globe_du_name, pr.planning_du_name) AS display_site_name
        FROM punchlist_items pi
        LEFT JOIN globe_sites g ON g.du_id=pi.duid
        LEFT JOIN planning_sites pr ON pr.du_id=pi.duid
        LEFT JOIN projects p ON p.id=pi.project_id
        LEFT JOIN employees e ON e.id=pi.assigned_employee_id
        WHERE {' AND '.join(conditions)}
        ORDER BY pi.updated_at DESC, pi.id DESC
        LIMIT 1000
        """,
        params,
    )
    rows = rows_to_dicts(cursor)
    summary = {
        "total": len(rows),
        "open": 0,
        "in_progress": 0,
        "rectified": 0,
        "verified": 0,
        "closed": 0,
        "high": 0,
        "critical": 0,
        "overdue": 0,
    }

    for row in rows:
        row["assigned_name"] = full_employee_name(row)
        status_key = clean_text(row.get("status")).lower().replace(" ", "_")

        if status_key in summary:
            summary[status_key] += 1

        if row.get("priority") == "HIGH":
            summary["high"] += 1
        if row.get("priority") == "CRITICAL":
            summary["critical"] += 1

        target_date = parse_date_value(row.get("target_date"))

        if target_date and target_date < date.today() and row.get("status") != "CLOSED":
            summary["overdue"] += 1

    return {"rows": rows, "summary": summary}


def build_punchlist_report_workbook(data):

    return build_report_workbook(
        "Punchlist Report",
        [
            ("METRICS", ["METRIC", "VALUE"], [[key.replace("_", " ").upper(), value] for key, value in data["summary"].items()]),
            (
                "PUNCHLIST",
                ["ITEM", "DUID", "SITE", "TITLE", "PRIORITY", "STATUS", "ASSIGNED", "RAISED", "TARGET", "RECTIFIED", "VERIFIED", "VERIFIED BY"],
                [
                    [
                        row.get("item_number") or row.get("id"),
                        row.get("duid"),
                        row.get("display_site_name"),
                        row.get("title"),
                        row.get("priority"),
                        row.get("status"),
                        row.get("assigned_name"),
                        row.get("raised_date"),
                        row.get("target_date"),
                        row.get("rectified_date"),
                        row.get("verified_date"),
                        row.get("verified_by"),
                    ]
                    for row in data["rows"]
                ],
            ),
        ],
    )


def fetch_pat_acceptance_report(cursor, filters):

    date_from, date_to = parse_report_date_filters(filters)
    conditions = ["TRUE"]
    params = []

    if filters.get("project_id"):
        conditions.append("(pr.project_id=%s OR sa.project_id=%s)")
        params.extend([filters["project_id"], filters["project_id"]])

    if filters.get("duid"):
        conditions.append("COALESCE(pr.duid, sa.duid)=%s")
        params.append(filters["duid"])

    if filters.get("result"):
        result = normalize_choice(filters["result"], PAT_RESULTS, "")

        if not result:
            raise ValueError("Invalid PAT result.")

        conditions.append("pr.result=%s")
        params.append(result)

    if filters.get("acceptance_status"):
        status = normalize_choice(filters["acceptance_status"], ACCEPTANCE_STATUSES, "")

        if not status:
            raise ValueError("Invalid acceptance status.")

        conditions.append("COALESCE(sa.acceptance_status, 'NOT READY')=%s")
        params.append(status)

    if date_from:
        conditions.append("(pr.pat_date IS NULL OR pr.pat_date >= %s)")
        params.append(date_from)

    if date_to:
        conditions.append("(pr.pat_date IS NULL OR pr.pat_date <= %s)")
        params.append(date_to)

    add_team_leader_duid_scope(cursor, conditions, params, "COALESCE(pr.duid, sa.duid)")

    cursor.execute(
        f"""
        {SITE_REFERENCE_CTE}
        SELECT pr.id AS pat_id,
               COALESCE(pr.duid, sa.duid) AS duid,
               COALESCE(pr.project_id, sa.project_id) AS project_id,
               pr.pat_reference,
               pr.pat_date,
               pr.inspector_name,
               pr.vendor_name,
               pr.result,
               pr.remarks AS pat_remarks,
               COALESCE(sa.acceptance_status, 'NOT READY') AS acceptance_status,
               sa.accepted_by,
               sa.accepted_at,
               sa.acceptance_reference,
               sa.remarks AS acceptance_remarks,
               p.project_name,
               p.project_code,
               COALESCE(g.site_name, g.sitename, g.globe_du_name, ps.planning_du_name) AS display_site_name
        FROM pat_records pr
        FULL OUTER JOIN site_acceptance sa ON sa.duid=pr.duid
        LEFT JOIN globe_sites g ON g.du_id=COALESCE(pr.duid, sa.duid)
        LEFT JOIN planning_sites ps ON ps.du_id=COALESCE(pr.duid, sa.duid)
        LEFT JOIN projects p ON p.id=COALESCE(pr.project_id, sa.project_id)
        WHERE {' AND '.join(conditions)}
        ORDER BY COALESCE(pr.pat_date, sa.accepted_at::date) DESC NULLS LAST,
                 COALESCE(pr.id, sa.id) DESC
        LIMIT 1000
        """,
        params,
    )
    rows = rows_to_dicts(cursor)
    summary = {
        "pat_pending": 0,
        "pat_passed": 0,
        "pat_passed_with_punchlist": 0,
        "pat_failed": 0,
        "accepted_sites": 0,
        "not_ready": 0,
        "ready": 0,
    }

    accepted_duids = set()
    ready_duids = set()

    for row in rows:
        result = row.get("result") or "PENDING"
        if result == "PENDING":
            summary["pat_pending"] += 1
        elif result == "PASSED":
            summary["pat_passed"] += 1
        elif result == "PASSED WITH PUNCHLIST":
            summary["pat_passed_with_punchlist"] += 1
        elif result == "FAILED":
            summary["pat_failed"] += 1

        if row.get("acceptance_status") == "ACCEPTED":
            accepted_duids.add(row.get("duid"))
        elif row.get("acceptance_status") == "READY":
            ready_duids.add(row.get("duid"))
        else:
            summary["not_ready"] += 1

    summary["accepted_sites"] = len([duid for duid in accepted_duids if duid])
    summary["ready"] = len([duid for duid in ready_duids if duid])

    return {"rows": rows, "summary": summary}


def build_pat_acceptance_workbook(data):

    return build_report_workbook(
        "PAT Acceptance Report",
        [
            ("METRICS", ["METRIC", "VALUE"], [[key.replace("_", " ").upper(), value] for key, value in data["summary"].items()]),
            (
                "PAT ACCEPTANCE",
                ["DUID", "SITE", "PROJECT", "PAT REF", "PAT DATE", "INSPECTOR", "RESULT", "ACCEPTANCE", "ACCEPTED BY", "ACCEPTED AT", "REFERENCE"],
                [
                    [
                        row.get("duid"),
                        row.get("display_site_name"),
                        row.get("project_name") or row.get("project_code"),
                        row.get("pat_reference"),
                        row.get("pat_date"),
                        row.get("inspector_name"),
                        row.get("result"),
                        row.get("acceptance_status"),
                        row.get("accepted_by"),
                        row.get("accepted_at"),
                        row.get("acceptance_reference"),
                    ]
                    for row in data["rows"]
                ],
            ),
        ],
    )


def handover_readiness_label(data):

    site = data["site"]
    acceptance_status = data["acceptance_info"].get("display_status")

    if acceptance_status == "ACCEPTED" and site.get("overall_status") == "Completed":
        return "FINAL HANDOVER PACKAGE"

    if acceptance_status in ("READY", "ACCEPTED"):
        return "READY DRAFT HANDOVER PACKAGE"

    return "DRAFT HANDOVER PACKAGE"


def add_handover_file(zip_file, evidence, archive_folder, used_names, index_rows):

    display_name = evidence.get("display_name") or "Document"
    rel_path = evidence.get("rel_path")
    abs_path = controlled_upload_abs_path(rel_path)

    if not abs_path:
        index_rows.append([evidence.get("source"), display_name, "Missing or unavailable"])
        return

    safe_name = secure_filename(display_name) or os.path.basename(abs_path)
    archive_name = f"{archive_folder}/{safe_name}"
    counter = 2

    while archive_name in used_names:
        stem, ext = os.path.splitext(safe_name)
        archive_name = f"{archive_folder}/{stem}_{counter}{ext}"
        counter += 1

    used_names.add(archive_name)
    zip_file.write(abs_path, archive_name)
    index_rows.append([evidence.get("source"), display_name, "Copied"])


def build_handover_readme(data):

    site = data["site"]
    acceptance_info = data["acceptance_info"]
    readiness = acceptance_info.get("readiness") or {}
    reasons = list(readiness.get("reasons") or [])

    if handover_readiness_label(data) != "FINAL HANDOVER PACKAGE":
        record = acceptance_info.get("record") or {}
        if record.get("acceptance_status") != "ACCEPTED" and "Site acceptance is not ready." not in reasons:
            reasons.append("Site acceptance is not ready.")

    lines = [
        f"RUC System handover package for {site['du_id']}",
        f"Package type: {handover_readiness_label(data)}",
        f"Readiness: {readiness.get('calculated_status') or acceptance_info.get('display_status') or 'UNKNOWN'}",
        f"Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
    ]

    if reasons:
        lines.extend(["", "Blocking Reasons:"])
        lines.extend(f"- {reason}" for reason in reasons)

    lines.extend(
        [
            "",
            "Original evidence files were copied, not moved.",
            "Missing evidence is listed in Document_Index.xlsx.",
            "",
        ]
    )

    return "\n".join(lines)


def build_document_index_workbook(index_rows):

    return build_report_workbook(
        "Handover Document Index",
        [("DOCUMENT INDEX", ["SOURCE", "DOCUMENT", "STATUS"], index_rows)],
    )


@app.route("/reports")
@permission_required("export_reports")
def reports_center():

    conn = connect_db()
    cursor = conn.cursor()

    if is_team_leader_role():
        duids = get_team_leader_duids(cursor)

        if duids:
            cursor.execute(
                "SELECT COUNT(DISTINCT project_id) FROM teams WHERE active IS TRUE AND du_id = ANY(%s)",
                (duids,),
            )
            total_projects = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM telecom_sites WHERE du_id = ANY(%s)", (duids,))
            operational_sites = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM daily_site_logs WHERE duid = ANY(%s)", (duids,))
            daily_reports = cursor.fetchone()[0]
            cursor.execute(
                "SELECT COUNT(*) FROM punchlist_items WHERE duid = ANY(%s) AND status <> 'CLOSED'",
                (duids,),
            )
            open_punchlists = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM pat_records WHERE duid = ANY(%s)", (duids,))
            pat_records_count = cursor.fetchone()[0]
            cursor.execute(
                "SELECT COUNT(*) FROM site_acceptance WHERE duid = ANY(%s) AND acceptance_status='ACCEPTED'",
                (duids,),
            )
            accepted_sites = cursor.fetchone()[0]
        else:
            total_projects = 0
            operational_sites = 0
            daily_reports = 0
            open_punchlists = 0
            pat_records_count = 0
            accepted_sites = 0
    elif is_hr_role():
        total_projects = 0
        operational_sites = 0
        daily_reports = 0
        open_punchlists = 0
        pat_records_count = 0
        accepted_sites = 0
    else:
        cursor.execute("SELECT COUNT(*) FROM projects")
        total_projects = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM telecom_sites")
        operational_sites = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM daily_site_logs")
        daily_reports = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM punchlist_items WHERE status <> 'CLOSED'")
        open_punchlists = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM pat_records")
        pat_records_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM site_acceptance WHERE acceptance_status='ACCEPTED'")
        accepted_sites = cursor.fetchone()[0]

    cursor.close()
    conn.close()

    return render_template(
        "reports.html",
        total_projects=total_projects,
        operational_sites=operational_sites,
        daily_reports=daily_reports,
        open_punchlists=open_punchlists,
        pat_records_count=pat_records_count,
        accepted_sites=accepted_sites,
        show_operational_reports=is_super_admin_role() or is_team_leader_role(),
        show_project_reports=is_super_admin_role(),
        show_personnel_reports=can("manage_personnel") or can("manage_safety") or can("view_team"),
    )


@app.route("/reports/site")
@any_permission_required("manage_sites", "team_leader_portal")
def site_report_select():

    duid = clean_text(request.args.get("duid"))

    if duid:
        try:
            duid = validate_duid_value(duid)
        except ValueError as exc:
            flash(str(exc))
        else:
            return redirect(url_for("site_completion_report", duid=duid))

    if is_team_leader_role():
        conn = connect_db()
        cursor = conn.cursor()
        duids = get_team_leader_duids(cursor)
        cursor.close()
        conn.close()
    else:
        duids = get_duids_for_select()

    return render_template(
        "report_site_select.html",
        duids=duids,
    )


@app.route("/reports/site/<path:duid>")
@any_permission_required("manage_sites", "team_leader_portal")
def site_completion_report(duid):

    conn = connect_db()
    cursor = conn.cursor()
    denied = enforce_team_leader_site_scope(cursor, conn, validate_duid_value(duid))

    if denied:
        cursor.close()
        conn.close()
        return denied

    data = fetch_site_completion_report(cursor, duid)
    cursor.close()
    conn.close()

    if not data:
        return "Site not found"

    return render_template("site_completion_report.html", **data)


@app.route("/reports/site/<path:duid>/pdf")
@any_permission_required("manage_sites", "team_leader_portal")
def site_completion_report_pdf(duid):

    conn = connect_db()
    cursor = conn.cursor()
    denied = enforce_team_leader_site_scope(cursor, conn, validate_duid_value(duid))

    if denied:
        cursor.close()
        conn.close()
        return denied

    data = fetch_site_completion_report(cursor, duid)
    cursor.close()
    conn.close()

    if not data:
        return "Site not found"

    safe_duid = clean_report_filename(data["site"]["du_id"])
    filename = f"RUC_SITE_{safe_duid}_{report_date_stamp()}.pdf"
    return pdf_response(
        f"Site Completion Report - {data['site']['du_id']}",
        site_report_pdf_sections(data),
        filename,
    )


@app.route("/reports/site/<path:duid>/excel")
@any_permission_required("manage_sites", "team_leader_portal")
def site_completion_report_excel(duid):

    conn = connect_db()
    cursor = conn.cursor()
    denied = enforce_team_leader_site_scope(cursor, conn, validate_duid_value(duid))

    if denied:
        cursor.close()
        conn.close()
        return denied

    data = fetch_site_completion_report(cursor, duid)
    cursor.close()
    conn.close()

    if not data:
        return "Site not found"

    safe_duid = clean_report_filename(data["site"]["du_id"])
    filename = f"RUC_SITE_{safe_duid}_{report_date_stamp()}.xlsx"
    return workbook_response(build_site_report_workbook(data), filename)


@app.route("/reports/project")
@permission_required("manage_projects")
def project_report_select():

    if is_team_leader_role():
        return access_denied("Team Leaders can use site and team reports only.")

    project_id = clean_text(request.args.get("project_id"))

    if project_id:
        return redirect(url_for("project_management_report", project_id=project_id))

    return render_template(
        "report_project_select.html",
        projects=get_projects_for_select(),
    )


@app.route("/reports/project/<int:project_id>")
@permission_required("manage_projects")
def project_management_report(project_id):

    if is_team_leader_role():
        return access_denied("Team Leaders can use site and team reports only.")

    conn = connect_db()
    cursor = conn.cursor()
    data = fetch_project_management_report(cursor, project_id)
    cursor.close()
    conn.close()

    if not data:
        return "Project not found"

    return render_template("project_management_report.html", **data)


@app.route("/reports/project/<int:project_id>/pdf")
@permission_required("manage_projects")
def project_management_report_pdf(project_id):

    if is_team_leader_role():
        return access_denied("Team Leaders can use site and team reports only.")

    conn = connect_db()
    cursor = conn.cursor()
    data = fetch_project_management_report(cursor, project_id)
    cursor.close()
    conn.close()

    if not data:
        return "Project not found"

    safe_code = clean_report_filename(data["project"].get("project_code") or project_id)
    filename = f"RUC_PROJECT_{safe_code}_{report_date_stamp()}.pdf"
    project_identity = (
        f"{data['project'].get('project_name')} ({data['project'].get('project_code')})"
        if data["project"].get("project_code")
        else data["project"].get("project_name")
    )
    return pdf_response(
        f"Project Management Report - {project_identity}",
        project_report_pdf_sections(data),
        filename,
    )


@app.route("/reports/project/<int:project_id>/excel")
@permission_required("manage_projects")
def project_management_report_excel(project_id):

    if is_team_leader_role():
        return access_denied("Team Leaders can use site and team reports only.")

    conn = connect_db()
    cursor = conn.cursor()
    data = fetch_project_management_report(cursor, project_id)
    cursor.close()
    conn.close()

    if not data:
        return "Project not found"

    safe_code = clean_report_filename(data["project"].get("project_code") or project_id)
    filename = f"RUC_PROJECT_{safe_code}_{report_date_stamp()}.xlsx"
    return workbook_response(build_project_report_workbook(data), filename)


@app.route("/reports/personnel")
@any_permission_required("manage_personnel", "manage_safety", "view_team")
def personnel_safety_report():

    filters = {
        "project_id": clean_text(request.args.get("project_id")),
        "duid": clean_text(request.args.get("duid")),
        "role": clean_text(request.args.get("role")),
        "safety_status": clean_text(request.args.get("safety_status")),
    }
    conn = connect_db()
    cursor = conn.cursor()
    data = fetch_personnel_report(cursor, filters)
    cursor.close()
    conn.close()

    return render_template(
        "personnel_safety_report.html",
        rows=data["rows"],
        summary=data["summary"],
        filters=filters,
        projects=get_projects_for_select(),
        duids=get_duids_for_select(),
        safety_statuses=ACCESS_STATUSES,
    )


@app.route("/reports/personnel/export")
@any_permission_required("manage_personnel", "manage_safety", "view_team")
def personnel_safety_report_export():

    filters = {
        "project_id": clean_text(request.args.get("project_id")),
        "duid": clean_text(request.args.get("duid")),
        "role": clean_text(request.args.get("role")),
        "safety_status": clean_text(request.args.get("safety_status")),
    }
    conn = connect_db()
    cursor = conn.cursor()
    data = fetch_personnel_report(cursor, filters)
    cursor.close()
    conn.close()
    return workbook_response(build_personnel_workbook(data), f"RUC_PERSONNEL_SAFETY_{report_date_stamp()}.xlsx")


@app.route("/reports/daily")
@permission_required("manage_operations")
def daily_operations_report():

    filters = {
        "project_id": clean_text(request.args.get("project_id")),
        "duid": clean_text(request.args.get("duid")),
        "date_from": clean_text(request.args.get("date_from")),
        "date_to": clean_text(request.args.get("date_to")),
        "stage": clean_text(request.args.get("stage")),
    }
    error = ""
    data = {"rows": [], "summary": {}}

    try:
        conn = connect_db()
        cursor = conn.cursor()
        data = fetch_daily_report(cursor, filters)
        cursor.close()
        conn.close()
    except ValueError as exc:
        error = str(exc)

    return render_template(
        "daily_report.html",
        rows=data["rows"],
        summary=data["summary"],
        filters=filters,
        error=error,
        projects=get_projects_for_select(),
        duids=get_duids_for_select(),
        stages=SITE_STAGES,
    )


@app.route("/reports/daily/export")
@permission_required("manage_operations")
def daily_operations_report_export():

    filters = {
        "project_id": clean_text(request.args.get("project_id")),
        "duid": clean_text(request.args.get("duid")),
        "date_from": clean_text(request.args.get("date_from")),
        "date_to": clean_text(request.args.get("date_to")),
        "stage": clean_text(request.args.get("stage")),
    }

    try:
        conn = connect_db()
        cursor = conn.cursor()
        data = fetch_daily_report(cursor, filters)
        cursor.close()
        conn.close()
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("daily_operations_report", **filters))

    return workbook_response(build_daily_report_workbook(data), f"RUC_DAILY_OPERATIONS_{report_date_stamp()}.xlsx")


@app.route("/reports/punchlist")
@permission_required("manage_punchlist")
def punchlist_management_report():

    filters = {
        "project_id": clean_text(request.args.get("project_id")),
        "duid": clean_text(request.args.get("duid")),
        "priority": clean_text(request.args.get("priority")),
        "status": clean_text(request.args.get("status")),
        "assigned_employee_id": clean_text(request.args.get("assigned_employee_id")),
        "date_from": clean_text(request.args.get("date_from")),
        "date_to": clean_text(request.args.get("date_to")),
    }
    error = ""
    data = {"rows": [], "summary": {}}

    try:
        conn = connect_db()
        cursor = conn.cursor()
        data = fetch_punchlist_report(cursor, filters)
        cursor.close()
        conn.close()
    except ValueError as exc:
        error = str(exc)

    return render_template(
        "punchlist_report.html",
        rows=data["rows"],
        summary=data["summary"],
        filters=filters,
        error=error,
        projects=get_projects_for_select(),
        duids=get_duids_for_select(),
        employees=get_employees_for_select(),
        priorities=PUNCHLIST_PRIORITIES,
        statuses=PUNCHLIST_STATUSES,
    )


@app.route("/reports/punchlist/export")
@permission_required("manage_punchlist")
def punchlist_management_report_export():

    filters = {
        "project_id": clean_text(request.args.get("project_id")),
        "duid": clean_text(request.args.get("duid")),
        "priority": clean_text(request.args.get("priority")),
        "status": clean_text(request.args.get("status")),
        "assigned_employee_id": clean_text(request.args.get("assigned_employee_id")),
        "date_from": clean_text(request.args.get("date_from")),
        "date_to": clean_text(request.args.get("date_to")),
    }

    try:
        conn = connect_db()
        cursor = conn.cursor()
        data = fetch_punchlist_report(cursor, filters)
        cursor.close()
        conn.close()
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("punchlist_management_report", **filters))

    return workbook_response(build_punchlist_report_workbook(data), f"RUC_PUNCHLIST_{report_date_stamp()}.xlsx")


@app.route("/reports/pat_acceptance")
@any_permission_required("manage_acceptance", "view_pat", "manage_pat")
def pat_acceptance_report():

    filters = {
        "project_id": clean_text(request.args.get("project_id")),
        "duid": clean_text(request.args.get("duid")),
        "result": clean_text(request.args.get("result")),
        "acceptance_status": clean_text(request.args.get("acceptance_status")),
        "date_from": clean_text(request.args.get("date_from")),
        "date_to": clean_text(request.args.get("date_to")),
    }
    error = ""
    data = {"rows": [], "summary": {}}

    try:
        conn = connect_db()
        cursor = conn.cursor()
        data = fetch_pat_acceptance_report(cursor, filters)
        cursor.close()
        conn.close()
    except ValueError as exc:
        error = str(exc)

    return render_template(
        "pat_acceptance_report.html",
        rows=data["rows"],
        summary=data["summary"],
        filters=filters,
        error=error,
        projects=get_projects_for_select(),
        duids=get_duids_for_select(),
        pat_results=PAT_RESULTS,
        acceptance_statuses=ACCEPTANCE_STATUSES,
    )


@app.route("/reports/pat_acceptance/export")
@any_permission_required("manage_acceptance", "view_pat", "manage_pat")
def pat_acceptance_report_export():

    filters = {
        "project_id": clean_text(request.args.get("project_id")),
        "duid": clean_text(request.args.get("duid")),
        "result": clean_text(request.args.get("result")),
        "acceptance_status": clean_text(request.args.get("acceptance_status")),
        "date_from": clean_text(request.args.get("date_from")),
        "date_to": clean_text(request.args.get("date_to")),
    }

    try:
        conn = connect_db()
        cursor = conn.cursor()
        data = fetch_pat_acceptance_report(cursor, filters)
        cursor.close()
        conn.close()
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("pat_acceptance_report", **filters))

    return workbook_response(build_pat_acceptance_workbook(data), f"RUC_PAT_ACCEPTANCE_{report_date_stamp()}.xlsx")


@app.route("/reports/site/<path:duid>/handover")
@any_permission_required("manage_sites", "team_leader_portal")
def site_handover(duid):

    conn = connect_db()
    cursor = conn.cursor()
    denied = enforce_team_leader_site_scope(cursor, conn, validate_duid_value(duid))

    if denied:
        cursor.close()
        conn.close()
        return denied

    data = fetch_site_completion_report(cursor, duid)
    cursor.close()
    conn.close()

    if not data:
        return "Site not found"

    return render_template(
        "site_handover.html",
        package_label=handover_readiness_label(data),
        **data,
    )


@app.route("/reports/site/<path:duid>/handover/package", methods=["GET", "POST"])
@any_permission_required("manage_sites", "team_leader_portal")
def site_handover_package(duid):

    if request.method != "POST":
        return render_error_page(
            405,
            "Method Not Allowed",
            "Use the handover page button to generate a handover package.",
        )

    conn = connect_db()
    cursor = conn.cursor()
    denied = enforce_team_leader_site_scope(cursor, conn, validate_duid_value(duid))

    if denied:
        cursor.close()
        conn.close()
        return denied

    data = fetch_site_completion_report(cursor, duid)
    cursor.close()
    conn.close()

    if not data:
        return "Site not found"

    os.makedirs(GENERATED_REPORTS_DIR, exist_ok=True)
    safe_duid = clean_report_filename(data["site"]["du_id"])
    package_mode = "FINAL" if handover_readiness_label(data) == "FINAL HANDOVER PACKAGE" else "DRAFT"
    zip_filename = f"{safe_duid}_HANDOVER_{package_mode}_{report_timestamp()}_{uuid.uuid4().hex[:8]}.zip"
    zip_path = os.path.join(GENERATED_REPORTS_DIR, zip_filename)
    index_rows = []
    used_names = set()

    site_pdf = build_pdf_bytes(
        f"Site Completion Report - {data['site']['du_id']}",
        site_report_pdf_sections(data),
    )
    site_xlsx = BytesIO()
    build_site_report_workbook(data).save(site_xlsx)
    site_xlsx.seek(0)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr("Site_Completion_Report.pdf", site_pdf.getvalue())
        zip_file.writestr("Site_Completion_Report.xlsx", site_xlsx.getvalue())

        for evidence in data["evidence"]:
            source = clean_text(evidence.get("source"))

            if source == "Punchlist Evidence":
                folder = "Punchlist"
            elif source == "PAT Document":
                folder = "PAT"
            elif source == "Daily Evidence":
                folder = "Daily_Evidence"
            elif source == "Safety Document":
                folder = "Safety_Documents"
            else:
                folder = "Documents"

            add_handover_file(zip_file, evidence, folder, used_names, index_rows)

        if not index_rows:
            index_rows.append(["Evidence", "No controlled evidence files were available", "Missing or unavailable"])

        index_xlsx = BytesIO()
        build_document_index_workbook(index_rows).save(index_xlsx)
        index_xlsx.seek(0)
        zip_file.writestr("Document_Index.xlsx", index_xlsx.getvalue())
        zip_file.writestr("README.txt", build_handover_readme(data))

    audit_event(
        "HANDOVER_PACKAGE_GENERATED",
        "telecom_site",
        data["site"]["du_id"],
        f"Generated {package_mode} handover package.",
    )

    return send_file(zip_path, as_attachment=True, download_name=zip_filename, mimetype="application/zip")


#############################################
# RESET SYSTEM
#############################################


@app.route("/reset_system", methods=["POST"])
@permission_required("reset_system")
def reset_system():

    conn = connect_db()
    cursor = conn.cursor()

    #################################
    # CLEAR DATABASE
    #################################

    reset_tables = [
        "punchlist_files",
        "site_acceptance",
        "pat_records",
        "punchlist_items",
        "incident_attachments",
        "daily_log_files",
        "daily_attendance",
        "daily_site_logs",
        "toolbox_attendance",
        "incident_reports",
        "toolbox_talks",
        "permit_to_work",
        "telecom_tasks",
        "telecom_sites",
        "safety_documents",
        "site_assignments",
        "employees",
        "projects",
    ]
    cursor.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema='public'
          AND table_name = ANY(%s)
        """,
        (reset_tables,),
    )
    existing_tables = [row[0] for row in cursor.fetchall()]

    ordered_existing_tables = [table for table in reset_tables if table in existing_tables]

    if ordered_existing_tables:
        table_list = ", ".join(ordered_existing_tables)
        cursor.execute(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE")

    audit_event(
        "SYSTEM_RESET",
        "system",
        "reset_system",
        "System reset route executed.",
        conn=conn,
    )
    conn.commit()
    cursor.close()
    conn.close()

    #################################
    # DELETE UPLOAD FILES
    #################################

    upload_folders = [
        "uploads/photos",
        "uploads/nbi",
        "uploads/certificates",
        "uploads/signatures",
        "uploads/secid",
        "uploads/wah",
        os.path.join("static", "uploads", "projects"),
        "id_cards",
    ]

    for folder in upload_folders:

        clear_folder_contents(safe_abs_path(folder))

    #################################
    # DELETE ONLY PROJECT EXCEL FILES
    #################################

    excel_folder = EXCEL_DIR

    if os.path.exists(excel_folder):

        for file in os.listdir(excel_folder):

            file_path = os.path.join(excel_folder, file)

            # Skip master folder
            if os.path.isdir(file_path):
                continue

            # Delete only project Excel files
            if file.endswith(".xlsx"):
                os.remove(file_path)

    return "System Reset Successfully"


#############################################
# LOGOUT
#############################################


@app.route("/logout", methods=["POST"])
@login_required
def logout():

    audit_event(
        "AUTH_LOGOUT",
        "admin",
        session.get("admin_id"),
        "User logged out.",
    )

    session.clear()
    return redirect("/")


@app.route("/healthz", methods=["GET"])
def healthz():

    return jsonify({"status": "ok"})


#############################################
# DEVELOPMENT SERVER
#############################################

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=config_bool("RUC_DEBUG", False),
        use_reloader=False,
    )
