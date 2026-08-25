import os
import shutil
import tempfile
import time
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest import mock

from openpyxl import Workbook, load_workbook

import app as ruc
import verify_backup


BASE_DIR = Path(__file__).resolve().parents[1]
STAGE6_BACKUP_SET = Path(
    os.environ.get(
        "RUC_STAGE6_BACKUP_SET",
        str(BASE_DIR / "backups" / "disaster" / "20260825_210820"),
    )
)


class RucRegressionTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._audit_patch = mock.patch.object(ruc, "audit_event", lambda *args, **kwargs: None)
        cls._audit_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._audit_patch.stop()

    def setUp(self):
        self.client = ruc.app.test_client()

    def login_as(self, role):
        accounts = {
            "super_admin": {
                "admin_id": 2,
                "admin": "Marian",
                "role": "super_admin",
                "employee_id": None,
            },
            "hr": {
                "admin_id": 81,
                "admin": "LU",
                "role": "hr",
                "employee_id": None,
            },
            "team_leader": {
                "admin_id": 82,
                "admin": "BENJAMIN SINTIM",
                "role": "team_leader",
                "employee_id": 17,
            },
        }

        with self.client.session_transaction() as session:
            session.clear()
            session.update(accounts[role])

    def csrf_for_public_form(self):
        response = self.client.get("/form/52143")
        self.assertEqual(response.status_code, 200)

        with self.client.session_transaction() as session:
            return session.get(ruc.CSRF_SESSION_KEY)

    def assert_security_headers(self, response):
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.headers.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertEqual(
            response.headers.get("Referrer-Policy"),
            "strict-origin-when-cross-origin",
        )

    def get_status(self, path):
        response = self.client.get(path)

        try:
            return response.status_code
        finally:
            response.close()

    def post_status(self, path, data=None):
        response = self.client.post(path, data=data or {})

        try:
            return response.status_code
        finally:
            response.close()


class SecurityRegressionTests(RucRegressionTestCase):

    def test_app_import_and_route_count(self):
        self.assertEqual(len(ruc.app.url_map._rules), 88)

    def test_secret_key_is_required(self):
        with mock.patch.dict(os.environ, {"SECRET_KEY": ""}, clear=True):
            with self.assertRaises(ruc.ConfigurationError):
                ruc.get_secret_key()

    def test_healthz_methods(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})
        self.assertEqual(self.client.post("/healthz").status_code, 405)

    def test_login_page_and_csrf_rejection(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assert_security_headers(response)

        response = self.client.post("/", data={"username": "nobody", "password": "bad"})
        self.assertEqual(response.status_code, 400)

        with self.client.session_transaction() as session:
            session[ruc.CSRF_SESSION_KEY] = "expected-token"

        response = self.client.post(
            "/",
            data={
                "username": "nobody",
                "password": "bad",
                ruc.CSRF_FORM_FIELD: "wrong-token",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_public_form_post_requires_valid_csrf(self):
        response = self.client.post("/form/52143", data={})
        self.assertEqual(response.status_code, 400)

        with self.client.session_transaction() as session:
            session[ruc.CSRF_SESSION_KEY] = "expected-token"

        response = self.client.post(
            "/form/52143",
            data={ruc.CSRF_FORM_FIELD: "wrong-token"},
        )
        self.assertEqual(response.status_code, 400)

    def test_sensitive_routes_are_post_and_csrf_protected(self):
        self.login_as("super_admin")
        self.assertEqual(self.get_status("/logout"), 405)
        self.assertEqual(self.post_status("/logout"), 400)

        self.login_as("super_admin")
        self.assertEqual(self.get_status("/reset_system"), 405)
        self.assertEqual(self.post_status("/reset_system"), 400)

        self.login_as("super_admin")
        self.assertEqual(self.get_status("/reports/site/NL106/handover/package"), 405)
        self.assertEqual(
            self.post_status("/reports/site/NL106/handover/package"),
            400,
        )

    def test_security_configuration_defaults(self):
        self.assertFalse(ruc.app.debug)
        self.assertFalse(ruc.config_bool("RUC_TRUST_PROXY_HEADERS", False))
        self.assertTrue(ruc.app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(ruc.app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertEqual(ruc.LOGIN_RATE_LIMIT_ATTEMPTS, 5)
        self.assertEqual(ruc.LOGIN_RATE_LIMIT_WINDOW_SECONDS, 60)
        self.assertEqual(ruc.LOGIN_RATE_LIMIT_BLOCK_SECONDS, 300)

    def test_authenticated_html_is_not_cached(self):
        self.login_as("super_admin")
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        self.assertEqual(response.headers.get("Pragma"), "no-cache")
        self.assertEqual(response.headers.get("Expires"), "0")


class RbacRegressionTests(RucRegressionTestCase):

    def test_unauthenticated_protected_routes_redirect(self):
        for path in ["/dashboard", "/users", "/master_tracker", "/reports"]:
            with self.subTest(path=path):
                self.assertIn(self.get_status(path), {302, 401, 403})

    def test_super_admin_representative_access(self):
        self.login_as("super_admin")
        allowed_paths = [
            "/dashboard",
            "/users",
            "/projects",
            "/sites",
            "/reports",
            "/master_tracker",
        ]

        for path in allowed_paths:
            with self.subTest(path=path):
                self.assertEqual(self.get_status(path), 200)

    def test_hr_scope_and_denials(self):
        self.login_as("hr")
        allowed_paths = ["/dashboard", "/search", "/safety", "/reports/personnel"]

        for path in allowed_paths:
            with self.subTest(path=path):
                self.assertEqual(self.get_status(path), 200)

        denied_paths = [
            "/master_tracker",
            "/open_excel/52143",
            "/sites/new",
            "/daily_operations",
            "/telecom_tasks",
            "/site_assignments",
        ]

        for path in denied_paths:
            with self.subTest(path=path):
                self.assertEqual(self.get_status(path), 403)

    def test_team_leader_scope_and_denials(self):
        self.login_as("team_leader")
        allowed_paths = ["/team_leader", "/sites/NL105", "/sites/NL108"]

        for path in allowed_paths:
            with self.subTest(path=path):
                self.assertEqual(self.get_status(path), 200)

        denied_paths = [
            "/sites/NL106",
            "/master_tracker",
            "/open_excel/52143",
            "/users",
            "/audit_logs",
            "/id_generator",
        ]

        for path in denied_paths:
            with self.subTest(path=path):
                self.assertEqual(self.get_status(path), 403)


class PublicFormRegressionTests(RucRegressionTestCase):

    def test_valid_project_form_get_issues_csrf(self):
        token = self.csrf_for_public_form()
        self.assertTrue(token)

    def test_upload_size_configuration_is_active(self):
        self.assertGreater(ruc.app.config["MAX_CONTENT_LENGTH"], 0)
        self.assertEqual(
            ruc.app.config["MAX_CONTENT_LENGTH"],
            int(os.environ.get("MAX_UPLOAD_BYTES", 16 * 1024 * 1024)),
        )

    def test_unsupported_upload_extension_fails_without_employee_insert(self):
        conn = ruc.connect_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM employees")
        before_count = cursor.fetchone()[0]
        cursor.close()
        conn.close()

        token = self.csrf_for_public_form()
        data = {
            ruc.CSRF_FORM_FIELD: token,
            "first_name": "Regression",
            "middle_name": "",
            "last_name": "Upload",
            "position": "Tester",
            "email": "regression@example.invalid",
            "mobile": "0000000000",
            "phone_type": "Test",
            "ftap_imei": "",
            "ftap_email": "",
            "philtower_imei": "",
            "philtower_email": "",
            "telecom_role": "Tester",
            "assigned_du_id": "NL106",
            "sec_number": "",
            "nbi_reference": "",
            "wah_reference": "",
            "first_aid_reference": "",
            "sec_expiry": "",
            "nbi_issue_date": "",
            "nbi_expiry_date": "",
            "wah_issue_date": "",
            "wah_expiry_date": "",
            "first_aid_issue_date": "",
            "first_aid_expiry_date": "",
            "photo": (BytesIO(b"not an image"), "bad.txt"),
            "nbi": (BytesIO(b"%PDF-1.4"), "nbi.pdf"),
            "certificate": (BytesIO(b"%PDF-1.4"), "cert.pdf"),
            "signature": (BytesIO(b"png"), "signature.png"),
        }
        response = self.client.post(
            "/form/52143",
            data=data,
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Unsupported file type", response.data)

        conn = ruc.connect_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM employees")
        after_count = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        self.assertEqual(after_count, before_count)


class WorkbookSafetyRegressionTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.target = self.work_dir / "target.xlsx"
        self.candidate = self.work_dir / "candidate.xlsx"
        self.backup_dir = self.work_dir / "backups"
        self._create_workbook(self.target, "old")
        self._create_workbook(self.candidate, "new")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_workbook(self, path, value):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Sheet"
        worksheet["A1"] = value
        workbook.save(path)

    def _cell_value(self, path):
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            return workbook["Sheet"]["A1"].value
        finally:
            workbook.close()

    def test_lock_acquisition_release_and_timeout(self):
        lock_path = Path(ruc.workbook_lock_path(str(self.target)))

        with ruc.workbook_write_lock(str(self.target), timeout=1):
            self.assertTrue(lock_path.exists())

            with self.assertRaises(ruc.WorkbookBusyError):
                with ruc.workbook_write_lock(str(self.target), timeout=0.01):
                    pass

        self.assertFalse(lock_path.exists())

    def test_stale_dead_lock_recovery(self):
        lock_path = Path(ruc.workbook_lock_path(str(self.target)))
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("pid=0\ncreated_at=2000-01-01T00:00:00\n", encoding="utf-8")
        old_time = time.time() - ruc.WORKBOOK_STALE_LOCK_SECONDS - 10
        os.utime(lock_path, (old_time, old_time))

        with ruc.workbook_write_lock(str(self.target), timeout=1):
            self.assertTrue(lock_path.exists())

        self.assertFalse(lock_path.exists())

    def test_atomic_replacement_success_and_backup(self):
        result = ruc.replace_persistent_workbook_file(
            str(self.target),
            str(self.candidate),
            expected_sheets=("Sheet",),
            backup_folder=str(self.backup_dir),
            operation="regression workbook replace",
        )

        self.assertEqual(self._cell_value(self.target), "new")
        self.assertTrue(result["backup_path"])
        self.assertTrue(Path(result["backup_path"]).exists())

    def test_invalid_candidate_leaves_original_valid(self):
        invalid_candidate = self.work_dir / "invalid.xlsx"
        invalid_candidate.write_bytes(b"not a workbook")

        with self.assertRaises(ruc.WorkbookSafetyError):
            ruc.replace_persistent_workbook_file(
                str(self.target),
                str(invalid_candidate),
                expected_sheets=("Sheet",),
                backup_folder=str(self.backup_dir),
                operation="regression invalid replace",
            )

        self.assertEqual(self._cell_value(self.target), "old")
        temp_files = list(self.work_dir.glob(ruc.WORKBOOK_TEMP_PREFIX + "*"))
        self.assertEqual(temp_files, [])

    def test_validation_failure_prevents_success_state(self):
        with self.assertRaises(ruc.WorkbookValidationError):
            ruc.validate_workbook_file(str(self.target), expected_sheets=("Missing",))


class BackupVerificationRegressionTests(unittest.TestCase):

    def test_stage6_backup_set_verifies(self):
        self.assertTrue(STAGE6_BACKUP_SET.exists(), f"Missing backup set: {STAGE6_BACKUP_SET}")
        result = verify_backup.verify_backup_set(STAGE6_BACKUP_SET)
        self.assertEqual(result["status"], "PASS")

    def test_stage6_backup_archive_contains_required_categories(self):
        manifest = verify_backup.load_manifest(STAGE6_BACKUP_SET)
        archive_path = verify_backup.resolve_relative_file(
            STAGE6_BACKUP_SET,
            manifest["runtime_archive"],
        )

        with zipfile.ZipFile(archive_path, "r") as archive:
            names = archive.namelist()

        for category in ["excel_files", "uploads", "static/uploads", "id_cards", "generated_reports"]:
            with self.subTest(category=category):
                self.assertTrue(
                    any(
                        name.rstrip("/") == category or name.startswith(category + "/")
                        for name in names
                    )
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
