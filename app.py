from flask import Flask, render_template, request, redirect, session, send_file, url_for
import psycopg2
from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from PIL import Image, ImageDraw, ImageFont
import os
import random
from werkzeug.utils import secure_filename
import shutil
from datetime import datetime, date, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from flask import send_from_directory
from functools import wraps
import uuid
from io import BytesIO


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

    old_wb = load_workbook(old_file)
    new_wb = load_workbook(new_file)

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

    old_du_ids = set()
    new_du_ids = set()

    # Collect old DU IDs
    for row in range(2, old_ws.max_row + 1):

        du = old_ws.cell(row=row, column=du_col_old).value

        if du:
            old_du_ids.add(str(du).strip())

    # Collect new DU IDs
    for row in range(2, new_ws.max_row + 1):

        du = new_ws.cell(row=row, column=du_col_new).value

        if du:

            du = str(du).strip()

            if du in new_du_ids:
                return f"Duplicate DU ID detected: {du}"

            new_du_ids.add(du)

    #################################
    # CHECK FOR MISSING DU IDs
    #################################

    missing_du = old_du_ids - new_du_ids

    if missing_du:
        return f"Missing DU IDs detected: {list(missing_du)[:5]}"

    return "OK"


#############################################
# BACKUP FILE
#############################################


def backup_file(file_path, backup_folder):

    if not os.path.exists(backup_folder):
        os.makedirs(backup_folder)

    if os.path.exists(file_path):

        time_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        file_name = os.path.basename(file_path)

        new_name = time_stamp + "_" + file_name

        backup_path = os.path.join(backup_folder, new_name)

        shutil.copy(file_path, backup_path)


#############################################
# FLASK APP
#############################################

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "ruc_secret_local_dev")
app.config["MAX_CONTENT_LENGTH"] = int(
    os.environ.get("MAX_UPLOAD_BYTES", 16 * 1024 * 1024)
)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
LEGACY_UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
STATIC_UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
EXCEL_DIR = os.path.join(BASE_DIR, "excel_files")
MASTER_TRACKER_PATH = os.path.join(EXCEL_DIR, "master", "NLZ_MASTER_TRACKER.xlsx")
ID_CARD_DIR = os.path.join(BASE_DIR, "id_cards")
ID_TEMPLATE_DIR = os.path.join(BASE_DIR, "id_templates")

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
PAT_STATUSES = ["MISSING", "PENDING", "IN PROGRESS", "PASSED", "FAILED", "WAIVED"]
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


@app.route("/static/<path:filename>", endpoint="static")
def static_files(filename):

    normalized = filename.replace("\\", "/")

    if normalized.startswith("uploads/") and "admin" not in session:
        return redirect("/")

    if not normalized.startswith(("css/", "images/", "uploads/")):
        return "Invalid static path"

    return send_from_directory(safe_abs_path("static"), normalized)


def login_required(view):

    @wraps(view)
    def wrapped(*args, **kwargs):

        if "admin" not in session:
            return redirect("/")

        return view(*args, **kwargs)

    return wrapped


def admin_required(view):

    @wraps(view)
    def wrapped(*args, **kwargs):

        if "admin" not in session:
            return redirect("/")

        if session.get("role") != "admin":
            return "Access Denied"

        return view(*args, **kwargs)

    return wrapped


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

    database_url = os.environ.get("DATABASE_URL")

    if database_url:
        return psycopg2.connect(database_url)

    else:
        return psycopg2.connect(
            host=os.environ.get("DB_HOST", "localhost"),
            database=os.environ.get("DB_NAME", "ruc_system"),
            user=os.environ.get("DB_USER", "postgres"),
            password=os.environ.get("DB_PASSWORD", "3598"),
            port=os.environ.get("DB_PORT", "5432"),
        )


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
    cursor.execute(
        """
        SELECT id,
               first_name,
               last_name,
               position,
               telecom_role,
               assigned_du_id,
               project_id
        FROM employees
        ORDER BY first_name, last_name, id
        """
    )
    employees = rows_to_dicts(cursor)
    cursor.close()
    conn.close()
    return employees


def full_employee_name(emp):

    return clean_text(
        clean_text(emp.get("first_name")) + " " + clean_text(emp.get("last_name"))
    )


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


def send_stored_file(rel_path):

    rel_path = clean_text(rel_path).replace("\\", "/").lstrip("/")
    rel_path = os.path.normpath(rel_path).replace("\\", "/")

    if not rel_path:
        return "File not found"

    if rel_path == "." or rel_path.startswith("../") or "/../" in rel_path:
        return "Invalid file path"

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
    progress_before = validate_progress(request.form.get("progress_before"))
    progress_after = validate_progress(request.form.get("progress_after"))
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
            "Progress after cannot be less than progress before without a blocker or note"
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

    if daily_log_id:
        cursor.execute(
            """
            SELECT DISTINCT ON (e.id)
                   e.id,
                   e.first_name,
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

    for raw_employee_id in employee_ids:
        employee_id = clean_text(raw_employee_id)

        if not employee_id:
            continue

        cursor.execute(
            """
            SELECT id,
                   first_name,
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

    backup_file(excel_path, safe_abs_path("backups", "excel"))
    wb = load_workbook(excel_path)
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

    for col, value in enumerate(values, start=1):
        ws.cell(row=row, column=col).value = value

    attendance_ws = wb["ATTENDANCE"]
    rows_to_delete = []

    for current_row in range(2, attendance_ws.max_row + 1):
        if attendance_ws.cell(row=current_row, column=1).value == daily_log_id:
            rows_to_delete.append(current_row)

    for current_row in reversed(rows_to_delete):
        attendance_ws.delete_rows(current_row, 1)

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
               e.last_name
        FROM daily_attendance da
        LEFT JOIN employees e ON da.employee_id = e.id
        WHERE da.daily_log_id=%s
        ORDER BY e.first_name, e.last_name, da.employee_id
        """,
        (daily_log_id,),
    )
    attendance_rows = rows_to_dicts(cursor)

    for attendance in attendance_rows:
        attendance_ws.append(
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
            ]
        )

    wb.save(excel_path)


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

    full_name = clean_text(
        clean_text(employee.get("first_name")) + " " + clean_text(employee.get("last_name"))
    )
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

    for col, value in enumerate(values, start=1):
        ws.cell(row=row, column=col).value = value

    widths = [28, 10, 18, 18, 18, 32, 18, 25, 32, 25, 32, 18, 18, 16, 16, 18, 18]
    for col, width in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = width


def update_master_tracker_safety(employee):

    du_id = clean_text(employee.get("assigned_du_id"))

    if not du_id or not os.path.exists(MASTER_TRACKER_PATH):
        return

    wb = load_workbook(MASTER_TRACKER_PATH)

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

    ws.cell(row=row, column=1).value = du_id
    ws.cell(row=row, column=2).value = employee_id
    ws.cell(row=row, column=3).value = clean_text(
        clean_text(employee.get("first_name")) + " " + clean_text(employee.get("last_name"))
    )
    ws.cell(row=row, column=4).value = employee.get("telecom_role", "")
    ws.cell(row=row, column=5).value = employee.get("project_id", "")
    ws.cell(row=row, column=6).value = employee.get("nbi_expiry_date", "")
    ws.cell(row=row, column=7).value = employee.get("wah_expiry_date", "")
    ws.cell(row=row, column=8).value = employee.get("first_aid_expiry_date", "")
    ws.cell(row=row, column=9).value = employee.get("overall_safety_status", "")
    ws.cell(row=row, column=10).value = datetime.now().strftime("%Y-%m-%d %H:%M")

    wb.save(MASTER_TRACKER_PATH)


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
@login_required
def master_tracker():

    return send_file(MASTER_TRACKER_PATH, as_attachment=True)


#############################################
# UPLOAD MASTER TRACKER
#############################################


@app.route("/upload_master_tracker", methods=["GET", "POST"])
@login_required
def upload_master_tracker():

    master_path = MASTER_TRACKER_PATH

    if request.method == "POST":

        file = request.files["tracker"]

        if file.filename == "":
            return "No file selected"

        if not allowed_file(file.filename, {"xlsx"}):
            return "Only .xlsx files are allowed"

        upload_path = safe_abs_path("excel_files", "master", "upload_temp.xlsx")
        file.save(upload_path)

        # Validate tracker
        result = validate_tracker(master_path, upload_path)

        if result != "OK":
            os.remove(upload_path)
            return result

        # Backup old tracker
        backup_file(master_path, safe_abs_path("backups", "master"))

        # Replace old tracker
        os.replace(upload_path, master_path)

        return "Master Tracker Updated Successfully"

    return render_template("upload_master_tracker.html")


#############################################
# LOGIN
#############################################


@app.route("/", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form["username"]
        password = request.form["password"]

        conn = connect_db()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT username,password,role
            FROM admins
            WHERE username=%s
            """,
            (username,),
        )

        admin = cursor.fetchone()

        cursor.close()
        conn.close()

        if admin and check_password_hash(admin[1], password):

            session["admin"] = admin[0]
            session["role"] = admin[2]

            return redirect("/dashboard")

        else:
            return "Wrong Username or Password"

    return render_template("login.html")


#############################################
# DASHBOARD WITH STATISTICS
#############################################


@app.route("/dashboard")
@login_required
def dashboard():

    conn = connect_db()
    cursor = conn.cursor()

    filters = {
        "du_id": clean_text(request.args.get("du_id")),
        "project_id": clean_text(request.args.get("project_id")),
        "employee_id": clean_text(request.args.get("employee_id")),
        "safety_status": clean_text(request.args.get("safety_status")),
        "task_status": clean_text(request.args.get("task_status")),
        "permit_status": clean_text(request.args.get("permit_status")),
    }

    cursor.execute(
        """
        SELECT id, project_name, region, company, project_code, date_created
        FROM projects
        ORDER BY date_created DESC, id DESC
        """
    )
    projects = rows_to_dicts(cursor)

    cursor.execute("SELECT COUNT(*) FROM projects")
    total_projects = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM employees")
    total_employees = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(DISTINCT du_id)
        FROM (
            SELECT du_id FROM globe_nlz
            UNION
            SELECT du_id FROM planning_reference
            UNION
            SELECT du_id FROM telecom_sites
        ) all_duids
        WHERE du_id IS NOT NULL
          AND TRIM(du_id) <> ''
        """
    )
    total_telecom_sites = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE overall_status='Active') AS active_sites,
            COUNT(*) FILTER (WHERE overall_status='Completed') AS completed_sites,
            COUNT(*) FILTER (WHERE overall_status IN ('On Hold','Blocked')) AS attention_sites
        FROM telecom_sites
        """
    )
    site_status_counts = cursor.fetchone()
    active_sites = site_status_counts[0] or 0
    completed_sites = site_status_counts[1] or 0
    attention_sites = site_status_counts[2] or 0

    cursor.execute(
        f"""
        {SITE_REFERENCE_CTE}
        SELECT COALESCE(ts.current_stage, 'Planning') AS current_stage,
               COUNT(*) AS site_count
        FROM all_duids d
        LEFT JOIN telecom_sites ts ON ts.du_id = d.du_id
        GROUP BY COALESCE(ts.current_stage, 'Planning')
        ORDER BY site_count DESC, current_stage
        """
    )
    sites_by_stage = rows_to_dicts(cursor)

    cursor.execute(
        """
        SELECT id,
               first_name,
               last_name,
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

    safety_compliant_workers = 0
    expiring_certificates = 0
    expired_certificates = 0
    missing_certificates = 0

    for emp in employees:
        emp.update(safety_summary_from_employee(emp))
        emp["full_name"] = full_employee_name(emp)

        if emp["overall_safety_status"] in ("VALID", "EXPIRING SOON"):
            safety_compliant_workers += 1

        for key in ("nbi_status", "wah_status", "first_aid_status"):
            if emp[key] == "EXPIRING SOON":
                expiring_certificates += 1
            elif emp[key] == "EXPIRED":
                expired_certificates += 1
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

    filtered_safety_employees = employees

    if filters["employee_id"]:
        filtered_safety_employees = [
            emp for emp in filtered_safety_employees if str(emp["id"]) == filters["employee_id"]
        ]

    if filters["du_id"]:
        filtered_safety_employees = [
            emp
            for emp in filtered_safety_employees
            if clean_text(emp.get("assigned_du_id")) == filters["du_id"]
        ]

    if filters["safety_status"]:
        filtered_safety_employees = [
            emp
            for emp in filtered_safety_employees
            if emp["overall_safety_status"] == filters["safety_status"]
        ]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM telecom_tasks
        WHERE status NOT IN ('COMPLETED','CLOSED','CANCELLED')
        """
    )
    open_telecom_tasks = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM permit_to_work
        WHERE status='ACTIVE'
          AND (valid_until IS NULL OR valid_until >= CURRENT_DATE)
        """
    )
    active_permits = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM incident_reports
        WHERE status NOT IN ('CLOSED','RESOLVED','CANCELLED')
        """
    )
    open_incidents = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM daily_site_logs
        WHERE report_date=CURRENT_DATE
        """
    )
    reports_today = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(DISTINCT duid)
        FROM daily_site_logs
        WHERE report_date=CURRENT_DATE
        """
    )
    active_sites_today = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM daily_attendance da
        JOIN daily_site_logs dsl ON da.daily_log_id = dsl.id
        WHERE dsl.report_date=CURRENT_DATE
          AND da.attendance_status IN ('Present','Late')
        """
    )
    personnel_present_today = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(DISTINCT duid)
        FROM daily_site_logs
        WHERE report_date=CURRENT_DATE
          AND (
              COALESCE(TRIM(blockers), '') <> ''
              OR COALESCE(TRIM(blocker_category), '') <> ''
          )
        """
    )
    sites_with_blockers = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(DISTINCT duid)
        FROM daily_site_logs
        WHERE updated_at::date=CURRENT_DATE
        """
    )
    sites_updated_today = cursor.fetchone()[0]

    task_conditions = ["TRUE"]
    task_params = []

    if filters["project_id"]:
        task_conditions.append("t.project_id=%s")
        task_params.append(filters["project_id"])

    if filters["du_id"]:
        task_conditions.append("t.du_id=%s")
        task_params.append(filters["du_id"])

    if filters["employee_id"]:
        task_conditions.append("t.assigned_employee_id=%s")
        task_params.append(filters["employee_id"])

    if filters["task_status"]:
        task_conditions.append("t.status=%s")
        task_params.append(filters["task_status"])

    cursor.execute(
        f"""
        SELECT t.id,
               t.du_id,
               t.task_type,
               t.priority,
               t.status,
               t.planned_date,
               p.project_name,
               e.first_name,
               e.last_name
        FROM telecom_tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        LEFT JOIN employees e ON t.assigned_employee_id = e.id
        WHERE {' AND '.join(task_conditions)}
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT 10
        """,
        task_params,
    )
    recent_tasks = rows_to_dicts(cursor)

    permit_conditions = ["TRUE"]
    permit_params = []

    if filters["project_id"]:
        permit_conditions.append("ptw.project_id=%s")
        permit_params.append(filters["project_id"])

    if filters["du_id"]:
        permit_conditions.append("ptw.du_id=%s")
        permit_params.append(filters["du_id"])

    if filters["permit_status"]:
        permit_conditions.append("ptw.status=%s")
        permit_params.append(filters["permit_status"])

    cursor.execute(
        f"""
        SELECT ptw.id,
               ptw.permit_number,
               ptw.du_id,
               ptw.permit_type,
               ptw.status,
               ptw.valid_until,
               p.project_name
        FROM permit_to_work ptw
        LEFT JOIN projects p ON ptw.project_id = p.id
        WHERE {' AND '.join(permit_conditions)}
        ORDER BY ptw.created_at DESC, ptw.id DESC
        LIMIT 10
        """,
        permit_params,
    )
    recent_permits = rows_to_dicts(cursor)

    incident_conditions = ["TRUE"]
    incident_params = []

    if filters["project_id"]:
        incident_conditions.append("ir.project_id=%s")
        incident_params.append(filters["project_id"])

    if filters["du_id"]:
        incident_conditions.append("ir.du_id=%s")
        incident_params.append(filters["du_id"])

    cursor.execute(
        f"""
        SELECT ir.id,
               ir.du_id,
               ir.incident_date,
               ir.severity,
               ir.category,
               ir.status,
               p.project_name,
               e.first_name,
               e.last_name
        FROM incident_reports ir
        LEFT JOIN projects p ON ir.project_id = p.id
        LEFT JOIN employees e ON ir.reported_by = e.id
        WHERE {' AND '.join(incident_conditions)}
        ORDER BY ir.created_at DESC, ir.id DESC
        LIMIT 10
        """,
        incident_params,
    )
    recent_incidents = rows_to_dicts(cursor)

    cursor.execute(
        f"""
        {SITE_REFERENCE_CTE}
        SELECT dsl.id,
               dsl.duid,
               dsl.report_date,
               dsl.current_stage,
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
        ORDER BY dsl.report_date DESC, dsl.created_at DESC, dsl.id DESC
        LIMIT 10
        """
    )
    recent_daily_activity = rows_to_dicts(cursor)

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

    total_ids = 0

    if os.path.exists(EXCEL_DIR):

        for file in os.listdir(EXCEL_DIR):

            if file.endswith(".xlsx"):

                wb = load_workbook(os.path.join(EXCEL_DIR, file))

                if "ID" in wb.sheetnames:

                    ws = wb["ID"]

                    total_ids += ws.max_row - 1

    return render_template(
        "dashboard.html",
        projects=projects,
        total_projects=total_projects,
        total_telecom_sites=total_telecom_sites,
        active_sites=active_sites,
        completed_sites=completed_sites,
        attention_sites=attention_sites,
        sites_by_stage=sites_by_stage,
        total_employees=total_employees,
        total_ids=total_ids,
        safety_compliant_workers=safety_compliant_workers,
        expiring_certificates=expiring_certificates,
        expired_certificates=expired_certificates,
        missing_certificates=missing_certificates,
        open_telecom_tasks=open_telecom_tasks,
        active_permits=active_permits,
        open_incidents=open_incidents,
        reports_today=reports_today,
        active_sites_today=active_sites_today,
        personnel_present_today=personnel_present_today,
        sites_with_blockers=sites_with_blockers,
        sites_updated_today=sites_updated_today,
        recent_daily_activity=recent_daily_activity,
        duids=duids,
        employees=employees,
        filters=filters,
        recent_tasks=recent_tasks,
        recent_permits=recent_permits,
        recent_incidents=recent_incidents,
        filtered_safety_employees=filtered_safety_employees[:10],
        attention_employees=attention_employees[:10],
    )


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
                OR e.last_name ILIKE %s
                OR e.email ILIKE %s
                OR e.mobile ILIKE %s
            )
            """
        )
        keyword = "%" + filters["search"] + "%"
        params.extend([keyword, keyword, keyword, keyword])

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

    cursor.execute(
        f"""
        SELECT e.id,
               e.project_id,
               e.first_name,
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
        ORDER BY e.first_name, e.last_name, e.id
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
@login_required
def edit_employee(emp_id):

    conn = connect_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT e.id,
               e.project_id,
               e.first_name,
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

        old_name = clean_text(
            clean_text(emp.get("first_name")) + " " + clean_text(emp.get("last_name"))
        )

        first_name = clean_text(request.form.get("first_name"))
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

        conn.commit()

        updated_employee = {
            "id": int(emp_id),
            "project_id": emp["project_id"],
            "first_name": first_name,
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

        if emp.get("project_code") and os.path.exists(project_excel_path(emp["project_code"])):
            wb = load_workbook(project_excel_path(emp["project_code"]))
            write_access_info_row(wb, emp, updated_employee, old_name=old_name)
            wb.save(project_excel_path(emp["project_code"]))

        update_master_tracker_safety(updated_employee)

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
@admin_required
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

        conn.commit()

        cursor.close()
        conn.close()

        #################################
        # CREATE EXCEL
        #################################

        wb = Workbook()

        yellow = PatternFill(start_color="FFFF00", fill_type="solid")
        bold = Font(bold=True)
        center = Alignment(horizontal="center")
        border = Border(
            left=Side(style="thin"),
            right=Side(style="thin"),
            top=Side(style="thin"),
            bottom=Side(style="thin"),
        )

        ws1 = wb.active
        ws1.title = "ACCESS INFO"

        headers = ACCESS_INFO_HEADERS

        ws1.append(headers)

        for col in range(1, len(headers) + 1):

            cell = ws1.cell(row=1, column=col)
            cell.fill = yellow
            cell.font = bold
            cell.alignment = center
            cell.border = border

        sheets = [
            "2X2",
            "NBI",
            "CERTIFICATES",
            "eSignature",
            "SEC ID",
            "WAH CERT",
            "ID",
        ]

        for s in sheets:

            ws = wb.create_sheet(s)

            if s == "SEC ID":
                ws.append(["NAME", "SEC NUMBER", "EXPIRY", "IMAGE"])
            elif s == "ID":
                ws.append(["NAME", "ID NUMBER", "EXPIRY", "IMAGE"])
            else:
                ws.append(["NAME", "IMAGE"])

        file_path = project_excel_path(project_code)
        wb.save(file_path)

        backup_file(file_path, safe_abs_path("backups", "excel"))

        return redirect("/dashboard")

    return render_template("create_project.html")


#############################################
# EMPLOYEE FORM
#############################################


@app.route("/form/<code>", methods=["GET", "POST"])
def form(code):

    conn = connect_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, project_name, region, company, project_code, date_created
        FROM projects
        WHERE project_code=%s
        """,
        (code,),
    )

    project = row_to_dict(cursor)

    if not project:
        cursor.close()
        conn.close()
        return "Invalid Project Link"

    #################################
    # FORM SUBMIT
    #################################

    if request.method == "POST":

        # TEXT DATA
        first_name = clean_text(request.form.get("first_name"))
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

        full_name = first_name + " " + last_name

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
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
        """,
            (
                project["id"],
                first_name,
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

        conn.commit()

        #################################
        # OPEN EXCEL
        #################################

        file_path = project_excel_path(code)
        wb = load_workbook(file_path)

        #################################
        # ACCESS INFO SHEET
        #################################

        employee_record = {
            "id": employee_id,
            "project_id": project["id"],
            "first_name": first_name,
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

            ws["B" + str(row + 1)] = full_name

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

        ws6.cell(row=row, column=1).value = full_name
        ws6.cell(row=row, column=2).value = sec_number
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

        #################################
        # SAVE EXCEL
        #################################

        wb.save(file_path)
        update_master_tracker_safety(employee_record)

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
@login_required
def open_excel(code):

    return send_file(project_excel_path(code))


#############################################
# DELETE PROJECT
#############################################


@app.route("/delete_project/<code>", methods=["POST"])
@admin_required
def delete_project(code):

    conn = connect_db()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM projects WHERE project_code=%s", (code,))

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

    return send_from_directory(safe_abs_path("uploads", "photos"), filename)


#############################################
# ID GENERATOR PAGE
#############################################


@app.route("/id_generator")
@login_required
def id_generator():

    conn = connect_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT e.id,
               e.first_name,
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
        SELECT file_path
        FROM safety_documents
        WHERE id=%s
        """,
        (document_id,),
    )
    document = row_to_dict(cursor)
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

    cursor.execute(
        f"""
        SELECT e.id,
               e.project_id,
               e.first_name,
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
@login_required
def safety_documents():

    conn = connect_db()
    cursor = conn.cursor()

    if request.method == "POST":
        if session.get("role") != "admin":
            cursor.close()
            conn.close()
            return "Access Denied"

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

        if updated_employee and updated_employee.get("project_code"):
            excel_path = project_excel_path(updated_employee["project_code"])

            if os.path.exists(excel_path):
                wb = load_workbook(excel_path)
                write_access_info_row(wb, updated_employee, updated_employee)
                wb.save(excel_path)

            update_master_tracker_safety(updated_employee)

        conn.commit()
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
@login_required
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

    cursor.execute(
        f"""
        {SITE_REFERENCE_CTE}
        {SITE_SELECT_COLUMNS},
               COALESCE(sa_count.assigned_workers, 0) AS assigned_workers,
               COALESCE(tt_count.open_tasks, 0) AS open_tasks,
               COALESCE(ptw_count.active_permits, 0) AS active_permits,
               COALESCE(ir_count.open_incidents, 0) AS open_incidents,
               COALESCE(daily_count.daily_reports, 0) AS daily_reports
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
        WHERE {' AND '.join(conditions)}
        ORDER BY d.du_id
        LIMIT 300
        """,
        params,
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
@admin_required
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
@login_required
def site_detail(du_id):

    du_id = validate_duid_value(du_id)
    conn = connect_db()
    cursor = conn.cursor()
    site = get_site_by_duid(cursor, du_id)

    if not site:
        cursor.close()
        conn.close()
        return "Site not found"

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
        projects=get_projects_for_select(),
        employees=get_employees_for_select(),
        task_types=TELECOM_TASK_TYPES,
        priorities=TASK_PRIORITIES,
        task_statuses=TASK_STATUSES,
        assignment_statuses=["ACTIVE", "INACTIVE", "COMPLETED"],
    )


@app.route("/sites/<path:du_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_site(du_id):

    du_id = validate_duid_value(du_id)
    conn = connect_db()
    cursor = conn.cursor()
    site = get_site_by_duid(cursor, du_id)

    if not site:
        cursor.close()
        conn.close()
        return "Site not found"

    if request.method == "POST":
        try:
            site_data = collect_site_form_data(cursor, du_id)
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        save_site_operational_record(cursor, site_data)
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
@login_required
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
@login_required
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
        ws.append(
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
            ]
        )

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
@admin_required
def new_daily_log(du_id):

    du_id = validate_duid_value(du_id)
    conn = connect_db()
    cursor = conn.cursor()
    site = get_site_by_duid(cursor, du_id)

    if not site:
        cursor.close()
        conn.close()
        return "Site not found"

    if request.method == "POST":
        try:
            log_data = collect_daily_log_form_data(cursor, du_id)
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        cursor.execute(
            """
            SELECT id
            FROM daily_site_logs
            WHERE duid=%s
              AND report_date=%s
            """,
            (du_id, log_data["report_date"]),
        )

        if cursor.fetchone():
            cursor.close()
            conn.close()
            return "Daily report already exists for this DUID and date"

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
            sync_daily_log_to_project_workbook(cursor, daily_log_id)
            conn.commit()
        except (ValueError, psycopg2.Error) as exc:
            conn.rollback()
            cursor.close()
            conn.close()
            return str(exc)

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
@login_required
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
@admin_required
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

    site = get_site_by_duid(cursor, log["duid"])

    if request.method == "POST":
        try:
            log_data = collect_daily_log_form_data(cursor, log["duid"], log)
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

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

        if cursor.fetchone():
            cursor.close()
            conn.close()
            return "Daily report already exists for this DUID and date"

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
            sync_daily_log_to_project_workbook(cursor, log_id)
            conn.commit()
        except (ValueError, psycopg2.Error) as exc:
            conn.rollback()
            cursor.close()
            conn.close()
            return str(exc)

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
@login_required
def daily_log_file(file_id):

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT file_path
        FROM daily_log_files
        WHERE id=%s
        """,
        (file_id,),
    )
    file_record = row_to_dict(cursor)
    cursor.close()
    conn.close()

    if not file_record or not file_record.get("file_path"):
        return "File not found"

    return send_stored_file(file_record["file_path"])


#############################################
# SITE ASSIGNMENTS
#############################################


@app.route("/site_assignments", methods=["GET", "POST"])
@login_required
def site_assignments():

    conn = connect_db()
    cursor = conn.cursor()

    if request.method == "POST":
        if session.get("role") != "admin":
            cursor.close()
            conn.close()
            return "Access Denied"

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
@login_required
def telecom_tasks():

    conn = connect_db()
    cursor = conn.cursor()

    if request.method == "POST":
        if session.get("role") != "admin":
            cursor.close()
            conn.close()
            return "Access Denied"

        project_id = clean_text(request.form.get("project_id"))
        return_to = safe_return_path(request.form.get("return_to"), "/telecom_tasks")

        try:
            du_id = validate_duid_value(request.form.get("du_id"))
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        task_type = clean_text(request.form.get("task_type"))
        description = clean_text(request.form.get("description"))
        priority = clean_text(request.form.get("priority")) or "MEDIUM"
        assigned_employee_id = clean_text(request.form.get("assigned_employee_id"))
        status = clean_text(request.form.get("status")) or "PENDING"

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
@login_required
def update_telecom_task_status(task_id):

    if session.get("role") != "admin":
        return "Access Denied"

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
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(return_to)


#############################################
# PERMIT TO WORK
#############################################


@app.route("/permits", methods=["GET", "POST"])
@login_required
def permits():

    conn = connect_db()
    cursor = conn.cursor()

    if request.method == "POST":
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
@login_required
def update_permit_status(permit_id):

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
    conn.commit()
    cursor.close()
    conn.close()
    return redirect("/permits")


#############################################
# TOOLBOX TALKS
#############################################


@app.route("/toolbox_talks", methods=["GET", "POST"])
@login_required
def toolbox_talks():

    conn = connect_db()
    cursor = conn.cursor()

    if request.method == "POST":
        project_id = clean_text(request.form.get("project_id"))
        try:
            du_id = validate_duid_value(request.form.get("du_id"))
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

        topic = clean_text(request.form.get("topic"))
        talk_date = clean_date(request.form.get("date"))
        conducted_by = clean_text(request.form.get("conducted_by"))
        notes = clean_text(request.form.get("notes"))
        employee_ids = request.form.getlist("employee_ids")

        if not duid_exists(cursor, du_id):
            cursor.close()
            conn.close()
            return "Invalid DUID"

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

        conn.commit()
        cursor.close()
        conn.close()
        return redirect("/toolbox_talks")

    cursor.execute(
        """
        SELECT tt.id,
               tt.project_id,
               tt.du_id,
               tt.topic,
               tt.date,
               tt.notes,
               p.project_name,
               e.first_name AS conducted_first_name,
               e.last_name AS conducted_last_name,
               COUNT(ta.employee_id) AS attendees
        FROM toolbox_talks tt
        LEFT JOIN projects p ON tt.project_id = p.id
        LEFT JOIN employees e ON tt.conducted_by = e.id
        LEFT JOIN toolbox_attendance ta ON ta.toolbox_talk_id = tt.id AND ta.attended=TRUE
        GROUP BY tt.id, p.project_name, e.first_name, e.last_name
        ORDER BY tt.date DESC, tt.id DESC
        LIMIT 200
        """
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
@login_required
def incidents():

    conn = connect_db()
    cursor = conn.cursor()

    if request.method == "POST":
        project_id = clean_text(request.form.get("project_id"))
        try:
            du_id = validate_duid_value(request.form.get("du_id"))
        except ValueError as exc:
            cursor.close()
            conn.close()
            return str(exc)

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
               e.last_name,
               COUNT(ia.id) AS attachments
        FROM incident_reports ir
        LEFT JOIN projects p ON ir.project_id = p.id
        LEFT JOIN employees e ON ir.reported_by = e.id
        LEFT JOIN incident_attachments ia ON ia.incident_report_id = ir.id
        WHERE {' AND '.join(conditions)}
        GROUP BY ir.id, p.project_name, e.first_name, e.last_name
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
@login_required
def update_incident_status(incident_id):

    status = clean_text(request.form.get("status"))

    if status not in INCIDENT_STATUSES:
        return "Invalid incident status"

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE incident_reports
        SET status=%s
        WHERE id=%s
        """,
        (status, incident_id),
    )
    conn.commit()
    cursor.close()
    conn.close()
    return redirect("/incidents")


#############################################
# GENERATE ID
#############################################


@app.route("/generate_id/<code>/<employee_id>", methods=["GET", "POST"])
@login_required
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

    name = clean_text(
        clean_text(emp.get("first_name")) + " " + clean_text(emp.get("last_name"))
    )
    position = emp["position"]
    telecom_role = emp.get("telecom_role") or position
    assigned_du_id = emp.get("assigned_du_id") or "UNASSIGNED"

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

        excel_path = project_excel_path(emp["project_code"])
        wb = load_workbook(excel_path)

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
                cursor.close()
                conn.close()
                return "ID Number Exists"

        #################################
        # LOAD ID TEMPLATES (PIXEL PERFECT)
        #################################

        from PIL import Image, ImageDraw, ImageFont

        front = Image.open(os.path.join(ID_TEMPLATE_DIR, "front.png")).convert("RGB")
        back = Image.open(os.path.join(ID_TEMPLATE_DIR, "back.png")).convert("RGB")

        front = front.resize((600, 900))
        back = back.resize((600, 900))

        draw_front = ImageDraw.Draw(front)
        draw_back = ImageDraw.Draw(back)

        #################################
        # LOAD PROFESSIONAL FONTS
        #################################

        try:
            font_big = ImageFont.truetype("arialbd.ttf", 42)
            font_small = ImageFont.truetype("arial.ttf", 26)
            font_badge = ImageFont.truetype("arialbd.ttf", 24)
        except:
            font_big = ImageFont.load_default()
            font_small = ImageFont.load_default()
            font_badge = ImageFont.load_default()

        #################################
        # FIX 2X2 PHOTO PERFECTLY
        #################################

        photo = Image.open(photo_path)

        size = min(photo.size)

        left = (photo.width - size) // 2
        top = (photo.height - size) // 2
        right = left + size
        bottom = top + size

        photo = photo.crop((left, top, right, bottom))
        photo = photo.resize((200, 200))

        #################################
        # EXACT PHOTO BOX LOCATION
        #################################

        PHOTO_X = 200
        PHOTO_Y = 210

        front.paste(photo, (PHOTO_X, PHOTO_Y))

        #################################
        # CENTER TEXT FUNCTION
        #################################

        def center_text(draw, text, font, y, width=600):

            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            x = (width - text_width) // 2
            draw.text((x, y), text, (0, 0, 0), font)

        #################################
        # FRONT TEXT (PIXEL PERFECT)
        #################################

        center_text(draw_front, name, font_big, 450)
        center_text(draw_front, telecom_role, font_small, 510)
        center_text(draw_front, "Employee ID: " + str(emp["id"]), font_small, 555)
        center_text(draw_front, "ID No: " + id_number, font_small, 595)
        center_text(draw_front, "DUID: " + assigned_du_id, font_small, 635)

        badge_text = emp["safety_badge"]
        badge_color = (
            (18, 120, 66)
            if emp["overall_safety_status"] in ("VALID", "EXPIRING SOON")
            else (150, 45, 45)
        )
        draw_front.rounded_rectangle((150, 685, 450, 735), radius=12, fill=badge_color)
        center_text(draw_front, badge_text, font_badge, 696)

        #################################
        # BACK TEXT
        #################################

        draw_back.text((180, 300), name, (0, 0, 0), font_small)
        draw_back.text((180, 340), "DUID: " + assigned_du_id, (0, 0, 0), font_small)
        draw_back.text((180, 380), address, (0, 0, 0), font_small)
        draw_back.text((180, 420), contact_number, (0, 0, 0), font_small)
        draw_back.text((220, 740), "EXPIRY: " + expiry, (0, 0, 0), font_small)

        #################################
        # SAVE ID CARDS
        #################################

        if not os.path.exists(ID_CARD_DIR):
            os.makedirs(ID_CARD_DIR)

        front_file = os.path.join(ID_CARD_DIR, safe_id_number + "_front.png")
        back_file = os.path.join(ID_CARD_DIR, safe_id_number + "_back.png")

        front.save(front_file, quality=100)
        back.save(back_file, quality=100)

        #################################
        # SAVE TO EXCEL
        #################################

        row = ws.max_row + 2

        ws.cell(row=row, column=1).value = name
        ws.cell(row=row, column=2).value = id_number
        ws.cell(row=row, column=3).value = expiry
        ws.cell(row=row, column=5).value = emp["id"]
        ws.cell(row=row, column=6).value = telecom_role
        ws.cell(row=row, column=7).value = assigned_du_id
        ws.cell(row=row, column=8).value = emp["overall_safety_status"]

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

        wb.save(excel_path)

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

    front_file = "id_cards/" + safe_id_number + "_front.png"
    back_file = "id_cards/" + safe_id_number + "_back.png"

    return render_template("print_id.html", front=front_file, back=back_file)


#############################################
# SERVE IMAGES
#############################################


@app.route("/id_cards/<filename>")
@login_required
def id_cards(filename):

    return send_from_directory(ID_CARD_DIR, filename)


#############################################
# RESET SYSTEM
#############################################


@app.route("/reset_system", methods=["POST"])
@admin_required
def reset_system():

    conn = connect_db()
    cursor = conn.cursor()

    #################################
    # CLEAR DATABASE
    #################################

    reset_tables = [
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


@app.route("/logout")
def logout():

    session.clear()
    return redirect("/")


#############################################
# RUN SERVER
#############################################

if __name__ == "__main__":
    app.run(debug=True)
