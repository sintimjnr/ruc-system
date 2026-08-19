-- RUC SYSTEM telecom site tracking and worker safety compliance upgrade.
-- Safe to run more than once. It adds only missing columns/tables/indexes.
-- It preserves existing data and keeps globe_nlz.du_id as the telecom site reference.

BEGIN;

ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS telecom_role VARCHAR(100),
    ADD COLUMN IF NOT EXISTS assigned_du_id TEXT,
    ADD COLUMN IF NOT EXISTS nbi_reference VARCHAR(100),
    ADD COLUMN IF NOT EXISTS nbi_issue_date DATE,
    ADD COLUMN IF NOT EXISTS nbi_expiry_date DATE,
    ADD COLUMN IF NOT EXISTS wah_reference VARCHAR(100),
    ADD COLUMN IF NOT EXISTS wah_issue_date DATE,
    ADD COLUMN IF NOT EXISTS wah_expiry_date DATE,
    ADD COLUMN IF NOT EXISTS wah_file TEXT,
    ADD COLUMN IF NOT EXISTS first_aid_reference VARCHAR(100),
    ADD COLUMN IF NOT EXISTS first_aid_issue_date DATE,
    ADD COLUMN IF NOT EXISTS first_aid_expiry_date DATE,
    ADD COLUMN IF NOT EXISTS first_aid_file TEXT,
    ADD COLUMN IF NOT EXISTS dossier_folder_path TEXT,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_employees_project_id
    ON employees(project_id);

CREATE INDEX IF NOT EXISTS idx_employees_assigned_du_id
    ON employees(assigned_du_id);

CREATE INDEX IF NOT EXISTS idx_employees_telecom_role
    ON employees(telecom_role);

CREATE INDEX IF NOT EXISTS idx_globe_nlz_du_id
    ON globe_nlz(du_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'employees_project_id_fkey_ruc_safe'
    ) THEN
        ALTER TABLE employees
            ADD CONSTRAINT employees_project_id_fkey_ruc_safe
            FOREIGN KEY (project_id)
            REFERENCES projects(id)
            ON DELETE SET NULL
            NOT VALID;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS site_assignments (
    id SERIAL PRIMARY KEY,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    du_id TEXT NOT NULL,
    employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
    role VARCHAR(100),
    assignment_status VARCHAR(30) DEFAULT 'ACTIVE',
    start_date DATE,
    end_date DATE,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_site_assignments_project_id
    ON site_assignments(project_id);

CREATE INDEX IF NOT EXISTS idx_site_assignments_du_id
    ON site_assignments(du_id);

CREATE INDEX IF NOT EXISTS idx_site_assignments_employee_id
    ON site_assignments(employee_id);

CREATE INDEX IF NOT EXISTS idx_site_assignments_status
    ON site_assignments(assignment_status);

CREATE TABLE IF NOT EXISTS safety_documents (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    document_type VARCHAR(30) NOT NULL,
    reference_number VARCHAR(100),
    issue_date DATE,
    expiry_date DATE,
    file_path TEXT,
    status VARCHAR(30),
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_safety_documents_employee_type
    ON safety_documents(employee_id, document_type);

CREATE INDEX IF NOT EXISTS idx_safety_documents_project_id
    ON safety_documents(project_id);

CREATE INDEX IF NOT EXISTS idx_safety_documents_document_type
    ON safety_documents(document_type);

CREATE INDEX IF NOT EXISTS idx_safety_documents_expiry_date
    ON safety_documents(expiry_date);

CREATE TABLE IF NOT EXISTS telecom_tasks (
    id SERIAL PRIMARY KEY,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    du_id TEXT NOT NULL,
    task_type VARCHAR(100) NOT NULL,
    description TEXT,
    priority VARCHAR(30) DEFAULT 'MEDIUM',
    assigned_employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    planned_date DATE,
    completed_date DATE,
    status VARCHAR(30) DEFAULT 'OPEN',
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_telecom_tasks_project_id
    ON telecom_tasks(project_id);

CREATE INDEX IF NOT EXISTS idx_telecom_tasks_du_id
    ON telecom_tasks(du_id);

CREATE INDEX IF NOT EXISTS idx_telecom_tasks_assigned_employee_id
    ON telecom_tasks(assigned_employee_id);

CREATE INDEX IF NOT EXISTS idx_telecom_tasks_status
    ON telecom_tasks(status);

CREATE INDEX IF NOT EXISTS idx_telecom_tasks_planned_date
    ON telecom_tasks(planned_date);

CREATE TABLE IF NOT EXISTS permit_to_work (
    id SERIAL PRIMARY KEY,
    permit_number VARCHAR(100) NOT NULL,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    du_id TEXT NOT NULL,
    permit_type VARCHAR(100),
    issued_to VARCHAR(150),
    issued_by VARCHAR(150),
    valid_from DATE,
    valid_until DATE,
    status VARCHAR(30) DEFAULT 'PENDING',
    file_path TEXT,
    notes TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_permit_to_work_project_id
    ON permit_to_work(project_id);

CREATE INDEX IF NOT EXISTS idx_permit_to_work_du_id
    ON permit_to_work(du_id);

CREATE INDEX IF NOT EXISTS idx_permit_to_work_status
    ON permit_to_work(status);

CREATE INDEX IF NOT EXISTS idx_permit_to_work_valid_until
    ON permit_to_work(valid_until);

CREATE TABLE IF NOT EXISTS toolbox_talks (
    id SERIAL PRIMARY KEY,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    du_id TEXT NOT NULL,
    topic VARCHAR(200) NOT NULL,
    date DATE NOT NULL,
    conducted_by INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    notes TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_toolbox_talks_project_id
    ON toolbox_talks(project_id);

CREATE INDEX IF NOT EXISTS idx_toolbox_talks_du_id
    ON toolbox_talks(du_id);

CREATE INDEX IF NOT EXISTS idx_toolbox_talks_date
    ON toolbox_talks(date);

CREATE TABLE IF NOT EXISTS toolbox_attendance (
    id SERIAL PRIMARY KEY,
    toolbox_talk_id INTEGER REFERENCES toolbox_talks(id) ON DELETE CASCADE,
    employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
    attended BOOLEAN DEFAULT TRUE,
    signature_path TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_toolbox_attendance_talk_employee
    ON toolbox_attendance(toolbox_talk_id, employee_id);

CREATE INDEX IF NOT EXISTS idx_toolbox_attendance_employee_id
    ON toolbox_attendance(employee_id);

CREATE TABLE IF NOT EXISTS incident_reports (
    id SERIAL PRIMARY KEY,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    du_id TEXT NOT NULL,
    reported_by INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    incident_date DATE NOT NULL,
    severity VARCHAR(30),
    category VARCHAR(100),
    description TEXT,
    action_taken TEXT,
    status VARCHAR(30) DEFAULT 'OPEN',
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_incident_reports_project_id
    ON incident_reports(project_id);

CREATE INDEX IF NOT EXISTS idx_incident_reports_du_id
    ON incident_reports(du_id);

CREATE INDEX IF NOT EXISTS idx_incident_reports_reported_by
    ON incident_reports(reported_by);

CREATE INDEX IF NOT EXISTS idx_incident_reports_status
    ON incident_reports(status);

CREATE INDEX IF NOT EXISTS idx_incident_reports_incident_date
    ON incident_reports(incident_date);

CREATE TABLE IF NOT EXISTS incident_attachments (
    id SERIAL PRIMARY KEY,
    incident_report_id INTEGER REFERENCES incident_reports(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    original_filename TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_incident_attachments_report_id
    ON incident_attachments(incident_report_id);

COMMIT;
