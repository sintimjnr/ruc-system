BEGIN;

CREATE TABLE IF NOT EXISTS punchlist_items (
    id SERIAL PRIMARY KEY,
    duid TEXT NOT NULL,
    telecom_site_id INTEGER REFERENCES telecom_sites(id) ON DELETE SET NULL,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    item_number VARCHAR(50),
    category VARCHAR(100),
    title VARCHAR(255) NOT NULL,
    description TEXT,
    priority VARCHAR(20) DEFAULT 'MEDIUM',
    status VARCHAR(30) DEFAULT 'OPEN',
    assigned_employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    raised_by TEXT,
    raised_date DATE DEFAULT CURRENT_DATE,
    target_date DATE,
    rectified_date DATE,
    verified_date DATE,
    verified_by TEXT,
    closure_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT punchlist_priority_check CHECK (priority IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    CONSTRAINT punchlist_status_check CHECK (status IN ('OPEN','IN PROGRESS','RECTIFIED','VERIFIED','CLOSED')),
    CONSTRAINT punchlist_dates_check CHECK (
        target_date IS NULL
        OR raised_date IS NULL
        OR target_date >= raised_date
    )
);

CREATE TABLE IF NOT EXISTS punchlist_files (
    id SERIAL PRIMARY KEY,
    punchlist_item_id INTEGER NOT NULL REFERENCES punchlist_items(id) ON DELETE CASCADE,
    file_type VARCHAR(30) NOT NULL DEFAULT 'GENERAL',
    filename TEXT,
    file_path TEXT NOT NULL,
    caption TEXT,
    uploaded_by TEXT,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT punchlist_files_type_check CHECK (file_type IN ('BEFORE','AFTER','GENERAL','DOCUMENT'))
);

CREATE TABLE IF NOT EXISTS pat_records (
    id SERIAL PRIMARY KEY,
    duid TEXT NOT NULL,
    telecom_site_id INTEGER REFERENCES telecom_sites(id) ON DELETE SET NULL,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    pat_reference VARCHAR(100),
    pat_date DATE NOT NULL DEFAULT CURRENT_DATE,
    inspector_name VARCHAR(150),
    vendor_name VARCHAR(150),
    towerco_customer VARCHAR(150),
    result VARCHAR(40) NOT NULL DEFAULT 'PENDING',
    remarks TEXT,
    document_filename TEXT,
    document_path TEXT,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pat_records_result_check CHECK (result IN ('PENDING','PASSED','PASSED WITH PUNCHLIST','FAILED'))
);

CREATE TABLE IF NOT EXISTS site_acceptance (
    id SERIAL PRIMARY KEY,
    duid TEXT NOT NULL,
    telecom_site_id INTEGER REFERENCES telecom_sites(id) ON DELETE SET NULL,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    acceptance_status VARCHAR(30) NOT NULL DEFAULT 'NOT READY',
    accepted_by TEXT,
    accepted_at TIMESTAMP,
    acceptance_reference VARCHAR(100),
    remarks TEXT,
    override_used BOOLEAN DEFAULT FALSE,
    override_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT site_acceptance_status_check CHECK (acceptance_status IN ('NOT READY','READY','ACCEPTED','REJECTED')),
    CONSTRAINT site_acceptance_override_reason_check CHECK (
        override_used IS FALSE
        OR override_reason IS NOT NULL
        OR acceptance_status <> 'ACCEPTED'
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_punchlist_items_duid_item_number
    ON punchlist_items(duid, item_number)
    WHERE item_number IS NOT NULL AND TRIM(item_number) <> '';

CREATE INDEX IF NOT EXISTS idx_punchlist_items_duid
    ON punchlist_items(duid);

CREATE INDEX IF NOT EXISTS idx_punchlist_items_project_id
    ON punchlist_items(project_id);

CREATE INDEX IF NOT EXISTS idx_punchlist_items_status
    ON punchlist_items(status);

CREATE INDEX IF NOT EXISTS idx_punchlist_items_priority
    ON punchlist_items(priority);

CREATE INDEX IF NOT EXISTS idx_punchlist_items_assigned_employee
    ON punchlist_items(assigned_employee_id);

CREATE INDEX IF NOT EXISTS idx_punchlist_files_item
    ON punchlist_files(punchlist_item_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pat_records_unique_duid_date_reference
    ON pat_records(duid, pat_date, COALESCE(pat_reference, ''));

CREATE INDEX IF NOT EXISTS idx_pat_records_duid
    ON pat_records(duid);

CREATE INDEX IF NOT EXISTS idx_pat_records_project_id
    ON pat_records(project_id);

CREATE INDEX IF NOT EXISTS idx_pat_records_result
    ON pat_records(result);

CREATE INDEX IF NOT EXISTS idx_pat_records_pat_date
    ON pat_records(pat_date);

CREATE UNIQUE INDEX IF NOT EXISTS idx_site_acceptance_unique_duid
    ON site_acceptance(duid);

CREATE INDEX IF NOT EXISTS idx_site_acceptance_project_id
    ON site_acceptance(project_id);

CREATE INDEX IF NOT EXISTS idx_site_acceptance_status
    ON site_acceptance(acceptance_status);

COMMIT;
